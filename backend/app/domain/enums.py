"""Shared enumerations for QoSense.

Every enum here is part of the public API contract (see ``docs/API_CONTRACT.md``).
Values are stable strings so the frontend can switch on them directly.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String enum that serialises to its value."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class DataMode(StrEnum):
    """Provenance of every value the API returns.

    ``SIMULATION`` and ``REPLAY`` must never be presented as ``LIVE``.
    """

    SIMULATION = "SIMULATION"
    LIVE = "LIVE"
    REPLAY = "REPLAY"


class ExperimentMode(StrEnum):
    """The four comparison modes defined in ``docs/EXPERIMENT_PLAN.md``."""

    NO_QOS = "NO_QOS"
    TRADITIONAL_ADAPTIVE = "TRADITIONAL_ADAPTIVE"
    CLASSIFIER_ONLY = "CLASSIFIER_ONLY"
    FULL_QOSENSE = "FULL_QOSENSE"


class ExperimentStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class TrafficClass(StrEnum):
    """Broad behaviour classes. These are not application identities."""

    INTERACTIVE = "INTERACTIVE"
    GAMING_LIKE = "GAMING_LIKE"
    STREAMING_LIKE = "STREAMING_LIKE"
    BULK = "BULK"
    WEB_VOICE = "WEB_VOICE"
    UNKNOWN = "UNKNOWN"


class Direction(StrEnum):
    UPLOAD = "UPLOAD"
    DOWNLOAD = "DOWNLOAD"


class Protocol(StrEnum):
    TCP = "TCP"
    UDP = "UDP"


class ModelState(StrEnum):
    """Actual readiness of a machine-learning artifact.

    ``READY`` is only permitted once an artifact is loaded and can run inference.
    """

    NOT_CONFIGURED = "NOT_CONFIGURED"
    TRAINING = "TRAINING"
    READY = "READY"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


class ClassificationSource(StrEnum):
    """Where a traffic class came from.

    Only ``MODEL_1`` means a trained Light Gradient Boosting Machine (LightGBM)
    artifact produced the label.
    """

    MODEL_1 = "MODEL_1"
    DETERMINISTIC_RULES = "DETERMINISTIC_RULES"
    SAFE_FALLBACK = "SAFE_FALLBACK"


class CalibrationStatus(StrEnum):
    NOT_CALIBRATED = "NOT_CALIBRATED"
    CALIBRATED = "CALIBRATED"
    UNAVAILABLE = "UNAVAILABLE"


class ClassificationDecision(StrEnum):
    ACCEPTED = "ACCEPTED"
    SAFE_FALLBACK = "SAFE_FALLBACK"


class RiskBand(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    LOW = "LOW"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"


class PredictionDecision(StrEnum):
    USED = "USED"
    IGNORED = "IGNORED"


class CongestionState(StrEnum):
    """Currently measured congestion. Never derived from a future prediction."""

    UNKNOWN = "UNKNOWN"
    CLEAR = "CLEAR"
    BUILDING = "BUILDING"
    CONGESTED = "CONGESTED"
    BUFFERBLOAT = "BUFFERBLOAT"


class DataQuality(StrEnum):
    NO_DATA = "NO_DATA"
    STALE = "STALE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    GOOD = "GOOD"


class PolicyProfile(StrEnum):
    """Named candidate actions evaluated by the policy engine (M4)."""

    KEEP_CURRENT = "KEEP_CURRENT"
    PROTECT_INTERACTIVE = "PROTECT_INTERACTIVE"
    LIMIT_BULK = "LIMIT_BULK"
    REBALANCE_CLASSES = "REBALANCE_CLASSES"
    CONSERVATIVE_SAFE = "CONSERVATIVE_SAFE"


class PolicyStatus(StrEnum):
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    ACCEPTED = "ACCEPTED"


class EnforcementStatus(StrEnum):
    """Outcome of handing a policy to an enforcer.

    ``SIMULATED`` is used by the local simulator. ``APPLIED`` is reserved for a
    real Linux read-back from the lab agent and must never be used otherwise.
    """

    SIMULATED = "SIMULATED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED_ROLLED_BACK = "FAILED_ROLLED_BACK"
    FAILED_SAFE_MODE = "FAILED_SAFE_MODE"


class VerificationOutcome(StrEnum):
    PENDING = "PENDING"
    ACCEPTABLE = "ACCEPTABLE"
    DEGRADED = "DEGRADED"
    INCONCLUSIVE = "INCONCLUSIVE"


class IntentType(StrEnum):
    PRIORITIZE_INTERACTIVE = "PRIORITIZE_INTERACTIVE"
    PROTECT_DEVICE = "PROTECT_DEVICE"


class IntentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class DeviceKind(StrEnum):
    GATEWAY = "GATEWAY"
    INTERNET = "INTERNET"
    LAPTOP = "LAPTOP"
    GAMING_CONSOLE = "GAMING_CONSOLE"
    DESKTOP = "DESKTOP"
    SMART_TV = "SMART_TV"
    SMARTPHONE = "SMARTPHONE"


class EventType(StrEnum):
    """WebSocket event types (see ``docs/API_CONTRACT.md`` section 9)."""

    EXPERIMENT_STATUS = "experiment.status"
    FLOW_DETECTED = "flow.detected"
    FLOW_UPDATED = "flow.updated"
    FLOW_CLASSIFIED = "flow.classified"
    CAPACITY_UPDATED = "capacity.updated"
    CONGESTION_PREDICTED = "congestion.predicted"
    POLICY_EVALUATING = "policy.evaluating"
    POLICY_PROPOSED = "policy.proposed"
    POLICY_REJECTED = "policy.rejected"
    POLICY_APPLIED = "policy.applied"
    ENFORCEMENT_VERIFIED = "enforcement.verified"
    METRICS_SNAPSHOT = "metrics.snapshot"
    VERIFICATION_COMPLETED = "verification.completed"
    ROLLBACK_STARTED = "rollback.started"
    ROLLBACK_COMPLETED = "rollback.completed"
    WATCHDOG_ALERT = "watchdog.alert"
    INTENT_UPDATED = "intent.updated"
    PACKET_LOSS_OBSERVED = "packet.loss_observed"
    EXPERIMENT_COMPLETED = "experiment.completed"
    SYSTEM_ERROR = "system.error"


class EventSeverity(StrEnum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
