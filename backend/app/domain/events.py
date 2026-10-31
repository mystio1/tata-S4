"""Typed event envelope streamed over the WebSocket."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.domain.enums import DataMode, EventSeverity, EventType
from app.domain.metrics import StrictModel


class EventEnvelope(StrictModel):
    """Every real-time message shares this envelope.

    ``sequence`` is monotonic per experiment. A client that sees a gap should
    request a fresh snapshot from ``GET /api/v1/system/status`` rather than
    guessing the missing state.
    """

    event_id: str
    experiment_id: str | None
    timestamp: datetime
    data_mode: DataMode
    type: EventType
    sequence: int
    severity: EventSeverity = EventSeverity.INFO
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class EventPage(StrictModel):
    events: list[EventEnvelope]
    total: int
    dropped: int = 0
