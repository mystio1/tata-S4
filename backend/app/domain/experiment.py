"""Experiment configuration, scenario definitions and run state."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from app.domain.enums import (
    DataMode,
    DeviceKind,
    Direction,
    ExperimentMode,
    ExperimentStatus,
    Protocol,
    TrafficClass,
)
from app.domain.metrics import StrictModel


class ParameterBound(StrictModel):
    """Validated bound for one scenario parameter.

    Scenario parameters are the only client-supplied numbers that reach the
    simulator or (later) the laboratory agent, so each one is bounded here.
    """

    name: str
    label: str
    unit: str
    minimum: float
    maximum: float
    default: float
    description: str


class FlowSpec(StrictModel):
    """One generated flow in a scenario.

    ``true_class`` is the generator's ground truth. It is recorded for labelling
    and evaluation only; it is never shown as a model prediction and never used
    as a model input feature.
    """

    flow_id: str
    device_id: str
    direction: Direction
    protocol: Protocol
    true_class: TrafficClass
    demand_model: str  # CONSTANT_BITRATE | GREEDY | BURSTY
    start_seconds: float = Field(ge=0)
    stop_seconds: float | None = None
    nominal_demand_mbps: float = Field(ge=0)
    mean_packet_bytes: float = Field(gt=0)
    packet_bytes_jitter: float = Field(ge=0)
    burst_period_seconds: float | None = None
    burst_duty_cycle: float | None = Field(default=None, ge=0, le=1)


class ImpairmentStep(StrictModel):
    """A scheduled change to the emulated bottleneck."""

    at_seconds: float = Field(ge=0)
    download_mbps: float | None = None
    upload_mbps: float | None = None
    base_rtt_ms: float | None = None
    description: str = ""


class ScenarioDefinition(StrictModel):
    """A predefined, named scenario. Clients may only reference these by id."""

    scenario_id: str
    name: str
    summary: str
    is_laboratory_emulation: bool = True
    default_duration_seconds: float
    default_download_mbps: float
    default_upload_mbps: float
    default_base_rtt_ms: float
    queue_limit_packets: int
    flows: list[FlowSpec]
    impairments: list[ImpairmentStep] = Field(default_factory=list)
    parameters: list[ParameterBound] = Field(default_factory=list)
    devices: list[tuple[str, str, DeviceKind]] = Field(default_factory=list)


class ExperimentCreateRequest(StrictModel):
    """Client request body for ``POST /api/v1/experiments``."""

    scenario_id: str
    mode: ExperimentMode
    data_mode: DataMode = DataMode.SIMULATION
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    duration_seconds: float | None = Field(default=None, gt=0)
    parameters: dict[str, float] = Field(default_factory=dict)
    repeat_label: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _reject_unsupported_data_modes(self) -> ExperimentCreateRequest:
        # LIVE is accepted by the schema but the manager still refuses it
        # unless a laboratory agent is actually configured, reachable and
        # enabled. Schema validation cannot see any of that, so the real
        # check lives where the agent's health is known.
        if self.data_mode is DataMode.REPLAY:
            raise ValueError("REPLAY data mode is not implemented yet.")
        return self


class ModeAvailability(StrictModel):
    """Whether a comparison mode can run with the components actually present."""

    mode: ExperimentMode
    available: bool
    degraded: bool
    requires: list[str] = Field(default_factory=list)
    detail: str


class ExperimentConfig(StrictModel):
    experiment_id: str
    scenario_id: str
    scenario_name: str
    mode: ExperimentMode
    data_mode: DataMode
    seed: int
    duration_seconds: float
    tick_seconds: float
    parameters: dict[str, float] = Field(default_factory=dict)
    repeat_label: str | None = None
    created_at: datetime
    weights_version: str
    feature_schema_version: str
    model_versions: dict[str, str] = Field(default_factory=dict)


class ExperimentState(StrictModel):
    config: ExperimentConfig
    status: ExperimentStatus
    mode_availability: ModeAvailability
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    elapsed_seconds: float = 0.0
    tick_count: int = 0
    event_count: int = 0
    last_sequence: int = 0
    notes: list[str] = Field(default_factory=list)


class MetricSummary(StrictModel):
    """Median and interquartile range for one metric across an experiment."""

    metric: str
    unit: str
    samples: int
    median: float | None
    p25: float | None
    p75: float | None
    minimum: float | None
    maximum: float | None


class ExperimentReport(StrictModel):
    experiment_id: str
    config: ExperimentConfig
    status: ExperimentStatus
    data_mode: DataMode
    generated_at: datetime
    duration_observed_seconds: float
    sample_count: int
    summaries: list[MetricSummary] = Field(default_factory=list)
    jain_fairness_index: float | None = None
    fairness_basis: str = ""
    policy_changes: int = 0
    safety_rejections: int = 0
    rollbacks: int = 0
    zero_progress_windows: int = 0
    bulk_transferred_megabytes: float | None = None
    limitations: list[str] = Field(default_factory=list)
