"""Experiment orchestration.

One :class:`ExperimentRuntime` owns everything about a single run: the
simulator, the observer, the classifier, both capacity estimators, the policy
engine, the Safety Guard, the enforcer, the verifier and the watchdog. The
manager owns the set of runs and the asynchronous loop that advances them.

Per tick, in this order:

1. advance the simulator;
2. feed the observer, and when a feature window closes, classify each flow;
3. update both capacity estimators from delivered rate and delay evidence;
4. publish the metrics snapshot and the flow updates;
5. on the policy cadence, evaluate candidates, run the Safety Guard and, if the
   selected profile differs from what is installed, enforce it transactionally;
6. finish any verification whose measurement window has elapsed, rolling back
   when the outcome is degraded;
7. stamp the watchdog heartbeat and expire due intent leases.

The loop drives the simulator by tick count, not by wall-clock drift, so a slow
frame or a slow client cannot change what the experiment measured.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import statistics
from datetime import UTC, datetime

from app.config import Settings
from app.control.capacity import CapacityEstimator
from app.control.classifier import TrafficClassifier
from app.control.congestion import CongestionPredictor
from app.control.enforcement import SimulatedEnforcer
from app.control.intent import IntentManager
from app.control.observer import FlowObserver
from app.control.policy_engine import PolicyEngine, PolicyInputs
from app.control.safety_guard import SafetyGuard, apply_safety_result
from app.control.verification import MeasurementWindow, Verifier, Watchdog
from app.domain.enums import (
    ClassificationSource,
    DataMode,
    Direction,
    EnforcementStatus,
    EventSeverity,
    EventType,
    ExperimentMode,
    ExperimentStatus,
    PolicyProfile,
    RiskBand,
    TrafficClass,
    VerificationOutcome,
)
from app.domain.experiment import (
    ExperimentConfig,
    ExperimentCreateRequest,
    ExperimentReport,
    ExperimentState,
    MetricSummary,
    ModeAvailability,
)
from app.domain.metrics import (
    FEATURE_SCHEMA_VERSION,
    ClassificationResult,
    DirectionMetrics,
    FlowState,
    MetricsSnapshot,
)
from app.domain.policy import PolicyHistoryEntry, PolicyProposal
from app.domain.topology import Topology
from app.models.registry import MODEL_1, MODEL_2, ModelRegistry
from app.runtime.event_bus import EventBus
from app.simulation.engine import SimulationEngine, jain_fairness_index
from app.simulation.scenarios import build_topology, clamp_parameters, get_scenario

_experiment_counter = itertools.count(1)

# How often the controller evaluates a policy, in seconds. Slower than the
# metric tick on purpose: the policy engine reacts to a trend, not to one
# sample.
POLICY_EVALUATION_INTERVAL_SECONDS = 1.0
# How often an unavailable-model congestion prediction record is republished,
# so the user interface keeps an accurate, current picture of Model 2's state.
CONGESTION_PREDICTION_INTERVAL_SECONDS = 2.0


def _mode_availability(mode: ExperimentMode, registry: ModelRegistry) -> ModeAvailability:
    """Report honestly whether a mode can run with the components present."""
    if mode is ExperimentMode.NO_QOS:
        return ModeAvailability(
            mode=mode,
            available=True,
            degraded=False,
            requires=[],
            detail="Reference condition. The controller observes but never shapes.",
        )
    if mode is ExperimentMode.TRADITIONAL_ADAPTIVE:
        return ModeAvailability(
            mode=mode,
            available=True,
            degraded=False,
            requires=["Deterministic rule classifier", "Capacity estimator", "Policy engine"],
            detail=(
                "Runs end to end. Traffic classes come from the versioned deterministic "
                "rule set, not from a trained model."
            ),
        )
    if mode is ExperimentMode.CLASSIFIER_ONLY:
        ready = registry.is_ready(MODEL_1.key)
        return ModeAvailability(
            mode=mode,
            available=True,
            degraded=not ready,
            requires=["Model 1 traffic classifier"],
            detail=(
                "Model 1 is loaded and classifying."
                if ready
                else (
                    "Model 1 is not trained in this build, so every flow takes the safe "
                    "UNKNOWN fallback and the policy engine avoids class-specific actions. "
                    "This run must not be presented as a working Mode 3 result."
                )
            ),
        )
    ready_1 = registry.is_ready(MODEL_1.key)
    ready_2 = registry.is_ready(MODEL_2.key)
    return ModeAvailability(
        mode=mode,
        available=True,
        degraded=not (ready_1 and ready_2),
        requires=["Model 1 traffic classifier", "Model 2 congestion predictor"],
        detail=(
            "Both models are loaded."
            if ready_1 and ready_2
            else (
                "Model 1 (Phase 4) and Model 2 (Phase 5, Round 2) are not trained in this "
                "build. Flows take the safe UNKNOWN fallback and no congestion prediction "
                "is produced. This run must not be presented as a working Mode 4 result."
            )
        ),
    )


class ExperimentRuntime:
    """A single experiment run and everything it owns."""

    def __init__(
        self,
        request: ExperimentCreateRequest,
        settings: Settings,
        registry: ModelRegistry,
        bus: EventBus,
    ) -> None:
        scenario = get_scenario(request.scenario_id)
        parameters, notes = clamp_parameters(scenario, request.parameters)

        duration = request.duration_seconds or parameters.get(
            "duration_seconds", scenario.default_duration_seconds
        )
        duration = min(
            max(duration, settings.min_scenario_duration_seconds),
            settings.max_scenario_duration_seconds,
        )
        parameters["duration_seconds"] = duration

        self.settings = settings
        self.registry = registry
        self.bus = bus
        self.scenario = scenario

        experiment_id = f"exp-{next(_experiment_counter):04d}"
        self.config = ExperimentConfig(
            experiment_id=experiment_id,
            scenario_id=scenario.scenario_id,
            scenario_name=scenario.name,
            mode=request.mode,
            data_mode=request.data_mode,
            seed=request.seed,
            duration_seconds=duration,
            tick_seconds=settings.tick_seconds,
            parameters=parameters,
            repeat_label=request.repeat_label,
            created_at=datetime.now(UTC),
            weights_version="qosense-policy-weights-v1",
            feature_schema_version=FEATURE_SCHEMA_VERSION,
            model_versions={
                MODEL_1.key: registry.version(MODEL_1.key),
                MODEL_2.key: registry.version(MODEL_2.key),
            },
        )
        self.mode_availability = _mode_availability(request.mode, registry)
        self.status = ExperimentStatus.CREATED
        self.notes = list(notes)
        if self.mode_availability.degraded:
            self.notes.append(self.mode_availability.detail)

        self.engine = SimulationEngine(
            scenario=scenario,
            parameters=parameters,
            seed=request.seed,
            tick_seconds=settings.tick_seconds,
            duration_seconds=duration,
        )
        self.topology: Topology = build_topology(scenario, request.data_mode)
        self.observer = FlowObserver()
        self.classifier = TrafficClassifier(registry)
        self.congestion_predictor = CongestionPredictor(registry)
        self.capacity = {
            Direction.DOWNLOAD: CapacityEstimator(settings, Direction.DOWNLOAD),
            Direction.UPLOAD: CapacityEstimator(settings, Direction.UPLOAD),
        }
        self.policy_engine = PolicyEngine(settings)
        self.safety_guard = SafetyGuard(settings)
        self.enforcer = SimulatedEnforcer(settings, self.engine)
        self.verifier = Verifier(settings)
        self.watchdog = Watchdog()
        self.intents = IntentManager(settings)

        self.flow_states: dict[str, FlowState] = {}
        # Surviving per-flow totals, kept after a flow ends so the report can
        # measure fairness over bytes actually delivered across the whole run
        # rather than over one instant, where a streaming player that happens
        # to be between buffer fills looks like it got nothing.
        self.flow_totals: dict[str, tuple[TrafficClass, Direction, int]] = {}
        self.classifications: dict[str, ClassificationResult] = {}
        self.latest_snapshot: MetricsSnapshot | None = None
        self.policy_history: list[PolicyHistoryEntry] = []
        self.current_proposal: PolicyProposal | None = None
        self.current_profile: PolicyProfile | None = None
        self.last_policy_change_elapsed = 0.0
        self.safety_rejections = 0
        self.rollbacks = 0
        self.policy_changes = 0
        self.zero_progress_windows = 0

        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._last_policy_eval = -POLICY_EVALUATION_INTERVAL_SECONDS
        self._last_prediction_publish = -CONGESTION_PREDICTION_INTERVAL_SECONDS
        self._rolling_window = MeasurementWindow()
        self._pending_verification: (
            tuple[str, MeasurementWindow, MeasurementWindow, float, str | None] | None
        ) = None
        self._samples: dict[str, list[float]] = {}
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public state
    # ------------------------------------------------------------------

    @property
    def experiment_id(self) -> str:
        return self.config.experiment_id

    @property
    def data_mode(self) -> DataMode:
        return self.config.data_mode

    def state(self) -> ExperimentState:
        return ExperimentState(
            config=self.config,
            status=self.status,
            mode_availability=self.mode_availability,
            started_at=self._started_at,
            stopped_at=self._stopped_at,
            elapsed_seconds=round(self.engine.elapsed_seconds, 3),
            tick_count=self.engine.tick_index,
            event_count=len(self.bus.recent(limit=10_000, experiment_id=self.experiment_id)),
            last_sequence=self.bus.peek_sequence(self.experiment_id),
            notes=self.notes,
        )

    def known_targets(self) -> set[str]:
        return {d.device_id for d in self.topology.devices} | set(self.flow_states)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _publish(
        self,
        event_type: EventType,
        payload: dict,
        summary: str,
        severity: EventSeverity = EventSeverity.INFO,
    ) -> str:
        event = self.bus.publish(
            event_type=event_type,
            experiment_id=self.experiment_id,
            data_mode=self.data_mode,
            payload=payload,
            summary=summary,
            severity=severity,
        )
        return event.event_id

    def start(self) -> None:
        if self.status is ExperimentStatus.RUNNING:
            return
        self._started_at = self._started_at or datetime.now(UTC)
        self.status = ExperimentStatus.RUNNING
        self.watchdog.heartbeat()
        self._publish(
            EventType.EXPERIMENT_STATUS,
            {"status": self.status.value, "state": self.state().model_dump(mode="json")},
            f"Experiment {self.experiment_id} started in {self.config.mode.value} mode.",
            EventSeverity.SUCCESS,
        )

    def pause(self) -> None:
        if self.status is not ExperimentStatus.RUNNING:
            return
        self.status = ExperimentStatus.PAUSED
        self._publish(
            EventType.EXPERIMENT_STATUS,
            {"status": self.status.value},
            "Experiment paused. The simulator is frozen; no measurement is taken while paused.",
        )

    def resume(self) -> None:
        if self.status is not ExperimentStatus.PAUSED:
            return
        self.status = ExperimentStatus.RUNNING
        self.watchdog.heartbeat()
        self._publish(
            EventType.EXPERIMENT_STATUS, {"status": self.status.value}, "Experiment resumed."
        )

    def stop(self, completed: bool = False) -> None:
        if self.status in (ExperimentStatus.STOPPED, ExperimentStatus.COMPLETED):
            return
        # Return the data plane to its unshaped baseline before finishing.
        self.engine.set_shaping(None, None)
        self.status = ExperimentStatus.COMPLETED if completed else ExperimentStatus.STOPPED
        self._stopped_at = datetime.now(UTC)
        self._publish(
            EventType.EXPERIMENT_COMPLETED if completed else EventType.EXPERIMENT_STATUS,
            {"status": self.status.value, "report": self.report().model_dump(mode="json")},
            (
                f"Experiment {self.experiment_id} completed after "
                f"{self.engine.elapsed_seconds:.1f} s."
                if completed
                else f"Experiment {self.experiment_id} stopped safely and shaping was removed."
            ),
            EventSeverity.SUCCESS,
        )

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Advance the experiment one tick."""
        result = self.engine.step()
        now = result.timestamp
        self.watchdog.heartbeat(now)

        # 1. Observe, and classify when a feature window closes.
        self.observer.observe_tick(
            result.flows,
            observed_packet_bytes={
                s.flow_id: s.observed_mean_packet_bytes for s in result.flows
            },
            tick_seconds=self.engine.dt,
            elapsed_seconds=result.elapsed_seconds,
        )
        for sample in result.flows:
            self.observer.set_protocol(sample.flow_id, sample.protocol)

        if self.observer.window_is_due:
            for window in self.observer.drain_windows(
                self.experiment_id, now, result.elapsed_seconds
            ):
                classification = self.classifier.classify_flow(window, self.config.mode)
                self.classifications[window.flow_id] = classification
                if classification.source is ClassificationSource.MODEL_1:
                    self.registry.record_inference(MODEL_1.key, now.isoformat())
                self._publish(
                    EventType.FLOW_CLASSIFIED,
                    {
                        "window": window.model_dump(mode="json"),
                        "classification": classification.model_dump(mode="json"),
                    },
                    (
                        f"{window.flow_id} classified as "
                        f"{classification.predicted_class.value} by "
                        f"{classification.source.value}."
                    ),
                )

        # 2. Capacity estimation from observed delivery and delay only.
        for direction, sample in result.directions.items():
            self.capacity[direction].observe(
                served_mbps=sample.served_bps / 1_000_000.0,
                offered_mbps=sample.offered_bps / 1_000_000.0,
                queue_occupancy_percent=sample.queue_occupancy_percent,
                added_delay_ms=result.added_delay_ms,
                elapsed_seconds=result.elapsed_seconds,
                now=now,
            )

        # 3. Flow state and metrics snapshot. The marks the shaper acts on are
        # the classes the controller assigned, and they take effect from the
        # next tick, as a connection mark would.
        self._update_flow_states(result, now)
        self.engine.set_flow_marks(
            {flow_id: state.traffic_class for flow_id, state in self.flow_states.items()}
        )
        snapshot = self._build_snapshot(result, now)
        self.latest_snapshot = snapshot
        self._record_samples(snapshot, result)
        self._publish(
            EventType.METRICS_SNAPSHOT,
            snapshot.model_dump(mode="json"),
            "",
        )

        if result.impairment_note:
            self._publish(
                EventType.EXPERIMENT_STATUS,
                {"impairment": result.impairment_note, "elapsed_seconds": result.elapsed_seconds},
                result.impairment_note,
                EventSeverity.WARNING,
            )
        if result.loss_observed:
            self._publish(
                EventType.PACKET_LOSS_OBSERVED,
                {
                    "elapsed_seconds": result.elapsed_seconds,
                    "dropped_packets": {
                        d.direction.value: d.dropped_packets for d in result.directions.values()
                    },
                    "packet_loss_percent": round(result.packet_loss_percent, 4),
                },
                "Packet loss measured at the emulated bottleneck.",
                EventSeverity.WARNING,
            )

        # 4. Model 2 state, republished periodically so the interface stays current.
        if (
            result.elapsed_seconds - self._last_prediction_publish
            >= CONGESTION_PREDICTION_INTERVAL_SECONDS
        ):
            self._last_prediction_publish = result.elapsed_seconds
            prediction = self.congestion_predictor.predict_congestion(self.experiment_id)
            self._publish(
                EventType.CONGESTION_PREDICTED,
                prediction.model_dump(mode="json"),
                (
                    "Model 2 is not available, so no congestion-risk prediction was produced."
                    if prediction.risk_band is RiskBand.UNAVAILABLE
                    else f"Congestion risk {prediction.risk_band.value}."
                ),
            )

        # 5. Rolling measurement window, then policy evaluation on its cadence.
        self._accumulate_window(self._rolling_window, result, snapshot)
        if self._pending_verification is not None:
            self._accumulate_window(self._pending_verification[2], result, snapshot)

        if self.config.mode is not ExperimentMode.NO_QOS and (
            result.elapsed_seconds - self._last_policy_eval
            >= POLICY_EVALUATION_INTERVAL_SECONDS
        ):
            self._last_policy_eval = result.elapsed_seconds
            self._evaluate_policy(result, now)

        # 6. Finish any verification whose window has elapsed.
        self._maybe_complete_verification(result, now)

        # 7. Expire intent leases server-side.
        for lease in self.intents.expire_due(now):
            self._publish(
                EventType.INTENT_UPDATED,
                lease.model_dump(mode="json"),
                (
                    f"Priority lease {lease.intent_id} expired. The ordinary policy will be "
                    f"re-evaluated."
                ),
            )
            self._last_policy_eval = -POLICY_EVALUATION_INTERVAL_SECONDS

        if self.engine.is_finished:
            self.stop(completed=True)

    # ------------------------------------------------------------------

    def _update_flow_states(self, result, now: datetime) -> None:
        seen: set[str] = set()
        for sample in result.flows:
            seen.add(sample.flow_id)
            classification = self.classifications.get(sample.flow_id)
            traffic_class = (
                classification.predicted_class if classification else TrafficClass.UNKNOWN
            )
            source = (
                classification.source if classification else ClassificationSource.SAFE_FALLBACK
            )
            device = next(
                (d for d in self.topology.devices if d.device_id == sample.device_id), None
            )
            existing = self.flow_states.get(sample.flow_id)

            state = FlowState(
                flow_id=sample.flow_id,
                device_id=sample.device_id,
                device_label=device.label if device else sample.device_id,
                direction=sample.direction,
                protocol=sample.protocol,
                traffic_class=traffic_class,
                classification_source=source,
                confidence=classification.calibrated_confidence if classification else None,
                started_at=existing.started_at if existing else now,
                last_update=now,
                bytes_per_second=round(sample.served_bps / 8.0, 2),
                packets_per_second=round(sample.packets_per_second, 2),
                demand_bytes_per_second=round(sample.demand_bps / 8.0, 2),
                served_fraction=round(sample.served_fraction, 4),
                flow_rtt_ms=round(sample.flow_rtt_ms, 2),
                queue_class=traffic_class.value,
                data_mode=self.data_mode,
                total_bytes=sample.total_bytes,
            )
            self.flow_states[sample.flow_id] = state
            self.flow_totals[sample.flow_id] = (
                traffic_class,
                sample.direction,
                sample.total_bytes,
            )

            if sample.just_started:
                self._publish(
                    EventType.FLOW_DETECTED,
                    state.model_dump(mode="json"),
                    f"New flow {sample.flow_id} detected on {state.device_label}.",
                )

        for flow_id in list(self.flow_states):
            if flow_id not in seen:
                self.flow_states.pop(flow_id)

    def _build_snapshot(self, result, now: datetime) -> MetricsSnapshot:
        download = result.directions[Direction.DOWNLOAD]
        upload = result.directions[Direction.UPLOAD]
        capacity = self.capacity[Direction.DOWNLOAD].current(now)

        per_direction = [
            DirectionMetrics(
                direction=d.direction,
                offered_mbps=round(d.offered_bps / 1_000_000.0, 4),
                served_mbps=round(d.served_bps / 1_000_000.0, 4),
                # The configured bottleneck is deliberately not exposed as a
                # measurement. It is the hidden quantity the estimator has to
                # discover, so publishing it would make the estimator untestable.
                link_rate_mbps=None,
                utilization_percent=None,
                queue_bytes=round(d.backlog_bits / 8.0, 2),
                queue_packets=d.queue_packets,
                queue_occupancy_percent=round(d.queue_occupancy_percent, 3),
                queue_delay_ms=round(d.queue_delay_ms, 3),
                dropped_packets=d.dropped_packets,
            )
            for d in (download, upload)
        ]

        return MetricsSnapshot(
            experiment_id=self.experiment_id,
            timestamp=now,
            elapsed_seconds=round(result.elapsed_seconds, 3),
            data_mode=self.data_mode,
            download_mbps=round(download.served_bps / 1_000_000.0, 4),
            upload_mbps=round(upload.served_bps / 1_000_000.0, 4),
            usable_capacity_mbps=capacity.usable_capacity_mbps,
            capacity_quality=capacity.quality,
            rtt_ms=round(result.rtt_ms, 3),
            idle_baseline_rtt_ms=round(result.idle_baseline_rtt_ms, 3),
            added_delay_ms=round(result.added_delay_ms, 3),
            jitter_ms=round(result.jitter_ms, 3),
            packet_loss_percent=round(result.packet_loss_percent, 4),
            queue_packets=download.queue_packets,
            queue_occupancy_percent=round(download.queue_occupancy_percent, 3),
            active_flows=len([s for s in result.flows if s.active]),
            congestion_state=result.congestion_state,
            # Measured state and predicted risk are separate fields and never
            # derived from one another.
            congestion_prediction=RiskBand.UNAVAILABLE,
            policy_id=self.enforcer.installed_policy_id,
            policy_profile=self.current_profile.value if self.current_profile else None,
            per_direction=per_direction,
        )

    def _class_goodput(self, result) -> dict[TrafficClass, float]:
        totals: dict[TrafficClass, float] = {}
        for sample in result.flows:
            state = self.flow_states.get(sample.flow_id)
            traffic_class = state.traffic_class if state else TrafficClass.UNKNOWN
            totals[traffic_class] = (
                totals.get(traffic_class, 0.0) + sample.served_bps / 1_000_000.0
            )
        return totals

    def _accumulate_window(
        self, window: MeasurementWindow, result, snapshot: MetricsSnapshot
    ) -> None:
        window.add(
            added_delay_ms=result.added_delay_ms,
            jitter_ms=result.jitter_ms,
            packet_loss_percent=result.packet_loss_percent,
            queue_occupancy_percent=snapshot.queue_occupancy_percent or 0.0,
            class_goodput_mbps=self._class_goodput(result),
        )
        # Keep only the most recent verification window's worth of samples.
        limit = max(1, int(self.settings.verification_window_seconds / self.engine.dt))
        series_list = [
            window.added_delay_ms,
            window.jitter_ms,
            window.packet_loss_percent,
            window.queue_occupancy_percent,
            *window.class_goodput_mbps.values(),
        ]
        for series in series_list:
            if len(series) > limit:
                del series[:-limit]

    def _record_samples(self, snapshot: MetricsSnapshot, result) -> None:
        def add(key: str, value: float | None) -> None:
            if value is not None:
                self._samples.setdefault(key, []).append(value)

        add("rtt_ms", snapshot.rtt_ms)
        add("added_delay_ms", snapshot.added_delay_ms)
        add("jitter_ms", snapshot.jitter_ms)
        add("packet_loss_percent", snapshot.packet_loss_percent)
        add("download_mbps", snapshot.download_mbps)
        add("upload_mbps", snapshot.upload_mbps)
        add("queue_occupancy_percent", snapshot.queue_occupancy_percent)
        add("queue_packets", float(snapshot.queue_packets or 0))
        add("usable_capacity_mbps", snapshot.usable_capacity_mbps)

        bulk = sum(
            s.served_bps / 1_000_000.0
            for s in result.flows
            if self.flow_states.get(s.flow_id)
            and self.flow_states[s.flow_id].traffic_class is TrafficClass.BULK
        )
        if any(
            self.flow_states.get(s.flow_id)
            and self.flow_states[s.flow_id].traffic_class is TrafficClass.BULK
            for s in result.flows
        ):
            self._samples.setdefault("bulk_goodput_mbps", []).append(bulk)
            if bulk <= 0.01:
                self.zero_progress_windows += 1

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def _evaluate_policy(self, result, now: datetime) -> None:
        download_capacity = self.capacity[Direction.DOWNLOAD].current(now)
        upload_capacity = self.capacity[Direction.UPLOAD].current(now)

        class_demand: dict[TrafficClass, float] = {}
        upload_demand: dict[TrafficClass, float] = {}
        comparable: dict[TrafficClass, list[tuple[str, float]]] = {}
        for sample in result.flows:
            if not sample.active:
                continue
            state = self.flow_states.get(sample.flow_id)
            traffic_class = state.traffic_class if state else TrafficClass.UNKNOWN
            demand_mbps = sample.demand_bps / 1_000_000.0
            if sample.direction is Direction.DOWNLOAD:
                class_demand[traffic_class] = class_demand.get(traffic_class, 0.0) + demand_mbps
                comparable.setdefault(traffic_class, []).append((sample.flow_id, demand_mbps))
            else:
                upload_demand[traffic_class] = (
                    upload_demand.get(traffic_class, 0.0) + demand_mbps
                )

        evidence = any(
            c.decision.value == "ACCEPTED" for c in self.classifications.values()
        )
        prediction = self.congestion_predictor.predict_congestion(self.experiment_id)
        active_leases = self.intents.active(now)
        seconds_since_change = result.elapsed_seconds - self.last_policy_change_elapsed

        evaluating_event = self._publish(
            EventType.POLICY_EVALUATING,
            {
                "elapsed_seconds": result.elapsed_seconds,
                "class_demand_mbps": {k.value: round(v, 4) for k, v in class_demand.items()},
                "usable_capacity_mbps": download_capacity.usable_capacity_mbps,
                "capacity_quality": download_capacity.quality.value,
                "added_delay_ms": round(result.added_delay_ms, 3),
                "jitter_ms": round(result.jitter_ms, 3),
                "packet_loss_percent": round(result.packet_loss_percent, 4),
                "queue_occupancy_percent": round(
                    result.directions[Direction.DOWNLOAD].queue_occupancy_percent, 3
                ),
                "congestion_state": result.congestion_state.value,
                "congestion_prediction": prediction.model_dump(mode="json"),
                "classification_evidence": evidence,
                "active_intents": [lease.model_dump(mode="json") for lease in active_leases],
                "current_profile": self.current_profile.value if self.current_profile else None,
                "seconds_since_policy_change": round(seconds_since_change, 2),
            },
            "Evaluating candidate policies.",
        )

        inputs = PolicyInputs(
            experiment_id=self.experiment_id,
            mode=self.config.mode,
            data_mode=self.data_mode,
            now=now,
            elapsed_seconds=result.elapsed_seconds,
            class_demand_mbps=class_demand,
            comparable_flow_demand=comparable,
            download_capacity=download_capacity,
            upload_capacity=upload_capacity,
            upload_class_demand_mbps=upload_demand,
            added_delay_ms=result.added_delay_ms,
            jitter_ms=result.jitter_ms,
            packet_loss_percent=result.packet_loss_percent,
            queue_occupancy_percent=result.directions[
                Direction.DOWNLOAD
            ].queue_occupancy_percent,
            congestion_prediction=prediction,
            classification_evidence=evidence,
            current_profile=self.current_profile,
            current_allocations=self.enforcer.installed_allocations,
            seconds_since_policy_change=seconds_since_change,
            active_intent_classes=self.intents.boosted_classes(now),
            intent_valid=bool(active_leases),
            source_event_ids=[evaluating_event],
        )

        proposal = self.policy_engine.evaluate(inputs)
        is_change = (
            proposal.profile is not PolicyProfile.KEEP_CURRENT
            and proposal.profile is not self.current_profile
        )
        safety = self.safety_guard.evaluate(
            proposal,
            capacity=download_capacity,
            current_allocations=self.enforcer.installed_allocations,
            seconds_since_policy_change=seconds_since_change,
            measurement_age_seconds=0.0,
            bulk_demand_mbps=class_demand.get(TrafficClass.BULK, 0.0),
            intent_valid=bool(active_leases),
            intent_present=bool(active_leases),
            rollback_available=True,
            is_policy_change=is_change,
            now=now,
        )
        proposal = apply_safety_result(proposal, safety)
        self.current_proposal = proposal

        self._publish(
            EventType.POLICY_PROPOSED,
            proposal.model_dump(mode="json"),
            f"Policy engine selected {proposal.profile.value}.",
        )

        if not safety.accepted:
            self.safety_rejections += 1
            self.policy_history.append(PolicyHistoryEntry(proposal=proposal))
            self._publish(
                EventType.POLICY_REJECTED,
                proposal.model_dump(mode="json"),
                (
                    "Safety Guard rejected the proposal: "
                    + ", ".join(safety.rejection_reasons)
                    + ". The previous safe state is retained."
                ),
                EventSeverity.WARNING,
            )
            return

        if not is_change:
            self.policy_history.append(PolicyHistoryEntry(proposal=proposal))
            return

        # A change: snapshot the measurement window before enforcing so the
        # verifier has a genuine before picture.
        before_window = MeasurementWindow(
            added_delay_ms=list(self._rolling_window.added_delay_ms),
            jitter_ms=list(self._rolling_window.jitter_ms),
            packet_loss_percent=list(self._rolling_window.packet_loss_percent),
            queue_occupancy_percent=list(self._rolling_window.queue_occupancy_percent),
            class_goodput_mbps={
                k: list(v) for k, v in self._rolling_window.class_goodput_mbps.items()
            },
        )

        enforcement = self.enforcer.apply_policy(proposal)
        entry = PolicyHistoryEntry(proposal=proposal, enforcement=enforcement)
        self.policy_history.append(entry)

        if enforcement.status is EnforcementStatus.SIMULATED:
            self.current_profile = proposal.profile
            self.last_policy_change_elapsed = result.elapsed_seconds
            self.policy_changes += 1
            self._publish(
                EventType.POLICY_APPLIED,
                enforcement.model_dump(mode="json"),
                (
                    f"{proposal.profile.value} installed on the simulator in "
                    f"{enforcement.apply_duration_ms:.2f} ms. This is a simulated "
                    f"application, not live Linux traffic control."
                ),
                EventSeverity.SUCCESS,
            )
            self._publish(
                EventType.ENFORCEMENT_VERIFIED,
                (enforcement.read_back.model_dump(mode="json") if enforcement.read_back else {}),
                "Read-back matches the intended shaping state.",
            )
            self._pending_verification = (
                proposal.policy_id,
                before_window,
                MeasurementWindow(),
                result.elapsed_seconds,
                enforcement.snapshot_id,
            )
        else:
            self._publish(
                EventType.SYSTEM_ERROR,
                enforcement.model_dump(mode="json"),
                f"Enforcement failed: {enforcement.error}",
                EventSeverity.CRITICAL,
            )

    def _maybe_complete_verification(self, result, now: datetime) -> None:
        if self._pending_verification is None:
            return
        policy_id, before, after, applied_at, snapshot_id = self._pending_verification
        if result.elapsed_seconds - applied_at < self.settings.verification_window_seconds:
            return

        verification = self.verifier.verify(policy_id, before, after, self.data_mode, now)
        self._pending_verification = None

        for entry in reversed(self.policy_history):
            if entry.proposal.policy_id == policy_id:
                entry.verification = verification
                break

        self._publish(
            EventType.VERIFICATION_COMPLETED,
            verification.model_dump(mode="json"),
            verification.explanation,
            (
                EventSeverity.SUCCESS
                if verification.outcome is VerificationOutcome.ACCEPTABLE
                else EventSeverity.WARNING
            ),
        )

        if verification.outcome is not VerificationOutcome.DEGRADED or snapshot_id is None:
            return

        self._publish(
            EventType.ROLLBACK_STARTED,
            {"policy_id": policy_id, "snapshot_id": snapshot_id},
            "Outcome degraded. Restoring the previous configuration.",
            EventSeverity.WARNING,
        )
        restore = self.enforcer.restore_snapshot(snapshot_id)
        self.rollbacks += 1
        restored_ok = restore.status is EnforcementStatus.SIMULATED
        if restored_ok:
            if not self.enforcer.installed_allocations:
                self.current_profile = None
            self.last_policy_change_elapsed = result.elapsed_seconds
        self._publish(
            EventType.ROLLBACK_COMPLETED,
            restore.model_dump(mode="json"),
            (
                "Previous configuration restored and confirmed by read-back."
                if restored_ok
                else f"Rollback could not be confirmed: {restore.error}"
            ),
            EventSeverity.SUCCESS if restored_ok else EventSeverity.CRITICAL,
        )
        if not restored_ok:
            self._publish(
                EventType.WATCHDOG_ALERT,
                {"policy_id": policy_id, "snapshot_id": snapshot_id},
                "Restore could not be verified. Entering safe recovery.",
                EventSeverity.CRITICAL,
            )

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    @staticmethod
    def _summary(metric: str, unit: str, values: list[float]) -> MetricSummary:
        if not values:
            return MetricSummary(
                metric=metric, unit=unit, samples=0, median=None, p25=None,
                p75=None, minimum=None, maximum=None,
            )
        ordered = sorted(values)

        def percentile(fraction: float) -> float:
            if len(ordered) == 1:
                return ordered[0]
            position = fraction * (len(ordered) - 1)
            low = int(position)
            high = min(low + 1, len(ordered) - 1)
            weight = position - low
            return ordered[low] * (1 - weight) + ordered[high] * weight

        return MetricSummary(
            metric=metric,
            unit=unit,
            samples=len(values),
            median=round(statistics.median(ordered), 4),
            p25=round(percentile(0.25), 4),
            p75=round(percentile(0.75), 4),
            minimum=round(ordered[0], 4),
            maximum=round(ordered[-1], 4),
        )

    def report(self) -> ExperimentReport:
        units = {
            "rtt_ms": "ms",
            "added_delay_ms": "ms",
            "jitter_ms": "ms",
            "packet_loss_percent": "percent",
            "download_mbps": "Mbps",
            "upload_mbps": "Mbps",
            "queue_occupancy_percent": "percent",
            "queue_packets": "packets",
            "usable_capacity_mbps": "Mbps",
            "bulk_goodput_mbps": "Mbps",
        }
        summaries = [
            self._summary(metric, units.get(metric, ""), values)
            for metric, values in sorted(self._samples.items())
        ]

        # Fairness across comparable flows only: same class, same entitlement,
        # two or more flows. Computed over bytes delivered across the whole
        # run, not over one sampling interval.
        fairness: float | None = None
        basis = "Not computed: no class had two or more comparable flows."
        by_class: dict[TrafficClass, list[float]] = {}
        for traffic_class, direction, total_bytes in self.flow_totals.values():
            if direction is not Direction.DOWNLOAD:
                continue
            by_class.setdefault(traffic_class, []).append(float(total_bytes))
        for traffic_class, totals in by_class.items():
            if len(totals) < 2:
                continue
            index = jain_fairness_index(totals)
            if index is not None:
                fairness = index if fairness is None else min(fairness, index)
                basis = (
                    f"Jain's fairness index across {len(totals)} comparable "
                    f"{traffic_class.value} flows, over bytes delivered during the run."
                )

        bulk_bytes = sum(
            self.engine.total_bytes(flow_id)
            for flow_id, runtime in self.engine.flows.items()
            if runtime.spec.true_class is TrafficClass.BULK
        )

        limitations = [
            "All values come from the deterministic simulator and are labelled SIMULATION. "
            "They are not measurements of a real network.",
            "No Linux traffic control was applied. Enforcement is simulated and reported as "
            "SIMULATED, never APPLIED.",
            f"Model 1 is {self.registry.status(MODEL_1).state.value}; "
            f"Model 2 is {self.registry.status(MODEL_2).state.value}.",
        ]
        if self.mode_availability.degraded:
            limitations.append(self.mode_availability.detail)
        if self.config.mode is ExperimentMode.TRADITIONAL_ADAPTIVE:
            limitations.append(
                "Traffic classes came from the deterministic rule set, which is the Mode 2 "
                "baseline, not a trained model."
            )

        return ExperimentReport(
            experiment_id=self.experiment_id,
            config=self.config,
            status=self.status,
            data_mode=self.data_mode,
            generated_at=datetime.now(UTC),
            duration_observed_seconds=round(self.engine.elapsed_seconds, 3),
            sample_count=len(self._samples.get("rtt_ms", [])),
            summaries=summaries,
            jain_fairness_index=round(fairness, 4) if fairness is not None else None,
            fairness_basis=basis,
            policy_changes=self.policy_changes,
            safety_rejections=self.safety_rejections,
            rollbacks=self.rollbacks,
            zero_progress_windows=self.zero_progress_windows,
            bulk_transferred_megabytes=round(bulk_bytes / 1_000_000.0, 3) if bulk_bytes else None,
            limitations=limitations,
        )


class ExperimentManager:
    """Owns every experiment and the single asynchronous loop that drives them."""

    def __init__(self, settings: Settings, registry: ModelRegistry, bus: EventBus) -> None:
        self.settings = settings
        self.registry = registry
        self.bus = bus
        self._runs: dict[str, ExperimentRuntime] = {}
        self._loop_task: asyncio.Task[None] | None = None
        self._stopping = False

    # -- registry -------------------------------------------------------

    def create(self, request: ExperimentCreateRequest) -> ExperimentRuntime:
        active = [
            r
            for r in self._runs.values()
            if r.status in (ExperimentStatus.RUNNING, ExperimentStatus.PAUSED)
        ]
        if len(active) >= self.settings.max_concurrent_experiments:
            raise RuntimeError(
                f"At most {self.settings.max_concurrent_experiments} experiments may be "
                f"active at once. Stop one before creating another."
            )
        runtime = ExperimentRuntime(request, self.settings, self.registry, self.bus)
        self._runs[runtime.experiment_id] = runtime
        return runtime

    def get(self, experiment_id: str) -> ExperimentRuntime:
        return self._runs[experiment_id]

    def all(self) -> list[ExperimentRuntime]:
        return list(self._runs.values())

    def latest(self) -> ExperimentRuntime | None:
        running = [r for r in self._runs.values() if r.status is ExperimentStatus.RUNNING]
        if running:
            return running[-1]
        return list(self._runs.values())[-1] if self._runs else None

    def reset(self, experiment_id: str) -> ExperimentRuntime:
        """Return the laboratory to a known baseline and rebuild the run."""
        existing = self._runs[experiment_id]
        existing.stop()
        self.bus.clear(experiment_id)
        request = ExperimentCreateRequest(
            scenario_id=existing.config.scenario_id,
            mode=existing.config.mode,
            data_mode=existing.config.data_mode,
            seed=existing.config.seed,
            duration_seconds=existing.config.duration_seconds,
            parameters=existing.config.parameters,
            repeat_label=existing.config.repeat_label,
        )
        replacement = ExperimentRuntime(request, self.settings, self.registry, self.bus)
        self._runs[replacement.experiment_id] = replacement
        return replacement

    # -- loop -----------------------------------------------------------

    async def _run_loop(self) -> None:
        interval = self.settings.tick_seconds
        while not self._stopping:
            started = asyncio.get_running_loop().time()
            for runtime in list(self._runs.values()):
                if runtime.status is not ExperimentStatus.RUNNING:
                    continue
                try:
                    runtime.tick()
                except Exception as exc:  # pragma: no cover - defensive
                    runtime.status = ExperimentStatus.FAILED
                    self.bus.publish(
                        event_type=EventType.SYSTEM_ERROR,
                        experiment_id=runtime.experiment_id,
                        data_mode=runtime.data_mode,
                        payload={"error": str(exc), "error_type": type(exc).__name__},
                        summary=f"Experiment failed: {exc}",
                        severity=EventSeverity.CRITICAL,
                    )
            # Sleep by the remaining slice so a slow tick does not accumulate
            # drift into the next one.
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    def start_loop(self) -> None:
        """Start the tick loop if it is not already running.

        Safe to call from a synchronous request handler, which runs in a
        worker thread with no event loop of its own. In that case the loop
        started at application startup is already doing the work.
        """
        if not self.settings.autorun_experiment_loop:
            return
        if self._loop_task is not None and not self._loop_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._stopping = False
        self._loop_task = asyncio.create_task(self._run_loop())

    async def shutdown(self) -> None:
        self._stopping = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(Exception):  # pragma: no cover - teardown
                await self._loop_task
            self._loop_task = None
        for runtime in self._runs.values():
            if runtime.status is ExperimentStatus.RUNNING:
                runtime.stop()
