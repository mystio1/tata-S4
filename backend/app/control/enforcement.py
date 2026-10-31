"""M5 - enforcement.

Two enforcers exist behind one interface.

``SimulatedEnforcer``
    Installs shaping on the local simulator. Its result status is always
    ``SIMULATED``, never ``APPLIED``. The read-back it returns is the
    simulator's own installed state, labelled as such. Nothing here touches the
    host's networking.

``LabAgentEnforcer``
    The boundary to a privileged Ubuntu laboratory agent, which is the only
    component permitted to run privileged Linux network operations. It is not
    reachable in this build, so it refuses every request with
    ``FAILED_SAFE_MODE`` rather than pretending.

The transaction shape is the same for both, because the simulated path exists
to exercise the real one:

1. snapshot the current state;
2. apply a named, validated profile - never a command string;
3. read the state back;
4. compare the read-back against what was intended;
5. roll back and re-verify when they differ.

There is deliberately no method anywhere in this module that accepts shell
text, a queue discipline string or an interface name from a caller.
"""

from __future__ import annotations

import itertools
import time
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from app.config import Settings
from app.domain.enums import DataMode, EnforcementStatus, TrafficClass
from app.domain.policy import (
    ClassAllocation,
    EnforcementResult,
    PolicyProposal,
    ReadBackState,
)
from app.simulation.engine import SimulationEngine

_transaction_counter = itertools.count(1)
_snapshot_counter = itertools.count(1)

# How far a read-back rate may differ from the intended rate before the
# transaction is treated as mismatched.
READ_BACK_TOLERANCE_MBPS = 0.01


def _allocations_to_shaping(
    allocations: list[ClassAllocation],
) -> dict[TrafficClass, tuple[float, float]]:
    return {a.traffic_class: (a.ceiling_mbps, a.guaranteed_floor_mbps) for a in allocations}


class Enforcer(ABC):
    """Common interface for anything that can install a policy."""

    data_mode: DataMode

    @abstractmethod
    def snapshot(self) -> str:
        """Capture the current state and return a restorable snapshot id."""

    @abstractmethod
    def apply_policy(self, proposal: PolicyProposal) -> EnforcementResult:
        """Apply a named profile transactionally and read the result back."""

    @abstractmethod
    def restore_snapshot(self, snapshot_id: str) -> EnforcementResult:
        """Restore a previously captured snapshot and verify the restore."""

    @abstractmethod
    def read_state(self) -> ReadBackState:
        """Read the currently installed state."""


class SimulatedEnforcer(Enforcer):
    """Installs shaping on the deterministic simulator."""

    data_mode = DataMode.SIMULATION

    def __init__(self, settings: Settings, engine: SimulationEngine) -> None:
        self._settings = settings
        self._engine = engine
        self._installed: list[ClassAllocation] = []
        self._installed_upload: list[ClassAllocation] = []
        self._installed_policy_id: str | None = None
        self._snapshots: dict[
            str, tuple[list[ClassAllocation], list[ClassAllocation], str | None]
        ] = {}

    # -- state ---------------------------------------------------------

    @property
    def installed_allocations(self) -> list[ClassAllocation]:
        return list(self._installed)

    @property
    def installed_policy_id(self) -> str | None:
        return self._installed_policy_id

    def snapshot(self) -> str:
        snapshot_id = f"snap-{next(_snapshot_counter):04d}"
        self._snapshots[snapshot_id] = (
            list(self._installed),
            list(self._installed_upload),
            self._installed_policy_id,
        )
        return snapshot_id

    def read_state(self) -> ReadBackState:
        return ReadBackState(
            data_mode=DataMode.SIMULATION,
            source="deterministic simulator shaping state",
            matches_intent=True,
            upload_path=self._describe_upload_path(),
            download_path=self._describe_download_path(),
            observed_class_rates_mbps={
                a.traffic_class: a.ceiling_mbps for a in self._installed
            },
        )

    def _describe_upload_path(self) -> list[str]:
        if not self._installed_upload:
            return ["device", "gateway egress (unshaped single queue)", "wide area network"]
        return [
            "device",
            "connection mark",
            "Hierarchical Token Bucket class on the wide area network egress",
            "Fair Queuing Controlled Delay queue",
            "wide area network",
        ]

    def _describe_download_path(self) -> list[str]:
        if not self._installed:
            return ["wide area network", "gateway (unshaped single queue)", "device"]
        return [
            "wide area network",
            "ingress filter",
            "redirect to Intermediate Functional Block",
            "Hierarchical Token Bucket class on the Intermediate Functional Block egress",
            "Fair Queuing Controlled Delay queue",
            "device",
        ]

    # -- transaction ---------------------------------------------------

    def apply_policy(self, proposal: PolicyProposal) -> EnforcementResult:
        requested_at = datetime.now(UTC)
        started = time.perf_counter()
        transaction_id = f"txn-{next(_transaction_counter):05d}"
        snapshot_id = self.snapshot()

        download = proposal.class_allocations
        upload = proposal.upload_class_allocations

        if not download:
            # An empty allocation means "stop shaping". That is a valid named
            # outcome, not a failure.
            self._engine.set_shaping(None, None)
            self._installed = []
            self._installed_upload = []
        else:
            self._engine.set_shaping(
                _allocations_to_shaping(download),
                _allocations_to_shaping(upload) if upload else _allocations_to_shaping(download),
            )
            self._installed = list(download)
            self._installed_upload = list(upload)

        self._installed_policy_id = proposal.policy_id

        read_back = self._verify_read_back(download)
        status = (
            EnforcementStatus.SIMULATED
            if read_back.matches_intent
            else EnforcementStatus.FAILED_ROLLED_BACK
        )
        if not read_back.matches_intent:
            self._restore(snapshot_id)

        return EnforcementResult(
            policy_id=proposal.policy_id,
            transaction_id=transaction_id,
            status=status,
            data_mode=DataMode.SIMULATION,
            requested_at=requested_at,
            completed_at=datetime.now(UTC),
            apply_duration_ms=round((time.perf_counter() - started) * 1000.0, 4),
            snapshot_id=snapshot_id,
            read_back=read_back,
            error=read_back.mismatch_detail,
        )

    def _verify_read_back(self, intended: list[ClassAllocation]) -> ReadBackState:
        state = self.read_state()
        expected = {a.traffic_class: a.ceiling_mbps for a in intended}
        observed = state.observed_class_rates_mbps
        mismatches = [
            f"{traffic_class.value}: intended {rate:.3f} Mbps, read back "
            f"{observed.get(traffic_class, 0.0):.3f} Mbps"
            for traffic_class, rate in expected.items()
            if abs(observed.get(traffic_class, 0.0) - rate) > READ_BACK_TOLERANCE_MBPS
        ]
        state.matches_intent = not mismatches
        state.mismatch_detail = "; ".join(mismatches) if mismatches else None
        return state

    def _restore(self, snapshot_id: str) -> None:
        download, upload, policy_id = self._snapshots[snapshot_id]
        if download:
            self._engine.set_shaping(
                _allocations_to_shaping(download),
                _allocations_to_shaping(upload) if upload else _allocations_to_shaping(download),
            )
        else:
            self._engine.set_shaping(None, None)
        self._installed = list(download)
        self._installed_upload = list(upload)
        self._installed_policy_id = policy_id

    def restore_snapshot(self, snapshot_id: str) -> EnforcementResult:
        requested_at = datetime.now(UTC)
        started = time.perf_counter()
        transaction_id = f"txn-{next(_transaction_counter):05d}"

        if snapshot_id not in self._snapshots:
            return EnforcementResult(
                policy_id=self._installed_policy_id or "unknown",
                transaction_id=transaction_id,
                status=EnforcementStatus.FAILED_SAFE_MODE,
                data_mode=DataMode.SIMULATION,
                requested_at=requested_at,
                completed_at=datetime.now(UTC),
                apply_duration_ms=round((time.perf_counter() - started) * 1000.0, 4),
                snapshot_id=snapshot_id,
                read_back=None,
                error=f"Snapshot '{snapshot_id}' is not available to restore.",
            )

        self._restore(snapshot_id)
        read_back = self._verify_read_back(self._installed)
        return EnforcementResult(
            policy_id=self._installed_policy_id or "restored",
            transaction_id=transaction_id,
            status=(
                EnforcementStatus.SIMULATED
                if read_back.matches_intent
                else EnforcementStatus.FAILED_SAFE_MODE
            ),
            data_mode=DataMode.SIMULATION,
            requested_at=requested_at,
            completed_at=datetime.now(UTC),
            apply_duration_ms=round((time.perf_counter() - started) * 1000.0, 4),
            snapshot_id=snapshot_id,
            read_back=read_back,
            error=read_back.mismatch_detail,
        )


class LabAgentEnforcer(Enforcer):
    """Boundary to the privileged Ubuntu laboratory agent.

    Not reachable in this build. Every call refuses rather than reporting a
    success that was never verified against real Linux state.
    """

    data_mode = DataMode.LIVE

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def is_configured(self) -> bool:
        return bool(
            self._settings.lab_agent_url
            and self._settings.lab_agent_token
            and self._settings.enable_live_enforcement
        )

    def _refusal(self, policy_id: str) -> EnforcementResult:
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
            error=(
                "No Ubuntu laboratory agent is connected. Live Linux traffic control is "
                "implemented in Phase 3 and must never be reported as applied without a "
                "verified read-back of real queue discipline state."
            ),
        )

    def snapshot(self) -> str:
        raise RuntimeError("No laboratory agent is connected; nothing can be snapshotted.")

    def apply_policy(self, proposal: PolicyProposal) -> EnforcementResult:
        return self._refusal(proposal.policy_id)

    def restore_snapshot(self, snapshot_id: str) -> EnforcementResult:
        return self._refusal("restore")

    def read_state(self) -> ReadBackState:
        return ReadBackState(
            data_mode=DataMode.LIVE,
            source="no laboratory agent connected",
            matches_intent=False,
            mismatch_detail="Live state cannot be read because no agent is connected.",
        )
