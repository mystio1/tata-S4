"""QoSense FastAPI application.

Run locally with::

    uvicorn app.main:app --reload

This service owns experiment state and streams typed events. It performs no
privileged Linux networking of any kind and exposes no command interface; see
``docs/SAFETY_SECURITY.md`` for the laboratory agent boundary that privileged
operations live behind.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import v1, ws
from app.config import Settings, get_settings
from app.state import AppState

logger = logging.getLogger("qosense")

DESCRIPTION = """
QoSense is an adaptive, intent-aware home Quality of Service engine.

This build serves the `SIMULATION` data mode only. Every response carries a
`data_mode` field, policy application is reported as `SIMULATED` rather than
`APPLIED`, and both LightGBM models report their real readiness state rather
than a placeholder.
""".strip()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.qosense = AppState.build(settings)
        # One loop advances every running experiment. Starting it here means a
        # synchronous request handler never has to create it.
        app.state.qosense.manager.start_loop()
        logger.info(
            "QoSense %s starting in %s; live enforcement enabled: %s",
            settings.build_version,
            settings.env,
            settings.enable_live_enforcement,
        )
        try:
            yield
        finally:
            await app.state.qosense.manager.shutdown()
            logger.info("QoSense stopped and all experiments were ended safely.")

    app = FastAPI(
        title="QoSense API",
        version=settings.build_version,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.include_router(v1.router)
    app.include_router(ws.router)

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/", tags=["system"])
    def root() -> dict[str, str]:
        return {
            "name": "QoSense API",
            "version": settings.build_version,
            "data_mode": "SIMULATION",
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return app


app = create_app()
