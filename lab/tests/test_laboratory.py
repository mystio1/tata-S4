"""Integration tests against the real Linux testbed.

These build namespaces, move packets and install traffic control, so they
need root on a Linux host whose kernel has the queueing modules. Everywhere
else they skip, which keeps the suite runnable on a developer machine while
still being the tests that actually prove enforcement works.

Run them with::

    sudo .venv/bin/python -m pytest tests/test_laboratory.py -v
"""

from __future__ import annotations

import os
import platform
import time

import pytest

from agent import enforcement as enf
from agent import measurement as m
from agent import topology
from agent.commands import in_namespace
from agent.safety import IF_GW_WAN, IF_IFB, NS_GATEWAY

pytestmark = pytest.mark.laboratory

requires_laboratory = pytest.mark.skipif(
    platform.system() != "Linux" or os.geteuid() != 0,
    reason="needs root on a Linux host with the traffic-control modules",
)

LINK = topology.LinkProfile(download_mbps=100.0, upload_mbps=20.0, base_rtt_ms=18.0)


@pytest.fixture(scope="module")
def laboratory():
    """Build the testbed once, and always tear it down."""
    missing = topology.load_modules()
    if missing:
        pytest.skip(f"kernel cannot load: {', '.join(missing)}")
    topology.build(LINK)
    try:
        yield
    finally:
        m.stop_generators()
        topology.teardown()


@requires_laboratory
class TestTopology:
    def test_the_laboratory_forwards_end_to_end(self, laboratory) -> None:
        assert topology.connectivity_check()["reachable"] is True

    def test_every_namespace_and_interface_exists(self, laboratory) -> None:
        state = topology.status()
        assert state["built"] is True
        gateway = state["interfaces"]["qos-gw"]
        assert IF_GW_WAN in gateway
        assert IF_IFB in gateway

    def test_measured_delay_matches_the_configured_baseline(self, laboratory) -> None:
        probe = m.probe_rtt(count=8)
        assert probe["rtt_ms"] is not None
        assert probe["rtt_ms"] == pytest.approx(LINK.base_rtt_ms, abs=4.0)

    def test_the_bottleneck_is_installed_on_the_provider(self, laboratory) -> None:
        from agent.safety import IF_ISP_GW, NS_ISP

        output = in_namespace(
            NS_ISP, ["tc", "qdisc", "show", "dev", IF_ISP_GW]
        ).stdout
        assert "netem" in output
        assert "100Mbit" in output

    def test_reset_returns_the_laboratory_to_a_known_state(self, laboratory) -> None:
        topology.reset(LINK)
        assert topology.connectivity_check()["reachable"] is True


@requires_laboratory
class TestEnforcement:
    def _request(self, bulk_ceiling: float = 55.0) -> enf.PolicyRequest:
        return enf.PolicyRequest(
            policy_id="policy-test-0001",
            profile="PROTECT_INTERACTIVE",
            download=enf.DirectionProfile(
                total_mbps=92.0,
                classes=(
                    enf.ClassRate("INTERACTIVE", 8.0, 4.0),
                    enf.ClassRate("BULK", bulk_ceiling, 2.0),
                ),
            ),
            upload=enf.DirectionProfile(
                total_mbps=18.0, classes=(enf.ClassRate("BULK", 14.0, 1.0),)
            ),
            flows=(enf.FlowSelector(5209, "TCP", "BULK"),),
        )

    def test_a_policy_is_applied_and_confirmed_by_read_back(self, laboratory) -> None:
        result = enf.apply_policy(self._request())
        assert result["status"] == "APPLIED", result
        assert result["read_back"]["matches_intent"] is True
        assert result["mismatches"] == []

    def test_the_kernel_reports_the_rates_that_were_asked_for(self, laboratory) -> None:
        enf.apply_policy(self._request(bulk_ceiling=44.0))
        state = enf.read_state(IF_IFB)
        assert state.class_ceilings_mbps["BULK"] == pytest.approx(44.0, abs=0.5)
        assert state.class_ceilings_mbps["INTERACTIVE"] == pytest.approx(8.0, abs=0.5)

    def test_unmatched_traffic_still_has_a_class(self, laboratory) -> None:
        """A shaping gap must never black-hole a flow."""
        enf.apply_policy(self._request())
        state = enf.read_state(IF_IFB)
        assert "UNKNOWN" in state.class_ceilings_mbps

    def test_the_download_path_uses_the_virtual_interface(self, laboratory) -> None:
        result = enf.apply_policy(self._request())
        path = " ".join(result["read_back"]["download_path"]).lower()
        assert "intermediate functional block" in path
        qdisc = enf.read_state(IF_IFB).qdisc
        assert "htb" in qdisc

    def test_each_class_holds_a_controlled_delay_queue(self, laboratory) -> None:
        enf.apply_policy(self._request())
        assert "fq_codel" in enf.read_state(IF_IFB).qdisc

    def test_a_snapshot_restores_and_verifies(self, laboratory) -> None:
        enf.clear_shaping()
        result = enf.apply_policy(self._request())
        assert result["status"] == "APPLIED"
        restore = enf.restore_snapshot(result["snapshot_id"])
        assert restore["restored"] is True
        assert restore["verified"] is True

    def test_restoring_an_unknown_snapshot_fails_safely(self, laboratory) -> None:
        restore = enf.restore_snapshot("snap-does-not-exist")
        assert restore["restored"] is False
        assert restore["error"]

    def test_clearing_shaping_keeps_the_measurement_counters(self, laboratory) -> None:
        """Tearing the counters down with the shaping would blind the observer."""
        m.install_flow_counters(
            [{"name": "bulk", "port": 5209, "protocol": "TCP", "direction": "DOWNLOAD"}]
        )
        enf.apply_policy(self._request())
        enf.clear_shaping()
        from agent.safety import IF_GW_LAN

        output = in_namespace(
            NS_GATEWAY,
            ["tc", "filter", "show", "dev", IF_GW_LAN, "egress"],
            check=False,
        ).stdout
        assert "pref 209" in output


@requires_laboratory
class TestMeasurement:
    def test_counters_report_real_packet_sizes(self, laboratory) -> None:
        """The classifier leads with packet size, so it must be measured.

        A constant substituted here would quietly decide the classifier's
        answer for every flow.
        """
        topology.reset(LINK)
        m.reset_log_directory()
        m.install_flow_counters(
            [
                {"name": "bulk", "port": 5209, "protocol": "TCP", "direction": "DOWNLOAD"},
                {"name": "call", "port": 5201, "protocol": "UDP", "direction": "DOWNLOAD"},
            ]
        )
        m.start_flow(
            name="bulk", port=5209, protocol="TCP", traffic_class="BULK",
            direction="DOWNLOAD", duration_seconds=12,
        )
        m.start_flow(
            name="call", port=5201, protocol="UDP", traffic_class="INTERACTIVE",
            direction="DOWNLOAD", duration_seconds=12, rate_mbps=3.2, packet_bytes=760,
        )
        time.sleep(6)

        counters = m.flow_counters()
        m.stop_generators()

        assert counters["bulk"]["mean_packet_bytes"] > 1200
        assert 700 < counters["call"]["mean_packet_bytes"] < 900
        assert counters["bulk"]["total_packets"] > 0

    def test_the_continuous_probe_reports_a_window(self, laboratory) -> None:
        m.start_probe()
        time.sleep(2.0)
        probe = m.recent_probe(window_seconds=1.5)
        m.stop_probe()
        assert probe["probes_received"] > 0
        assert probe["rtt_ms"] is not None

    def test_an_empty_probe_window_reports_nothing_rather_than_zero(
        self, laboratory
    ) -> None:
        """A probe that returned nothing did not measure zero delay."""
        m.stop_probe()
        m.reset_log_directory()
        probe = m.recent_probe(window_seconds=1.0)
        assert probe["rtt_ms"] is None
        assert probe["jitter_ms"] is None
        assert probe["packet_loss_percent"] is None
        assert probe["reason"]


@requires_laboratory
class TestShapingChangesBehaviour:
    """The point of the whole project, measured rather than asserted."""

    def test_shaping_below_the_bottleneck_removes_the_standing_queue(
        self, laboratory
    ) -> None:
        topology.reset(LINK)
        m.reset_log_directory()
        enf.clear_shaping()
        m.start_probe()

        idle = []
        time.sleep(1.5)
        idle_probe = m.recent_probe(window_seconds=1.0)
        idle.append(idle_probe["rtt_ms"])
        assert idle_probe["rtt_ms"] is not None

        m.start_flow(
            name="bulk", port=5209, protocol="TCP", traffic_class="BULK",
            direction="DOWNLOAD", duration_seconds=40,
        )
        time.sleep(8)
        unshaped = m.recent_probe(window_seconds=2.0)
        unshaped_queue = m.queue_stats()["bottleneck_download"]["queue_packets"]

        result = enf.apply_policy(
            enf.PolicyRequest(
                policy_id="policy-test-shaping",
                profile="LIMIT_BULK",
                download=enf.DirectionProfile(
                    total_mbps=70.0, classes=(enf.ClassRate("BULK", 60.0, 2.0),)
                ),
                upload=enf.DirectionProfile(
                    total_mbps=18.0, classes=(enf.ClassRate("BULK", 14.0, 1.0),)
                ),
                flows=(enf.FlowSelector(5209, "TCP", "BULK"),),
            )
        )
        assert result["status"] == "APPLIED"
        time.sleep(8)
        shaped = m.recent_probe(window_seconds=2.0)

        m.stop_generators()
        enf.clear_shaping()

        added_unshaped = unshaped["rtt_ms"] - idle_probe["rtt_ms"]
        added_shaped = shaped["rtt_ms"] - idle_probe["rtt_ms"]

        # An unmanaged drop-tail buffer fills, and shaping below it empties.
        assert added_unshaped > 40.0, f"expected bufferbloat, saw {added_unshaped:.1f} ms"
        assert unshaped_queue > 50
        assert added_shaped < added_unshaped / 4, (
            f"shaping should have relieved the queue: {added_unshaped:.1f} ms -> "
            f"{added_shaped:.1f} ms"
        )

    def test_bulk_still_progresses_while_shaped(self, laboratory) -> None:
        topology.reset(LINK)
        m.reset_log_directory()
        m.install_flow_counters(
            [{"name": "bulk", "port": 5209, "protocol": "TCP", "direction": "DOWNLOAD"}]
        )
        enf.apply_policy(
            enf.PolicyRequest(
                policy_id="policy-test-progress",
                profile="LIMIT_BULK",
                download=enf.DirectionProfile(
                    total_mbps=70.0, classes=(enf.ClassRate("BULK", 40.0, 2.0),)
                ),
                upload=enf.DirectionProfile(
                    total_mbps=18.0, classes=(enf.ClassRate("BULK", 14.0, 1.0),)
                ),
                flows=(enf.FlowSelector(5209, "TCP", "BULK"),),
            )
        )
        m.start_flow(
            name="bulk", port=5209, protocol="TCP", traffic_class="BULK",
            direction="DOWNLOAD", duration_seconds=14,
        )
        time.sleep(5)
        m.flow_counters()
        time.sleep(4)
        counters = m.flow_counters()
        m.stop_generators()
        enf.clear_shaping()

        throughput = counters["bulk"]["throughput_mbps"]
        assert throughput is not None
        assert counters["bulk"]["measures"] == "delivered"
        # Shaped, not starved: comfortably moving traffic, and held at or
        # under the ceiling the policy set.
        assert 10.0 < throughput <= 45.0, throughput
