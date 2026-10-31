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
# A sample taken while our own shaper was the binding constraint says nothing
# about the link. Treating it as evidence creates a ratchet: shape below the
# link, measure the shaped rate, lower the estimate, shape lower again. The
# estimator therefore discards any sample where delivery reached this fraction
# of the rate we ourselves imposed.
SHAPING_LIMITED_FRACTION = 0.92
# Delivered throughput is a hard lower bound on capacity: the link provably
# carried it. The estimate is never allowed below the best rate actually
# delivered within this trailing window.
DELIVERED_FLOOR_WINDOW_SECONDS = 20.0
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
        self._delivered_history: deque[tuple[datetime, float]] = deque(maxlen=400)

    def observe(
        self,
        *,
        served_mbps: float,
        offered_mbps: float,
        queue_occupancy_percent: float,
        added_delay_ms: float,
        elapsed_seconds: float,
        shaped_total_mbps: float | None = None,
        now: datetime | None = None,
    ) -> CapacityEstimate:
        """Feed one sampling interval of observations and return the estimate.

        ``shaped_total_mbps`` is the total rate this controller is itself
        imposing, when it is imposing one. It is the difference between "the
        link could not go faster" and "we would not let it", and without it
        the estimator mistakes its own policy for the link's limit.
        """
        now = now or datetime.now(UTC)
        self._window_seconds = elapsed_seconds

        if served_mbps > 0:
            self._delivered_history.append((now, served_mbps))
        self._prune_history(now)

        congested = (
            queue_occupancy_percent >= SATURATION_QUEUE_OCCUPANCY_PERCENT
            or added_delay_ms >= SATURATION_ADDED_DELAY_MS
        )
        demand_met = offered_mbps >= served_mbps * 0.98
        shaping_limited = (
            shaped_total_mbps is not None
            and shaped_total_mbps > 0
            and served_mbps >= shaped_total_mbps * SHAPING_LIMITED_FRACTION
        )

        saturated = congested and demand_met and not shaping_limited
        self._last_saturated = saturated
        self._last_sample_mbps = served_mbps

        if saturated and served_mbps > 0:
            self._recent_samples.append(served_mbps)
            self._valid_sample_count += 1
            self._last_valid_sample_at = now
            self._apply_sample(served_mbps)
            self._reason = "SATURATED_DELIVERY_RATE_SAMPLE"
        elif shaping_limited:
            self._reason = "SAMPLE_LIMITED_BY_OUR_OWN_SHAPING_IGNORED"
        elif self._estimate_mbps is None:
            self._reason = "LOW_DEMAND_CAPACITY_NOT_OBSERVABLE"
        else:
            self._reason = "NO_NEW_SATURATED_SAMPLE_RETAINING_ESTIMATE"

        # Stop an existing estimate ratcheting below what the link has just
        # been seen to carry. This is a floor on an estimate already made, not
        # grounds to invent one: a quiet link delivering a little says nothing
        # about its ceiling, and treating that as an estimate would shape a
        # fast link down to the trickle it happened to be carrying.
        if self._estimate_mbps is not None:
            floor = self._delivered_floor()
            if floor is not None and self._estimate_mbps < floor:
                self._estimate_mbps = floor
                self._reason = "RAISED_TO_OBSERVED_DELIVERED_RATE"

        return self.current(now=now)

    def _prune_history(self, now: datetime) -> None:
        cutoff = now.timestamp() - DELIVERED_FLOOR_WINDOW_SECONDS
        while self._delivered_history and self._delivered_history[0][0].timestamp() < cutoff:
            self._delivered_history.popleft()

    def _delivered_floor(self) -> float | None:
        if not self._delivered_history:
            return None
        return max(rate for _, rate in self._delivered_history)

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
