"""Tests for the deterministic simulator.

These pin the properties the rest of the system relies on: reproducibility,
queue growth being offered load minus service rate, and shaping actually
changing the measured outcome.
"""

from __future__ import annotations

import pytest

from app.domain.enums import CongestionState, Direction, TrafficClass
from app.simulation.engine import SimulationEngine, jain_fairness_index
from tests.conftest import build_engine


def _run(engine: SimulationEngine, seconds: float) -> list:
    return [engine.step() for _ in range(int(seconds / engine.dt))]


class TestDeterminism:
    def test_same_seed_produces_identical_runs(self) -> None:
        a, b = build_engine(seed=7), build_engine(seed=7)
        for _ in range(200):
            left, right = a.step(), b.step()
            assert left.rtt_ms == right.rtt_ms
            assert left.added_delay_ms == right.added_delay_ms
            assert left.packet_loss_percent == right.packet_loss_percent

    def test_different_seeds_diverge(self) -> None:
        a, b = build_engine(seed=1), build_engine(seed=2)
        series_a = [a.step().rtt_ms for _ in range(100)]
        series_b = [b.step().rtt_ms for _ in range(100)]
        assert series_a != series_b

    def test_reproducible_across_engine_instances_after_shaping(self) -> None:
        shaping = {TrafficClass.BULK: (30.0, 2.0)}
        a, b = build_engine(seed=11), build_engine(seed=11)
        for engine in (a, b):
            _run(engine, 30.0)
            engine.set_shaping(shaping, shaping)
        assert [a.step().rtt_ms for _ in range(50)] == [b.step().rtt_ms for _ in range(50)]


class TestQueueDynamics:
    def test_queue_stays_empty_while_offered_load_is_below_capacity(self) -> None:
        engine = build_engine()
        # The bulk transfer starts at 20 s; before that the mix fits easily.
        for result in _run(engine, 15.0):
            download = result.directions[Direction.DOWNLOAD]
            assert download.offered_bps <= download.link_bps
            assert download.backlog_bits == pytest.approx(0.0, abs=1.0)

    def test_queue_grows_by_offered_minus_served(self) -> None:
        engine = build_engine()
        _run(engine, 19.0)
        previous = engine.links[Direction.DOWNLOAD].backlog_bits
        for _ in range(30):
            result = engine.step()
            download = result.directions[Direction.DOWNLOAD]
            expected = max(
                0.0, previous + (download.offered_bps - download.link_bps) * engine.dt
            )
            # Active queue management may trim the backlog but never add to it.
            assert download.backlog_bits <= expected + 1.0
            previous = download.backlog_bits

    def test_backlog_is_capped_at_the_configured_buffer(self) -> None:
        engine = build_engine()
        for result in _run(engine, 60.0):
            download = result.directions[Direction.DOWNLOAD]
            assert download.backlog_bits <= engine.backlog_limit_bits + 1.0
            assert 0.0 <= download.queue_occupancy_percent <= 100.0

    def test_queueing_delay_matches_backlog_over_link_rate(self) -> None:
        engine = build_engine()
        for result in _run(engine, 40.0):
            download = result.directions[Direction.DOWNLOAD]
            expected_ms = download.backlog_bits / download.link_bps * 1000.0
            assert download.queue_delay_ms == pytest.approx(expected_ms, rel=1e-6)


class TestBufferbloatAndRecovery:
    def test_greedy_transfer_fills_an_unmanaged_buffer(self) -> None:
        """Drop-tail only signals on overflow, so delay rises before loss."""
        engine = build_engine()
        results = _run(engine, 45.0)
        peak_delay = max(r.added_delay_ms for r in results)
        peak_occupancy = max(
            r.directions[Direction.DOWNLOAD].queue_occupancy_percent for r in results
        )
        assert peak_delay > 50.0
        assert peak_occupancy > 90.0
        assert any(r.congestion_state is CongestionState.CONGESTED for r in results)

    def test_shaping_drains_the_queue_and_protects_delay(self) -> None:
        engine = build_engine()
        _run(engine, 40.0)
        congested = engine.step().added_delay_ms
        assert congested > 20.0

        shaping = {
            TrafficClass.INTERACTIVE: (8.0, 5.0),
            TrafficClass.GAMING_LIKE: (3.0, 1.0),
            TrafficClass.STREAMING_LIKE: (20.0, 6.0),
            TrafficClass.WEB_VOICE: (5.0, 1.0),
            TrafficClass.BULK: (55.0, 2.0),
        }
        engine.set_flow_marks(
            {flow_id: runtime.spec.true_class for flow_id, runtime in engine.flows.items()}
        )
        engine.set_shaping(shaping, shaping)

        settled = _run(engine, 15.0)[-20:]
        assert max(r.added_delay_ms for r in settled) < 10.0
        assert max(r.packet_loss_percent for r in settled) < 0.1

    def test_shaping_still_lets_bulk_make_progress(self) -> None:
        engine = build_engine()
        _run(engine, 25.0)
        before = engine.total_bytes("iso-download")
        shaping = {TrafficClass.BULK: (40.0, 2.0)}
        engine.set_flow_marks({"iso-download": TrafficClass.BULK})
        engine.set_shaping(shaping, shaping)
        _run(engine, 20.0)
        assert engine.total_bytes("iso-download") > before


class TestFlowMarks:
    def test_shaper_follows_the_controllers_mark_not_ground_truth(self) -> None:
        """A misclassification must have a real consequence.

        If the data plane followed the generator's label, comparing a rule set
        against a model would measure nothing.
        """
        engine = build_engine()
        _run(engine, 25.0)
        # Mark the bulk transfer as interactive and give interactive a tiny
        # ceiling. If marks are honoured, the transfer is throttled.
        engine.set_flow_marks({"iso-download": TrafficClass.INTERACTIVE})
        shaping = {TrafficClass.INTERACTIVE: (5.0, 1.0)}
        engine.set_shaping(shaping, shaping)
        _run(engine, 5.0)

        before = engine.total_bytes("iso-download")
        _run(engine, 5.0)
        delivered_mbps = (engine.total_bytes("iso-download") - before) * 8 / 5.0 / 1e6
        assert delivered_mbps < 7.0

    def test_unmarked_traffic_is_not_black_holed(self) -> None:
        """A class the policy forgot must fall through to best effort."""
        engine = build_engine()
        _run(engine, 25.0)
        engine.set_flow_marks({})  # everything unmarked
        engine.set_shaping({TrafficClass.INTERACTIVE: (5.0, 1.0)}, None)
        results = _run(engine, 5.0)
        assert all(r.directions[Direction.DOWNLOAD].served_bps > 0 for r in results)


class TestImpairments:
    def test_bottleneck_drop_is_applied_at_the_scheduled_time(self) -> None:
        engine = build_engine(scenario_id="bottleneck_drop", duration=80.0)
        before = _run(engine, 40.0)[-1]
        after = _run(engine, 20.0)[-1]
        assert before.directions[Direction.DOWNLOAD].link_bps == pytest.approx(100e6)
        assert after.directions[Direction.DOWNLOAD].link_bps == pytest.approx(20e6)

    def test_impairment_is_reported_once(self) -> None:
        engine = build_engine(scenario_id="bottleneck_drop", duration=80.0)
        notes = [r.impairment_note for r in _run(engine, 60.0) if r.impairment_note]
        assert len(notes) == 1


class TestJainFairness:
    def test_equal_shares_score_one(self) -> None:
        assert jain_fairness_index([10.0, 10.0, 10.0]) == pytest.approx(1.0)

    def test_one_starved_flow_lowers_the_index(self) -> None:
        assert jain_fairness_index([10.0, 10.0, 0.0]) == pytest.approx(2 / 3)

    def test_single_flow_is_not_meaningful(self) -> None:
        assert jain_fairness_index([10.0]) is None

    def test_no_traffic_is_not_meaningful(self) -> None:
        assert jain_fairness_index([0.0, 0.0]) is None


class TestLifecycle:
    def test_flows_start_and_stop_on_schedule(self) -> None:
        engine = build_engine()
        early = _run(engine, 5.0)[-1]
        assert "iso-download" not in {s.flow_id for s in early.flows if s.active}
        later = _run(engine, 20.0)[-1]
        assert "iso-download" in {s.flow_id for s in later.flows if s.active}

    def test_progress_and_completion_track_duration(self) -> None:
        engine = build_engine(duration=10.0)
        _run(engine, 10.0)
        assert engine.is_finished
        assert engine.progress == pytest.approx(1.0)
