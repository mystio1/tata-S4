"""Application configuration and safety bounds.

Bounds here are enforced server-side. A client cannot widen them.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QOSENSE_", env_file=".env", extra="ignore"
    )

    env: str = "development"
    build_version: str = "0.1.0-phase1"
    data_mode: str = "SIMULATION"
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Laboratory agent. Empty means no privileged Linux control is reachable,
    # which is the default and the only supported state in this build.
    lab_agent_url: str = ""
    lab_agent_token: str = ""
    enable_live_enforcement: bool = False

    # Experiment safety bounds.
    max_scenario_duration_seconds: float = Field(default=600.0, gt=0)
    min_scenario_duration_seconds: float = Field(default=10.0, gt=0)
    max_intent_duration_seconds: float = Field(default=3600.0, gt=0)
    max_policy_rate_step_percent: float = Field(default=20.0, gt=0, le=100)
    require_policy_readback: bool = True
    max_concurrent_experiments: int = Field(default=4, ge=1, le=16)

    # Simulation engine.
    tick_seconds: float = Field(default=0.2, gt=0.01, le=1.0)
    # Whether the server advances running experiments on its own. Tests turn
    # this off so they can step the simulator deterministically.
    autorun_experiment_loop: bool = True
    event_history_limit: int = Field(default=2000, ge=100)
    subscriber_queue_limit: int = Field(default=512, ge=32)

    # Capacity estimator (M3A).
    capacity_ewma_alpha: float = Field(default=0.25, gt=0, le=1)
    capacity_sample_staleness_seconds: float = Field(default=5.0, gt=0)
    capacity_headroom_fraction: float = Field(default=0.95, gt=0, le=1)

    # Policy engine (M4).
    policy_min_hold_seconds: float = Field(default=4.0, ge=0)
    policy_cooldown_seconds: float = Field(default=3.0, ge=0)
    added_delay_target_ms: float = Field(default=30.0, gt=0)
    bulk_minimum_floor_mbps: float = Field(default=2.0, ge=0)
    verification_window_seconds: float = Field(default=4.0, gt=0)

    # Model artifacts. Empty means the model is NOT_CONFIGURED.
    traffic_classifier_path: str = ""
    congestion_predictor_path: str = ""

    @property
    def origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
