"""Shutdown must be graceful.

Cancelling the tick loop raises ``asyncio.CancelledError``, which derives
from ``BaseException`` rather than ``Exception``. A handler that catches only
``Exception`` lets it escape and turns an ordinary stop into a failed
shutdown, which is how the server ends up exiting non-zero on Ctrl-C.
"""

from __future__ import annotations

import asyncio

from app.config import Settings
from app.domain.enums import ExperimentMode
from app.models.registry import ModelRegistry
from app.runtime.event_bus import EventBus
from app.runtime.experiment import ExperimentManager
from app.state import AppState
from tests.conftest import build_runtime


async def test_shutdown_cancels_the_loop_without_raising() -> None:
    settings = Settings(autorun_experiment_loop=True, env="test")
    manager = ExperimentManager(
        settings,
        ModelRegistry(settings),
        EventBus(history_limit=200, subscriber_queue_limit=32),
    )
    manager.start_loop()
    await asyncio.sleep(0.05)
    # Must not raise.
    await manager.shutdown()


async def test_shutdown_stops_running_experiments(settings: Settings) -> None:
    state = AppState.build(settings)
    runtime = build_runtime(state, mode=ExperimentMode.NO_QOS, duration=60.0)
    runtime.start()
    runtime.tick()
    await state.manager.shutdown()
    assert runtime.status.value in ("STOPPED", "COMPLETED")
    assert runtime.engine.shaping_active is False
