"""M4 - deterministic adaptive policy engine.

The engine builds every named candidate action, scores each one from
measurable quantities, rejects any candidate that breaks a hard constraint and
selects the cheapest eligible candidate. It is a state machine with hysteresis,
not a learned controller, and it is the only component that decides what the
shaper should do.

Where the allocations come from
-------------------------------

No fixed percentage split is used anywhere. Each candidate derives absolute
rates from two measured inputs: the per-class demand observed this interval and
the usable capacity estimate from M3A. If there is no usable capacity estimate,
class-specific candidates are rejected rather than guessed at.

How candidates are scored
-------------------------

Four cost components, each in ``[0, 1]`` and each computed from a measurable
quantity. A component that cannot be computed is ``None`` and is left out of
the total rather than filled in:

``interactive_delay_risk``
    For ``KEEP_CURRENT`` this is *measured*: delay above the idle baseline
    divided by the configured target. For any candidate that changes the
    shaper this is *modelled*: how far the candidate's ceilings fall below
    delay-sensitive demand, and how far the total of its ceilings oversubscribes
    the usable capacity. The distinction is reported so the user interface can
    show which candidates were judged on evidence and which on a projection.

``bulk_progress_risk``
    How far the candidate's bulk ceiling falls below the rate the link could
    otherwise have given bulk traffic. ``None`` when no bulk flow is active.

``fairness_penalty``
    One minus Jain's fairness index over the projected rates of *comparable*
    flows only, meaning flows in the same class with the same entitlement.
    ``None`` when no class has two or more comparable flows.

``churn_penalty``
    A changed profile shortly after the last change costs more than one made
    after the link has settled. Zero for ``KEEP_CURRENT``.

The weights live in ``POLICY_WEIGHTS`` and are versioned with
``POLICY_WEIGHTS_VERSION``. They are QoSense's own configuration, derived from
the metrics this project measures; no competitor's damage table or weighting is
used. They are expected to be tuned against held-out experiments in Round 2,
and the version string is what makes an earlier run reproducible.

Hard constraints always win. A candidate with any violation is ineligible no
matter how cheap its score is.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import Settings
from app.domain.enums import (
    DataMode,
    DataQuality,
    ExperimentMode,
    PolicyProfile,
    PolicyStatus,
    RiskBand,
    TrafficClass,
)
from app.domain.metrics import CapacityEstimate, CongestionPrediction
from app.domain.policy import (
    POLICY_WEIGHTS_VERSION,
    CandidateCost,
    ClassAllocation,
    PolicyProposal,
)
from app.simulation.engine import jain_fairness_index

# Cost weights. Versioned by POLICY_WEIGHTS_VERSION.
POLICY_WEIGHTS: dict[str, float] = {
    "interactive_delay_risk": 0.50,
    "bulk_progress_risk": 0.22,
    "fairness_penalty": 0.18,
    "churn_penalty": 0.10,
}

# Classes whose quality suffers immediately when they are not served.
DELAY_SENSITIVE_CLASSES = (
    TrafficClass.INTERACTIVE,
    TrafficClass.GAMING_LIKE,
    TrafficClass.WEB_VOICE,
)
# Classes that absorb a rate limit by slowing down rather than breaking.
ELASTIC_CLASSES = (TrafficClass.STREAMING_LIKE, TrafficClass.BULK)

# Headroom granted above a delay-sensitive class's measured demand so shaping
# does not pin it to exactly its current rate.
PROTECT_HEADROOM = 1.25
# How much of the capacity left over after delay-sensitive demand LIMIT_BULK
# allows bulk traffic to use.
LIMIT_BULK_RESIDUAL_FRACTION = 0.6
# Oversubscription of the usable rate at which modelled interactive risk
# reaches its maximum.
OVERSUBSCRIPTION_FULL_RISK = 0.25

_policy_counter = itertools.count(1)


@dataclass
class PolicyInputs:
    """Everything M4 looks at for one evaluation."""

    experiment_id: str
    mode: ExperimentMode
    data_mode: DataMode
    now: datetime
    elapsed_seconds: float
    class_demand_mbps: dict[TrafficClass, float]
    comparable_flow_demand: dict[TrafficClass, list[tuple[str, float]]]
    download_capacity: CapacityEstimate
    upload_capacity: CapacityEstimate
    upload_class_demand_mbps: dict[TrafficClass, float]
    added_delay_ms: float
    jitter_ms: float
    packet_loss_percent: float
    queue_occupancy_percent: float
    congestion_prediction: CongestionPrediction
    classification_evidence: bool
    current_profile: PolicyProfile | None
    current_allocations: list[ClassAllocation]
    seconds_since_policy_change: float
    active_intent_classes: tuple[TrafficClass, ...] = ()
    intent_valid: bool = False
    source_event_ids: list[str] = field(default_factory=list)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


class PolicyEngine:
    """Builds, scores and selects candidate policies."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Classes an active, valid user intent asks to protect. Refreshed at
        # the start of every evaluation.
        self._intent_boost: tuple[TrafficClass, ...] = ()

    # ------------------------------------------------------------------
    # Candidate construction
    # ------------------------------------------------------------------

    def _build_allocations(
        self,
        profile: PolicyProfile,
        demand: dict[TrafficClass, float],
        usable_mbps: float,
        current: list[ClassAllocation],
    ) -> list[ClassAllocation] | None:
        """Derive absolute per-class rates for one candidate.

        Returns ``None`` when the candidate cannot be expressed, for example
        ``KEEP_CURRENT`` when nothing is installed yet.
        """
        active = {c: d for c, d in demand.items() if d > 0}
        if profile is PolicyProfile.KEEP_CURRENT:
            return list(current) if current else None

        if not active:
            return None

        bulk_floor = self._settings.bulk_minimum_floor_mbps

        if profile is PolicyProfile.CONSERVATIVE_SAFE:
            # Do not pick winners. Give every class room up to the whole link
            # and rely on fair queuing to keep the standing queue short.
            return self._bound_step(
                [
                    ClassAllocation(
                        traffic_class=c,
                        ceiling_mbps=round(usable_mbps, 4),
                        guaranteed_floor_mbps=round(
                            bulk_floor if c is TrafficClass.BULK else 0.0, 4
                        ),
                        share_of_capacity_percent=100.0,
                    )
                    for c in active
                ],
                current,
                usable_mbps,
            )

        if profile is PolicyProfile.REBALANCE_CLASSES:
            # Max-min fair by class: a class wanting less than an equal share
            # takes only what it wants and the rest is split among the others.
            ceilings = self._max_min_by_class(active, usable_mbps)
            return self._bound_step(
                [
                    ClassAllocation(
                        traffic_class=c,
                        ceiling_mbps=round(ceilings[c], 4),
                        guaranteed_floor_mbps=round(
                            bulk_floor
                            if c is TrafficClass.BULK
                            else min(active[c], ceilings[c]) * 0.5,
                            4,
                        ),
                        share_of_capacity_percent=(
                            round(ceilings[c] / usable_mbps * 100.0, 2)
                            if usable_mbps > 0
                            else None
                        ),
                    )
                    for c in active
                ],
                current,
                usable_mbps,
            )

        # PROTECT_INTERACTIVE and LIMIT_BULK share a shape: cover delay
        # sensitive demand with headroom first, then decide what is left for
        # the elastic classes.
        allocations: dict[TrafficClass, tuple[float, float]] = {}
        protected_total = 0.0
        for c in active:
            if c in DELAY_SENSITIVE_CLASSES or c in self._intent_boost:
                ceiling = active[c] * PROTECT_HEADROOM
                allocations[c] = (ceiling, active[c])
                protected_total += ceiling

        residual = max(0.0, usable_mbps - protected_total)

        streaming_demand = active.get(TrafficClass.STREAMING_LIKE, 0.0)
        if TrafficClass.STREAMING_LIKE in active:
            if profile is PolicyProfile.LIMIT_BULK:
                stream_ceiling = min(streaming_demand, residual)
            else:
                stream_ceiling = min(streaming_demand * PROTECT_HEADROOM, residual)
            allocations[TrafficClass.STREAMING_LIKE] = (
                stream_ceiling,
                min(streaming_demand * 0.6, stream_ceiling),
            )
            residual = max(0.0, residual - stream_ceiling)

        for c in active:
            if c in allocations:
                continue
            if c is TrafficClass.BULK or c is TrafficClass.UNKNOWN:
                if profile is PolicyProfile.LIMIT_BULK:
                    ceiling = max(bulk_floor, residual * LIMIT_BULK_RESIDUAL_FRACTION)
                else:
                    ceiling = max(bulk_floor, residual)
                allocations[c] = (ceiling, bulk_floor if c is TrafficClass.BULK else 0.0)
            else:
                allocations[c] = (min(active[c] * PROTECT_HEADROOM, usable_mbps), 0.0)

        return self._bound_step(
            [
                ClassAllocation(
                    traffic_class=c,
                    ceiling_mbps=round(ceiling, 4),
                    guaranteed_floor_mbps=round(min(floor, ceiling), 4),
                    share_of_capacity_percent=(
                        round(ceiling / usable_mbps * 100.0, 2) if usable_mbps > 0 else None
                    ),
                )
                for c, (ceiling, floor) in allocations.items()
            ],
            current,
            usable_mbps,
        )

    def _bound_step(
        self,
        proposed: list[ClassAllocation],
        current: list[ClassAllocation],
        usable_mbps: float,
    ) -> list[ClassAllocation]:
        """Limit how far one evaluation may move any class ceiling.

        Bounded changes are a safety property, so the engine applies the limit
        when it builds a candidate rather than proposing something the Safety
        Guard would only reject. The guard still checks it independently; this
        makes a rejection there a genuine anomaly instead of routine churn.
        A large correction is still reachable, just over several evaluations.
        """
        if not current or usable_mbps <= 0:
            return proposed
        # Stay just inside the limit. Clamping to exactly the limit and then
        # rounding the rate can land a hair above it, and the Safety Guard
        # independently recomputes the step, so an exact clamp produces
        # rejections that are arithmetic artefacts rather than real findings.
        max_step = usable_mbps * self._settings.max_policy_rate_step_percent / 100.0 * 0.999
        before = {a.traffic_class: a.ceiling_mbps for a in current}

        bounded: list[ClassAllocation] = []
        for alloc in proposed:
            previous = before.get(alloc.traffic_class)
            if previous is None:
                bounded.append(alloc)
                continue
            ceiling = min(max(alloc.ceiling_mbps, previous - max_step), previous + max_step)
            bounded.append(
                alloc.model_copy(
                    update={
                        "ceiling_mbps": round(ceiling, 4),
                        "guaranteed_floor_mbps": round(
                            min(alloc.guaranteed_floor_mbps, ceiling), 4
                        ),
                        "share_of_capacity_percent": (
                            round(ceiling / usable_mbps * 100.0, 2) if usable_mbps > 0 else None
                        ),
                    }
                )
            )
        return bounded

    @staticmethod
    def _max_min_by_class(
        demand: dict[TrafficClass, float], capacity: float
    ) -> dict[TrafficClass, float]:
        allocation = dict.fromkeys(demand, 0.0)
        remaining = capacity
        pending = set(demand)
        while pending and remaining > 1e-9:
            share = remaining / len(pending)
            settled = {c for c in pending if demand[c] - allocation[c] <= share}
            if not settled:
                for c in pending:
                    allocation[c] += share
                break
            for c in settled:
                grant = demand[c] - allocation[c]
                allocation[c] += grant
                remaining -= grant
            pending -= settled
        return allocation

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score_candidate(
        self,
        profile: PolicyProfile,
        allocations: list[ClassAllocation] | None,
        inputs: PolicyInputs,
    ) -> CandidateCost:
        notes: list[str] = []
        violations: list[str] = []
        usable = inputs.download_capacity.usable_capacity_mbps
        demand = inputs.class_demand_mbps
        bulk_floor = self._settings.bulk_minimum_floor_mbps

        if allocations is None:
            return CandidateCost(
                profile=profile,
                interactive_delay_risk=None,
                bulk_progress_risk=None,
                fairness_penalty=None,
                churn_penalty=None,
                total_cost=None,
                hard_constraint_violations=["NO_ALLOCATION_AVAILABLE"],
                eligible=False,
                notes=["Candidate cannot be expressed in the current state."],
            )

        by_class = {a.traffic_class: a for a in allocations}

        # ---- hard constraints -------------------------------------------------
        for alloc in allocations:
            if alloc.guaranteed_floor_mbps > alloc.ceiling_mbps + 1e-9:
                violations.append("VIOLATION_FLOOR_ABOVE_CEILING")
                break

        if demand.get(TrafficClass.BULK, 0.0) > 0:
            bulk_alloc = by_class.get(TrafficClass.BULK)
            if bulk_alloc is None or bulk_alloc.ceiling_mbps + 1e-9 < bulk_floor:
                violations.append("VIOLATION_BULK_MINIMUM_SERVICE")

        if usable is not None:
            floors_total = sum(a.guaranteed_floor_mbps for a in allocations)
            if floors_total > usable + 1e-9:
                violations.append("VIOLATION_FLOORS_EXCEED_CAPACITY")

        changes_policy = profile is not PolicyProfile.KEEP_CURRENT and (
            inputs.current_profile is None or profile is not inputs.current_profile
        )
        if (
            changes_policy
            and inputs.seconds_since_policy_change < self._settings.policy_min_hold_seconds
        ):
            violations.append("VIOLATION_MIN_HOLD_NOT_SATISFIED")

        if changes_policy and not self._capacity_usable(inputs.download_capacity):
            violations.append("VIOLATION_CAPACITY_ESTIMATE_UNRELIABLE")

        class_specific = profile in (PolicyProfile.PROTECT_INTERACTIVE, PolicyProfile.LIMIT_BULK)
        if class_specific and not inputs.classification_evidence:
            violations.append("VIOLATION_CLASS_EVIDENCE_INSUFFICIENT")

        if changes_policy and usable is not None:
            step = self._largest_rate_step_percent(inputs.current_allocations, allocations, usable)
            if step is not None and step > self._settings.max_policy_rate_step_percent:
                notes.append(
                    f"Largest single-class rate change is {step:.1f} percent of the usable rate; "
                    f"the Safety Guard limit is "
                    f"{self._settings.max_policy_rate_step_percent:.0f} percent."
                )

        # ---- interactive delay risk -------------------------------------------
        interactive_risk: float | None
        if profile is PolicyProfile.KEEP_CURRENT:
            # Measured, not modelled: this is what the link is doing right now.
            interactive_risk = _clamp(
                inputs.added_delay_ms / self._settings.added_delay_target_ms
            )
            notes.append("Interactive risk measured from current delay above the idle baseline.")
        elif usable is None:
            interactive_risk = None
            notes.append("No usable capacity estimate, so interactive risk cannot be projected.")
        else:
            starvation = 0.0
            sensitive_demand = sum(demand.get(c, 0.0) for c in DELAY_SENSITIVE_CLASSES)
            if sensitive_demand > 0:
                unmet = sum(
                    max(0.0, demand.get(c, 0.0) - by_class[c].ceiling_mbps)
                    for c in DELAY_SENSITIVE_CLASSES
                    if c in by_class
                )
                missing = sum(
                    demand.get(c, 0.0) for c in DELAY_SENSITIVE_CLASSES if c not in by_class
                )
                starvation = _clamp((unmet + missing) / sensitive_demand)
            total_ceiling = sum(a.ceiling_mbps for a in allocations)
            oversubscription = max(0.0, total_ceiling - usable) / usable if usable > 0 else 0.0
            pressure = _clamp(oversubscription / OVERSUBSCRIPTION_FULL_RISK)
            interactive_risk = max(starvation, pressure)
            notes.append(
                f"Interactive risk projected from ceilings: starvation {starvation:.2f}, "
                f"oversubscription pressure {pressure:.2f}."
            )

        # ---- bulk progress risk ------------------------------------------------
        bulk_demand = demand.get(TrafficClass.BULK, 0.0)
        bulk_risk: float | None
        if bulk_demand <= 0:
            bulk_risk = None
            notes.append("No bulk flow active, so bulk progress risk does not apply.")
        elif usable is None:
            bulk_risk = None
        else:
            sensitive_demand = sum(demand.get(c, 0.0) for c in DELAY_SENSITIVE_CLASSES)
            available_for_bulk = max(0.0, usable - sensitive_demand)
            bulk_allocation = by_class.get(TrafficClass.BULK)
            bulk_ceiling = bulk_allocation.ceiling_mbps if bulk_allocation else 0.0
            if available_for_bulk <= 0:
                bulk_risk = 0.0 if bulk_ceiling > 0 else 1.0
            else:
                bulk_risk = _clamp(1.0 - bulk_ceiling / available_for_bulk)

        # ---- fairness ----------------------------------------------------------
        fairness_penalty = self._fairness_penalty(inputs, by_class, usable)

        # ---- churn -------------------------------------------------------------
        if not changes_policy:
            churn = 0.0
        else:
            cooldown = max(self._settings.policy_cooldown_seconds, 1e-9)
            settled = inputs.seconds_since_policy_change - self._settings.policy_min_hold_seconds
            churn = _clamp(1.0 - settled / cooldown)

        components = {
            "interactive_delay_risk": interactive_risk,
            "bulk_progress_risk": bulk_risk,
            "fairness_penalty": fairness_penalty,
            "churn_penalty": churn,
        }
        usable_weights = {
            name: POLICY_WEIGHTS[name] for name, value in components.items() if value is not None
        }
        total_cost: float | None
        if not usable_weights:
            total_cost = None
        else:
            weight_sum = sum(usable_weights.values())
            total_cost = round(
                sum(POLICY_WEIGHTS[n] * components[n] for n in usable_weights) / weight_sum, 6  # type: ignore[operator]
            )

        return CandidateCost(
            profile=profile,
            interactive_delay_risk=(
                round(interactive_risk, 4) if interactive_risk is not None else None
            ),
            bulk_progress_risk=round(bulk_risk, 4) if bulk_risk is not None else None,
            fairness_penalty=(
                round(fairness_penalty, 4) if fairness_penalty is not None else None
            ),
            churn_penalty=round(churn, 4),
            total_cost=total_cost,
            hard_constraint_violations=sorted(set(violations)),
            eligible=not violations and total_cost is not None,
            notes=notes,
        )

    def _fairness_penalty(
        self,
        inputs: PolicyInputs,
        by_class: dict[TrafficClass, ClassAllocation],
        usable: float | None,
    ) -> float | None:
        """One minus Jain's index over comparable flows' projected rates.

        Comparable means same traffic class and therefore the same service
        entitlement. The index is never computed across different priority
        classes, because an intentional difference is not unfairness.
        """
        worst: float | None = None
        for traffic_class, members in inputs.comparable_flow_demand.items():
            if len(members) < 2:
                continue
            alloc = by_class.get(traffic_class)
            if alloc is None:
                continue
            demands = [d for _, d in members]
            projected = self._project_within_class(demands, alloc.ceiling_mbps)
            index = jain_fairness_index(projected)
            if index is None:
                continue
            penalty = _clamp(1.0 - index)
            worst = penalty if worst is None else max(worst, penalty)
        return worst

    @staticmethod
    def _project_within_class(demands: list[float], ceiling: float) -> list[float]:
        """Max-min share of a class ceiling across its own flows."""
        allocation = [0.0] * len(demands)
        remaining = ceiling
        pending = {i for i, d in enumerate(demands) if d > 0}
        while pending and remaining > 1e-9:
            share = remaining / len(pending)
            settled = {i for i in pending if demands[i] - allocation[i] <= share}
            if not settled:
                for i in pending:
                    allocation[i] += share
                break
            for i in settled:
                grant = demands[i] - allocation[i]
                allocation[i] += grant
                remaining -= grant
            pending -= settled
        return allocation

    @staticmethod
    def _largest_rate_step_percent(
        current: list[ClassAllocation], proposed: list[ClassAllocation], usable: float
    ) -> float | None:
        if not current or usable <= 0:
            return None
        current_by_class = {a.traffic_class: a.ceiling_mbps for a in current}
        largest = 0.0
        for alloc in proposed:
            before = current_by_class.get(alloc.traffic_class)
            if before is None:
                continue
            largest = max(largest, abs(alloc.ceiling_mbps - before) / usable * 100.0)
        return largest

    def _capacity_usable(self, estimate: CapacityEstimate) -> bool:
        return estimate.usable_capacity_mbps is not None and estimate.quality in (
            DataQuality.GOOD,
            DataQuality.LOW_CONFIDENCE,
        )

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, inputs: PolicyInputs) -> PolicyProposal:
        """Score every candidate and return the selected proposal."""
        self._intent_boost = inputs.active_intent_classes if inputs.intent_valid else ()

        usable_download = inputs.download_capacity.usable_capacity_mbps or 0.0
        usable_upload = inputs.upload_capacity.usable_capacity_mbps or 0.0

        candidates: list[CandidateCost] = []
        allocation_by_profile: dict[PolicyProfile, list[ClassAllocation] | None] = {}

        for profile in PolicyProfile:
            allocations = self._build_allocations(
                profile, inputs.class_demand_mbps, usable_download, inputs.current_allocations
            )
            allocation_by_profile[profile] = allocations
            candidates.append(self._score_candidate(profile, allocations, inputs))

        eligible = [c for c in candidates if c.eligible and c.total_cost is not None]
        if eligible:
            # Deterministic tie-breaking: lowest cost, then the profile that
            # changes least, then declaration order.
            order = list(PolicyProfile)
            selected = min(
                eligible,
                key=lambda c: (
                    c.total_cost,
                    0 if c.profile is inputs.current_profile else 1,
                    order.index(c.profile),
                ),
            )
            chosen_profile = selected.profile
            reason_codes = self._reason_codes(inputs, selected)
            explanation = self._explain(inputs, selected, candidates)
        else:
            chosen_profile = PolicyProfile.KEEP_CURRENT
            reason_codes = ["NO_ELIGIBLE_CANDIDATE", "RETAINING_CURRENT_SAFE_STATE"]
            explanation = (
                "No candidate passed its hard constraints, so the current state is retained. "
                + "; ".join(
                    f"{c.profile.value}: {', '.join(c.hard_constraint_violations)}"
                    for c in candidates
                    if c.hard_constraint_violations
                )
            )

        chosen_allocations = allocation_by_profile.get(chosen_profile) or []
        upload_allocations = (
            self._build_allocations(
                chosen_profile,
                inputs.upload_class_demand_mbps,
                usable_upload,
                [],
            )
            or []
        )

        policy_id = f"policy-{next(_policy_counter):04d}"
        valid_until: datetime | None = None
        if self._intent_boost:
            valid_until = inputs.now + timedelta(seconds=self._settings.policy_min_hold_seconds)

        return PolicyProposal(
            policy_id=policy_id,
            experiment_id=inputs.experiment_id,
            profile=chosen_profile,
            created_at=inputs.now,
            data_mode=inputs.data_mode,
            reason_codes=reason_codes,
            explanation=explanation,
            source_event_ids=list(inputs.source_event_ids),
            class_allocations=chosen_allocations,
            upload_class_allocations=upload_allocations,
            class_rates_mbps={a.traffic_class: a.ceiling_mbps for a in chosen_allocations},
            candidates=candidates,
            weights_version=POLICY_WEIGHTS_VERSION,
            based_on_capacity_mbps=inputs.download_capacity.usable_capacity_mbps,
            valid_until=valid_until,
            status=PolicyStatus.PROPOSED,
        )

    def _reason_codes(self, inputs: PolicyInputs, selected: CandidateCost) -> list[str]:
        codes: list[str] = [f"SELECTED_{selected.profile.value}"]
        if inputs.added_delay_ms >= self._settings.added_delay_target_ms:
            codes.append("ADDED_DELAY_ABOVE_TARGET")
        elif inputs.added_delay_ms >= self._settings.added_delay_target_ms * 0.5:
            codes.append("ADDED_DELAY_APPROACHING_TARGET")
        if inputs.queue_occupancy_percent >= 50.0:
            codes.append("QUEUE_OCCUPANCY_HIGH")
        if inputs.packet_loss_percent > 0.1:
            codes.append("PACKET_LOSS_OBSERVED")
        if inputs.class_demand_mbps.get(TrafficClass.BULK, 0.0) > 0:
            codes.append("BULK_FLOW_PRESENT")
        if any(inputs.class_demand_mbps.get(c, 0.0) > 0 for c in DELAY_SENSITIVE_CLASSES):
            codes.append("DELAY_SENSITIVE_FLOW_PRESENT")
        if not inputs.classification_evidence:
            codes.append("CLASS_EVIDENCE_INSUFFICIENT")
        if inputs.congestion_prediction.risk_band is RiskBand.UNAVAILABLE:
            codes.append("CONGESTION_PREDICTION_UNAVAILABLE")
        elif inputs.congestion_prediction.risk_band in (RiskBand.ELEVATED, RiskBand.HIGH):
            codes.append("CONGESTION_RISK_ELEVATED")
        if inputs.intent_valid:
            codes.append("USER_INTENT_ACTIVE")
        if not self._capacity_usable(inputs.download_capacity):
            codes.append("CAPACITY_ESTIMATE_UNRELIABLE")
        return codes

    def _explain(
        self, inputs: PolicyInputs, selected: CandidateCost, candidates: list[CandidateCost]
    ) -> str:
        scored = [c for c in candidates if c.total_cost is not None and c.eligible]
        runner_up = sorted(scored, key=lambda c: c.total_cost)[1:2]  # type: ignore[arg-type]
        parts = [
            f"Selected {selected.profile.value} with a computed cost of {selected.total_cost:.3f}."
        ]
        if runner_up:
            other = runner_up[0]
            parts.append(
                f"Next best was {other.profile.value} at {other.total_cost:.3f}."
            )
        rejected = [c for c in candidates if c.hard_constraint_violations]
        if rejected:
            parts.append(
                "Rejected by hard constraint: "
                + "; ".join(
                    f"{c.profile.value} ({', '.join(c.hard_constraint_violations)})"
                    for c in rejected
                )
                + "."
            )
        parts.append(
            f"Measured delay above baseline was {inputs.added_delay_ms:.1f} ms against a "
            f"{self._settings.added_delay_target_ms:.0f} ms target."
        )
        if inputs.download_capacity.usable_capacity_mbps is None:
            parts.append("No usable capacity estimate was available.")
        else:
            parts.append(
                f"Usable download capacity was estimated at "
                f"{inputs.download_capacity.usable_capacity_mbps:.1f} Mbps "
                f"({inputs.download_capacity.quality.value})."
            )
        return " ".join(parts)
