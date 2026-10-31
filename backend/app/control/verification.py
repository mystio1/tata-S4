"""M6 - verification, rollback decision and the independent watchdog.

Verification compares a measurement window taken before a policy change with
one taken after it, using the same probe method and the same window length.
The acceptance rule is explicit and testable, and every criterion that was
evaluated is reported so the user interface can show why a policy was kept or
restored.

Nothing here claims an improvement it cannot measure. When either window has
too few samples the outcome is ``INCONCLUSIVE`` and the policy is retained
unchanged, because rolling back on missing evidence is its own kind of churn.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config import Settings
from app.domain.enums import DataMode, TrafficClass, VerificationOutcome
from app.domain.policy import SafetyCheck, VerificationResult

MINIMUM_SAMPLES_PER_WINDOW = 5
# A policy must not make delay-sensitive conditions worse by more than this.
MAX_ADDED_DELAY_REGRESSION_MS = 5.0
MAX_LOSS_REGRESSION_PERCENT = 0.5
# Bulk traffic must retain at least this fraction of the goodput it had before.
MIN_BULK_GOODPUT_RETENTION = 0.25


@dataclass
class MeasurementWindow:
    """Accumulates the samples a verification decision is based on."""

    added_delay_ms: list[float] = field(default_factory=list)
    jitter_ms: list[float] = field(default_factory=list)
    packet_loss_percent: list[float] = field(default_factory=list)
    queue_occupancy_percent: list[float] = field(default_factory=list)
    class_goodput_mbps: dict[TrafficClass, list[float]] = field(default_factory=dict)

    def add(
        self,
        *,
        added_delay_ms: float,
        jitter_ms: float,
        packet_loss_percent: float,
        queue_occupancy_percent: float,
        class_goodput_mbps: dict[TrafficClass, float],
    ) -> None:
        self.added_delay_ms.append(added_delay_ms)
        self.jitter_ms.append(jitter_ms)
        self.packet_loss_percent.append(packet_loss_percent)
        self.queue_occupancy_percent.append(queue_occupancy_percent)
        for traffic_class, value in class_goodput_mbps.items():
            self.class_goodput_mbps.setdefault(traffic_class, []).append(value)

    @property
    def sample_count(self) -> int:
        return len(self.added_delay_ms)

    @property
    def is_sufficient(self) -> bool:
        return self.sample_count >= MINIMUM_SAMPLES_PER_WINDOW

    def median(self, series: list[float]) -> float | None:
        return statistics.median(series) if series else None

    def median_goodput(self, traffic_class: TrafficClass) -> float | None:
        values = self.class_goodput_mbps.get(traffic_class)
        return statistics.median(values) if values else None


class Verifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def verify(
        self,
        policy_id: str,
        before: MeasurementWindow,
        after: MeasurementWindow,
        data_mode: DataMode,
        now: datetime | None = None,
    ) -> VerificationResult:
        now = now or datetime.now(UTC)

        before_delay = before.median(before.added_delay_ms)
        after_delay = after.median(after.added_delay_ms)
        before_jitter = before.median(before.jitter_ms)
        after_jitter = after.median(after.jitter_ms)
        before_loss = before.median(before.packet_loss_percent)
        after_loss = after.median(after.packet_loss_percent)
        before_queue = before.median(before.queue_occupancy_percent)
        after_queue = after.median(after.queue_occupancy_percent)
        before_bulk = before.median_goodput(TrafficClass.BULK)
        after_bulk = after.median_goodput(TrafficClass.BULK)

        criteria: list[SafetyCheck] = []

        if not before.is_sufficient or not after.is_sufficient:
            criteria.append(
                SafetyCheck(
                    name="sufficient_samples",
                    passed=False,
                    detail=(
                        f"Need at least {MINIMUM_SAMPLES_PER_WINDOW} samples in each window; "
                        f"got {before.sample_count} before and {after.sample_count} after."
                    ),
                )
            )
            return VerificationResult(
                policy_id=policy_id,
                outcome=VerificationOutcome.INCONCLUSIVE,
                data_mode=data_mode,
                completed_at=now,
                measurement_window_seconds=self._settings.verification_window_seconds,
                before_added_delay_ms=before_delay,
                after_added_delay_ms=after_delay,
                before_jitter_ms=before_jitter,
                after_jitter_ms=after_jitter,
                before_packet_loss_percent=before_loss,
                after_packet_loss_percent=after_loss,
                before_queue_occupancy_percent=before_queue,
                after_queue_occupancy_percent=after_queue,
                before_bulk_goodput_mbps=before_bulk,
                after_bulk_goodput_mbps=after_bulk,
                criteria=criteria,
                explanation=(
                    "Not enough measurements to judge the change. The policy is retained "
                    "unchanged and will be reviewed at the next evaluation."
                ),
            )

        criteria.append(
            SafetyCheck(
                name="sufficient_samples",
                passed=True,
                detail=(
                    f"{before.sample_count} samples before and {after.sample_count} after, "
                    f"using the same probe method and interval."
                ),
            )
        )

        assert before_delay is not None and after_delay is not None
        delay_ok = after_delay <= before_delay + MAX_ADDED_DELAY_REGRESSION_MS
        criteria.append(
            SafetyCheck(
                name="interactive_delay_not_worse",
                passed=delay_ok,
                detail=(
                    f"Median delay above baseline moved from {before_delay:.1f} ms to "
                    f"{after_delay:.1f} ms (allowed regression "
                    f"{MAX_ADDED_DELAY_REGRESSION_MS:.0f} ms)."
                ),
            )
        )

        assert before_loss is not None and after_loss is not None
        loss_ok = after_loss <= before_loss + MAX_LOSS_REGRESSION_PERCENT
        criteria.append(
            SafetyCheck(
                name="packet_loss_not_worse",
                passed=loss_ok,
                detail=(
                    f"Median packet loss moved from {before_loss:.2f} percent to "
                    f"{after_loss:.2f} percent."
                ),
            )
        )

        if before_bulk is None or after_bulk is None or before_bulk <= 0:
            criteria.append(
                SafetyCheck(
                    name="bulk_progress_retained",
                    passed=True,
                    detail="No bulk traffic measured in both windows, so this does not apply.",
                )
            )
            bulk_ok = True
        else:
            bulk_ok = after_bulk >= before_bulk * MIN_BULK_GOODPUT_RETENTION
            criteria.append(
                SafetyCheck(
                    name="bulk_progress_retained",
                    passed=bulk_ok,
                    detail=(
                        f"Median bulk goodput moved from {before_bulk:.2f} Mbps to "
                        f"{after_bulk:.2f} Mbps; at least "
                        f"{MIN_BULK_GOODPUT_RETENTION:.0%} must be retained."
                    ),
                )
            )

        outcome = (
            VerificationOutcome.ACCEPTABLE
            if delay_ok and loss_ok and bulk_ok
            else VerificationOutcome.DEGRADED
        )

        delay_change = after_delay - before_delay
        direction = "fell" if delay_change < 0 else "rose"
        explanation = (
            f"Delay above the idle baseline {direction} by {abs(delay_change):.1f} ms "
            f"and packet loss moved by {after_loss - before_loss:+.2f} percentage points. "
        )
        explanation += (
            "The outcome meets the acceptance rule, so the policy is kept."
            if outcome is VerificationOutcome.ACCEPTABLE
            else "The outcome fails the acceptance rule, so the previous configuration is restored."
        )

        return VerificationResult(
            policy_id=policy_id,
            outcome=outcome,
            data_mode=data_mode,
            completed_at=now,
            measurement_window_seconds=self._settings.verification_window_seconds,
            before_added_delay_ms=before_delay,
            after_added_delay_ms=after_delay,
            before_jitter_ms=before_jitter,
            after_jitter_ms=after_jitter,
            before_packet_loss_percent=before_loss,
            after_packet_loss_percent=after_loss,
            before_queue_occupancy_percent=before_queue,
            after_queue_occupancy_percent=after_queue,
            before_bulk_goodput_mbps=before_bulk,
            after_bulk_goodput_mbps=after_bulk,
            criteria=criteria,
            explanation=explanation,
        )


class Watchdog:
    """Independent heartbeat monitor.

    The watchdog does not depend on the controller it monitors deciding to tell
    it something is wrong. The controller stamps a heartbeat each tick; if the
    stamp stops advancing for longer than the timeout, the watchdog's only
    authority is to request the known-safe profile.
    """

    def __init__(self, timeout_seconds: float = 3.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._last_heartbeat: datetime | None = None
        self._alerts: int = 0
        self._recovery_requested = False

    def heartbeat(self, now: datetime | None = None) -> None:
        self._last_heartbeat = now or datetime.now(UTC)
        self._recovery_requested = False

    @property
    def last_heartbeat(self) -> datetime | None:
        return self._last_heartbeat

    @property
    def alert_count(self) -> int:
        return self._alerts

    def check(self, now: datetime | None = None) -> bool:
        """Return ``True`` when safe recovery should be activated."""
        now = now or datetime.now(UTC)
        if self._last_heartbeat is None:
            return False
        age = (now - self._last_heartbeat).total_seconds()
        if age <= self.timeout_seconds or self._recovery_requested:
            return False
        self._alerts += 1
        self._recovery_requested = True
        return True

    def age_seconds(self, now: datetime | None = None) -> float | None:
        if self._last_heartbeat is None:
            return None
        return ((now or datetime.now(UTC)) - self._last_heartbeat).total_seconds()
