"""M5 enforcement through the privileged Ubuntu laboratory agent.

Every privileged operation happens on the other side of the agent boundary.
This class sends a validated policy profile and reports back exactly what the
agent said the kernel did.

``APPLIED`` is returned only when the agent's read-back of real queue
discipline state matched the request. There is no path here that can
construct that status on its own, which is the point: the status means a
kernel confirmed it, or it does not appear.
"""

from __future__ import annotations

import itertools
import logging
import time
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.domain.enums import DataMode, EnforcementStatus, TrafficClass
from app.domain.policy import (
    ClassAllocation,
    EnforcementResult,
    PolicyProposal,
    ReadBackState,
)
from app.lab.client import LabAgentClient, LabAgentError

logger = logging.getLogger("qosense.control.lab_enforcer")

_transaction_counter = itertools.count(1)

# The agent's own outcome vocabulary, mapped onto this project's statuses.
LAB_STATUS = {
    "APPLIED": EnforcementStatus.APPLIED,
    "REJECTED": EnforcementStatus.REJECTED,
    "FAILED_ROLLED_BACK": EnforcementStatus.FAILED_ROLLED_BACK,
    "FAILED_SAFE_MODE": EnforcementStatus.FAILED_SAFE_MODE,
}


def _allocations_payload(allocations: list[ClassAllocation]) -> list[dict[str, Any]]:
    return [
        {
            "traffic_class": allocation.traffic_class.value,
            "ceiling_mbps": round(allocation.ceiling_mbps, 4),
            "floor_mbps": round(allocation.guaranteed_floor_mbps, 4),
        }
        for allocation in allocations
    ]


def _observed_rates(payload: dict[str, Any]) -> dict[TrafficClass, float]:
    return {
        TrafficClass(name): float(value)
        for name, value in (payload.get("observed_class_rates_mbps") or {}).items()
        if name in TrafficClass.__members__
    }


class LabAgentEnforcer:
    """Applies policy to real Linux traffic control, via the agent."""

    data_mode = DataMode.LIVE

    def __init__(self, settings: Settings, client: LabAgentClient | None = None) -> None:
        self._settings = settings
        self._client = client or (
            LabAgentClient(settings.lab_agent_url, settings.lab_agent_token)
            if settings.lab_agent_url
            else None
        )
        self._installed: list[ClassAllocation] = []
        self._installed_upload: list[ClassAllocation] = []
        self._installed_policy_id: str | None = None
        self._last_snapshot_id: str | None = None
        # How the data path should recognise each classified flow. The
        # controller decided the class from behaviour; this only carries that
        # decision into a traffic-control filter.
        self._flow_selectors: list[dict[str, Any]] = []

    # -- state ---------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return bool(
            self._settings.lab_agent_url
            and self._settings.lab_agent_token
            and self._settings.enable_live_enforcement
            and self._client is not None
        )

    @property
    def installed_allocations(self) -> list[ClassAllocation]:
        return list(self._installed)

    @property
    def installed_policy_id(self) -> str | None:
        return self._installed_policy_id

    def set_flow_selectors(self, selectors: list[dict[str, Any]]) -> None:
        self._flow_selectors = selectors

    # -- refusal ---------------------------------------------------------

    def _refusal(self, policy_id: str, reason: str) -> EnforcementResult:
        now = datetime.now(UTC)
        return EnforcementResult(
            policy_id=policy_id,
            transaction_id=f"txn-{next(_transaction_counter):05d}",
            status=EnforcementStatus.FAILED_SAFE_MODE,
            data_mode=DataMode.LIVE,
            requested_at=now,
            completed_at=now,
            apply_duration_ms=0.0,
            snapshot_id=None,
            read_back=None,
            error=reason,
        )

    # -- operations ------------------------------------------------------

    def snapshot(self) -> str:
        """The agent snapshots inside its own apply transaction.

        A separate snapshot call would invite a caller to capture state at one
        moment and apply at another, which is exactly the race the transaction
        exists to prevent.
        """
        return self._last_snapshot_id or "snapshot-taken-by-agent"

    def apply_policy(self, proposal: PolicyProposal) -> EnforcementResult:
        if not self.is_configured or self._client is None:
            return self._refusal(
                proposal.policy_id,
                "No Ubuntu laboratory agent is configured, so nothing can be applied to a "
                "real link. Set the agent address and token and enable live enforcement.",
            )

        requested_at = datetime.now(UTC)
        started = time.perf_counter()

        download_total = proposal.based_on_capacity_mbps or sum(
            a.ceiling_mbps for a in proposal.class_allocations
        )
        upload_allocations = proposal.upload_class_allocations or proposal.class_allocations
        upload_total = sum(a.ceiling_mbps for a in upload_allocations)

        payload = {
            "policy_id": proposal.policy_id,
            "profile": proposal.profile.value,
            "download": {
                "total_mbps": round(max(0.1, download_total), 4),
                "classes": _allocations_payload(proposal.class_allocations),
            },
            "upload": {
                "total_mbps": round(max(0.1, upload_total), 4),
                "classes": _allocations_payload(upload_allocations),
            },
            "flows": self._flow_selectors,
        }

        try:
            response = self._client.apply_policy(payload)
        except LabAgentError as exc:
            return self._refusal(proposal.policy_id, str(exc))

        status = LAB_STATUS.get(
            str(response.get("status")), EnforcementStatus.FAILED_SAFE_MODE
        )
        read_back_payload = response.get("read_back") or {}
        self._last_snapshot_id = response.get("snapshot_id")

        if status is EnforcementStatus.APPLIED:
            self._installed = list(proposal.class_allocations)
            self._installed_upload = list(upload_allocations)
            self._installed_policy_id = proposal.policy_id
        else:
            self._installed = []
            self._installed_upload = []
            self._installed_policy_id = None

        mismatches = response.get("mismatches") or []
        return EnforcementResult(
            policy_id=proposal.policy_id,
            transaction_id=str(response.get("transaction_id", "unknown")),
            status=status,
            data_mode=DataMode.LIVE,
            requested_at=requested_at,
            completed_at=datetime.now(UTC),
            apply_duration_ms=float(
                response.get("apply_duration_ms", (time.perf_counter() - started) * 1000.0)
            ),
            snapshot_id=response.get("snapshot_id"),
            read_back=ReadBackState(
                data_mode=DataMode.LIVE,
                source=str(read_back_payload.get("source", "Linux traffic control")),
                matches_intent=bool(read_back_payload.get("matches_intent")),
                upload_path=list(read_back_payload.get("upload_path") or []),
                download_path=list(read_back_payload.get("download_path") or []),
                observed_class_rates_mbps=_observed_rates(read_back_payload),
                mismatch_detail="; ".join(mismatches) or response.get("error"),
            ),
            error=response.get("error"),
        )

    def restore_snapshot(self, snapshot_id: str) -> EnforcementResult:
        if not self.is_configured or self._client is None:
            return self._refusal("restore", "No laboratory agent is configured.")

        requested_at = datetime.now(UTC)
        try:
            response = self._client.restore_snapshot(snapshot_id)
        except LabAgentError as exc:
            return self._refusal("restore", str(exc))

        verified = bool(response.get("verified"))
        if verified:
            self._installed = []
            self._installed_upload = []
            self._installed_policy_id = None

        return EnforcementResult(
            policy_id=self._installed_policy_id or "restored",
            transaction_id=f"txn-{next(_transaction_counter):05d}",
            status=(
                EnforcementStatus.APPLIED if verified else EnforcementStatus.FAILED_SAFE_MODE
            ),
            data_mode=DataMode.LIVE,
            requested_at=requested_at,
            completed_at=datetime.now(UTC),
            apply_duration_ms=float(response.get("duration_ms", 0.0)),
            snapshot_id=snapshot_id,
            read_back=None,
            error=response.get("error"),
        )

    def read_state(self) -> ReadBackState:
        if not self.is_configured or self._client is None:
            return ReadBackState(
                data_mode=DataMode.LIVE,
                source="no laboratory agent configured",
                matches_intent=False,
                mismatch_detail="Live state cannot be read because no agent is configured.",
            )
        try:
            state = self._client.read_tc_state()
        except LabAgentError as exc:
            return ReadBackState(
                data_mode=DataMode.LIVE,
                source="laboratory agent unreachable",
                matches_intent=False,
                mismatch_detail=str(exc),
            )

        download = state.get("download", {})
        observed = {
            TrafficClass(name): float(value)
            for name, value in (download.get("class_ceilings_mbps") or {}).items()
            if name in TrafficClass.__members__
        }
        return ReadBackState(
            data_mode=DataMode.LIVE,
            source="Linux traffic control on the gateway namespace",
            matches_intent=True,
            observed_class_rates_mbps=observed,
        )
