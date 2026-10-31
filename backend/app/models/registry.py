"""Machine-learning model registry.

Both QoSense models are Light Gradient Boosting Machine (LightGBM) artifacts
that are trained in a separate phase. This registry reports what is *actually*
loaded. It never reports ``READY`` for a model it cannot run, and it never
produces a probability when no artifact exists.

Phase status at the time of writing:

* Model 1 (traffic classifier)  - designed, not trained. Phase 4 / Round 1.
* Model 2 (congestion predictor) - designed, not trained. Phase 5 / Round 2.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import Settings
from app.domain.enums import ModelState
from app.domain.metrics import FEATURE_SCHEMA_VERSION, StrictModel

NOT_TRAINED_VERSION = "not-trained"


@dataclass(frozen=True)
class ModelSlot:
    key: str
    model_name: str
    display_name: str
    purpose: str
    algorithm: str
    target: str
    implementation_phase: str
    round_allocation: str


MODEL_1 = ModelSlot(
    key="traffic_classifier",
    model_name="traffic_classifier",
    display_name="Model 1 - Traffic classifier",
    purpose="Predicts a broad traffic behaviour class for one flow feature window.",
    algorithm="LightGBM multiclass",
    target="INTERACTIVE, GAMING_LIKE, STREAMING_LIKE, BULK, WEB_VOICE, UNKNOWN",
    implementation_phase="Phase 4",
    round_allocation="Round 1",
)

MODEL_2 = ModelSlot(
    key="congestion_predictor",
    model_name="congestion_predictor",
    display_name="Model 2 - Congestion-risk predictor",
    purpose=(
        "Predicts whether a defined congestion event occurs within a future "
        "horizon after the observation window."
    ),
    algorithm="LightGBM binary",
    target="Congestion event in (t, t + horizon]",
    implementation_phase="Phase 5",
    round_allocation="Round 2",
)


class ModelStatus(StrictModel):
    """Public status of one model slot."""

    key: str
    model_name: str
    display_name: str
    purpose: str
    algorithm: str
    target: str
    state: ModelState
    version: str
    artifact_path: str | None
    feature_schema_version: str | None
    calibration_available: bool
    last_inference_at: str | None
    inference_count: int
    implementation_phase: str
    round_allocation: str
    detail: str


class ModelRegistry:
    """Reports the real state of both model slots.

    Loading is attempted only when a path is configured. There is no code path
    that fabricates a loaded model.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._paths = {
            MODEL_1.key: settings.traffic_classifier_path,
            MODEL_2.key: settings.congestion_predictor_path,
        }
        self._loaded: dict[str, object] = {}
        self._inference_counts: dict[str, int] = {MODEL_1.key: 0, MODEL_2.key: 0}
        self._last_inference: dict[str, str | None] = {MODEL_1.key: None, MODEL_2.key: None}
        self._load_errors: dict[str, str] = {}
        self._attempt_load()

    def _attempt_load(self) -> None:
        for key, path in self._paths.items():
            if not path:
                continue
            if not os.path.isfile(path):
                self._load_errors[key] = f"Configured artifact not found at '{path}'."
                continue
            # Loading is deliberately not implemented until the training
            # pipeline for that phase exists. Reporting FAILED here is honest:
            # a path was configured but nothing can run.
            self._load_errors[key] = (
                "Artifact loading is not implemented in this build. "
                "Train and integrate the model in its phase before enabling it."
            )

    def state(self, key: str) -> ModelState:
        if key in self._loaded:
            return ModelState.READY
        if key in self._load_errors:
            return ModelState.FAILED
        if not self._paths.get(key):
            return ModelState.NOT_CONFIGURED
        return ModelState.NOT_CONFIGURED

    def is_ready(self, key: str) -> bool:
        return self.state(key) is ModelState.READY

    def version(self, key: str) -> str:
        return NOT_TRAINED_VERSION if not self.is_ready(key) else "unknown"

    def record_inference(self, key: str, timestamp: str) -> None:
        self._inference_counts[key] = self._inference_counts.get(key, 0) + 1
        self._last_inference[key] = timestamp

    def status(self, slot: ModelSlot) -> ModelStatus:
        state = self.state(slot.key)
        detail = {
            ModelState.NOT_CONFIGURED: (
                f"No artifact configured. Designed in docs/ML_SPEC.md and scheduled for "
                f"{slot.implementation_phase} ({slot.round_allocation}). "
                f"This model is not producing any prediction."
            ),
            ModelState.FAILED: self._load_errors.get(slot.key, "Artifact could not be loaded."),
            ModelState.READY: "Artifact loaded and able to run inference.",
            ModelState.TRAINING: "Training in progress.",
            ModelState.DISABLED: "Disabled by configuration.",
        }[state]

        return ModelStatus(
            key=slot.key,
            model_name=slot.model_name,
            display_name=slot.display_name,
            purpose=slot.purpose,
            algorithm=slot.algorithm,
            target=slot.target,
            state=state,
            version=self.version(slot.key),
            artifact_path=self._paths.get(slot.key) or None,
            feature_schema_version=FEATURE_SCHEMA_VERSION if slot.key == MODEL_1.key else None,
            calibration_available=False,
            last_inference_at=self._last_inference.get(slot.key),
            inference_count=self._inference_counts.get(slot.key, 0),
            implementation_phase=slot.implementation_phase,
            round_allocation=slot.round_allocation,
            detail=detail,
        )

    def all_statuses(self) -> list[ModelStatus]:
        return [self.status(MODEL_1), self.status(MODEL_2)]
