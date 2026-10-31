"""M1 - flow observer.

Aggregates per-tick flow samples into the feature windows the classifier
consumes. A classifier is never run on an individual packet: the unit of
inference is a window of flow behaviour, which is both cheaper and a better
description of what a flow is doing.

Only observable quantities enter a window. The scenario's ground-truth class,
the generator configuration and the emulated bottleneck setting are all
withheld, so a model trained on these windows cannot learn a shortcut that
would not exist against a real link.

No application payload is read, stored or derived from.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.enums import Direction, Protocol
from app.domain.metrics import (
    FEATURE_SCHEMA_VERSION,
    FlowFeatures,
    FlowFeatureWindow,
)
from app.simulation.engine import FlowSample

# How much observed behaviour a window covers before it is emitted.
DEFAULT_WINDOW_MS = 2000
# Smoothing applied to the activity ratio across consecutive windows.
ACTIVITY_EWMA_ALPHA = 0.35


@dataclass
class _FlowAccumulator:
    flow_id: str
    device_id: str
    direction: Direction
    protocol: Protocol
    mean_packet_bytes_samples: list[float] = field(default_factory=list)
    rate_samples_bps: list[float] = field(default_factory=list)
    packets_per_second_samples: list[float] = field(default_factory=list)
    bytes_total: float = 0.0
    packets_total: float = 0.0
    up_bytes: float = 0.0
    down_bytes: float = 0.0
    up_packets: float = 0.0
    down_packets: float = 0.0
    active_ticks: int = 0
    total_ticks: int = 0
    burst_count: int = 0
    _was_above: bool = False
    first_seen_elapsed: float | None = None
    last_seen_elapsed: float = 0.0
    # Activity ratio smoothed across windows. A single window can land wholly
    # inside a streaming player's buffer fill and look perfectly busy, which is
    # indistinguishable from a bulk transfer. Carrying the ratio across windows
    # exposes the duty cycle that actually separates the two.
    long_run_active_ratio: float | None = None

    def reset_window(self) -> None:
        self.mean_packet_bytes_samples.clear()
        self.rate_samples_bps.clear()
        self.packets_per_second_samples.clear()
        self.bytes_total = 0.0
        self.packets_total = 0.0
        self.up_bytes = 0.0
        self.down_bytes = 0.0
        self.up_packets = 0.0
        self.down_packets = 0.0
        self.active_ticks = 0
        self.total_ticks = 0
        self.burst_count = 0
        self._was_above = False


class FlowObserver:
    """Builds feature windows from the per-tick flow samples."""

    def __init__(self, window_ms: int = DEFAULT_WINDOW_MS) -> None:
        self.window_ms = window_ms
        self._accumulators: dict[str, _FlowAccumulator] = {}
        self._window_elapsed_ms = 0.0

    def observe_tick(
        self,
        samples: list[FlowSample],
        *,
        observed_packet_bytes: dict[str, float],
        tick_seconds: float,
        elapsed_seconds: float,
    ) -> None:
        """Record one tick of flow samples.

        ``observed_packet_bytes`` is the mean packet size the observer measured
        for each flow during this tick, which in the simulator comes from the
        generated traffic rather than from the flow's configuration record.
        """
        self._window_elapsed_ms += tick_seconds * 1000.0

        for sample in samples:
            acc = self._accumulators.get(sample.flow_id)
            if acc is None:
                acc = _FlowAccumulator(
                    flow_id=sample.flow_id,
                    device_id=sample.device_id,
                    direction=sample.direction,
                    protocol=Protocol.UDP,
                    first_seen_elapsed=elapsed_seconds,
                )
                self._accumulators[sample.flow_id] = acc

            acc.total_ticks += 1
            acc.last_seen_elapsed = elapsed_seconds

            rate = sample.served_bps
            acc.rate_samples_bps.append(rate)
            acc.packets_per_second_samples.append(sample.packets_per_second)
            # Only record a packet size that was actually measured. A flow
            # with no measurement yet contributes nothing rather than a
            # guess, because packet size is what the classifier leads with.
            packet_bytes = observed_packet_bytes.get(sample.flow_id, 0.0)
            if packet_bytes > 0:
                acc.mean_packet_bytes_samples.append(packet_bytes)

            tick_bytes = rate * tick_seconds / 8.0
            tick_packets = sample.packets_per_second * tick_seconds
            acc.bytes_total += tick_bytes
            acc.packets_total += tick_packets
            if sample.direction is Direction.UPLOAD:
                acc.up_bytes += tick_bytes
                acc.up_packets += tick_packets
            else:
                acc.down_bytes += tick_bytes
                acc.down_packets += tick_packets

            # A tick counts as active when the flow actually moved traffic.
            if rate > 0:
                acc.active_ticks += 1

            # Count rising edges above the flow's own mean as bursts.
            mean_rate = (
                statistics.fmean(acc.rate_samples_bps) if acc.rate_samples_bps else 0.0
            )
            above = rate > mean_rate * 1.2 and rate > 0
            if above and not acc._was_above:
                acc.burst_count += 1
            acc._was_above = above

    def set_protocol(self, flow_id: str, protocol: Protocol) -> None:
        acc = self._accumulators.get(flow_id)
        if acc is not None:
            acc.protocol = protocol

    @property
    def window_is_due(self) -> bool:
        return self._window_elapsed_ms >= self.window_ms

    def drain_windows(
        self, experiment_id: str, observed_at: datetime, elapsed_seconds: float
    ) -> list[FlowFeatureWindow]:
        """Emit a feature window per observed flow and start a new window."""
        windows: list[FlowFeatureWindow] = []
        window_seconds = self._window_elapsed_ms / 1000.0

        for acc in self._accumulators.values():
            if acc.total_ticks == 0 or acc.packets_total <= 0:
                acc.reset_window()
                continue

            packet_sizes = acc.mean_packet_bytes_samples or [
                acc.bytes_total / acc.packets_total
            ]
            mean_packet_bytes = statistics.fmean(packet_sizes)
            std_packet_bytes = (
                statistics.pstdev(packet_sizes) if len(packet_sizes) > 1 else 0.0
            )

            packets_per_second = (
                acc.packets_total / window_seconds if window_seconds > 0 else 0.0
            )
            bytes_per_second = acc.bytes_total / window_seconds if window_seconds > 0 else 0.0

            mean_interarrival_ms = (
                1000.0 / packets_per_second if packets_per_second > 0 else 0.0
            )
            # Variation in the packet rate across the window maps directly to
            # variation in the gap between packets.
            pps_samples = [p for p in acc.packets_per_second_samples if p > 0]
            if len(pps_samples) > 1:
                gaps = [1000.0 / p for p in pps_samples]
                std_interarrival_ms = statistics.pstdev(gaps)
            else:
                std_interarrival_ms = 0.0

            duration_ms = max(
                0.0,
                (acc.last_seen_elapsed - (acc.first_seen_elapsed or acc.last_seen_elapsed))
                * 1000.0,
            )

            window_active_ratio = acc.active_ticks / acc.total_ticks if acc.total_ticks else 0.0
            if acc.long_run_active_ratio is None:
                acc.long_run_active_ratio = window_active_ratio
            else:
                acc.long_run_active_ratio += ACTIVITY_EWMA_ALPHA * (
                    window_active_ratio - acc.long_run_active_ratio
                )

            windows.append(
                FlowFeatureWindow(
                    experiment_id=experiment_id,
                    flow_id=acc.flow_id,
                    device_id=acc.device_id,
                    observed_at=observed_at,
                    window_ms=int(self._window_elapsed_ms),
                    direction=acc.direction,
                    protocol=acc.protocol,
                    feature_schema_version=FEATURE_SCHEMA_VERSION,
                    features=FlowFeatures(
                        mean_packet_bytes=round(mean_packet_bytes, 3),
                        std_packet_bytes=round(std_packet_bytes, 3),
                        min_packet_bytes=round(min(packet_sizes), 3),
                        max_packet_bytes=round(max(packet_sizes), 3),
                        mean_interarrival_ms=round(mean_interarrival_ms, 4),
                        std_interarrival_ms=round(std_interarrival_ms, 4),
                        packets_per_second=round(packets_per_second, 3),
                        bytes_per_second=round(bytes_per_second, 3),
                        flow_duration_ms=round(duration_ms, 1),
                        up_bytes=int(acc.up_bytes),
                        down_bytes=int(acc.down_bytes),
                        up_packets=int(acc.up_packets),
                        down_packets=int(acc.down_packets),
                        burst_count=acc.burst_count,
                        active_time_ratio=round(acc.long_run_active_ratio, 4),
                    ),
                )
            )
            acc.reset_window()

        self._window_elapsed_ms = 0.0
        return windows

    def forget(self, flow_id: str) -> None:
        self._accumulators.pop(flow_id, None)
