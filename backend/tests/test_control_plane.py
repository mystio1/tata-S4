"""Tests for the analytics and control components.

Covers the capacity estimator, the rule classifier, the model registry's
honesty about readiness, the policy engine's scoring and hard constraints, the
Safety Guard, the verifier, the watchdog and intent leases.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.control.capacity import CapacityEstimator
from app.control.classifier import RULES_VERSION, TrafficClassifier, classify_with_rules
from app.control.congestion import CongestionPredictor
from app.control.enforcement import LabAgentEnforcer, SimulatedEnforcer
from app.control.intent import IntentManager, IntentRejected
from app.control.policy_engine import PolicyEngine, PolicyInputs
from app.control.safety_guard import SafetyGuard, apply_safety_result
from app.control.verification import MeasurementWindow, Verifier, Watchdog
from app.domain.enums import (
    CalibrationStatus,
    ClassificationDecision,
    ClassificationSource,
    DataMode,
    DataQuality,
    Direction,
    EnforcementStatus,
    ExperimentMode,
    IntentStatus,
    IntentType,
    ModelState,
    PolicyProfile,
    PolicyStatus,
    PredictionDecision,
    Protocol,
    RiskBand,
    TrafficClass,
    VerificationOutcome,
)
from app.domain.metrics import (
    CapacityEstimate,
    FlowFeatures,
    FlowFeatureWindow,
)
from app.domain.policy import ClassAllocation
from app.models.registry import MODEL_1, MODEL_2, ModelRegistry
from tests.conftest import build_engine

NOW = datetime(2026, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# M3A capacity estimator
# ---------------------------------------------------------------------------


class TestCapacityEstimator:
    def test_reports_no_data_before_any_sample(self, estimator: CapacityEstimator) -> None:
        current = estimator.current(now=NOW)
        assert current.usable_capacity_mbps is None
        assert current.quality is DataQuality.NO_DATA

    def test_ignores_samples_taken_on_an_idle_link(self, estimator: CapacityEstimator) -> None:
        """An unsaturated link reveals nothing about its ceiling."""
        for _ in range(20):
            estimator.observe(
                served_mbps=5.0,
                offered_mbps=5.0,
                queue_occupancy_percent=0.0,
                added_delay_ms=0.0,
                elapsed_seconds=1.0,
                now=NOW,
            )
        current = estimator.current(now=NOW)
        assert current.usable_capacity_mbps is None
        assert current.reason == "LOW_DEMAND_CAPACITY_NOT_OBSERVABLE"

    def test_learns_from_saturated_samples(self, estimator: CapacityEstimator) -> None:
        for _ in range(10):
            estimator.observe(
                served_mbps=100.0,
                offered_mbps=130.0,
                queue_occupancy_percent=40.0,
                added_delay_ms=50.0,
                elapsed_seconds=1.0,
                now=NOW,
            )
        current = estimator.current(now=NOW)
        assert current.quality is DataQuality.GOOD
        assert current.usable_capacity_mbps == pytest.approx(95.0, rel=0.01)

    def test_applies_configured_headroom(self, settings: Settings) -> None:
        estimator = CapacityEstimator(settings, Direction.DOWNLOAD)
        for _ in range(10):
            estimator.observe(
                served_mbps=50.0,
                offered_mbps=80.0,
                queue_occupancy_percent=30.0,
                added_delay_ms=40.0,
                elapsed_seconds=1.0,
                now=NOW,
            )
        expected = 50.0 * settings.capacity_headroom_fraction
        assert estimator.current(now=NOW).usable_capacity_mbps == pytest.approx(expected, rel=1e-3)

    def test_goes_stale_rather_than_claiming_a_current_value(
        self, estimator: CapacityEstimator, settings: Settings
    ) -> None:
        for _ in range(5):
            estimator.observe(
                served_mbps=100.0,
                offered_mbps=130.0,
                queue_occupancy_percent=40.0,
                added_delay_ms=50.0,
                elapsed_seconds=1.0,
                now=NOW,
            )
        later = NOW + timedelta(seconds=settings.capacity_sample_staleness_seconds + 1)
        current = estimator.current(now=later)
        assert current.quality is DataQuality.STALE
        assert current.usable_capacity_mbps is not None  # last validated estimate retained

    def test_bounds_how_far_one_sample_moves_the_estimate(
        self, estimator: CapacityEstimator
    ) -> None:
        for _ in range(5):
            estimator.observe(
                served_mbps=100.0,
                offered_mbps=130.0,
                queue_occupancy_percent=40.0,
                added_delay_ms=50.0,
                elapsed_seconds=1.0,
                now=NOW,
            )
        before = estimator.current(now=NOW).usable_capacity_mbps
        estimator.observe(
            served_mbps=1.0,
            offered_mbps=130.0,
            queue_occupancy_percent=90.0,
            added_delay_ms=200.0,
            elapsed_seconds=1.0,
            now=NOW,
        )
        after = estimator.current(now=NOW).usable_capacity_mbps
        assert before is not None and after is not None
        assert after >= before * 0.5 - 1e-6

    def test_tracks_a_real_bottleneck_drop_without_reading_the_configuration(self) -> None:
        """The estimator only ever sees delivered rate and delay."""
        from app.config import get_settings

        estimator = CapacityEstimator(get_settings(), Direction.DOWNLOAD)
        engine = build_engine(scenario_id="bottleneck_drop", duration=90.0)
        observed: list[float] = []
        for index in range(int(80 / engine.dt)):
            result = engine.step()
            download = result.directions[Direction.DOWNLOAD]
            estimate = estimator.observe(
                served_mbps=download.served_bps / 1e6,
                offered_mbps=download.offered_bps / 1e6,
                queue_occupancy_percent=download.queue_occupancy_percent,
                added_delay_ms=result.added_delay_ms,
                elapsed_seconds=result.elapsed_seconds,
                now=NOW + timedelta(seconds=index * engine.dt),
            )
            if estimate.usable_capacity_mbps is not None:
                observed.append(estimate.usable_capacity_mbps)
        # Configured bottleneck falls from 100 Mbps to 20 Mbps at 45 s.
        assert max(observed) > 80.0
        assert min(observed) < 25.0


# ---------------------------------------------------------------------------
# M2 classification
# ---------------------------------------------------------------------------


def _window(
    *,
    mean_packet_bytes: float,
    bytes_per_second: float,
    packets_per_second: float,
    active_time_ratio: float,
    protocol: Protocol = Protocol.TCP,
    std_interarrival_ms: float = 1.0,
) -> FlowFeatureWindow:
    return FlowFeatureWindow(
        experiment_id="exp-test",
        flow_id="flow-test",
        device_id="device-test",
        observed_at=NOW,
        window_ms=2000,
        direction=Direction.DOWNLOAD,
        protocol=protocol,
        features=FlowFeatures(
            mean_packet_bytes=mean_packet_bytes,
            std_packet_bytes=10.0,
            min_packet_bytes=mean_packet_bytes - 10,
            max_packet_bytes=mean_packet_bytes + 10,
            mean_interarrival_ms=1000.0 / max(packets_per_second, 1e-6),
            std_interarrival_ms=std_interarrival_ms,
            packets_per_second=packets_per_second,
            bytes_per_second=bytes_per_second,
            flow_duration_ms=10_000.0,
            up_bytes=0,
            down_bytes=int(bytes_per_second * 2),
            up_packets=0,
            down_packets=int(packets_per_second * 2),
            burst_count=1,
            active_time_ratio=active_time_ratio,
        ),
    )


class TestRuleClassifier:
    def test_identifies_a_sustained_large_segment_transfer_as_bulk(self) -> None:
        traffic_class, rule = classify_with_rules(
            _window(
                mean_packet_bytes=1460,
                bytes_per_second=9_000_000,
                packets_per_second=6_100,
                active_time_ratio=1.0,
            )
        )
        assert traffic_class is TrafficClass.BULK
        assert rule == "RULE_BULK_SUSTAINED_LARGE_SEGMENTS"

    def test_identifies_a_bursty_large_segment_flow_as_streaming(self) -> None:
        traffic_class, _ = classify_with_rules(
            _window(
                mean_packet_bytes=1420,
                bytes_per_second=1_100_000,
                packets_per_second=770,
                active_time_ratio=0.55,
            )
        )
        assert traffic_class is TrafficClass.STREAMING_LIKE

    def test_identifies_small_frequent_datagrams_as_gaming(self) -> None:
        traffic_class, _ = classify_with_rules(
            _window(
                mean_packet_bytes=180,
                bytes_per_second=75_000,
                packets_per_second=420,
                active_time_ratio=1.0,
                protocol=Protocol.UDP,
            )
        )
        assert traffic_class is TrafficClass.GAMING_LIKE

    def test_identifies_steady_medium_datagrams_as_interactive(self) -> None:
        traffic_class, _ = classify_with_rules(
            _window(
                mean_packet_bytes=760,
                bytes_per_second=400_000,
                packets_per_second=520,
                active_time_ratio=1.0,
                protocol=Protocol.UDP,
                std_interarrival_ms=0.5,
            )
        )
        assert traffic_class is TrafficClass.INTERACTIVE

    def test_classification_survives_a_rate_reduction(self) -> None:
        """Shaping lowers a flow's rate; it must not change its class.

        Classification and enforcement form a loop through the link, so a class
        that depends mainly on byte rate can be driven downward by its own
        policy.
        """
        full = _window(
            mean_packet_bytes=1420,
            bytes_per_second=2_000_000,
            packets_per_second=1_400,
            active_time_ratio=0.55,
        )
        throttled = _window(
            mean_packet_bytes=1420,
            bytes_per_second=200_000,
            packets_per_second=140,
            active_time_ratio=0.55,
        )
        assert classify_with_rules(full)[0] is classify_with_rules(throttled)[0]

    def test_unrecognised_behaviour_becomes_unknown(self) -> None:
        traffic_class, rule = classify_with_rules(
            _window(
                mean_packet_bytes=700,
                bytes_per_second=40_000_000,
                packets_per_second=7_100,
                active_time_ratio=1.0,
            )
        )
        assert traffic_class is TrafficClass.UNKNOWN
        assert rule == "RULE_NO_MATCH"

    def test_rule_results_carry_no_invented_probability(self, settings: Settings) -> None:
        classifier = TrafficClassifier(ModelRegistry(settings))
        result = classifier.classify_flow(
            _window(
                mean_packet_bytes=1460,
                bytes_per_second=9_000_000,
                packets_per_second=6_100,
                active_time_ratio=1.0,
            ),
            ExperimentMode.TRADITIONAL_ADAPTIVE,
        )
        assert result.source is ClassificationSource.DETERMINISTIC_RULES
        assert result.model_version == RULES_VERSION
        assert result.probabilities == {}
        assert result.calibrated_confidence is None
        assert result.calibration_status is CalibrationStatus.UNAVAILABLE

    def test_model_modes_fall_back_safely_while_model_1_is_untrained(
        self, settings: Settings
    ) -> None:
        classifier = TrafficClassifier(ModelRegistry(settings))
        for mode in (ExperimentMode.CLASSIFIER_ONLY, ExperimentMode.FULL_QOSENSE):
            result = classifier.classify_flow(
                _window(
                    mean_packet_bytes=1460,
                    bytes_per_second=9_000_000,
                    packets_per_second=6_100,
                    active_time_ratio=1.0,
                ),
                mode,
            )
            assert result.predicted_class is TrafficClass.UNKNOWN
            assert result.decision is ClassificationDecision.SAFE_FALLBACK
            assert result.reason == "MODEL_NOT_READY"
            assert result.calibrated_confidence is None


# ---------------------------------------------------------------------------
# Model registry and Model 2
# ---------------------------------------------------------------------------


class TestModelRegistry:
    def test_unconfigured_models_are_reported_as_such(self, settings: Settings) -> None:
        registry = ModelRegistry(settings)
        for status in registry.all_statuses():
            assert status.state is ModelState.NOT_CONFIGURED
            assert status.version == "not-trained"
            assert status.calibration_available is False

    def test_a_configured_but_missing_artifact_fails_rather_than_loading(self) -> None:
        registry = ModelRegistry(Settings(traffic_classifier_path="does/not/exist.txt"))
        assert registry.state(MODEL_1.key) is ModelState.FAILED
        assert registry.is_ready(MODEL_1.key) is False

    def test_model_2_returns_an_explicit_unavailability_record(self, settings: Settings) -> None:
        predictor = CongestionPredictor(ModelRegistry(settings))
        prediction = predictor.predict_congestion("exp-test")
        assert prediction.risk_band is RiskBand.UNAVAILABLE
        assert prediction.decision is PredictionDecision.IGNORED
        assert prediction.congestion_probability is None
        assert prediction.prediction_horizon_seconds is None
        assert prediction.reason == "MODEL_NOT_READY"

    def test_model_2_is_scheduled_for_its_own_phase(self, settings: Settings) -> None:
        status = ModelRegistry(settings).status(MODEL_2)
        assert status.round_allocation == "Round 2"
        assert "not producing any prediction" in status.detail


# ---------------------------------------------------------------------------
# M4 policy engine
# ---------------------------------------------------------------------------


def _capacity(
    mbps: float | None, quality: DataQuality = DataQuality.GOOD
) -> CapacityEstimate:
    return CapacityEstimate(
        direction=Direction.DOWNLOAD,
        usable_capacity_mbps=mbps,
        delivery_rate_sample_mbps=mbps,
        sample_count=10,
        sample_window_seconds=5.0,
        ewma_alpha=0.25,
        quality=quality,
        saturated_observation=True,
        reason="TEST",
        updated_at=NOW,
    )


def _inputs(settings: Settings, **overrides) -> PolicyInputs:
    from app.control.congestion import CongestionPredictor as _Predictor

    prediction = _Predictor(ModelRegistry(settings)).predict_congestion("exp-test")
    base = dict(
        experiment_id="exp-test",
        mode=ExperimentMode.TRADITIONAL_ADAPTIVE,
        data_mode=DataMode.SIMULATION,
        now=NOW,
        elapsed_seconds=30.0,
        class_demand_mbps={
            TrafficClass.INTERACTIVE: 3.2,
            TrafficClass.GAMING_LIKE: 0.6,
            TrafficClass.STREAMING_LIKE: 8.5,
            TrafficClass.BULK: 85.0,
        },
        comparable_flow_demand={},
        download_capacity=_capacity(95.0),
        upload_capacity=_capacity(19.0),
        upload_class_demand_mbps={TrafficClass.INTERACTIVE: 1.6},
        added_delay_ms=90.0,
        jitter_ms=12.0,
        packet_loss_percent=1.5,
        queue_occupancy_percent=95.0,
        congestion_prediction=prediction,
        classification_evidence=True,
        current_profile=None,
        current_allocations=[],
        seconds_since_policy_change=30.0,
    )
    base.update(overrides)
    return PolicyInputs(**base)  # type: ignore[arg-type]


class TestPolicyEngine:
    def test_every_candidate_is_scored_or_explicitly_rejected(
        self, settings: Settings
    ) -> None:
        proposal = PolicyEngine(settings).evaluate(_inputs(settings))
        assert {c.profile for c in proposal.candidates} == set(PolicyProfile)
        for candidate in proposal.candidates:
            assert candidate.total_cost is not None or candidate.hard_constraint_violations

    def test_selects_a_shaping_profile_when_delay_is_above_target(
        self, settings: Settings
    ) -> None:
        proposal = PolicyEngine(settings).evaluate(_inputs(settings))
        assert proposal.profile is not PolicyProfile.KEEP_CURRENT
        assert proposal.class_allocations
        assert "ADDED_DELAY_ABOVE_TARGET" in proposal.reason_codes

    def test_rates_are_derived_from_capacity_and_demand_not_fixed_percentages(
        self, settings: Settings
    ) -> None:
        engine = PolicyEngine(settings)
        narrow = engine.evaluate(_inputs(settings, download_capacity=_capacity(40.0)))
        wide = engine.evaluate(_inputs(settings, download_capacity=_capacity(200.0)))
        narrow_bulk = narrow.class_rates_mbps[TrafficClass.BULK]
        wide_bulk = wide.class_rates_mbps[TrafficClass.BULK]
        assert wide_bulk > narrow_bulk

    def test_refuses_class_specific_action_without_classification_evidence(
        self, settings: Settings
    ) -> None:
        proposal = PolicyEngine(settings).evaluate(
            _inputs(settings, classification_evidence=False)
        )
        by_profile = {c.profile: c for c in proposal.candidates}
        for profile in (PolicyProfile.PROTECT_INTERACTIVE, PolicyProfile.LIMIT_BULK):
            assert "VIOLATION_CLASS_EVIDENCE_INSUFFICIENT" in by_profile[
                profile
            ].hard_constraint_violations
            assert by_profile[profile].eligible is False

    def test_refuses_to_change_policy_on_an_unreliable_capacity_estimate(
        self, settings: Settings
    ) -> None:
        proposal = PolicyEngine(settings).evaluate(
            _inputs(settings, download_capacity=_capacity(None, DataQuality.NO_DATA))
        )
        assert proposal.profile is PolicyProfile.KEEP_CURRENT
        assert "NO_ELIGIBLE_CANDIDATE" in proposal.reason_codes

    def test_honours_the_minimum_hold_time(self, settings: Settings) -> None:
        proposal = PolicyEngine(settings).evaluate(
            _inputs(settings, seconds_since_policy_change=0.5)
        )
        changing = [c for c in proposal.candidates if c.profile is not PolicyProfile.KEEP_CURRENT]
        assert all(
            "VIOLATION_MIN_HOLD_NOT_SATISFIED" in c.hard_constraint_violations for c in changing
        )

    def test_keeps_bulk_above_its_minimum_service_floor(self, settings: Settings) -> None:
        proposal = PolicyEngine(settings).evaluate(_inputs(settings))
        bulk = proposal.class_rates_mbps.get(TrafficClass.BULK)
        assert bulk is not None
        assert bulk >= settings.bulk_minimum_floor_mbps

    def test_bounds_how_far_one_evaluation_moves_a_ceiling(self, settings: Settings) -> None:
        current = [
            ClassAllocation(
                traffic_class=TrafficClass.BULK, ceiling_mbps=80.0, guaranteed_floor_mbps=2.0
            )
        ]
        proposal = PolicyEngine(settings).evaluate(
            _inputs(
                settings,
                current_profile=PolicyProfile.PROTECT_INTERACTIVE,
                current_allocations=current,
            )
        )
        bulk = proposal.class_rates_mbps.get(TrafficClass.BULK)
        if bulk is not None:
            max_step = 95.0 * settings.max_policy_rate_step_percent / 100.0
            assert abs(bulk - 80.0) <= max_step + 1e-6

    def test_keep_current_risk_is_measured_and_a_change_is_projected(
        self, settings: Settings
    ) -> None:
        proposal = PolicyEngine(settings).evaluate(
            _inputs(
                settings,
                current_profile=PolicyProfile.CONSERVATIVE_SAFE,
                current_allocations=[
                    ClassAllocation(
                        traffic_class=TrafficClass.BULK,
                        ceiling_mbps=95.0,
                        guaranteed_floor_mbps=2.0,
                    )
                ],
            )
        )
        keep = next(c for c in proposal.candidates if c.profile is PolicyProfile.KEEP_CURRENT)
        assert any("measured" in note for note in keep.notes)
        change = next(
            c for c in proposal.candidates if c.profile is PolicyProfile.PROTECT_INTERACTIVE
        )
        assert any("projected" in note for note in change.notes)

    def test_components_that_cannot_be_computed_are_left_out(self, settings: Settings) -> None:
        proposal = PolicyEngine(settings).evaluate(
            _inputs(
                settings,
                class_demand_mbps={TrafficClass.INTERACTIVE: 3.2},
            )
        )
        for candidate in proposal.candidates:
            if candidate.eligible:
                assert candidate.bulk_progress_risk is None
                assert candidate.total_cost is not None

    def test_fairness_uses_comparable_flows_only(self, settings: Settings) -> None:
        proposal = PolicyEngine(settings).evaluate(
            _inputs(
                settings,
                comparable_flow_demand={
                    TrafficClass.STREAMING_LIKE: [("a", 16.0), ("b", 16.0), ("c", 16.0)],
                    TrafficClass.BULK: [("bulk", 85.0)],
                },
            )
        )
        scored = [c for c in proposal.candidates if c.fairness_penalty is not None]
        assert scored, "fairness should be computable when a class has several flows"

    def test_selection_is_deterministic(self, settings: Settings) -> None:
        engine = PolicyEngine(settings)
        first = engine.evaluate(_inputs(settings))
        second = engine.evaluate(_inputs(settings))
        assert first.profile is second.profile
        assert first.class_rates_mbps == second.class_rates_mbps


# ---------------------------------------------------------------------------
# Safety Guard
# ---------------------------------------------------------------------------


class TestSafetyGuard:
    def _proposal(self, settings: Settings, **overrides):
        return PolicyEngine(settings).evaluate(_inputs(settings, **overrides))

    def _evaluate(self, settings: Settings, proposal, **overrides):
        base = dict(
            capacity=_capacity(95.0),
            current_allocations=[],
            seconds_since_policy_change=30.0,
            measurement_age_seconds=0.2,
            bulk_demand_mbps=85.0,
            intent_valid=False,
            intent_present=False,
            rollback_available=True,
            is_policy_change=True,
            now=NOW,
        )
        base.update(overrides)
        return SafetyGuard(settings).evaluate(proposal, **base)  # type: ignore[arg-type]

    def test_accepts_a_sound_proposal(self, settings: Settings) -> None:
        result = self._evaluate(settings, self._proposal(settings))
        assert result.accepted, result.rejection_reasons

    def test_every_check_is_reported_whether_it_passed_or_not(
        self, settings: Settings
    ) -> None:
        result = self._evaluate(settings, self._proposal(settings))
        names = {c.name for c in result.checks}
        assert {
            "profile_known",
            "measurements_fresh",
            "capacity_fresh",
            "total_allocation_within_capacity",
            "floors_within_ceilings",
            "bulk_minimum_service",
            "rate_change_within_limit",
            "minimum_hold_satisfied",
            "intent_valid",
            "rollback_available",
        } <= names

    def test_rejects_stale_measurements(self, settings: Settings) -> None:
        result = self._evaluate(
            settings, self._proposal(settings), measurement_age_seconds=60.0
        )
        assert not result.accepted
        assert "measurements_fresh" in result.rejection_reasons

    def test_rejects_when_no_rollback_is_available(self, settings: Settings) -> None:
        result = self._evaluate(settings, self._proposal(settings), rollback_available=False)
        assert not result.accepted
        assert "rollback_available" in result.rejection_reasons

    def test_rejects_a_change_inside_the_minimum_hold(self, settings: Settings) -> None:
        result = self._evaluate(
            settings, self._proposal(settings), seconds_since_policy_change=0.5
        )
        assert not result.accepted
        assert "minimum_hold_satisfied" in result.rejection_reasons

    def test_allows_an_unchanged_profile_inside_the_minimum_hold(
        self, settings: Settings
    ) -> None:
        result = self._evaluate(
            settings,
            self._proposal(settings),
            seconds_since_policy_change=0.5,
            is_policy_change=False,
        )
        assert result.accepted, result.rejection_reasons

    def test_rejects_a_floor_above_its_ceiling(self, settings: Settings) -> None:
        proposal = self._proposal(settings)
        proposal.class_allocations = [
            ClassAllocation(
                traffic_class=TrafficClass.BULK, ceiling_mbps=5.0, guaranteed_floor_mbps=50.0
            )
        ]
        result = self._evaluate(settings, proposal)
        assert not result.accepted
        assert "floors_within_ceilings" in result.rejection_reasons

    def test_rejects_starving_active_bulk_traffic(self, settings: Settings) -> None:
        proposal = self._proposal(settings)
        proposal.class_allocations = [
            ClassAllocation(
                traffic_class=TrafficClass.INTERACTIVE,
                ceiling_mbps=90.0,
                guaranteed_floor_mbps=1.0,
            )
        ]
        result = self._evaluate(settings, proposal)
        assert not result.accepted
        assert "bulk_minimum_service" in result.rejection_reasons

    def test_an_unshaped_proposal_cannot_breach_the_bulk_floor(
        self, settings: Settings
    ) -> None:
        proposal = self._proposal(settings)
        proposal.class_allocations = []
        result = self._evaluate(settings, proposal)
        assert "bulk_minimum_service" not in result.rejection_reasons

    def test_rejects_an_oversized_rate_step(self, settings: Settings) -> None:
        proposal = self._proposal(settings)
        proposal.class_allocations = [
            ClassAllocation(
                traffic_class=TrafficClass.BULK, ceiling_mbps=90.0, guaranteed_floor_mbps=2.0
            )
        ]
        result = self._evaluate(
            settings,
            proposal,
            current_allocations=[
                ClassAllocation(
                    traffic_class=TrafficClass.BULK,
                    ceiling_mbps=10.0,
                    guaranteed_floor_mbps=2.0,
                )
            ],
        )
        assert not result.accepted
        assert "rate_change_within_limit" in result.rejection_reasons

    def test_rejection_marks_the_proposal_and_keeps_the_reasons(
        self, settings: Settings
    ) -> None:
        proposal = self._proposal(settings)
        result = self._evaluate(settings, proposal, rollback_available=False)
        updated = apply_safety_result(proposal, result)
        assert updated.status is PolicyStatus.REJECTED
        assert updated.safety is not None
        assert updated.safety.rejection_reasons


# ---------------------------------------------------------------------------
# M5 enforcement
# ---------------------------------------------------------------------------


class TestEnforcement:
    def _proposal(self, settings: Settings):
        return PolicyEngine(settings).evaluate(_inputs(settings))

    def test_simulated_application_is_never_labelled_applied(
        self, settings: Settings
    ) -> None:
        engine = build_engine()
        enforcer = SimulatedEnforcer(settings, engine)
        result = enforcer.apply_policy(self._proposal(settings))
        assert result.status is EnforcementStatus.SIMULATED
        assert result.status is not EnforcementStatus.APPLIED
        assert result.data_mode is DataMode.SIMULATION

    def test_read_back_confirms_the_installed_rates(self, settings: Settings) -> None:
        engine = build_engine()
        enforcer = SimulatedEnforcer(settings, engine)
        proposal = self._proposal(settings)
        result = enforcer.apply_policy(proposal)
        assert result.read_back is not None
        assert result.read_back.matches_intent
        assert result.read_back.observed_class_rates_mbps == proposal.class_rates_mbps

    def test_read_back_describes_the_upload_and_download_paths(
        self, settings: Settings
    ) -> None:
        engine = build_engine()
        enforcer = SimulatedEnforcer(settings, engine)
        state = enforcer.apply_policy(self._proposal(settings)).read_back
        assert state is not None
        assert any("Intermediate Functional Block" in step for step in state.download_path)
        assert any("Hierarchical Token Bucket" in step for step in state.upload_path)

    def test_a_snapshot_can_be_restored(self, settings: Settings) -> None:
        engine = build_engine()
        enforcer = SimulatedEnforcer(settings, engine)
        snapshot = enforcer.snapshot()
        enforcer.apply_policy(self._proposal(settings))
        assert enforcer.installed_allocations
        restore = enforcer.restore_snapshot(snapshot)
        assert restore.status is EnforcementStatus.SIMULATED
        assert enforcer.installed_allocations == []

    def test_restoring_an_unknown_snapshot_enters_safe_mode(
        self, settings: Settings
    ) -> None:
        enforcer = SimulatedEnforcer(settings, build_engine())
        result = enforcer.restore_snapshot("snap-does-not-exist")
        assert result.status is EnforcementStatus.FAILED_SAFE_MODE

    def test_the_lab_agent_refuses_while_disconnected(self, settings: Settings) -> None:
        enforcer = LabAgentEnforcer(settings)
        assert enforcer.is_configured is False
        result = enforcer.apply_policy(self._proposal(settings))
        assert result.status is EnforcementStatus.FAILED_SAFE_MODE
        assert result.error is not None and "laboratory agent" in result.error


# ---------------------------------------------------------------------------
# M6 verification and watchdog
# ---------------------------------------------------------------------------


def _window_of(
    delay: float, loss: float = 0.0, bulk: float = 40.0, samples: int = 10
) -> MeasurementWindow:
    window = MeasurementWindow()
    for _ in range(samples):
        window.add(
            added_delay_ms=delay,
            jitter_ms=delay / 10,
            packet_loss_percent=loss,
            queue_occupancy_percent=delay,
            class_goodput_mbps={TrafficClass.BULK: bulk},
        )
    return window


class TestVerification:
    def test_an_improvement_is_accepted(self, settings: Settings) -> None:
        result = Verifier(settings).verify(
            "policy-1", _window_of(90.0), _window_of(2.0), DataMode.SIMULATION, NOW
        )
        assert result.outcome is VerificationOutcome.ACCEPTABLE

    def test_a_delay_regression_is_degraded(self, settings: Settings) -> None:
        result = Verifier(settings).verify(
            "policy-1", _window_of(5.0), _window_of(80.0), DataMode.SIMULATION, NOW
        )
        assert result.outcome is VerificationOutcome.DEGRADED
        assert any(
            c.name == "interactive_delay_not_worse" and not c.passed for c in result.criteria
        )

    def test_starving_bulk_traffic_is_degraded_even_if_delay_improved(
        self, settings: Settings
    ) -> None:
        result = Verifier(settings).verify(
            "policy-1",
            _window_of(90.0, bulk=60.0),
            _window_of(1.0, bulk=1.0),
            DataMode.SIMULATION,
            NOW,
        )
        assert result.outcome is VerificationOutcome.DEGRADED
        assert any(c.name == "bulk_progress_retained" and not c.passed for c in result.criteria)

    def test_too_few_samples_is_inconclusive_rather_than_a_guess(
        self, settings: Settings
    ) -> None:
        result = Verifier(settings).verify(
            "policy-1",
            _window_of(90.0, samples=2),
            _window_of(2.0, samples=2),
            DataMode.SIMULATION,
            NOW,
        )
        assert result.outcome is VerificationOutcome.INCONCLUSIVE

    def test_the_result_reports_both_sides_of_the_comparison(
        self, settings: Settings
    ) -> None:
        result = Verifier(settings).verify(
            "policy-1", _window_of(90.0), _window_of(2.0), DataMode.SIMULATION, NOW
        )
        assert result.before_added_delay_ms == pytest.approx(90.0)
        assert result.after_added_delay_ms == pytest.approx(2.0)
        assert result.measurement_window_seconds == settings.verification_window_seconds


class TestWatchdog:
    def test_stays_quiet_while_the_heartbeat_advances(self) -> None:
        watchdog = Watchdog(timeout_seconds=3.0)
        watchdog.heartbeat(NOW)
        assert watchdog.check(NOW + timedelta(seconds=1)) is False
        assert watchdog.alert_count == 0

    def test_requests_recovery_when_the_heartbeat_stops(self) -> None:
        watchdog = Watchdog(timeout_seconds=3.0)
        watchdog.heartbeat(NOW)
        assert watchdog.check(NOW + timedelta(seconds=10)) is True
        assert watchdog.alert_count == 1

    def test_requests_recovery_only_once_per_outage(self) -> None:
        watchdog = Watchdog(timeout_seconds=3.0)
        watchdog.heartbeat(NOW)
        watchdog.check(NOW + timedelta(seconds=10))
        assert watchdog.check(NOW + timedelta(seconds=11)) is False
        assert watchdog.alert_count == 1

    def test_recovers_after_the_heartbeat_returns(self) -> None:
        watchdog = Watchdog(timeout_seconds=3.0)
        watchdog.heartbeat(NOW)
        watchdog.check(NOW + timedelta(seconds=10))
        watchdog.heartbeat(NOW + timedelta(seconds=11))
        assert watchdog.check(NOW + timedelta(seconds=20)) is True
        assert watchdog.alert_count == 2


# ---------------------------------------------------------------------------
# Intent leases
# ---------------------------------------------------------------------------


class TestIntentLeases:
    def _manager(self, settings: Settings) -> IntentManager:
        return IntentManager(settings)

    def test_creates_a_lease_for_a_known_target(self, settings: Settings) -> None:
        lease = self._manager(settings).create(
            experiment_id="exp-1",
            target_type="DEVICE",
            target_id="laptop",
            intent_type=IntentType.PRIORITIZE_INTERACTIVE,
            duration_seconds=60,
            reason="Work call",
            known_targets={"laptop"},
            now=NOW,
        )
        assert lease.status is IntentStatus.ACTIVE
        assert lease.expires_at == NOW + timedelta(seconds=60)

    def test_rejects_an_unknown_target(self, settings: Settings) -> None:
        with pytest.raises(IntentRejected, match="not known"):
            self._manager(settings).create(
                experiment_id=None,
                target_type="DEVICE",
                target_id="toaster",
                intent_type=IntentType.PRIORITIZE_INTERACTIVE,
                duration_seconds=60,
                reason="",
                known_targets={"laptop"},
                now=NOW,
            )

    def test_rejects_a_duration_beyond_the_configured_maximum(
        self, settings: Settings
    ) -> None:
        with pytest.raises(IntentRejected, match="exceeds"):
            self._manager(settings).create(
                experiment_id=None,
                target_type="DEVICE",
                target_id="laptop",
                intent_type=IntentType.PRIORITIZE_INTERACTIVE,
                duration_seconds=settings.max_intent_duration_seconds + 1,
                reason="",
                known_targets={"laptop"},
                now=NOW,
            )

    def test_expiry_is_enforced_by_the_server(self, settings: Settings) -> None:
        manager = self._manager(settings)
        manager.create(
            experiment_id=None,
            target_type="DEVICE",
            target_id="laptop",
            intent_type=IntentType.PRIORITIZE_INTERACTIVE,
            duration_seconds=10,
            reason="",
            known_targets={"laptop"},
            now=NOW,
        )
        assert manager.active(NOW + timedelta(seconds=5))
        expired = manager.expire_due(NOW + timedelta(seconds=11))
        assert len(expired) == 1
        assert expired[0].status is IntentStatus.EXPIRED
        assert manager.active(NOW + timedelta(seconds=11)) == []

    def test_a_second_lease_for_the_same_target_replaces_the_first(
        self, settings: Settings
    ) -> None:
        manager = self._manager(settings)
        first = manager.create(
            experiment_id=None,
            target_type="DEVICE",
            target_id="laptop",
            intent_type=IntentType.PRIORITIZE_INTERACTIVE,
            duration_seconds=60,
            reason="",
            known_targets={"laptop"},
            now=NOW,
        )
        manager.create(
            experiment_id=None,
            target_type="DEVICE",
            target_id="laptop",
            intent_type=IntentType.PROTECT_DEVICE,
            duration_seconds=60,
            reason="",
            known_targets={"laptop"},
            now=NOW,
        )
        active = manager.active(NOW)
        assert len(active) == 1
        assert active[0].intent_id != first.intent_id

    def test_an_expired_lease_boosts_nothing(self, settings: Settings) -> None:
        manager = self._manager(settings)
        manager.create(
            experiment_id=None,
            target_type="DEVICE",
            target_id="laptop",
            intent_type=IntentType.PRIORITIZE_INTERACTIVE,
            duration_seconds=10,
            reason="",
            known_targets={"laptop"},
            now=NOW,
        )
        assert manager.boosted_classes(NOW) != ()
        assert manager.boosted_classes(NOW + timedelta(seconds=20)) == ()


class TestCapacityRatchet:
    """The estimator must not mistake our own shaping for the link's limit.

    Shaping below the link, measuring the shaped rate, lowering the estimate
    and shaping lower again is a positive feedback loop. Left unchecked it
    walks a 100 Mbps link down to a few megabits, which is exactly what it
    did on the laboratory before these two guards existed.
    """

    def _saturate(self, estimator: CapacityEstimator, mbps: float, **extra) -> None:
        for _ in range(8):
            estimator.observe(
                served_mbps=mbps,
                offered_mbps=mbps * 1.3,
                queue_occupancy_percent=40.0,
                added_delay_ms=60.0,
                elapsed_seconds=1.0,
                now=NOW,
                **extra,
            )

    def test_a_sample_limited_by_our_own_shaping_is_ignored(
        self, estimator: CapacityEstimator
    ) -> None:
        self._saturate(estimator, 95.0)
        before = estimator.current(now=NOW).usable_capacity_mbps
        assert before is not None

        # Now we shape to 40 Mbps and naturally only ever observe 40.
        self._saturate(estimator, 40.0, shaped_total_mbps=40.0)
        after = estimator.current(now=NOW)
        assert after.usable_capacity_mbps == pytest.approx(before, rel=0.01)
        assert after.reason == "SAMPLE_LIMITED_BY_OUR_OWN_SHAPING_IGNORED"

    def test_the_floor_does_not_invent_an_estimate_on_a_quiet_link(
        self, estimator: CapacityEstimator
    ) -> None:
        """A trickle over an idle link says nothing about its ceiling.

        Treating it as an estimate would shape a fast link down to whatever
        it happened to be carrying.
        """
        for _ in range(10):
            estimator.observe(
                served_mbps=2.0,
                offered_mbps=2.0,
                queue_occupancy_percent=0.0,
                added_delay_ms=0.0,
                elapsed_seconds=1.0,
                now=NOW,
            )
        assert estimator.current(now=NOW).usable_capacity_mbps is None

    def test_an_existing_estimate_is_floored_by_delivered_rate(
        self, estimator: CapacityEstimator
    ) -> None:
        self._saturate(estimator, 40.0)
        # The link then demonstrably carries far more than the estimate.
        for _ in range(6):
            estimator.observe(
                served_mbps=90.0,
                offered_mbps=95.0,
                queue_occupancy_percent=30.0,
                added_delay_ms=40.0,
                elapsed_seconds=1.0,
                now=NOW,
            )
        current = estimator.current(now=NOW)
        assert current.usable_capacity_mbps is not None
        assert current.usable_capacity_mbps >= 90.0 * 0.95 - 1e-6

    def test_repeated_shaped_samples_do_not_walk_the_estimate_down(
        self, estimator: CapacityEstimator
    ) -> None:
        self._saturate(estimator, 95.0)
        shaped = estimator.current(now=NOW).usable_capacity_mbps
        assert shaped is not None

        # Ten rounds of the feedback loop: shape to the estimate, observe it.
        for _ in range(10):
            total = estimator.current(now=NOW).usable_capacity_mbps
            assert total is not None
            self._saturate(estimator, total, shaped_total_mbps=total)

        final = estimator.current(now=NOW).usable_capacity_mbps
        assert final is not None
        assert final >= shaped * 0.95

    def test_an_unshaped_drop_is_still_tracked_downward(
        self, estimator: CapacityEstimator
    ) -> None:
        """A real bottleneck reduction must still be followed."""
        self._saturate(estimator, 95.0)
        later = NOW + timedelta(seconds=60)
        for _ in range(12):
            estimator.observe(
                served_mbps=19.0,
                offered_mbps=90.0,
                queue_occupancy_percent=80.0,
                added_delay_ms=200.0,
                elapsed_seconds=1.0,
                now=later,
            )
        final = estimator.current(now=later).usable_capacity_mbps
        assert final is not None
        assert final < 40.0
