"""Measurement and flow observation schemas (M1, M3A, M3B outputs).

Unit conventions
----------------
Every field name carries its unit. ``null`` means "not measured" and must never
be replaced by an illustrative value.

Metric definitions used throughout QoSense (``docs/EXPERIMENT_PLAN.md`` s.4):

* ``rtt_ms`` - probe round-trip time. Not one-way application latency.
* ``added_delay_ms`` - loaded ``rtt_ms`` minus the idle baseline round-trip
  time measured with the same probe method.
* ``jitter_ms`` - packet delay variation, defined here as the exponentially
  weighted moving average of the absolute difference between consecutive probe
  round-trip times. This single definition is used everywhere.
* ``packet_loss_percent`` - dropped packets divided by offered packets over the
  sampling interval, as a percentage.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import (
    CalibrationStatus,
    ClassificationDecision,
    ClassificationSource,
    CongestionState,
    DataMode,
    DataQuality,
    Direction,
    PredictionDecision,
    Protocol,
    RiskBand,
    TrafficClass,
)

FEATURE_SCHEMA_VERSION = "flow-features-v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class FlowFeatures(StrictModel):
    """Versioned feature window statistics produced by the flow observer (M1).

    Field names, units and ordering belong to ``FEATURE_SCHEMA_VERSION``. Adding
    or reordering fields requires a new schema version.
    """

    mean_packet_bytes: float = Field(ge=0)
    std_packet_bytes: float = Field(ge=0)
    min_packet_bytes: float = Field(ge=0)
    max_packet_bytes: float = Field(ge=0)
    mean_interarrival_ms: float = Field(ge=0)
    std_interarrival_ms: float = Field(ge=0)
    packets_per_second: float = Field(ge=0)
    bytes_per_second: float = Field(ge=0)
    flow_duration_ms: float = Field(ge=0)
    up_bytes: int = Field(ge=0)
    down_bytes: int = Field(ge=0)
    up_packets: int = Field(ge=0)
    down_packets: int = Field(ge=0)
    burst_count: int = Field(ge=0)
    active_time_ratio: float = Field(ge=0, le=1)


class FlowFeatureWindow(StrictModel):
    """One observation window for one flow. The unit of classifier input."""

    experiment_id: str
    flow_id: str
    device_id: str
    observed_at: datetime
    window_ms: int = Field(gt=0)
    direction: Direction
    protocol: Protocol
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    features: FlowFeatures


class ClassificationResult(StrictModel):
    """Output of ``classify_flow``. Honest about where the label came from."""

    flow_id: str
    model_name: str = "traffic_classifier"
    model_version: str
    source: ClassificationSource
    predicted_class: TrafficClass
    probabilities: dict[TrafficClass, float] = Field(default_factory=dict)
    calibrated_confidence: float | None = None
    calibration_status: CalibrationStatus = CalibrationStatus.NOT_CALIBRATED
    decision: ClassificationDecision
    reason: str
    inference_ms: float | None = None
    feature_schema_version: str = FEATURE_SCHEMA_VERSION


class CongestionPrediction(StrictModel):
    """Output of ``predict_congestion`` (Model 2).

    A probability here describes a *future* event in ``(t, t + horizon]``. It is
    never the same thing as ``MetricsSnapshot.congestion_state``.
    """

    experiment_id: str
    model_name: str = "congestion_predictor"
    model_version: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    prediction_horizon_seconds: float | None = None
    congestion_probability: float | None = None
    risk_band: RiskBand = RiskBand.UNAVAILABLE
    decision: PredictionDecision = PredictionDecision.IGNORED
    reason: str
    inference_ms: float | None = None


class CapacityEstimate(StrictModel):
    """Output of the deterministic usable-capacity estimator (M3A).

    ``usable_capacity_mbps`` is derived only from observed delivery rate and
    delay evidence. The configured laboratory bottleneck is never read here.
    """

    direction: Direction
    usable_capacity_mbps: float | None
    delivery_rate_sample_mbps: float | None
    sample_count: int = Field(ge=0)
    sample_window_seconds: float = Field(ge=0)
    ewma_alpha: float = Field(ge=0, le=1)
    quality: DataQuality
    saturated_observation: bool
    reason: str
    updated_at: datetime | None = None
    fallback_capacity_mbps: float | None = None


class FlowState(StrictModel):
    """Current view of one flow for the Live Traffic table and 3D scene."""

    flow_id: str
    device_id: str
    device_label: str
    direction: Direction
    protocol: Protocol
    traffic_class: TrafficClass
    classification_source: ClassificationSource
    confidence: float | None
    started_at: datetime
    last_update: datetime
    bytes_per_second: float
    packets_per_second: float
    demand_bytes_per_second: float
    served_fraction: float = Field(ge=0, le=1)
    flow_rtt_ms: float | None
    queue_class: str
    data_mode: DataMode
    total_bytes: int = Field(ge=0)


class DirectionMetrics(StrictModel):
    """Per-direction link state for one sampling interval."""

    direction: Direction
    offered_mbps: float
    served_mbps: float
    link_rate_mbps: float | None
    utilization_percent: float | None
    queue_bytes: float
    queue_packets: int
    queue_occupancy_percent: float
    queue_delay_ms: float
    dropped_packets: int


class MetricsSnapshot(StrictModel):
    """One sampling interval of network state.

    ``null`` means not measured. See the module docstring for metric
    definitions.
    """

    experiment_id: str | None
    timestamp: datetime
    elapsed_seconds: float
    data_mode: DataMode
    download_mbps: float | None = None
    upload_mbps: float | None = None
    usable_capacity_mbps: float | None = None
    capacity_quality: DataQuality = DataQuality.NO_DATA
    rtt_ms: float | None = None
    idle_baseline_rtt_ms: float | None = None
    added_delay_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss_percent: float | None = None
    queue_packets: int | None = None
    queue_occupancy_percent: float | None = None
    active_flows: int = 0
    congestion_state: CongestionState = CongestionState.UNKNOWN
    congestion_prediction: RiskBand = RiskBand.UNAVAILABLE
    policy_id: str | None = None
    policy_profile: str | None = None
    per_direction: list[DirectionMetrics] = Field(default_factory=list)
