"""M3A - deterministic usable-capacity estimator.

This is not a machine-learning model. It estimates how much of the path is
actually usable from two kinds of observed evidence:

1. **Delivery-rate samples.** A sample only counts as evidence of capacity when
   the link was *saturated* while it was taken, that is, offered load met or
   exceeded what was served and a queue was present. A rate measured on an idle
   link says nothing about the ceiling.
2. **Delay trend.** Round-trip time rising above the idle baseline while the
   delivered rate stops rising is the signal that the path is at its limit.

Valid samples are smoothed with an exponentially weighted moving average::

    estimate <- estimate + alpha * (sample - estimate)

``alpha`` comes from configuration (``capacity_ewma_alpha``, default 0.25) so
its sensitivity can be tested. A larger alpha tracks a bottleneck change faster
and is noisier; a smaller alpha is steadier and slower.

The estimator never reads the configured laboratory bottleneck. It only ever
sees delivered rates and delays, exactly as it would against a real link. That
restriction is what makes the bottleneck-drop scenario a real test of it.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

from app.config import Settings
from app.domain.enums import DataQuality, Direction
from app.domain.metrics import CapacityEstimate

# A delivery-rate sample is only treated as capacity evidence when the queue is
# at least this deep, which means the link had more offered load than it could
# serve.
SATURATION_QUEUE_OCCUPANCY_PERCENT = 2.0
# Or when the delay has risen by at least this much above the idle baseline.
SATURATION_ADDED_DELAY_MS = 4.0
# How much the estimate may move in one update, as a fraction of the current
# estimate. This bounds the response to a single odd sample.
MAX_STEP_FRACTION = 0.5


class CapacityEstimator:
    """Per-direction usable-capacity estimator with an explicit quality state."""

    def __init__(self, settings: Settings, direction: Direction) -> None:
        self._settings = settings
        self.direction = direction
        self._estimate_mbps: float | None = None
        self._last_valid_sample_at: datetime | None = None
        self._last_sample_mbps: float | None = None
        self._valid_sample_count = 0
        self._recent_samples: deque[float] = deque(maxlen=40)
        self._window_seconds = 0.0
        self._last_saturated = False
        self._reason = "NO_SAMPLES_YET"

    def observe(
        self,
        *,
        served_mbps: float,
        offered_mbps: float,
        queue_occupancy_percent: float,
        added_delay_ms: float,
        elapsed_seconds: float,
        now: datetime | None = None,
    ) -> CapacityEstimate:
        """Feed one sampling interval of observations and return the estimate."""
        now = now or datetime.now(UTC)
        self._window_seconds = elapsed_seconds

        saturated = (
            queue_occupancy_percent >= SATURATION_QUEUE_OCCUPANCY_PERCENT
            or added_delay_ms >= SATURATION_ADDED_DELAY_MS
        ) and offered_mbps >= served_mbps * 0.98
        self._last_saturated = saturated
        self._last_sample_mbps = served_mbps

        if saturated and served_mbps > 0:
            self._recent_samples.append(served_mbps)
            self._valid_sample_count += 1
            self._last_valid_sample_at = now
            self._apply_sample(served_mbps)
            self._reason = "SATURATED_DELIVERY_RATE_SAMPLE"
        elif self._estimate_mbps is None:
            self._reason = "LOW_DEMAND_CAPACITY_NOT_OBSERVABLE"
        else:
            self._reason = "NO_NEW_SATURATED_SAMPLE_RETAINING_ESTIMATE"

        return self.current(now=now)

    def _apply_sample(self, sample_mbps: float) -> None:
        if self._estimate_mbps is None:
            self._estimate_mbps = sample_mbps
            return
        alpha = self._settings.capacity_ewma_alpha
        proposed = self._estimate_mbps + alpha * (sample_mbps - self._estimate_mbps)
        # Bound how far one update may move the estimate.
        ceiling = self._estimate_mbps * (1.0 + MAX_STEP_FRACTION)
        floor = self._estimate_mbps * (1.0 - MAX_STEP_FRACTION)
        self._estimate_mbps = min(max(proposed, floor), ceiling)

    def _quality(self, now: datetime) -> DataQuality:
        if self._estimate_mbps is None:
            return DataQuality.NO_DATA
        if self._last_valid_sample_at is None:
            return DataQuality.NO_DATA
        age = (now - self._last_valid_sample_at).total_seconds()
        if age > self._settings.capacity_sample_staleness_seconds:
            return DataQuality.STALE
        if self._valid_sample_count < 3:
            return DataQuality.LOW_CONFIDENCE
        return DataQuality.GOOD

    def current(self, now: datetime | None = None) -> CapacityEstimate:
        now = now or datetime.now(UTC)
        quality = self._quality(now)
        usable = self._estimate_mbps
        if usable is not None:
            # Keep a configured safety margin below the observed rate so a
            # policy built on this estimate does not immediately oversubscribe.
            usable = usable * self._settings.capacity_headroom_fraction

        return CapacityEstimate(
            direction=self.direction,
            usable_capacity_mbps=round(usable, 4) if usable is not None else None,
            delivery_rate_sample_mbps=(
                round(self._last_sample_mbps, 4) if self._last_sample_mbps is not None else None
            ),
            sample_count=self._valid_sample_count,
            sample_window_seconds=round(self._window_seconds, 3),
            ewma_alpha=self._settings.capacity_ewma_alpha,
            quality=quality,
            saturated_observation=self._last_saturated,
            reason=self._reason,
            updated_at=self._last_valid_sample_at,
            fallback_capacity_mbps=None,
        )

    def is_usable_for_policy(self, now: datetime | None = None) -> bool:
        """Whether the estimate is good enough to build a policy on."""
        return self._quality(now or datetime.now(UTC)) in (
            DataQuality.GOOD,
            DataQuality.LOW_CONFIDENCE,
        )
