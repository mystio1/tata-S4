"""Policy, safety, enforcement and verification schemas (M4, Safety Guard, M5, M6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.domain.enums import (
    DataMode,
    EnforcementStatus,
    IntentStatus,
    IntentType,
    PolicyProfile,
    PolicyStatus,
    TrafficClass,
    VerificationOutcome,
)
from app.domain.metrics import StrictModel

POLICY_WEIGHTS_VERSION = "qosense-policy-weights-v1"


class ClassAllocation(StrictModel):
    """Per-class shaping configuration derived from the capacity estimate.

    Rates are absolute. QoSense does not use fixed universal percentages; every
    value here is computed from measured demand and the estimated usable rate.
    """

    traffic_class: TrafficClass
    ceiling_mbps: float = Field(ge=0)
    guaranteed_floor_mbps: float = Field(ge=0)
    share_of_capacity_percent: float | None = None


class CandidateCost(StrictModel):
    """One scored candidate action.

    Every component is computed from a measurable quantity. When a component
    cannot be computed it is ``None`` and excluded from ``total_cost``; it is
    never replaced by a plausible-looking number.
    """

    profile: PolicyProfile
    interactive_delay_risk: float | None
    bulk_progress_risk: float | None
    fairness_penalty: float | None
    churn_penalty: float | None
    total_cost: float | None
    hard_constraint_violations: list[str] = Field(default_factory=list)
    eligible: bool
    notes: list[str] = Field(default_factory=list)


class SafetyCheck(StrictModel):
    name: str
    passed: bool
    detail: str


class SafetyCheckResult(StrictModel):
    accepted: bool
    checks: list[SafetyCheck]
    rejection_reasons: list[str] = Field(default_factory=list)
    evaluated_at: datetime


class PolicyProposal(StrictModel):
    """A candidate policy produced by M4, before the Safety Guard runs."""

    policy_id: str
    experiment_id: str
    profile: PolicyProfile
    created_at: datetime
    data_mode: DataMode
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str
    source_event_ids: list[str] = Field(default_factory=list)
    # Download is the primary shaped path (via Intermediate Functional Block
    # redirection on a real link). Upload is shaped separately on egress, so
    # the two allocation sets are kept apart rather than assumed symmetric.
    class_allocations: list[ClassAllocation] = Field(default_factory=list)
    upload_class_allocations: list[ClassAllocation] = Field(default_factory=list)
    class_rates_mbps: dict[TrafficClass, float] = Field(default_factory=dict)
    candidates: list[CandidateCost] = Field(default_factory=list)
    weights_version: str = POLICY_WEIGHTS_VERSION
    based_on_capacity_mbps: float | None = None
    valid_until: datetime | None = None
    status: PolicyStatus = PolicyStatus.PROPOSED
    safety: SafetyCheckResult | None = None


class ReadBackState(StrictModel):
    """What the enforcer reports the data plane actually looks like.

    In ``SIMULATION`` this is the simulator's own shaping state, clearly
    labelled. In ``LIVE`` it must come from reading real queue discipline,
    class and filter state back from the Linux lab agent.
    """

    data_mode: DataMode
    source: str
    matches_intent: bool
    upload_path: list[str] = Field(default_factory=list)
    download_path: list[str] = Field(default_factory=list)
    observed_class_rates_mbps: dict[TrafficClass, float] = Field(default_factory=dict)
    mismatch_detail: str | None = None


class EnforcementResult(StrictModel):
    policy_id: str
    transaction_id: str
    status: EnforcementStatus
    data_mode: DataMode
    requested_at: datetime
    completed_at: datetime
    apply_duration_ms: float
    snapshot_id: str | None = None
    read_back: ReadBackState | None = None
    error: str | None = None


class VerificationResult(StrictModel):
    """M6 before/after comparison using consistent probes and intervals."""

    policy_id: str
    outcome: VerificationOutcome
    data_mode: DataMode
    completed_at: datetime
    measurement_window_seconds: float
    before_added_delay_ms: float | None
    after_added_delay_ms: float | None
    before_jitter_ms: float | None
    after_jitter_ms: float | None
    before_packet_loss_percent: float | None
    after_packet_loss_percent: float | None
    before_queue_occupancy_percent: float | None
    after_queue_occupancy_percent: float | None
    before_bulk_goodput_mbps: float | None
    after_bulk_goodput_mbps: float | None
    criteria: list[SafetyCheck] = Field(default_factory=list)
    explanation: str


class IntentLease(StrictModel):
    """Bounded temporary priority request. Expiry is enforced server-side."""

    intent_id: str
    experiment_id: str | None
    target_type: str
    target_id: str
    intent_type: IntentType
    reason: str
    created_at: datetime
    expires_at: datetime
    status: IntentStatus
    remaining_seconds: float
    rejection_reason: str | None = None


class PolicyHistoryEntry(StrictModel):
    proposal: PolicyProposal
    enforcement: EnforcementResult | None = None
    verification: VerificationResult | None = None
