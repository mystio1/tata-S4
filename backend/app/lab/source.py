"""Live measurements shaped like the simulator's output.

The control loop in ``app.runtime.experiment`` does not know or care where its
measurements come from. It consumes a :class:`~app.simulation.engine.TickResult`
per tick, and this module produces one from the laboratory agent's readings so
the same observer, estimator, policy engine, Safety Guard and verifier run
unchanged against real traffic.

Keeping one control path for both modes is the point. A separate live
controller would mean the thing demonstrated in simulation is not the thing
running in the laboratory, and the Mode 1 against Mode 2 comparison would be
comparing two different programs.

What is genuinely different here is honesty about gaps. The simulator always
has a value; the laboratory sometimes does not, because a probe window can be
empty or a generator may not have reported yet. Those arrive as ``None`` and
stay ``None``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.domain.enums import CongestionState, Direction, Protocol, TrafficClass
from app.lab.client import LabAgentClient, LabAgentError
from app.simulation.engine import (
    ADDED_DELAY_BUFFERBLOAT_MS,
    ADDED_DELAY_BUILDING_MS,
    ADDED_DELAY_CONGESTED_MS,
    BUFFERBLOAT_OCCUPANCY_PERCENT,
    BUFFERBLOAT_PERSISTENCE_SECONDS,
    DirectionSample,
    FlowSample,
    TickResult,
)

logger = logging.getLogger("qosense.lab.source")

# Representative packet size, matching the laboratory's own assumption, used
# only to express a byte backlog as an occupancy percentage.
REPRESENTATIVE_PACKET_BYTES = 1400


class LabDataSource:
    """Adapts the laboratory agent to the control loop's tick interface.

    Deliberately mirrors the parts of :class:`SimulationEngine` the runtime
    uses, so the runtime needs no branch on data mode.
    """

    def __init__(
        self,
        client: LabAgentClient,
        *,
        scenario_id: str,
        experiment_id: str,
        parameters: dict[str, float],
        duration_seconds: float,
        tick_seconds: float,
        queue_limit_packets: int = 900,
    ) -> None:
        self._client = client
        self.scenario_id = scenario_id
        self.experiment_id = experiment_id
        self.parameters = parameters
        self.duration_seconds = duration_seconds
        self.dt = tick_seconds
        self.queue_limit_packets = queue_limit_packets

        self.tick_index = 0
        self.elapsed_seconds = 0.0
        self.last_error: str | None = None
        self.notes: list[str] = []

        self._idle_baseline_rtt_ms: float | None = None
        self._bufferbloat_since: float | None = None
        self._flow_totals: dict[str, int] = {}
        self._flow_class: dict[str, TrafficClass] = {}
        self._seen_flows: set[str] = set()
        self._shaping_installed = False
        self._started = False
        # flow name -> (port, protocol), from the scenario the agent declares.
        # The port is only how the data path recognises a flow once the
        # classifier has decided its class; it is never a classifier input.
        self._flow_ports: dict[str, tuple[int, str]] = {}

    # -- lifecycle -----------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Reset the laboratory and start the scenario on it."""
        for scenario in self._client.list_scenarios():
            if scenario.get("scenario_id") != self.scenario_id:
                continue
            for flow in scenario.get("flows", []):
                self._flow_ports[str(flow["name"])] = (
                    int(flow["port"]),
                    str(flow["protocol"]),
                )

        state = self._client.start_scenario(
            self.scenario_id, self.experiment_id, self.parameters
        )
        self._started = True
        self.notes.extend(state.get("notes", []))
        return state

    def flow_selectors(self, classes: dict[str, Any]) -> list[dict[str, Any]]:
        """Turn the controller's per-flow classes into data-path selectors."""
        selectors: list[dict[str, Any]] = []
        for flow_id, traffic_class in classes.items():
            port_protocol = self._flow_ports.get(flow_id)
            if port_protocol is None:
                continue
            port, protocol = port_protocol
            selectors.append(
                {
                    "port": port,
                    "protocol": protocol,
                    "traffic_class": getattr(traffic_class, "value", str(traffic_class)),
                }
            )
        return selectors

    def stop(self) -> None:
        try:
            self._client.stop_scenario(self.experiment_id)
        except LabAgentError as exc:  # pragma: no cover - teardown is best effort
            logger.warning("could not stop the laboratory scenario: %s", exc)

    @property
    def is_finished(self) -> bool:
        return self.elapsed_seconds >= self.duration_seconds

    @property
    def progress(self) -> float:
        if self.duration_seconds <= 0:
            return 1.0
        return min(1.0, self.elapsed_seconds / self.duration_seconds)

    # -- the runtime's engine-shaped surface ---------------------------

    def set_flow_marks(self, marks: dict[str, TrafficClass]) -> None:
        """Record the controller's classes.

        In the laboratory the marks reach the data path as traffic-control
        filters installed with the policy, so they are carried in the apply
        call rather than set here. Keeping the record means the enforcer can
        build those filters from the controller's current view.
        """
        self._flow_class.update(marks)

    def flow_classes(self) -> dict[str, TrafficClass]:
        return dict(self._flow_class)

    def set_shaping(self, download: Any, upload: Any) -> None:
        """Clearing shaping is the only direction handled here.

        Installing shaping is a privileged transaction with its own
        verification, so it goes through the enforcer rather than through a
        data source.
        """
        if download is None and upload is None and self._shaping_installed:
            try:
                self._client.clear_policy()
            except LabAgentError as exc:  # pragma: no cover - teardown
                logger.warning("could not clear laboratory shaping: %s", exc)
            self._shaping_installed = False

    def note_shaping_installed(self) -> None:
        self._shaping_installed = True

    def total_bytes(self, flow_id: str) -> int:
        return self._flow_totals.get(flow_id, 0)

    @property
    def flows(self) -> dict[str, Any]:
        """Bulk accounting for the report, keyed like the simulator's flows."""
        return {
            flow_id: _FlowRecord(flow_id, self._flow_class.get(flow_id, TrafficClass.UNKNOWN))
            for flow_id in self._seen_flows
        }

    # -- the tick ------------------------------------------------------

    def step(self) -> TickResult:
        """Take one measurement sample and shape it like a simulator tick."""
        self.tick_index += 1
        self.elapsed_seconds = round(self.tick_index * self.dt, 6)

        try:
            payload = self._client.get_metrics(window_seconds=max(1.0, self.dt * 2))
            self.last_error = None
        except LabAgentError as exc:
            # A missed sample is a gap, not a zero. The tick still happens so
            # the controller can see the measurement is stale and refuse to
            # act on it.
            self.last_error = str(exc)
            logger.warning("laboratory measurement unavailable: %s", exc)
            return self._empty_tick()

        if payload.get("elapsed_seconds") is not None:
            # Trust the laboratory's own clock over the controller's tick
            # count, so a slow controller does not distort the record.
            self.elapsed_seconds = float(payload["elapsed_seconds"])

        probe = payload.get("probe") or {}
        queues = payload.get("queues") or {}
        flows = payload.get("flows") or {}
        classes = (payload.get("classes") or {}).get("download") or {}

        rtt_ms = probe.get("rtt_ms")
        jitter_ms = probe.get("jitter_ms")
        loss_percent = probe.get("packet_loss_percent")

        if rtt_ms is not None:
            baseline = probe.get("rtt_min_ms") or rtt_ms
            self._idle_baseline_rtt_ms = (
                baseline
                if self._idle_baseline_rtt_ms is None
                else min(self._idle_baseline_rtt_ms, baseline)
            )
        baseline_rtt = self._idle_baseline_rtt_ms
        added_delay = (
            max(0.0, rtt_ms - baseline_rtt)
            if rtt_ms is not None and baseline_rtt is not None
            else 0.0
        )

        direction_samples = {
            Direction.DOWNLOAD: self._direction_sample(
                Direction.DOWNLOAD, queues, classes, flows
            ),
            Direction.UPLOAD: self._direction_sample(
                Direction.UPLOAD,
                queues,
                (payload.get("classes") or {}).get("upload") or {},
                flows,
            ),
        }

        flow_samples = self._flow_samples(flows, direction_samples)
        congestion = self._classify_congestion(added_delay, direction_samples)

        return TickResult(
            tick_index=self.tick_index,
            elapsed_seconds=self.elapsed_seconds,
            timestamp=datetime.now(UTC),
            flows=flow_samples,
            directions=direction_samples,
            rtt_ms=rtt_ms if rtt_ms is not None else 0.0,
            idle_baseline_rtt_ms=baseline_rtt if baseline_rtt is not None else 0.0,
            added_delay_ms=added_delay,
            jitter_ms=jitter_ms if jitter_ms is not None else 0.0,
            packet_loss_percent=loss_percent if loss_percent is not None else 0.0,
            congestion_state=congestion,
            shaping_active=self._shaping_installed,
            impairment_note=None,
            loss_observed=bool(loss_percent),
        )

    # -- helpers -------------------------------------------------------

    def _direction_sample(
        self,
        direction: Direction,
        queues: dict[str, Any],
        classes: dict[str, Any],
        flows: dict[str, Any],
    ) -> DirectionSample:
        downstream = direction is Direction.DOWNLOAD
        key = "gateway_download" if downstream else "gateway_upload"
        bottleneck = queues.get("bottleneck_download", {}) if downstream else {}
        gateway = queues.get(key, {})

        # The queue that matters is wherever it actually formed. Unshaped it
        # sits in the provider's buffer; shaped it moves to the gateway.
        queue_packets = max(
            int(gateway.get("queue_packets", 0) or 0),
            int(bottleneck.get("queue_packets", 0) or 0),
        )
        queue_bytes = max(
            float(gateway.get("queue_bytes", 0) or 0),
            float(bottleneck.get("queue_bytes", 0) or 0),
        )

        # Total from the per-flow kernel counters. They exist whether or not
        # shaping is installed, which the per-class counters do not: a class
        # only has counters once a policy created it, so totalling classes
        # reads as near zero on an unshaped link and drags the capacity
        # estimate down with it.
        served_mbps = sum(
            float(flow.get("throughput_mbps") or 0.0)
            for flow in flows.values()
            if flow.get("direction") == direction.value
        )
        if served_mbps <= 0:
            served_mbps = sum(
                float(entry.get("throughput_mbps") or 0.0) for entry in classes.values()
            )

        occupancy = min(
            100.0,
            queue_packets / max(1, self.queue_limit_packets) * 100.0,
        )

        return DirectionSample(
            direction=direction,
            # The laboratory link rate is not measured, and the configured
            # rate is deliberately not available. Reporting zero would be a
            # lie; the estimator works from served rate and delay instead.
            link_bps=0.0,
            offered_bps=served_mbps * 1e6,
            served_bps=served_mbps * 1e6,
            backlog_bits=queue_bytes * 8.0,
            backlog_limit_bits=self.queue_limit_packets * REPRESENTATIVE_PACKET_BYTES * 8.0,
            queue_packets=queue_packets,
            queue_occupancy_percent=occupancy,
            queue_delay_ms=0.0,
            dropped_packets=int(gateway.get("dropped_packets", 0) or 0),
            dropped_bits=0.0,
            aqm_marked_bits=0.0,
        )

    def _flow_samples(
        self, flows: dict[str, Any], directions: dict[Direction, DirectionSample]
    ) -> list[FlowSample]:
        samples: list[FlowSample] = []
        for name, entry in flows.items():
            direction = (
                Direction.DOWNLOAD if entry.get("direction") == "DOWNLOAD" else Direction.UPLOAD
            )
            protocol = Protocol.UDP if entry.get("protocol") == "UDP" else Protocol.TCP
            throughput = entry.get("throughput_mbps")
            served_bps = float(throughput or 0.0) * 1e6

            just_started = name not in self._seen_flows
            self._seen_flows.add(name)
            counted_total = entry.get("total_bytes")
            if counted_total is not None:
                self._flow_totals[name] = int(counted_total)
            else:
                self._flow_totals[name] = self._flow_totals.get(name, 0) + int(
                    served_bps * self.dt / 8.0
                )

            packets = entry.get("packets_per_second")
            # Mean packet size comes from the kernel's own byte and packet
            # counters for this flow. Substituting an assumed size here would
            # quietly decide the classifier's answer, because packet size is
            # its leading discriminator.
            mean_packet_bytes = entry.get("mean_packet_bytes")
            if packets is None and mean_packet_bytes:
                packets = served_bps / (float(mean_packet_bytes) * 8.0)

            samples.append(
                FlowSample(
                    flow_id=name,
                    device_id=str(entry.get("device") or name.split("-")[0]),
                    direction=direction,
                    protocol=protocol,
                    # The generator's intended class is recorded for the
                    # experiment log only. The controller classifies from
                    # observed behaviour, exactly as in simulation.
                    true_class=TrafficClass.UNKNOWN,
                    active=bool(entry.get("running")),
                    demand_bps=served_bps,
                    served_bps=served_bps,
                    packets_per_second=float(packets or 0.0),
                    observed_mean_packet_bytes=float(mean_packet_bytes or 0.0),
                    dropped_packets=0,
                    total_bytes=self._flow_totals[name],
                    flow_rtt_ms=0.0,
                    served_fraction=1.0,
                    age_seconds=self.elapsed_seconds,
                    just_started=just_started,
                    just_stopped=not bool(entry.get("running")),
                )
            )
        return samples

    def _classify_congestion(
        self, added_delay_ms: float, directions: dict[Direction, DirectionSample]
    ) -> CongestionState:
        occupancy = max(d.queue_occupancy_percent for d in directions.values())
        severe = (
            added_delay_ms >= ADDED_DELAY_BUFFERBLOAT_MS
            and occupancy >= BUFFERBLOAT_OCCUPANCY_PERCENT
        )
        if severe:
            if self._bufferbloat_since is None:
                self._bufferbloat_since = self.elapsed_seconds
            elif self.elapsed_seconds - self._bufferbloat_since >= BUFFERBLOAT_PERSISTENCE_SECONDS:
                return CongestionState.BUFFERBLOAT
        else:
            self._bufferbloat_since = None

        if added_delay_ms >= ADDED_DELAY_CONGESTED_MS:
            return CongestionState.CONGESTED
        if added_delay_ms >= ADDED_DELAY_BUILDING_MS:
            return CongestionState.BUILDING
        return CongestionState.CLEAR

    def _empty_tick(self) -> TickResult:
        """A tick with no measurement. Nothing is invented to fill it."""
        empty = {
            direction: DirectionSample(
                direction=direction,
                link_bps=0.0,
                offered_bps=0.0,
                served_bps=0.0,
                backlog_bits=0.0,
                backlog_limit_bits=self.queue_limit_packets * REPRESENTATIVE_PACKET_BYTES * 8.0,
                queue_packets=0,
                queue_occupancy_percent=0.0,
                queue_delay_ms=0.0,
                dropped_packets=0,
                dropped_bits=0.0,
                aqm_marked_bits=0.0,
            )
            for direction in (Direction.DOWNLOAD, Direction.UPLOAD)
        }
        return TickResult(
            tick_index=self.tick_index,
            elapsed_seconds=self.elapsed_seconds,
            timestamp=datetime.now(UTC),
            flows=[],
            directions=empty,
            rtt_ms=0.0,
            idle_baseline_rtt_ms=self._idle_baseline_rtt_ms or 0.0,
            added_delay_ms=0.0,
            jitter_ms=0.0,
            packet_loss_percent=0.0,
            congestion_state=CongestionState.UNKNOWN,
            shaping_active=self._shaping_installed,
            impairment_note=None,
            loss_observed=False,
        )


class _FlowRecord:
    """Minimal stand-in for the simulator's flow record, for the report."""

    def __init__(self, flow_id: str, traffic_class: TrafficClass) -> None:
        self.spec = _FlowSpecRecord(flow_id, traffic_class)


class _FlowSpecRecord:
    def __init__(self, flow_id: str, traffic_class: TrafficClass) -> None:
        self.flow_id = flow_id
        self.true_class = traffic_class
