"""Safety Guard - the gate between a policy decision and any enforcement.

Every proposal passes through here before it reaches an enforcer, in simulation
as well as in a live laboratory. A rejection is a visible, logged outcome, not
a silent fallback: the proposal is marked ``REJECTED``, the failing checks are
attached to it and the previous known-safe state is retained.

The guard re-derives its own view of the proposal rather than trusting the
policy engine's. That is the point of having it: a bug in M4 should be caught
here rather than reaching a shaper.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.domain.enums import DataQuality, PolicyProfile, PolicyStatus, TrafficClass
from app.domain.metrics import CapacityEstimate
from app.domain.policy import (
    ClassAllocation,
    PolicyProposal,
    SafetyCheck,
    SafetyCheckResult,
)

# A measurement older than this is not a safe basis for a shaping change.
MAX_MEASUREMENT_AGE_SECONDS = 5.0


class SafetyGuard:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def evaluate(
        self,
        proposal: PolicyProposal,
        *,
        capacity: CapacityEstimate,
        current_allocations: list[ClassAllocation],
        seconds_since_policy_change: float,
        measurement_age_seconds: float,
        bulk_demand_mbps: float,
        intent_valid: bool,
        intent_present: bool,
        rollback_available: bool,
        is_policy_change: bool,
        now: datetime | None = None,
    ) -> SafetyCheckResult:
        now = now or datetime.now(UTC)
        checks: list[SafetyCheck] = []

        checks.append(self._check_profile_known(proposal))
        checks.append(self._check_measurements_fresh(measurement_age_seconds))
        checks.append(self._check_capacity_fresh(capacity, proposal))
        checks.append(self._check_total_allocation(proposal, capacity))
        checks.append(self._check_floors(proposal))
        checks.append(self._check_bulk_minimum_service(proposal, bulk_demand_mbps))
        checks.append(self._check_rate_change(proposal, current_allocations, capacity))
        checks.append(self._check_hold_time(is_policy_change, seconds_since_policy_change))
        checks.append(self._check_intent(intent_present, intent_valid))
        checks.append(self._check_rollback(rollback_available))

        failures = [c for c in checks if not c.passed]
        return SafetyCheckResult(
            accepted=not failures,
            checks=checks,
            rejection_reasons=[c.name for c in failures],
            evaluated_at=now,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _check_profile_known(proposal: PolicyProposal) -> SafetyCheck:
        known = proposal.profile in set(PolicyProfile)
        return SafetyCheck(
            name="profile_known",
            passed=known,
            detail=(
                f"Profile '{proposal.profile.value}' is one of the predefined named profiles."
                if known
                else f"Profile '{proposal.profile}' is not a predefined profile."
            ),
        )

    def _check_measurements_fresh(self, age_seconds: float) -> SafetyCheck:
        fresh = age_seconds <= MAX_MEASUREMENT_AGE_SECONDS
        return SafetyCheck(
            name="measurements_fresh",
            passed=fresh,
            detail=(
                f"Most recent measurement is {age_seconds:.2f} s old "
                f"(limit {MAX_MEASUREMENT_AGE_SECONDS:.0f} s)."
            ),
        )

    def _check_capacity_fresh(
        self, capacity: CapacityEstimate, proposal: PolicyProposal
    ) -> SafetyCheck:
        if proposal.profile is PolicyProfile.KEEP_CURRENT:
            return SafetyCheck(
                name="capacity_fresh",
                passed=True,
                detail="Keeping the current policy does not depend on the capacity estimate.",
            )
        ok = capacity.usable_capacity_mbps is not None and capacity.quality in (
            DataQuality.GOOD,
            DataQuality.LOW_CONFIDENCE,
        )
        return SafetyCheck(
            name="capacity_fresh",
            passed=ok,
            detail=(
                f"Capacity estimate quality is {capacity.quality.value} "
                f"from {capacity.sample_count} saturated sample(s)."
            ),
        )

    def _check_total_allocation(
        self, proposal: PolicyProposal, capacity: CapacityEstimate
    ) -> SafetyCheck:
        usable = capacity.usable_capacity_mbps
        if usable is None or not proposal.class_allocations:
            return SafetyCheck(
                name="total_allocation_within_capacity",
                passed=True,
                detail="No allocation to check against a usable capacity estimate.",
            )
        floors = sum(a.guaranteed_floor_mbps for a in proposal.class_allocations)
        ok = floors <= usable + 1e-9
        return SafetyCheck(
            name="total_allocation_within_capacity",
            passed=ok,
            detail=(
                f"Guaranteed floors total {floors:.2f} Mbps against a usable estimate of "
                f"{usable:.2f} Mbps. Ceilings may overlap because spare capacity is lent "
                f"between classes; only the floors are a firm commitment."
            ),
        )

    @staticmethod
    def _check_floors(proposal: PolicyProposal) -> SafetyCheck:
        bad = [
            a.traffic_class.value
            for a in proposal.class_allocations
            if a.guaranteed_floor_mbps > a.ceiling_mbps + 1e-9
        ]
        return SafetyCheck(
            name="floors_within_ceilings",
            passed=not bad,
            detail=(
                "Every guaranteed floor is at or below its class ceiling."
                if not bad
                else f"Floor exceeds ceiling for: {', '.join(bad)}."
            ),
        )

    def _check_bulk_minimum_service(
        self, proposal: PolicyProposal, bulk_demand_mbps: float
    ) -> SafetyCheck:
        floor = self._settings.bulk_minimum_floor_mbps
        if bulk_demand_mbps <= 0:
            return SafetyCheck(
                name="bulk_minimum_service",
                passed=True,
                detail="No bulk traffic is active, so the minimum service floor does not apply.",
            )
        if not proposal.class_allocations:
            return SafetyCheck(
                name="bulk_minimum_service",
                passed=True,
                detail=(
                    "The proposal installs no shaping at all, so no class is rate limited "
                    "and the minimum service floor cannot be breached."
                ),
            )
        allocation = next(
            (a for a in proposal.class_allocations if a.traffic_class is TrafficClass.BULK),
            None,
        )
        if allocation is None:
            return SafetyCheck(
                name="bulk_minimum_service",
                passed=False,
                detail=(
                    f"Bulk traffic is active and shaping is installed, but the proposal "
                    f"gives bulk no class of its own, so the {floor:.1f} Mbps minimum "
                    f"cannot be guaranteed."
                ),
            )
        ok = allocation.ceiling_mbps + 1e-9 >= floor
        return SafetyCheck(
            name="bulk_minimum_service",
            passed=ok,
            detail=(
                f"Bulk ceiling is {allocation.ceiling_mbps:.2f} Mbps against a "
                f"{floor:.1f} Mbps minimum service floor."
            ),
        )

    def _check_rate_change(
        self,
        proposal: PolicyProposal,
        current: list[ClassAllocation],
        capacity: CapacityEstimate,
    ) -> SafetyCheck:
        usable = capacity.usable_capacity_mbps
        if not current or usable is None or usable <= 0:
            return SafetyCheck(
                name="rate_change_within_limit",
                passed=True,
                detail="No previous allocation to compare against; the step limit does not apply.",
            )
        before = {a.traffic_class: a.ceiling_mbps for a in current}
        largest = 0.0
        worst_class = ""
        for alloc in proposal.class_allocations:
            previous = before.get(alloc.traffic_class)
            if previous is None:
                continue
            step = abs(alloc.ceiling_mbps - previous) / usable * 100.0
            if step > largest:
                largest, worst_class = step, alloc.traffic_class.value
        limit = self._settings.max_policy_rate_step_percent
        ok = largest <= limit + 1e-9
        return SafetyCheck(
            name="rate_change_within_limit",
            passed=ok,
            detail=(
                f"Largest single-class ceiling change is {largest:.1f} percent of the usable rate"
                + (f" ({worst_class})" if worst_class else "")
                + f", against a {limit:.0f} percent limit."
            ),
        )

    def _check_hold_time(self, is_policy_change: bool, seconds_since: float) -> SafetyCheck:
        if not is_policy_change:
            return SafetyCheck(
                name="minimum_hold_satisfied",
                passed=True,
                detail=(
                    "The proposal does not change the installed profile, so the minimum "
                    "hold does not apply."
                ),
            )
        minimum = self._settings.policy_min_hold_seconds
        ok = seconds_since >= minimum
        return SafetyCheck(
            name="minimum_hold_satisfied",
            passed=ok,
            detail=(
                f"{seconds_since:.1f} s since the last policy change, against a "
                f"{minimum:.1f} s minimum hold that prevents flapping."
            ),
        )

    @staticmethod
    def _check_intent(intent_present: bool, intent_valid: bool) -> SafetyCheck:
        if not intent_present:
            return SafetyCheck(
                name="intent_valid",
                passed=True,
                detail="No temporary priority lease is active.",
            )
        return SafetyCheck(
            name="intent_valid",
            passed=intent_valid,
            detail=(
                "The active priority lease has not expired."
                if intent_valid
                else "The priority lease has expired and must not influence the policy."
            ),
        )

    def _check_rollback(self, available: bool) -> SafetyCheck:
        if not self._settings.require_policy_readback:
            return SafetyCheck(
                name="rollback_available",
                passed=True,
                detail="Read-back and rollback requirement is disabled by configuration.",
            )
        return SafetyCheck(
            name="rollback_available",
            passed=available,
            detail=(
                "A known-safe snapshot exists and can be restored."
                if available
                else "No restorable snapshot is available, so no change may be applied."
            ),
        )


def apply_safety_result(proposal: PolicyProposal, result: SafetyCheckResult) -> PolicyProposal:
    """Attach the guard's verdict to the proposal."""
    proposal.safety = result
    proposal.status = PolicyStatus.ACCEPTED if result.accepted else PolicyStatus.REJECTED
    return proposal
