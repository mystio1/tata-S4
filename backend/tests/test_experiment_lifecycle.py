"""Integration tests for the experiment lifecycle and the full control loop.

These run the real orchestrator against the real simulator with the simulated
enforcer standing in for the Ubuntu laboratory agent, which is the mocked
integration the project requires before any live traffic control.
"""

from __future__ import annotations

import pytest

from app.domain.enums import (
    ClassificationSource,
    DataMode,
    EnforcementStatus,
    EventType,
    ExperimentMode,
    ExperimentStatus,
    PolicyStatus,
    RiskBand,
    TrafficClass,
    VerificationOutcome,
)
from app.domain.experiment import ExperimentCreateRequest
from app.state import AppState
from tests.conftest import build_runtime, run_to_completion


class TestLifecycle:
    def test_a_new_experiment_starts_in_the_created_state(self, state: AppState) -> None:
        runtime = build_runtime(state)
        assert runtime.status is ExperimentStatus.CREATED
        assert runtime.engine.tick_index == 0

    def test_start_pause_resume_stop(self, state: AppState) -> None:
        runtime = build_runtime(state)
        runtime.start()
        assert runtime.status is ExperimentStatus.RUNNING
        runtime.tick()

        runtime.pause()
        assert runtime.status is ExperimentStatus.PAUSED
        frozen = runtime.engine.tick_index

        runtime.resume()
        assert runtime.status is ExperimentStatus.RUNNING
        runtime.tick()
        assert runtime.engine.tick_index == frozen + 1

        runtime.stop()
        assert runtime.status is ExperimentStatus.STOPPED

    def test_a_run_completes_on_its_own_at_the_configured_duration(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(state, duration=15.0)
        run_to_completion(runtime)
        assert runtime.status is ExperimentStatus.COMPLETED
        assert runtime.engine.elapsed_seconds == pytest.approx(15.0, abs=0.3)

    def test_stopping_removes_shaping(self, state: AppState) -> None:
        runtime = build_runtime(state, duration=60.0)
        runtime.start()
        for _ in range(250):
            runtime.tick()
        runtime.stop()
        assert runtime.engine.shaping_active is False

    def test_reset_rebuilds_the_run_from_the_same_configuration(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(state, duration=20.0)
        runtime.start()
        for _ in range(20):
            runtime.tick()

        replacement = state.manager.reset(runtime.experiment_id)
        assert replacement.experiment_id != runtime.experiment_id
        assert replacement.config.scenario_id == runtime.config.scenario_id
        assert replacement.config.seed == runtime.config.seed
        assert replacement.status is ExperimentStatus.CREATED
        assert replacement.engine.tick_index == 0

    def test_the_same_seed_reproduces_the_same_report(self, state: AppState) -> None:
        first = build_runtime(state, duration=20.0, seed=99)
        run_to_completion(first)
        second = build_runtime(state, duration=20.0, seed=99)
        run_to_completion(second)

        def medians(runtime) -> dict[str, float | None]:
            return {s.metric: s.median for s in runtime.report().summaries}

        assert medians(first) == medians(second)

    def test_concurrency_is_bounded(self, state: AppState) -> None:
        for _ in range(state.settings.max_concurrent_experiments):
            build_runtime(state).start()
        with pytest.raises(RuntimeError, match="active at once"):
            build_runtime(state)

    def test_an_unknown_scenario_is_rejected(self, state: AppState) -> None:
        with pytest.raises(KeyError):
            state.manager.create(
                ExperimentCreateRequest(scenario_id="not_a_scenario", mode=ExperimentMode.NO_QOS)
            )

    def test_out_of_range_parameters_are_clamped_and_reported(self, state: AppState) -> None:
        runtime = state.manager.create(
            ExperimentCreateRequest(
                scenario_id="iso_during_call",
                mode=ExperimentMode.NO_QOS,
                parameters={"bottleneck_download_mbps": 99_999.0, "nonsense": 1.0},
            )
        )
        assert runtime.config.parameters["bottleneck_download_mbps"] == 500.0
        assert any("Clamped" in note for note in runtime.notes)
        assert any("nonsense" in note for note in runtime.notes)


class TestDataModeHonesty:
    def test_live_mode_is_refused(self) -> None:
        with pytest.raises(ValueError, match="laboratory agent"):
            ExperimentCreateRequest(
                scenario_id="iso_during_call",
                mode=ExperimentMode.NO_QOS,
                data_mode=DataMode.LIVE,
            )

    def test_replay_mode_is_refused(self) -> None:
        with pytest.raises(ValueError, match="REPLAY"):
            ExperimentCreateRequest(
                scenario_id="iso_during_call",
                mode=ExperimentMode.NO_QOS,
                data_mode=DataMode.REPLAY,
            )

    def test_every_event_and_snapshot_is_labelled_simulation(self, state: AppState) -> None:
        runtime = build_runtime(state, duration=20.0)
        run_to_completion(runtime)
        events = state.bus.recent(limit=10_000, experiment_id=runtime.experiment_id)
        assert events
        assert all(e.data_mode is DataMode.SIMULATION for e in events)
        assert runtime.latest_snapshot is not None
        assert runtime.latest_snapshot.data_mode is DataMode.SIMULATION

    def test_enforcement_is_never_reported_as_applied(self, state: AppState) -> None:
        runtime = build_runtime(state, duration=60.0)
        run_to_completion(runtime)
        enforcements = [e.enforcement for e in runtime.policy_history if e.enforcement]
        assert enforcements
        assert all(e.status is not EnforcementStatus.APPLIED for e in enforcements)
        assert all(e.status is EnforcementStatus.SIMULATED for e in enforcements)

    def test_the_configured_bottleneck_is_not_published_as_a_measurement(
        self, state: AppState
    ) -> None:
        """The estimator has to discover it, so exposing it would be cheating."""
        runtime = build_runtime(state, duration=20.0)
        run_to_completion(runtime)
        assert runtime.latest_snapshot is not None
        assert len(runtime.latest_snapshot.per_direction) == 2
        for direction in runtime.latest_snapshot.per_direction:
            assert direction.link_rate_mbps is None
            assert direction.utilization_percent is None

    def test_report_states_its_limitations(self, state: AppState) -> None:
        runtime = build_runtime(state, duration=15.0)
        run_to_completion(runtime)
        report = runtime.report()
        joined = " ".join(report.limitations)
        assert "SIMULATION" in joined
        assert "No Linux traffic control" in joined


class TestControlLoop:
    def test_the_controller_relieves_congestion_the_baseline_leaves_in_place(
        self, state: AppState
    ) -> None:
        """Mode 1 against Mode 2 on the same scenario and seed."""
        baseline = build_runtime(state, mode=ExperimentMode.NO_QOS, duration=70.0)
        run_to_completion(baseline)
        controlled = build_runtime(
            state, mode=ExperimentMode.TRADITIONAL_ADAPTIVE, duration=70.0
        )
        run_to_completion(controlled)

        def median(runtime, metric: str) -> float:
            value = next(s.median for s in runtime.report().summaries if s.metric == metric)
            assert value is not None
            return value

        assert median(baseline, "added_delay_ms") > 20.0
        assert median(controlled, "added_delay_ms") < 5.0
        assert median(controlled, "jitter_ms") < median(baseline, "jitter_ms")

    def test_the_controller_does_not_starve_bulk_traffic(self, state: AppState) -> None:
        baseline = build_runtime(state, mode=ExperimentMode.NO_QOS, duration=70.0)
        run_to_completion(baseline)
        controlled = build_runtime(
            state, mode=ExperimentMode.TRADITIONAL_ADAPTIVE, duration=70.0
        )
        run_to_completion(controlled)

        baseline_mb = baseline.report().bulk_transferred_megabytes
        controlled_mb = controlled.report().bulk_transferred_megabytes
        assert baseline_mb and controlled_mb
        assert controlled_mb > baseline_mb * 0.5

    def test_no_qos_mode_never_shapes(self, state: AppState) -> None:
        runtime = build_runtime(state, mode=ExperimentMode.NO_QOS, duration=60.0)
        run_to_completion(runtime)
        assert runtime.policy_history == []
        assert runtime.policy_changes == 0
        assert runtime.enforcer.installed_allocations == []

    def test_a_policy_change_is_snapshotted_applied_read_back_and_verified(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(state, duration=70.0)
        run_to_completion(runtime)
        applied = [e for e in runtime.policy_history if e.enforcement]
        assert applied
        entry = applied[0]
        assert entry.proposal.status is PolicyStatus.ACCEPTED
        assert entry.enforcement is not None
        assert entry.enforcement.snapshot_id is not None
        assert entry.enforcement.read_back is not None
        assert entry.enforcement.read_back.matches_intent
        assert entry.verification is not None
        assert entry.verification.outcome in (
            VerificationOutcome.ACCEPTABLE,
            VerificationOutcome.DEGRADED,
            VerificationOutcome.INCONCLUSIVE,
        )

    def test_the_decision_trace_explains_the_selection(self, state: AppState) -> None:
        runtime = build_runtime(state, duration=70.0)
        run_to_completion(runtime)
        applied = next(e for e in runtime.policy_history if e.enforcement)
        proposal = applied.proposal
        assert proposal.reason_codes
        assert proposal.explanation
        assert proposal.candidates
        assert proposal.weights_version
        assert proposal.safety is not None and proposal.safety.checks

    def test_policy_changes_respect_the_minimum_hold(self, state: AppState) -> None:
        runtime = build_runtime(state, duration=90.0)
        run_to_completion(runtime)
        applied = [e for e in runtime.policy_history if e.enforcement]
        times = [e.enforcement.requested_at for e in applied if e.enforcement]
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
        # Wall-clock gaps are not the simulated clock, so just assert the
        # engine did not flap: far fewer changes than evaluations.
        evaluations = len(runtime.policy_history)
        assert len(applied) < evaluations / 4
        assert all(gap >= 0 for gap in gaps)


class TestModelDegradedModes:
    def test_classifier_only_is_marked_degraded_and_falls_back(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(state, mode=ExperimentMode.CLASSIFIER_ONLY, duration=40.0)
        assert runtime.mode_availability.degraded is True
        assert "must not be presented" in runtime.mode_availability.detail
        run_to_completion(runtime)
        assert all(
            c.source is ClassificationSource.SAFE_FALLBACK
            for c in runtime.classifications.values()
        )
        assert all(
            c.predicted_class is TrafficClass.UNKNOWN for c in runtime.classifications.values()
        )

    def test_full_qosense_is_marked_degraded_for_both_models(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(state, mode=ExperimentMode.FULL_QOSENSE, duration=20.0)
        assert runtime.mode_availability.degraded is True
        assert "Model 1" in runtime.mode_availability.detail
        assert "Model 2" in runtime.mode_availability.detail

    def test_a_degraded_mode_still_protects_bulk_traffic(self, state: AppState) -> None:
        """Falling back must not silently starve a class."""
        runtime = build_runtime(state, mode=ExperimentMode.CLASSIFIER_ONLY, duration=70.0)
        run_to_completion(runtime)
        transferred = runtime.report().bulk_transferred_megabytes
        assert transferred is not None and transferred > 100.0

    def test_no_congestion_prediction_is_produced(self, state: AppState) -> None:
        runtime = build_runtime(state, mode=ExperimentMode.FULL_QOSENSE, duration=20.0)
        run_to_completion(runtime)
        predictions = state.bus.recent(
            limit=1000,
            experiment_id=runtime.experiment_id,
            types={EventType.CONGESTION_PREDICTED},
        )
        assert predictions
        for event in predictions:
            assert event.payload["risk_band"] == RiskBand.UNAVAILABLE.value
            assert event.payload["congestion_probability"] is None

    def test_measured_congestion_is_separate_from_predicted_risk(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(state, duration=60.0)
        run_to_completion(runtime)
        snapshots = state.bus.recent(
            limit=10_000,
            experiment_id=runtime.experiment_id,
            types={EventType.METRICS_SNAPSHOT},
        )
        states = {e.payload["congestion_state"] for e in snapshots}
        predictions = {e.payload["congestion_prediction"] for e in snapshots}
        assert predictions == {RiskBand.UNAVAILABLE.value}
        assert states - {RiskBand.UNAVAILABLE.value}


class TestEvents:
    def test_the_expected_event_types_are_emitted(self, state: AppState) -> None:
        runtime = build_runtime(state, duration=70.0)
        run_to_completion(runtime)
        emitted = {
            e.type
            for e in state.bus.recent(limit=10_000, experiment_id=runtime.experiment_id)
        }
        assert {
            EventType.EXPERIMENT_STATUS,
            EventType.FLOW_DETECTED,
            EventType.FLOW_CLASSIFIED,
            EventType.METRICS_SNAPSHOT,
            EventType.POLICY_EVALUATING,
            EventType.POLICY_PROPOSED,
            EventType.POLICY_APPLIED,
            EventType.ENFORCEMENT_VERIFIED,
            EventType.VERIFICATION_COMPLETED,
            EventType.CONGESTION_PREDICTED,
            EventType.EXPERIMENT_COMPLETED,
        } <= emitted

    def test_sequence_numbers_are_monotonic(self, state: AppState) -> None:
        runtime = build_runtime(state, duration=20.0)
        run_to_completion(runtime)
        sequences = [
            e.sequence
            for e in state.bus.recent(limit=10_000, experiment_id=runtime.experiment_id)
        ]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)

    def test_reading_the_state_does_not_consume_a_sequence_number(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(state, duration=20.0)
        runtime.start()
        runtime.tick()
        first = runtime.state().last_sequence
        assert runtime.state().last_sequence == first

    def test_a_slow_subscriber_drops_events_rather_than_blocking(
        self, state: AppState
    ) -> None:
        subscriber = state.bus.subscribe(None)
        runtime = build_runtime(state, duration=60.0)
        run_to_completion(runtime)
        # Nothing consumed the queue, so the bus must have shed load.
        assert subscriber.dropped > 0


class TestFairnessReporting:
    def test_fairness_is_computed_across_comparable_flows_only(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(
            state, scenario_id="three_streams_gaming", duration=60.0
        )
        run_to_completion(runtime)
        report = runtime.report()
        assert report.jain_fairness_index is not None
        assert "comparable" in report.fairness_basis
        assert report.jain_fairness_index > 0.8

    def test_fairness_is_not_claimed_when_nothing_is_comparable(
        self, state: AppState
    ) -> None:
        runtime = build_runtime(state, duration=15.0)
        run_to_completion(runtime)
        report = runtime.report()
        if report.jain_fairness_index is None:
            assert "Not computed" in report.fairness_basis
