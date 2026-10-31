"""QoSense HTTP API, version 1.

Conventions, matching ``docs/API_CONTRACT.md``:

* base path ``/api/v1``;
* UTC timestamps throughout;
* every telemetry response carries ``data_mode``;
* enums rather than free-form strings;
* bounded, validated scenario parameters.

There is deliberately no endpoint that accepts a shell command, a queue
discipline string or an interface name. Privileged Linux operations live
behind the laboratory agent boundary described in ``docs/SAFETY_SECURITY.md``
and are not reachable from here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import Field

from app.config import Settings, get_settings
from app.control.intent import IntentRejected
from app.domain.enums import (
    DataMode,
    Direction,
    EventType,
    ExperimentMode,
    IntentType,
)
from app.domain.events import EventPage
from app.domain.experiment import (
    ExperimentCreateRequest,
    ExperimentReport,
    ExperimentState,
    ModeAvailability,
    ScenarioDefinition,
)
from app.domain.metrics import (
    CapacityEstimate,
    ClassificationResult,
    FlowState,
    MetricsSnapshot,
    StrictModel,
)
from app.domain.policy import IntentLease, PolicyHistoryEntry, PolicyProposal
from app.domain.topology import Topology
from app.models.registry import ModelStatus
from app.runtime.experiment import ExperimentManager, ExperimentRuntime
from app.simulation.scenarios import SCENARIOS
from app.state import AppState, get_state

router = APIRouter(prefix="/api/v1")

SettingsDep = Annotated[Settings, Depends(get_settings)]
StateDep = Annotated[AppState, Depends(get_state)]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class HealthResponse(StrictModel):
    status: str
    build_version: str
    environment: str
    server_time: datetime


class LabAgentStatus(StrictModel):
    configured: bool
    reachable: bool
    live_enforcement_enabled: bool
    detail: str


class SystemStatus(StrictModel):
    build_version: str
    data_mode: DataMode
    supported_data_modes: list[DataMode]
    controller_running: bool
    models: list[ModelStatus]
    lab_agent: LabAgentStatus
    modes: list[ModeAvailability]
    active_experiment_id: str | None
    experiment: ExperimentState | None
    watchdog_last_heartbeat: datetime | None
    watchdog_alerts: int
    subscriber_count: int
    server_time: datetime


class QoSControllerView(StrictModel):
    """Everything the QoS Controller page needs in one response."""

    experiment_id: str | None
    data_mode: DataMode
    download_capacity: CapacityEstimate | None
    upload_capacity: CapacityEstimate | None
    measured_congestion: str
    predicted_congestion: dict[str, Any]
    current_profile: str | None
    current_proposal: PolicyProposal | None
    installed_class_rates_mbps: dict[str, float]
    active_intents: list[IntentLease]
    history: list[PolicyHistoryEntry]


class IntentCreateRequest(StrictModel):
    target_type: str = Field(default="DEVICE", max_length=16)
    target_id: str = Field(max_length=64)
    intent_type: IntentType = IntentType.PRIORITIZE_INTERACTIVE
    duration_seconds: float = Field(gt=0, le=86_400)
    reason: str = Field(default="", max_length=200)
    experiment_id: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve(state: AppState, experiment_id: str | None) -> ExperimentRuntime:
    manager: ExperimentManager = state.manager
    if experiment_id is None:
        runtime = manager.latest()
        if runtime is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No experiment exists yet. Create one first.",
            )
        return runtime
    try:
        return manager.get(experiment_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown experiment '{experiment_id}'.",
        ) from None


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        build_version=settings.build_version,
        environment=settings.env,
        server_time=datetime.now(UTC),
    )


@router.get("/system/status", response_model=SystemStatus, tags=["system"])
def system_status(state: StateDep, settings: SettingsDep) -> SystemStatus:
    from app.runtime.experiment import _mode_availability

    runtime = state.manager.latest()
    lab_configured = bool(settings.lab_agent_url and settings.lab_agent_token)
    return SystemStatus(
        build_version=settings.build_version,
        data_mode=DataMode.SIMULATION,
        supported_data_modes=[DataMode.SIMULATION],
        controller_running=runtime is not None and runtime.status.value == "RUNNING",
        models=state.registry.all_statuses(),
        lab_agent=LabAgentStatus(
            configured=lab_configured,
            reachable=False,
            live_enforcement_enabled=settings.enable_live_enforcement,
            detail=(
                "No Ubuntu laboratory agent is connected. LIVE data mode and real Linux "
                "traffic control are implemented in Phase 3 and are unavailable in this "
                "build, so nothing here can be reported as applied to a real link."
            ),
        ),
        modes=[_mode_availability(mode, state.registry) for mode in ExperimentMode],
        active_experiment_id=runtime.experiment_id if runtime else None,
        experiment=runtime.state() if runtime else None,
        watchdog_last_heartbeat=runtime.watchdog.last_heartbeat if runtime else None,
        watchdog_alerts=runtime.watchdog.alert_count if runtime else 0,
        subscriber_count=state.bus.subscriber_count,
        server_time=datetime.now(UTC),
    )


@router.get("/scenarios", response_model=list[ScenarioDefinition], tags=["system"])
def list_scenarios() -> list[ScenarioDefinition]:
    return list(SCENARIOS.values())


@router.get("/models/status", response_model=list[ModelStatus], tags=["models"])
def model_status(state: StateDep) -> list[ModelStatus]:
    return state.registry.all_statuses()


@router.get("/topology", response_model=Topology, tags=["telemetry"])
def topology(state: StateDep, experiment_id: str | None = None) -> Topology:
    return _resolve(state, experiment_id).topology


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


@router.get("/metrics/current", response_model=MetricsSnapshot | None, tags=["telemetry"])
def current_metrics(state: StateDep, experiment_id: str | None = None) -> MetricsSnapshot | None:
    return _resolve(state, experiment_id).latest_snapshot


@router.get("/flows", response_model=list[FlowState], tags=["telemetry"])
def flows(state: StateDep, experiment_id: str | None = None) -> list[FlowState]:
    runtime = _resolve(state, experiment_id)
    return sorted(runtime.flow_states.values(), key=lambda f: f.flow_id)


@router.get(
    "/flows/classifications", response_model=dict[str, ClassificationResult], tags=["telemetry"]
)
def flow_classifications(
    state: StateDep, experiment_id: str | None = None
) -> dict[str, ClassificationResult]:
    return _resolve(state, experiment_id).classifications


@router.get("/events", response_model=EventPage, tags=["telemetry"])
def events(
    state: StateDep,
    experiment_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=2000),
    event_type: list[EventType] | None = Query(default=None),
) -> EventPage:
    selected = set(event_type) if event_type else None
    items = state.bus.recent(limit=limit, experiment_id=experiment_id, types=selected)
    return EventPage(events=items, total=state.bus.total_events)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@router.get("/policies/current", response_model=QoSControllerView, tags=["policy"])
def current_policy(state: StateDep, experiment_id: str | None = None) -> QoSControllerView:
    runtime = _resolve(state, experiment_id)
    prediction = runtime.congestion_predictor.predict_congestion(runtime.experiment_id)
    return QoSControllerView(
        experiment_id=runtime.experiment_id,
        data_mode=runtime.data_mode,
        download_capacity=runtime.capacity[Direction.DOWNLOAD].current(),
        upload_capacity=runtime.capacity[Direction.UPLOAD].current(),
        measured_congestion=(
            runtime.latest_snapshot.congestion_state.value
            if runtime.latest_snapshot
            else "UNKNOWN"
        ),
        predicted_congestion=prediction.model_dump(mode="json"),
        current_profile=runtime.current_profile.value if runtime.current_profile else None,
        current_proposal=runtime.current_proposal,
        installed_class_rates_mbps={
            a.traffic_class.value: a.ceiling_mbps
            for a in runtime.enforcer.installed_allocations
        },
        active_intents=runtime.intents.active(),
        history=runtime.policy_history[-40:],
    )


@router.get("/policies/history", response_model=list[PolicyHistoryEntry], tags=["policy"])
def policy_history(
    state: StateDep,
    experiment_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[PolicyHistoryEntry]:
    return _resolve(state, experiment_id).policy_history[-limit:]


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


@router.post(
    "/experiments",
    response_model=ExperimentState,
    status_code=status.HTTP_201_CREATED,
    tags=["experiments"],
)
def create_experiment(state: StateDep, request: ExperimentCreateRequest) -> ExperimentState:
    try:
        runtime = state.manager.create(request)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from None
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from None
    return runtime.state()


@router.get("/experiments", response_model=list[ExperimentState], tags=["experiments"])
def list_experiments(state: StateDep) -> list[ExperimentState]:
    return [r.state() for r in state.manager.all()]


@router.get("/experiments/{experiment_id}", response_model=ExperimentState, tags=["experiments"])
def get_experiment(state: StateDep, experiment_id: str) -> ExperimentState:
    return _resolve(state, experiment_id).state()


@router.post(
    "/experiments/{experiment_id}/start", response_model=ExperimentState, tags=["experiments"]
)
def start_experiment(state: StateDep, experiment_id: str) -> ExperimentState:
    runtime = _resolve(state, experiment_id)
    if runtime.status.value in ("COMPLETED", "STOPPED"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This run has finished. Reset it or create a new one.",
        )
    runtime.start()
    state.manager.start_loop()
    return runtime.state()


@router.post(
    "/experiments/{experiment_id}/pause", response_model=ExperimentState, tags=["experiments"]
)
def pause_experiment(state: StateDep, experiment_id: str) -> ExperimentState:
    runtime = _resolve(state, experiment_id)
    runtime.pause()
    return runtime.state()


@router.post(
    "/experiments/{experiment_id}/resume", response_model=ExperimentState, tags=["experiments"]
)
def resume_experiment(state: StateDep, experiment_id: str) -> ExperimentState:
    runtime = _resolve(state, experiment_id)
    runtime.resume()
    state.manager.start_loop()
    return runtime.state()


@router.post(
    "/experiments/{experiment_id}/stop", response_model=ExperimentState, tags=["experiments"]
)
def stop_experiment(state: StateDep, experiment_id: str) -> ExperimentState:
    runtime = _resolve(state, experiment_id)
    runtime.stop()
    return runtime.state()


@router.post(
    "/experiments/{experiment_id}/reset", response_model=ExperimentState, tags=["experiments"]
)
def reset_experiment(state: StateDep, experiment_id: str) -> ExperimentState:
    try:
        return state.manager.reset(experiment_id).state()
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown experiment '{experiment_id}'.",
        ) from None


@router.get(
    "/experiments/{experiment_id}/report", response_model=ExperimentReport, tags=["experiments"]
)
def experiment_report(state: StateDep, experiment_id: str) -> ExperimentReport:
    return _resolve(state, experiment_id).report()


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------


@router.post(
    "/intents",
    response_model=IntentLease,
    status_code=status.HTTP_201_CREATED,
    tags=["intent"],
)
def create_intent(
    state: StateDep, request: Annotated[IntentCreateRequest, Body()]
) -> IntentLease:
    runtime = _resolve(state, request.experiment_id)
    try:
        lease = runtime.intents.create(
            experiment_id=runtime.experiment_id,
            target_type=request.target_type,
            target_id=request.target_id,
            intent_type=request.intent_type,
            duration_seconds=request.duration_seconds,
            reason=request.reason,
            known_targets=runtime.known_targets(),
        )
    except IntentRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None
    state.bus.publish(
        event_type=EventType.INTENT_UPDATED,
        experiment_id=runtime.experiment_id,
        data_mode=runtime.data_mode,
        payload=lease.model_dump(mode="json"),
        summary=(
            f"Temporary priority lease created for {lease.target_id}, expiring in "
            f"{lease.remaining_seconds:.0f} s. Expiry is enforced by the server."
        ),
    )
    return lease


@router.get("/intents", response_model=list[IntentLease], tags=["intent"])
def list_intents(state: StateDep, experiment_id: str | None = None) -> list[IntentLease]:
    return _resolve(state, experiment_id).intents.all_leases()


@router.delete("/intents/{intent_id}", response_model=IntentLease, tags=["intent"])
def cancel_intent(
    state: StateDep, intent_id: str, experiment_id: str | None = None
) -> IntentLease:
    runtime = _resolve(state, experiment_id)
    try:
        lease = runtime.intents.cancel(intent_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown lease '{intent_id}'."
        ) from None
    state.bus.publish(
        event_type=EventType.INTENT_UPDATED,
        experiment_id=runtime.experiment_id,
        data_mode=runtime.data_mode,
        payload=lease.model_dump(mode="json"),
        summary=f"Priority lease {intent_id} cancelled. The ordinary policy is re-evaluated.",
    )
    return lease
