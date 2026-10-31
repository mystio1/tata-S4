"""Deterministic laboratory simulator.

This module produces every number the ``SIMULATION`` data mode shows. It is a
single coupled model, not a collection of independent random generators: the
queue, the round-trip time, the loss rate and each flow's throughput all come
out of the same per-tick state update, so the charts and the three-dimensional
scene can never disagree with each other.

Model summary
-------------

For each direction and each tick of length ``dt``:

1. Every active flow declares a demand in bits per second.

   * ``CONSTANT_BITRATE`` and ``BURSTY`` flows are inelastic. Their demand does
     not depend on what the link gives them.
   * ``GREEDY`` flows are elastic and congestion controlled. They increase
     additively while no congestion signal arrives and decrease multiplicatively
     when one does, which is the behaviour that makes a bulk transfer fill a
     buffer.

2. The link allocates capacity.

   * With no shaping the link is a single first-in-first-out queue with
     drop-tail behaviour, so capacity is shared in proportion to demand.
   * With shaping active the link behaves like Hierarchical Token Bucket
     classes each holding a Fair Queuing Controlled Delay queue: class ceilings
     and guaranteed floors are honoured first, spare capacity is lent to classes
     that can use it, and flows inside a class share their class allocation
     max-min fairly.

3. The backlog integrates the difference between offered load and link rate::

       backlog_bits += (offered_bps - link_bps) * dt

   so queue growth is always offered load minus service rate. Nothing else
   writes to the backlog.

4. Active queue management trims the backlog.

   * Drop-tail (no shaping): the backlog is capped at the configured buffer
     size and the overflow is counted as loss. The elastic flows only learn
     about congestion at this point, which is why delay climbs first and loss
     follows. This is the bufferbloat behaviour the project is built to show.
   * Fair Queuing Controlled Delay (shaping active): the backlog is held near a
     small delay target and the trimmed bits are counted as early marks, so the
     elastic flows back off before the buffer fills.

5. Queueing delay is ``backlog_bits / link_bps``. Probe round-trip time is the
   idle path round-trip time plus the queueing delay of both directions.

Determinism
-----------

All variation comes from a single ``random.Random`` seeded with the experiment
seed. The same scenario, parameters and seed always produce the same tick
sequence, regardless of wall-clock timing or animation frame rate.
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.domain.enums import CongestionState, Direction, Protocol, TrafficClass
from app.domain.experiment import FlowSpec, ScenarioDefinition

# Fair Queuing Controlled Delay holds the standing queue near this delay.
CODEL_TARGET_MS = 6.0
# Elastic flows add this much sending rate per second while no congestion
# signal arrives, and multiply by the factor below when one does.
ELASTIC_ADDITIVE_INCREASE_MBPS_PER_S = 28.0
ELASTIC_MULTIPLICATIVE_DECREASE = 0.72
ELASTIC_MIN_RATE_MBPS = 0.4
# Smoothing for the reported jitter and loss series.
JITTER_EWMA_ALPHA = 0.2
LOSS_EWMA_ALPHA = 0.25
# Congestion state thresholds, in milliseconds of delay above the idle baseline.
ADDED_DELAY_BUILDING_MS = 10.0
ADDED_DELAY_CONGESTED_MS = 30.0
ADDED_DELAY_BUFFERBLOAT_MS = 90.0
BUFFERBLOAT_OCCUPANCY_PERCENT = 65.0
BUFFERBLOAT_PERSISTENCE_SECONDS = 2.0

MBPS = 1_000_000.0


def _mbps_to_bps(value: float) -> float:
    return value * MBPS


@dataclass
class FlowSample:
    """One tick of state for one flow."""

    flow_id: str
    device_id: str
    direction: Direction
    protocol: Protocol
    true_class: TrafficClass
    active: bool
    demand_bps: float
    served_bps: float
    packets_per_second: float
    # Mean packet size the observer measured this tick. This is what M1 sees;
    # it is not read from the generator's configuration record.
    observed_mean_packet_bytes: float
    dropped_packets: int
    total_bytes: int
    flow_rtt_ms: float
    served_fraction: float
    age_seconds: float
    just_started: bool
    just_stopped: bool


@dataclass
class DirectionSample:
    """One tick of link state for one direction."""

    direction: Direction
    link_bps: float
    offered_bps: float
    served_bps: float
    backlog_bits: float
    backlog_limit_bits: float
    queue_packets: int
    queue_occupancy_percent: float
    queue_delay_ms: float
    dropped_packets: int
    dropped_bits: float
    aqm_marked_bits: float


@dataclass
class TickResult:
    """Everything one simulator tick produced."""

    tick_index: int
    elapsed_seconds: float
    timestamp: datetime
    flows: list[FlowSample]
    directions: dict[Direction, DirectionSample]
    rtt_ms: float
    idle_baseline_rtt_ms: float
    added_delay_ms: float
    jitter_ms: float
    packet_loss_percent: float
    congestion_state: CongestionState
    shaping_active: bool
    impairment_note: str | None
    loss_observed: bool


@dataclass
class _FlowRuntime:
    """Mutable per-flow simulator state."""

    spec: FlowSpec
    rate_mbps: float
    active: bool = False
    was_active: bool = False
    started_at_s: float | None = None
    total_bytes: int = 0
    congestion_signal: bool = False
    dropped_packets: int = 0
    recent_served_bps: list[float] = field(default_factory=list)

    @property
    def is_elastic(self) -> bool:
        return self.spec.demand_model == "GREEDY"


@dataclass
class _LinkRuntime:
    direction: Direction
    link_mbps: float
    backlog_bits: float = 0.0
    dropped_packets_total: int = 0


class SimulationEngine:
    """Deterministic scenario simulator.

    The engine owns no timing. A caller advances it with :meth:`step`, which is
    what keeps the animation frame rate from affecting sampling.
    """

    def __init__(
        self,
        scenario: ScenarioDefinition,
        parameters: dict[str, float],
        seed: int,
        tick_seconds: float,
        duration_seconds: float,
    ) -> None:
        self.scenario = scenario
        self.parameters = parameters
        self.seed = seed
        self.dt = tick_seconds
        self.duration_seconds = duration_seconds
        self._rng = random.Random(seed)

        self.base_rtt_ms: float = float(
            parameters.get("base_rtt_ms", scenario.default_base_rtt_ms)
        )
        download_mbps = float(
            parameters.get("bottleneck_download_mbps", scenario.default_download_mbps)
        )
        upload_mbps = float(
            parameters.get("bottleneck_upload_mbps", scenario.default_upload_mbps)
        )

        self.links: dict[Direction, _LinkRuntime] = {
            Direction.DOWNLOAD: _LinkRuntime(Direction.DOWNLOAD, download_mbps),
            Direction.UPLOAD: _LinkRuntime(Direction.UPLOAD, upload_mbps),
        }

        self.flows: dict[str, _FlowRuntime] = {}
        for spec in scenario.flows:
            adjusted = self._apply_flow_parameter_overrides(spec)
            self.flows[adjusted.flow_id] = _FlowRuntime(
                spec=adjusted,
                rate_mbps=(
                    ELASTIC_MIN_RATE_MBPS
                    if adjusted.demand_model == "GREEDY"
                    else adjusted.nominal_demand_mbps
                ),
            )

        self._impairments = self._resolve_impairments()
        self._impairments_applied: set[int] = set()

        # Buffer size in bits, derived from the configured packet limit and a
        # representative packet size so queue occupancy has a real basis.
        self._representative_packet_bytes = 1400.0
        self.backlog_limit_bits = (
            scenario.queue_limit_packets * self._representative_packet_bytes * 8.0
        )

        self.tick_index = 0
        self.elapsed_seconds = 0.0
        self._previous_rtt_ms: float | None = None
        self._jitter_ms = 0.0
        self._loss_percent = 0.0
        self._idle_baseline_rtt_ms = self.base_rtt_ms
        self._baseline_locked = False
        self._bufferbloat_since: float | None = None

        self._shaping_download: dict[TrafficClass, tuple[float, float]] | None = None
        self._shaping_upload: dict[TrafficClass, tuple[float, float]] | None = None

        # Per-tick working state. Demand is drawn once per flow per tick and
        # reused, so the value a flow is reported as offering is exactly the
        # value the allocator saw.
        self._tick_demands: dict[str, float] = {}
        self._tick_allocation: dict[str, float] = {}
        self._tick_packet_bytes: dict[str, float] = {}

        # The class each flow is *marked* with, as the controller decided. This
        # is what the shaper acts on, exactly as a connection mark would on a
        # real gateway. The generator's ground-truth class is deliberately not
        # used here: if the data plane followed ground truth, a misclassification
        # would have no consequence and comparing a rule set against a model
        # would measure nothing.
        self._flow_marks: dict[str, TrafficClass] = {}

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------

    def _apply_flow_parameter_overrides(self, spec: FlowSpec) -> FlowSpec:
        """Apply bounded scenario parameters that move a flow's schedule."""
        start_override = self.parameters.get("download_start_after_seconds")
        if start_override is not None and spec.demand_model == "GREEDY":
            return spec.model_copy(update={"start_seconds": float(start_override)})
        return spec

    def _resolve_impairments(self) -> list[tuple[float, float | None, float | None, str]]:
        """Build the impairment schedule, honouring bounded parameter overrides."""
        steps: list[tuple[float, float | None, float | None, str]] = []
        for step in self.scenario.impairments:
            at = float(self.parameters.get("drop_at_seconds", step.at_seconds))
            download = step.download_mbps
            if download is not None:
                download = float(self.parameters.get("dropped_download_mbps", download))
            steps.append((at, download, step.upload_mbps, step.description))
        return sorted(steps, key=lambda s: s[0])

    # ------------------------------------------------------------------
    # Shaping
    # ------------------------------------------------------------------

    def set_shaping(
        self,
        download: dict[TrafficClass, tuple[float, float]] | None,
        upload: dict[TrafficClass, tuple[float, float]] | None,
    ) -> None:
        """Install per-class ``(ceiling_mbps, floor_mbps)`` shaping.

        Passing ``None`` removes shaping and returns the link to a single
        drop-tail first-in-first-out queue.
        """
        self._shaping_download = download
        self._shaping_upload = upload

    def set_flow_marks(self, marks: dict[str, TrafficClass]) -> None:
        """Install the controller's per-flow class marks.

        A mark applies to the traffic that follows it, so the caller sets marks
        after a tick and they take effect on the next one.
        """
        self._flow_marks = dict(marks)

    def mark_for(self, flow_id: str) -> TrafficClass:
        """The class the shaper will treat this flow as. Unmarked is UNKNOWN."""
        return self._flow_marks.get(flow_id, TrafficClass.UNKNOWN)

    @property
    def shaping_active(self) -> bool:
        return self._shaping_download is not None or self._shaping_upload is not None

    def _shaping_for(self, direction: Direction) -> dict[TrafficClass, tuple[float, float]] | None:
        return (
            self._shaping_download
            if direction is Direction.DOWNLOAD
            else self._shaping_upload
        )

    # ------------------------------------------------------------------
    # Demand
    # ------------------------------------------------------------------

    def _flow_demand_bps(self, runtime: _FlowRuntime) -> float:
        spec = runtime.spec
        if spec.demand_model == "GREEDY":
            return _mbps_to_bps(runtime.rate_mbps)

        # Small deterministic variation so inelastic sources are not perfectly
        # flat. The amplitude is a few percent, well below anything that would
        # change a policy decision on its own.
        wobble = 1.0 + self._rng.uniform(-0.035, 0.035)

        if spec.demand_model == "BURSTY":
            period = spec.burst_period_seconds or 6.0
            duty = spec.burst_duty_cycle or 0.5
            # A stable per-flow offset so the players do not all burst in step.
            # ``hash`` is not usable here: Python randomises string hashing per
            # process, which would make runs non-reproducible.
            offset = zlib.crc32(spec.flow_id.encode()) % 7
            phase = (self.elapsed_seconds + offset) % period
            on = phase < period * duty
            # An adaptive-bitrate player idles between buffer fills, so the
            # on-phase rate is the average rate divided by the duty cycle.
            return _mbps_to_bps(spec.nominal_demand_mbps / duty * wobble) if on else 0.0

        return _mbps_to_bps(spec.nominal_demand_mbps * wobble)

    def _update_elastic_rates(self) -> None:
        """Additive increase, multiplicative decrease for every elastic flow."""
        for runtime in self.flows.values():
            if not runtime.active or not runtime.is_elastic:
                continue
            if runtime.congestion_signal:
                runtime.rate_mbps = max(
                    ELASTIC_MIN_RATE_MBPS,
                    runtime.rate_mbps * ELASTIC_MULTIPLICATIVE_DECREASE,
                )
            else:
                runtime.rate_mbps = min(
                    runtime.spec.nominal_demand_mbps,
                    runtime.rate_mbps + ELASTIC_ADDITIVE_INCREASE_MBPS_PER_S * self.dt,
                )
            runtime.congestion_signal = False

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    @staticmethod
    def _max_min_share(demands: dict[str, float], capacity: float) -> dict[str, float]:
        """Classic max-min fair allocation of ``capacity`` across ``demands``."""
        allocation = dict.fromkeys(demands, 0.0)
        remaining = capacity
        unsatisfied = {k for k, v in demands.items() if v > 0}
        while unsatisfied and remaining > 1e-9:
            equal_share = remaining / len(unsatisfied)
            settled = {k for k in unsatisfied if demands[k] - allocation[k] <= equal_share}
            if not settled:
                for key in unsatisfied:
                    allocation[key] += equal_share
                remaining = 0.0
                break
            for key in settled:
                grant = demands[key] - allocation[key]
                allocation[key] += grant
                remaining -= grant
            unsatisfied -= settled
        return allocation

    def _allocate_shaped(
        self,
        demands: dict[str, float],
        classes: dict[str, TrafficClass],
        capacity_bps: float,
        shaping: dict[TrafficClass, tuple[float, float]],
    ) -> dict[str, float]:
        """Hierarchical Token Bucket style allocation with class ceilings.

        Guaranteed floors are met first, remaining capacity is lent out up to
        each class ceiling, and flows inside a class share their class
        allocation max-min fairly, which is what Fair Queuing Controlled Delay
        approximates.
        """
        class_demand: dict[TrafficClass, float] = {}
        for flow_id, demand in demands.items():
            class_demand[classes[flow_id]] = class_demand.get(classes[flow_id], 0.0) + demand

        class_allocation: dict[TrafficClass, float] = dict.fromkeys(class_demand, 0.0)
        remaining = capacity_bps

        def limits_for(traffic_class: TrafficClass) -> tuple[float, float]:
            """Ceiling and floor for a class, with a best-effort default.

            A class the policy did not mention falls through to a default class
            with the full link as its ceiling. A shaping gap must never silently
            black-hole traffic, which is what a zero ceiling would do.
            """
            return shaping.get(traffic_class, (capacity_bps / MBPS, 0.0))

        # Stage one: guaranteed floors, limited by what the class actually wants.
        for traffic_class, demand in class_demand.items():
            ceiling_mbps, floor_mbps = limits_for(traffic_class)
            floor = min(_mbps_to_bps(floor_mbps), demand, _mbps_to_bps(ceiling_mbps))
            grant = min(floor, remaining)
            class_allocation[traffic_class] = grant
            remaining -= grant

        # Stage two: lend spare capacity, max-min fair, bounded by each ceiling.
        headroom = {
            traffic_class: max(
                0.0,
                min(
                    class_demand[traffic_class],
                    _mbps_to_bps(limits_for(traffic_class)[0]),
                )
                - class_allocation[traffic_class],
            )
            for traffic_class in class_demand
        }
        lent = self._max_min_share(
            {str(k): v for k, v in headroom.items()}, max(0.0, remaining)
        )
        for traffic_class in class_demand:
            class_allocation[traffic_class] += lent.get(str(traffic_class), 0.0)

        # Stage three: share each class allocation across its own flows.
        allocation: dict[str, float] = {}
        for traffic_class, granted in class_allocation.items():
            members = {
                flow_id: demand
                for flow_id, demand in demands.items()
                if classes[flow_id] is traffic_class
            }
            allocation.update(self._max_min_share(members, granted))
        return allocation

    @staticmethod
    def _allocate_fifo(demands: dict[str, float], capacity_bps: float) -> dict[str, float]:
        """Single drop-tail queue: service in proportion to offered load."""
        offered = sum(demands.values())
        if offered <= capacity_bps or offered <= 0:
            return dict(demands)
        scale = capacity_bps / offered
        return {flow_id: demand * scale for flow_id, demand in demands.items()}

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def step(self) -> TickResult:
        """Advance the simulation by one tick and return the resulting state."""
        self.tick_index += 1
        self.elapsed_seconds = round(self.tick_index * self.dt, 6)
        self._tick_demands.clear()
        self._tick_allocation.clear()
        self._tick_packet_bytes.clear()
        impairment_note = self._apply_due_impairments()

        self._update_flow_lifecycle()
        self._update_elastic_rates()

        samples: list[FlowSample] = []
        direction_samples: dict[Direction, DirectionSample] = {}
        loss_observed = False

        for direction, link in self.links.items():
            result = self._step_direction(direction, link)
            direction_samples[direction] = result
            loss_observed = loss_observed or result.dropped_packets > 0

        queue_delay_total = sum(d.queue_delay_ms for d in direction_samples.values())
        rtt_ms = (
            self.base_rtt_ms
            + queue_delay_total
            + self._rng.uniform(-0.35, 0.35)
        )
        rtt_ms = max(0.5, rtt_ms)

        self._update_baseline(rtt_ms)
        added_delay_ms = max(0.0, rtt_ms - self._idle_baseline_rtt_ms)

        if self._previous_rtt_ms is not None:
            instantaneous = abs(rtt_ms - self._previous_rtt_ms)
            self._jitter_ms += JITTER_EWMA_ALPHA * (instantaneous - self._jitter_ms)
        self._previous_rtt_ms = rtt_ms

        offered_bits = sum(d.offered_bps for d in direction_samples.values()) * self.dt
        dropped_bits = sum(d.dropped_bits for d in direction_samples.values())
        instant_loss = (dropped_bits / offered_bits * 100.0) if offered_bits > 0 else 0.0
        self._loss_percent += LOSS_EWMA_ALPHA * (instant_loss - self._loss_percent)

        # Per-flow view, now that queueing delay for each direction is known.
        for runtime in self.flows.values():
            sample = self._build_flow_sample(runtime, direction_samples)
            if sample is not None:
                samples.append(sample)

        congestion_state = self._classify_congestion(added_delay_ms, direction_samples)

        return TickResult(
            tick_index=self.tick_index,
            elapsed_seconds=self.elapsed_seconds,
            timestamp=datetime.now(UTC),
            flows=samples,
            directions=direction_samples,
            rtt_ms=rtt_ms,
            idle_baseline_rtt_ms=self._idle_baseline_rtt_ms,
            added_delay_ms=added_delay_ms,
            jitter_ms=max(0.0, self._jitter_ms),
            packet_loss_percent=max(0.0, self._loss_percent),
            congestion_state=congestion_state,
            shaping_active=self.shaping_active,
            impairment_note=impairment_note,
            loss_observed=loss_observed,
        )

    def _apply_due_impairments(self) -> str | None:
        note: str | None = None
        for index, (at, download, upload, description) in enumerate(self._impairments):
            if index in self._impairments_applied or self.elapsed_seconds < at:
                continue
            if download is not None:
                self.links[Direction.DOWNLOAD].link_mbps = download
            if upload is not None:
                self.links[Direction.UPLOAD].link_mbps = upload
            self._impairments_applied.add(index)
            note = description
        return note

    def _update_flow_lifecycle(self) -> None:
        for runtime in self.flows.values():
            spec = runtime.spec
            runtime.was_active = runtime.active
            started = self.elapsed_seconds >= spec.start_seconds
            stopped = spec.stop_seconds is not None and self.elapsed_seconds >= spec.stop_seconds
            runtime.active = started and not stopped
            if runtime.active and runtime.started_at_s is None:
                runtime.started_at_s = self.elapsed_seconds

    def _step_direction(self, direction: Direction, link: _LinkRuntime) -> DirectionSample:
        capacity_bps = _mbps_to_bps(link.link_mbps)
        active = [r for r in self.flows.values() if r.active and r.spec.direction is direction]

        demands = {r.spec.flow_id: self._flow_demand_bps(r) for r in active}
        self._tick_demands.update(demands)
        # The packet sizes an observer would measure this tick: the generator's
        # nominal size with its own spread applied.
        for runtime in active:
            spec = runtime.spec
            self._tick_packet_bytes[spec.flow_id] = max(
                64.0,
                spec.mean_packet_bytes
                + self._rng.gauss(0.0, spec.packet_bytes_jitter)
                if spec.packet_bytes_jitter > 0
                else spec.mean_packet_bytes,
            )
        classes = {r.spec.flow_id: self.mark_for(r.spec.flow_id) for r in active}
        offered_bps = sum(demands.values())

        shaping = self._shaping_for(direction)
        if shaping:
            allocation = self._allocate_shaped(demands, classes, capacity_bps, shaping)
        else:
            allocation = self._allocate_fifo(demands, capacity_bps)

        served_bps = sum(allocation.values())

        # Queue growth is offered load minus service rate. Nothing else writes
        # to the backlog.
        link.backlog_bits = max(0.0, link.backlog_bits + (offered_bps - capacity_bps) * self.dt)

        dropped_bits = 0.0
        aqm_marked_bits = 0.0
        if shaping:
            # Fair Queuing Controlled Delay holds the standing queue near its
            # delay target by marking early rather than letting the buffer fill.
            codel_limit_bits = capacity_bps * (CODEL_TARGET_MS / 1000.0)
            if link.backlog_bits > codel_limit_bits:
                aqm_marked_bits = link.backlog_bits - codel_limit_bits
                link.backlog_bits = codel_limit_bits
            # A shaped class signals its elastic flows as soon as it cannot
            # serve their demand, which is what keeps the queue short.
            for runtime in active:
                if not runtime.is_elastic:
                    continue
                if allocation.get(runtime.spec.flow_id, 0.0) < demands[runtime.spec.flow_id] * 0.98:
                    runtime.congestion_signal = True
        else:
            # Drop-tail: elastic flows only learn about congestion once the
            # buffer actually overflows, so delay rises before loss appears.
            if link.backlog_bits > self.backlog_limit_bits:
                dropped_bits = link.backlog_bits - self.backlog_limit_bits
                link.backlog_bits = self.backlog_limit_bits
                for runtime in active:
                    if runtime.is_elastic:
                        runtime.congestion_signal = True

        # Attribute drops to flows in proportion to offered load and account
        # for delivered bytes.
        dropped_packets_total = 0
        for runtime in active:
            flow_id = runtime.spec.flow_id
            share = (demands[flow_id] / offered_bps) if offered_bps > 0 else 0.0
            flow_dropped_bits = dropped_bits * share
            packet_bits = runtime.spec.mean_packet_bytes * 8.0
            flow_dropped_packets = int(flow_dropped_bits / packet_bits) if packet_bits else 0
            runtime.dropped_packets = flow_dropped_packets
            dropped_packets_total += flow_dropped_packets
            runtime.total_bytes += int(allocation.get(flow_id, 0.0) * self.dt / 8.0)

        link.dropped_packets_total += dropped_packets_total

        queue_delay_ms = (link.backlog_bits / capacity_bps * 1000.0) if capacity_bps > 0 else 0.0
        queue_packets = int(link.backlog_bits / (self._representative_packet_bytes * 8.0))
        occupancy = (
            min(100.0, link.backlog_bits / self.backlog_limit_bits * 100.0)
            if self.backlog_limit_bits > 0
            else 0.0
        )

        self._tick_allocation.update(allocation)

        return DirectionSample(
            direction=direction,
            link_bps=capacity_bps,
            offered_bps=offered_bps,
            served_bps=served_bps,
            backlog_bits=link.backlog_bits,
            backlog_limit_bits=self.backlog_limit_bits,
            queue_packets=queue_packets,
            queue_occupancy_percent=occupancy,
            queue_delay_ms=queue_delay_ms,
            dropped_packets=dropped_packets_total,
            dropped_bits=dropped_bits,
            aqm_marked_bits=aqm_marked_bits,
        )

    def _build_flow_sample(
        self, runtime: _FlowRuntime, directions: dict[Direction, DirectionSample]
    ) -> FlowSample | None:
        spec = runtime.spec
        if not runtime.active and not runtime.was_active:
            return None

        served_bps = self._tick_allocation.get(spec.flow_id, 0.0)
        demand_bps = self._tick_demands.get(spec.flow_id, 0.0)
        packet_bytes = self._tick_packet_bytes.get(spec.flow_id, spec.mean_packet_bytes)
        packet_bits = packet_bytes * 8.0
        packets_per_second = served_bps / packet_bits if packet_bits else 0.0

        own = directions[spec.direction].queue_delay_ms
        other = directions[
            Direction.UPLOAD if spec.direction is Direction.DOWNLOAD else Direction.DOWNLOAD
        ].queue_delay_ms
        # A probe on this flow's path crosses its own direction's queue and the
        # reverse direction's queue on the way back.
        flow_rtt_ms = self.base_rtt_ms + own + other * 0.35

        return FlowSample(
            flow_id=spec.flow_id,
            device_id=spec.device_id,
            direction=spec.direction,
            protocol=spec.protocol,
            true_class=spec.true_class,
            active=runtime.active,
            demand_bps=demand_bps,
            served_bps=served_bps,
            packets_per_second=packets_per_second,
            observed_mean_packet_bytes=self._tick_packet_bytes.get(
                spec.flow_id, spec.mean_packet_bytes
            ),
            dropped_packets=runtime.dropped_packets,
            total_bytes=runtime.total_bytes,
            flow_rtt_ms=flow_rtt_ms,
            served_fraction=(
                min(1.0, served_bps / demand_bps)
                if demand_bps > 0
                else (1.0 if runtime.active else 0.0)
            ),
            age_seconds=(
                self.elapsed_seconds - runtime.started_at_s
                if runtime.started_at_s is not None
                else 0.0
            ),
            just_started=runtime.active and not runtime.was_active,
            just_stopped=runtime.was_active and not runtime.active,
        )

    def _update_baseline(self, rtt_ms: float) -> None:
        """Track the idle baseline round-trip time as the running minimum.

        The baseline is only allowed to fall. Taking the minimum is the standard
        way to recover an unloaded path delay from probes that may all have been
        taken under some load.
        """
        if not self._baseline_locked:
            self._idle_baseline_rtt_ms = rtt_ms
            self._baseline_locked = True
        else:
            self._idle_baseline_rtt_ms = min(self._idle_baseline_rtt_ms, rtt_ms)

    def _classify_congestion(
        self, added_delay_ms: float, directions: dict[Direction, DirectionSample]
    ) -> CongestionState:
        """Classify *currently measured* congestion. Never a prediction."""
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

    # ------------------------------------------------------------------
    # Introspection used by tests and the report builder
    # ------------------------------------------------------------------

    def flow_spec(self, flow_id: str) -> FlowSpec:
        return self.flows[flow_id].spec

    def total_bytes(self, flow_id: str) -> int:
        return self.flows[flow_id].total_bytes

    @property
    def is_finished(self) -> bool:
        return self.elapsed_seconds >= self.duration_seconds

    @property
    def progress(self) -> float:
        if self.duration_seconds <= 0:
            return 1.0
        return min(1.0, self.elapsed_seconds / self.duration_seconds)


def jain_fairness_index(values: list[float]) -> float | None:
    """Jain's fairness index across comparable flows only.

    Returns ``None`` when there are fewer than two samples, because the index is
    not meaningful for a single flow.
    """
    usable = [v for v in values if v is not None and not math.isnan(v)]
    if len(usable) < 2:
        return None
    total = sum(usable)
    if total <= 0:
        return None
    square_sum = sum(v * v for v in usable)
    if square_sum <= 0:
        return None
    return (total * total) / (len(usable) * square_sum)
