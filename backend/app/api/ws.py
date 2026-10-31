"""WebSocket event stream.

A client connects to ``/api/v1/ws/experiments/{experiment_id}`` (or
``/api/v1/ws/events`` for everything) and receives the typed envelopes defined
in ``app.domain.events``.

On connect the server sends a ``experiment.status`` envelope carrying the
current state, so a client that reconnects does not have to infer what it
missed. Each envelope carries a monotonic ``sequence``; a gap means events were
dropped for a slow client, and the client should fetch a fresh snapshot from
``GET /api/v1/system/status`` rather than rendering an inconsistent picture.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.domain.enums import DataMode, EventType
from app.domain.events import EventEnvelope
from app.state import AppState

router = APIRouter(prefix="/api/v1/ws")

# How long to wait for an event before sending a keepalive frame.
KEEPALIVE_SECONDS = 20.0


async def _pump(websocket: WebSocket, state: AppState, experiment_id: str | None) -> None:
    subscriber = state.bus.subscribe(experiment_id)
    try:
        await _send_hello(websocket, state, experiment_id)
        stream = subscriber.stream()
        while True:
            try:
                event = await asyncio.wait_for(stream.__anext__(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                await websocket.send_json(
                    {"type": "keepalive", "dropped": subscriber.dropped}
                )
                continue
            except StopAsyncIteration:  # pragma: no cover - subscriber closed
                break
            await websocket.send_text(event.model_dump_json())
    finally:
        state.bus.unsubscribe(subscriber)


async def _send_hello(
    websocket: WebSocket, state: AppState, experiment_id: str | None
) -> None:
    runtime = None
    if experiment_id is not None:
        with contextlib.suppress(KeyError):
            runtime = state.manager.get(experiment_id)
    else:
        runtime = state.manager.latest()

    payload: dict = {"connected": True}
    if runtime is not None:
        payload["state"] = runtime.state().model_dump(mode="json")
        if runtime.latest_snapshot is not None:
            payload["latest_snapshot"] = runtime.latest_snapshot.model_dump(mode="json")

    hello = EventEnvelope(
        event_id="evt-hello",
        experiment_id=experiment_id,
        timestamp=datetime.now(UTC),
        data_mode=runtime.data_mode if runtime else DataMode.SIMULATION,
        type=EventType.EXPERIMENT_STATUS,
        sequence=state.bus.peek_sequence(experiment_id),
        summary="Connected to the QoSense event stream.",
        payload=payload,
    )
    await websocket.send_text(hello.model_dump_json())


@router.websocket("/experiments/{experiment_id}")
async def experiment_stream(websocket: WebSocket, experiment_id: str) -> None:
    await websocket.accept()
    state: AppState = websocket.app.state.qosense
    try:
        await _pump(websocket, state, experiment_id)
    except WebSocketDisconnect:
        return
    except Exception:  # pragma: no cover - transport level
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/events")
async def global_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    state: AppState = websocket.app.state.qosense
    try:
        await _pump(websocket, state, None)
    except WebSocketDisconnect:
        return
    except Exception:  # pragma: no cover - transport level
        with contextlib.suppress(Exception):
            await websocket.close()
