"""Process-wide application state.

Everything mutable lives on one object so tests can build an isolated instance
instead of reaching for module globals.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.config import Settings, get_settings
from app.models.registry import ModelRegistry
from app.runtime.event_bus import EventBus
from app.runtime.experiment import ExperimentManager


@dataclass
class AppState:
    settings: Settings
    registry: ModelRegistry
    bus: EventBus
    manager: ExperimentManager

    @classmethod
    def build(cls, settings: Settings | None = None) -> AppState:
        settings = settings or get_settings()
        registry = ModelRegistry(settings)
        bus = EventBus(
            history_limit=settings.event_history_limit,
            subscriber_queue_limit=settings.subscriber_queue_limit,
        )
        manager = ExperimentManager(settings, registry, bus)
        return cls(settings=settings, registry=registry, bus=bus, manager=manager)


def get_state(request: Request) -> AppState:
    """FastAPI dependency returning the state attached to the application."""
    return request.app.state.qosense
