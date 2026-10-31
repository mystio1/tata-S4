"""Temporary user intent leases.

A lease is a bounded, auditable request to favour one device or flow for a
while. Three properties matter and are enforced here rather than in the
browser:

* **Expiry is server-side.** The lease carries an absolute expiry time and the
  controller re-checks it on every evaluation. A frontend countdown is a
  display, not the authority.
* **A lease is subordinate to safety and fairness.** It is an input to the
  policy engine, not an override of it. The Safety Guard can still reject a
  policy that a lease asked for.
* **Conflicts resolve deterministically.** A second lease for the same target
  replaces the first; leases for different targets coexist and the most
  recently created one wins where they disagree.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.domain.enums import IntentStatus, IntentType, TrafficClass
from app.domain.policy import IntentLease

_intent_counter = itertools.count(1)

# Which traffic classes each intent type asks the policy engine to favour.
INTENT_CLASS_MAP: dict[IntentType, tuple[TrafficClass, ...]] = {
    IntentType.PRIORITIZE_INTERACTIVE: (
        TrafficClass.INTERACTIVE,
        TrafficClass.GAMING_LIKE,
        TrafficClass.WEB_VOICE,
    ),
    IntentType.PROTECT_DEVICE: (TrafficClass.INTERACTIVE, TrafficClass.WEB_VOICE),
}

VALID_TARGET_TYPES = ("DEVICE", "FLOW")


class IntentRejected(ValueError):
    """Raised when a lease request fails validation."""


class IntentManager:
    """Creates, expires and reports temporary priority leases."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._leases: dict[str, IntentLease] = {}
        self._audit: list[IntentLease] = []

    def create(
        self,
        *,
        experiment_id: str | None,
        target_type: str,
        target_id: str,
        intent_type: IntentType,
        duration_seconds: float,
        reason: str,
        known_targets: set[str],
        now: datetime | None = None,
    ) -> IntentLease:
        now = now or datetime.now(UTC)

        if target_type not in VALID_TARGET_TYPES:
            raise IntentRejected(
                f"Target type must be one of {', '.join(VALID_TARGET_TYPES)}."
            )
        if target_id not in known_targets:
            raise IntentRejected(
                f"Target '{target_id}' is not known to the laboratory topology."
            )
        if duration_seconds <= 0:
            raise IntentRejected("Duration must be greater than zero seconds.")
        maximum = self._settings.max_intent_duration_seconds
        if duration_seconds > maximum:
            raise IntentRejected(
                f"Duration {duration_seconds:.0f} s exceeds the configured maximum of "
                f"{maximum:.0f} s."
            )

        # A new lease for the same target replaces the previous one.
        for lease_id, existing in list(self._leases.items()):
            if existing.target_id == target_id and existing.status is IntentStatus.ACTIVE:
                self._close(lease_id, IntentStatus.CANCELLED, now)

        lease = IntentLease(
            intent_id=f"intent-{next(_intent_counter):04d}",
            experiment_id=experiment_id,
            target_type=target_type,
            target_id=target_id,
            intent_type=intent_type,
            reason=reason[:200],
            created_at=now,
            expires_at=now + timedelta(seconds=duration_seconds),
            status=IntentStatus.ACTIVE,
            remaining_seconds=duration_seconds,
        )
        self._leases[lease.intent_id] = lease
        self._audit.append(lease)
        return lease

    def cancel(self, intent_id: str, now: datetime | None = None) -> IntentLease:
        now = now or datetime.now(UTC)
        if intent_id not in self._leases:
            raise KeyError(intent_id)
        return self._close(intent_id, IntentStatus.CANCELLED, now)

    def _close(self, intent_id: str, status: IntentStatus, now: datetime) -> IntentLease:
        lease = self._leases[intent_id]
        updated = lease.model_copy(update={"status": status, "remaining_seconds": 0.0})
        self._leases[intent_id] = updated
        self._audit.append(updated)
        return updated

    def expire_due(self, now: datetime | None = None) -> list[IntentLease]:
        """Expire any lease past its deadline. Returns the ones that expired."""
        now = now or datetime.now(UTC)
        expired: list[IntentLease] = []
        for intent_id, lease in list(self._leases.items()):
            if lease.status is not IntentStatus.ACTIVE:
                continue
            if now >= lease.expires_at:
                expired.append(self._close(intent_id, IntentStatus.EXPIRED, now))
        return expired

    def active(self, now: datetime | None = None) -> list[IntentLease]:
        now = now or datetime.now(UTC)
        result: list[IntentLease] = []
        for intent_id, lease in self._leases.items():
            if lease.status is not IntentStatus.ACTIVE:
                continue
            remaining = (lease.expires_at - now).total_seconds()
            if remaining <= 0:
                continue
            updated = lease.model_copy(update={"remaining_seconds": round(remaining, 2)})
            self._leases[intent_id] = updated
            result.append(updated)
        # Most recently created lease first, so conflicts resolve predictably.
        return sorted(result, key=lambda i: i.created_at, reverse=True)

    def boosted_classes(self, now: datetime | None = None) -> tuple[TrafficClass, ...]:
        classes: list[TrafficClass] = []
        for lease in self.active(now):
            for traffic_class in INTENT_CLASS_MAP.get(lease.intent_type, ()):
                if traffic_class not in classes:
                    classes.append(traffic_class)
        return tuple(classes)

    def history(self) -> list[IntentLease]:
        return list(self._audit)

    def all_leases(self) -> list[IntentLease]:
        return list(self._leases.values())
