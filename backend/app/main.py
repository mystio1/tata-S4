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

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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


_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


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

    # Serve the Vite build when it exists (production / Render).
    # In local dev the frontend runs separately on port 5173.
    if _FRONTEND_DIST.is_dir():
        assets = _FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/", include_in_schema=False)
        @app.get("/{path:path}", include_in_schema=False)
        def serve_spa(path: str = "") -> FileResponse:
            # Let API and WS routes take priority; catch everything else here.
            candidate = _FRONTEND_DIST / path
            if candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(_FRONTEND_DIST / "index.html"))

    else:
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
