"""Shared test fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.control.capacity import CapacityEstimator
from app.domain.enums import DataMode, Direction, ExperimentMode
from app.domain.experiment import ExperimentCreateRequest
from app.main import create_app
from app.runtime.experiment import ExperimentRuntime
from app.simulation.engine import SimulationEngine
from app.simulation.scenarios import clamp_parameters, get_scenario
from app.state import AppState


@pytest.fixture
def settings() -> Settings:
    """Settings with no laboratory agent and no model artifacts configured."""
    return Settings(
        env="test",
        lab_agent_url="",
        lab_agent_token="",
        enable_live_enforcement=False,
        traffic_classifier_path="",
        congestion_predictor_path="",
        # Tests step the simulator themselves so results are deterministic.
        autorun_experiment_loop=False,
    )


@pytest.fixture
def state(settings: Settings) -> AppState:
    return AppState.build(settings)


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def build_engine(
    scenario_id: str = "iso_during_call",
    seed: int = 42,
    duration: float = 60.0,
    overrides: dict[str, float] | None = None,
) -> SimulationEngine:
    scenario = get_scenario(scenario_id)
    parameters, _ = clamp_parameters(scenario, overrides or {})
    return SimulationEngine(
        scenario=scenario,
        parameters=parameters,
        seed=seed,
        tick_seconds=0.2,
        duration_seconds=duration,
    )


def build_runtime(
    state: AppState,
    mode: ExperimentMode = ExperimentMode.TRADITIONAL_ADAPTIVE,
    scenario_id: str = "iso_during_call",
    duration: float = 60.0,
    seed: int = 42,
) -> ExperimentRuntime:
    return state.manager.create(
        ExperimentCreateRequest(
            scenario_id=scenario_id,
            mode=mode,
            data_mode=DataMode.SIMULATION,
            seed=seed,
            duration_seconds=duration,
        )
    )


def run_to_completion(runtime: ExperimentRuntime, max_ticks: int = 5000) -> None:
    runtime.start()
    for _ in range(max_ticks):
        if runtime.status.value != "RUNNING":
            return
        runtime.tick()
    raise AssertionError("Experiment did not finish within the tick budget.")


@pytest.fixture
def estimator(settings: Settings) -> CapacityEstimator:
    return CapacityEstimator(settings, Direction.DOWNLOAD)
