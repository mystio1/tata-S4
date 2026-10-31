"""The privileged laboratory agent's network interface.

This is the only way into the privileged side, and it exposes exactly the
operations listed in ``docs/SAFETY_SECURITY.md`` section 2 and nothing else:

* ``health_check``      → ``GET  /health``
* ``list_scenarios``    → ``GET  /scenarios``
* ``reset_lab``         → ``POST /reset``
* ``start_scenario``    → ``POST /scenario/start``
* ``stop_scenario``     → ``POST /scenario/stop``
* ``get_metrics``       → ``GET  /metrics``
* ``apply_policy``      → ``POST /policy/apply``
* ``restore_snapshot``  → ``POST /policy/restore``
* ``read_tc_state``     → ``GET  /tc-state``

There is no endpoint that takes a command, a queue discipline string, an
interface name or a namespace name. Every body is a typed model with bounded
fields, and every identifier is checked by :mod:`.safety` before it reaches a
privileged call.

Binding and authentication
--------------------------

The agent binds to the loopback address by default, so it is reachable from
the machine hosting it and from nothing else. Every operation except
``/health`` requires the bearer token from ``QOSENSE_LAB_AGENT_TOKEN``. The
agent refuses to start without a token unless it is explicitly told the
deployment is a local development one.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from . import enforcement as enf
from . import topology
from .commands import COMMAND_LOG, resolve_executables
from .runner import RUNNER
from .safety import (
    SUPPORTED_CLASSES,
    ValidationError,
)
from .scenarios import SCENARIOS

logger = logging.getLogger("qosense.lab.service")

AGENT_VERSION = "0.1.0-phase3"

TOKEN = os.environ.get("QOSENSE_LAB_AGENT_TOKEN", "")
ALLOW_NO_TOKEN = os.environ.get("QOSENSE_LAB_AGENT_ALLOW_NO_TOKEN", "") == "yes"


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    """Authenticate a privileged request."""
    if not TOKEN:
        if ALLOW_NO_TOKEN:
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "The laboratory agent has no token configured. Set "
                "QOSENSE_LAB_AGENT_TOKEN before using privileged operations."
            ),
        )
    expected = f"Bearer {TOKEN}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid laboratory agent token is required.",
        )


Authenticated = Annotated[None, Depends(require_token)]


# ---------------------------------------------------------------------------
# Request bodies. Every field is bounded; none of them names an interface.
# ---------------------------------------------------------------------------


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResetRequest(StrictBody):
    download_mbps: float | None = Field(default=None, gt=0, le=1000)
    upload_mbps: float | None = Field(default=None, gt=0, le=1000)
    base_rtt_ms: float | None = Field(default=None, ge=0, le=2000)
    queue_limit_packets: int | None = Field(default=None, ge=4, le=20_000)


class StartScenarioRequest(StrictBody):
    scenario_id: str = Field(max_length=64)
    experiment_id: str = Field(max_length=64)
    parameters: dict[str, float] = Field(default_factory=dict)


class StopScenarioRequest(StrictBody):
    experiment_id: str | None = Field(default=None, max_length=64)


class ClassRateBody(StrictBody):
    traffic_class: str = Field(max_length=32)
    ceiling_mbps: float = Field(gt=0, le=1000)
    floor_mbps: float = Field(ge=0, le=1000)


class DirectionBody(StrictBody):
    total_mbps: float = Field(gt=0, le=1000)
    classes: list[ClassRateBody] = Field(min_length=1, max_length=8)


class FlowSelectorBody(StrictBody):
    port: int = Field(ge=1, le=65_535)
    protocol: str = Field(max_length=8)
    traffic_class: str = Field(max_length=32)


class ApplyPolicyRequest(StrictBody):
    policy_id: str = Field(max_length=64)
    profile: str = Field(max_length=32)
    download: DirectionBody
    upload: DirectionBody
    flows: list[FlowSelectorBody] = Field(default_factory=list, max_length=16)


class RestoreRequest(StrictBody):
    snapshot_id: str = Field(max_length=64)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(
        title="QoSense laboratory agent",
        version=AGENT_VERSION,
        description=(
            "Privileged Linux network operations for the QoSense testbed. "
            "Only predefined, validated scenarios and policy profiles can be "
            "executed. There is no command interface."
        ),
    )

    @app.exception_handler(ValidationError)
    async def _validation_handler(_, exc: ValidationError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=422, content={"detail": str(exc)})

    # -- health ------------------------------------------------------------

    @app.get("/health", tags=["agent"])
    def health() -> dict[str, Any]:
        """Unauthenticated, so the controller can see the agent exists."""
        executables = resolve_executables()
        lab = topology.status()
        return {
            "status": "ok",
            "agent_version": AGENT_VERSION,
            "data_mode": "LIVE",
            "authentication_required": bool(TOKEN) or not ALLOW_NO_TOKEN,
            "laboratory_built": lab["built"],
            "tooling": {name: name in executables for name in ("ip", "tc", "ping", "iperf3")},
            "supported_classes": list(SUPPORTED_CLASSES),
            "server_time": datetime.now(UTC).isoformat(),
        }

    # -- scenarios ---------------------------------------------------------

    @app.get("/scenarios", tags=["scenarios"])
    def list_scenarios(_: Authenticated) -> list[dict[str, Any]]:
        return [scenario.as_dict() for scenario in SCENARIOS.values()]

    @app.post("/reset", tags=["scenarios"])
    def reset_lab(_: Authenticated, body: ResetRequest | None = None) -> dict[str, Any]:
        request = body or ResetRequest()
        link: topology.LinkProfile | None = None
        if any(
            value is not None
            for value in (
                request.download_mbps,
                request.upload_mbps,
                request.base_rtt_ms,
                request.queue_limit_packets,
            )
        ):
            base = topology.DEFAULT_LINK
            link = topology.LinkProfile(
                download_mbps=request.download_mbps or base.download_mbps,
                upload_mbps=request.upload_mbps or base.upload_mbps,
                base_rtt_ms=(
                    request.base_rtt_ms if request.base_rtt_ms is not None else base.base_rtt_ms
                ),
                queue_limit_packets=request.queue_limit_packets or base.queue_limit_packets,
            )
        return RUNNER.reset(link)

    @app.post("/scenario/start", tags=["scenarios"])
    def start_scenario(_: Authenticated, body: StartScenarioRequest) -> dict[str, Any]:
        return RUNNER.start(body.scenario_id, body.experiment_id, body.parameters)

    @app.post("/scenario/stop", tags=["scenarios"])
    def stop_scenario(_: Authenticated, body: StopScenarioRequest | None = None) -> dict[str, Any]:
        return RUNNER.stop((body or StopScenarioRequest()).experiment_id)

    @app.get("/scenario/state", tags=["scenarios"])
    def scenario_state(_: Authenticated) -> dict[str, Any]:
        return RUNNER.describe()

    # -- measurement -------------------------------------------------------

    @app.get("/metrics", tags=["measurement"])
    def get_metrics(_: Authenticated, window_seconds: float = 2.0) -> dict[str, Any]:
        """Return immediately with the most recent measurement window."""
        return RUNNER.metrics(window_seconds=max(0.5, min(30.0, window_seconds)))

    # -- policy ------------------------------------------------------------

    @app.post("/policy/apply", tags=["policy"])
    def apply_policy(_: Authenticated, body: ApplyPolicyRequest) -> dict[str, Any]:
        request = enf.PolicyRequest(
            policy_id=body.policy_id,
            profile=body.profile,
            download=enf.DirectionProfile(
                total_mbps=body.download.total_mbps,
                classes=tuple(
                    enf.ClassRate(c.traffic_class, c.ceiling_mbps, c.floor_mbps)
                    for c in body.download.classes
                ),
            ),
            upload=enf.DirectionProfile(
                total_mbps=body.upload.total_mbps,
                classes=tuple(
                    enf.ClassRate(c.traffic_class, c.ceiling_mbps, c.floor_mbps)
                    for c in body.upload.classes
                ),
            ),
            flows=tuple(
                enf.FlowSelector(f.port, f.protocol, f.traffic_class) for f in body.flows
            ),
        )
        return enf.apply_policy(request)

    @app.post("/policy/restore", tags=["policy"])
    def restore_snapshot(_: Authenticated, body: RestoreRequest) -> dict[str, Any]:
        return enf.restore_snapshot(body.snapshot_id)

    @app.post("/policy/clear", tags=["policy"])
    def clear_policy(_: Authenticated) -> dict[str, Any]:
        enf.clear_shaping()
        return {"cleared": True, "state": enf.read_all_state()}

    @app.get("/tc-state", tags=["policy"])
    def read_tc_state(_: Authenticated) -> dict[str, Any]:
        """The kernel's own view of the shaped devices."""
        return enf.read_all_state()

    # -- audit -------------------------------------------------------------

    @app.get("/audit", tags=["agent"])
    def audit(_: Authenticated, limit: int = 50) -> dict[str, Any]:
        """Every privileged command this agent has run, with exit status."""
        return {"commands": COMMAND_LOG.recent(max(1, min(400, limit)))}

    return app


app = create_app()
