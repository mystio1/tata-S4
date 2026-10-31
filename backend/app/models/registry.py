"""Machine-learning model registry.

Both QoSense models are Light Gradient Boosting Machine (LightGBM) artifacts.
This registry reports what is *actually* loaded. It never reports ``READY``
for a model it cannot run, and it never produces a probability when no
artifact exists.

Phase status at the time of writing:

* Model 1 (traffic classifier)  - trained artifact loaded. Reused from
  AQoSE, the team's own earlier prototype (see
  ``docs/COMPETITOR_STUDY.md``), with a documented feature-vector and
  class-taxonomy adaptation in ``app.models.aqose_features``. Phase 4 /
  Round 1.
* Model 2 (congestion predictor) - designed, not trained. Phase 5 / Round 2.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.domain.enums import ModelState
from app.domain.metrics import FEATURE_SCHEMA_VERSION, StrictModel

logger = logging.getLogger("qosense.models.registry")

NOT_TRAINED_VERSION = "not-trained"


@dataclass
class LoadedTrafficClassifier:
    """An actually-loaded Model 1 artifact: the estimator plus its metadata.

    ``feature_names`` is checked against ``aqose_features.FEATURE_NAMES`` at
    load time, the same guard AQoSE's own ``model.load`` makes: a model
    trained on a different feature list would silently misread every row.
    """

    estimator: Any
    meta: dict[str, Any]


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
            if key == MODEL_1.key:
                self._attempt_load_traffic_classifier(path)
            else:
                # Model 2's training pipeline does not exist yet (Phase 5,
                # Round 2). A path being configured without a pipeline behind
                # it is reported as failed, not silently ignored.
                self._load_errors[key] = (
                    "Model 2 training is not implemented. Scheduled for Phase 5, Round 2."
                )

    def _attempt_load_traffic_classifier(self, path: str) -> None:
        from app.models import aqose_features

        try:
            import joblib
        except ImportError as exc:  # pragma: no cover - dependency missing
            self._load_errors[MODEL_1.key] = f"joblib is not installed: {exc}"
            return

        meta_path = os.path.join(os.path.dirname(path), "meta.json")
        if not os.path.isfile(meta_path):
            self._load_errors[MODEL_1.key] = f"Artifact metadata not found at '{meta_path}'."
            return

        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            self._load_errors[MODEL_1.key] = f"Could not read artifact metadata: {exc}"
            return

        # Refuse a model trained on a different feature list. Its numbers
        # would mean something else; loading it anyway would be a silent
        # correctness bug, not a working model.
        if meta.get("features") != aqose_features.FEATURE_NAMES:
            self._load_errors[MODEL_1.key] = (
                "Artifact was trained on a different feature list than "
                "app.models.aqose_features.FEATURE_NAMES expects; refusing to load it."
            )
            return

        try:
            estimator = joblib.load(path)
        except Exception as exc:  # noqa: BLE001 - any load failure is reported, not raised
            self._load_errors[MODEL_1.key] = f"Could not load artifact: {exc}"
            return

        if not hasattr(estimator, "predict_proba") or not hasattr(estimator, "classes_"):
            self._load_errors[MODEL_1.key] = (
                "Loaded object has no predict_proba/classes_; not a usable classifier."
            )
            return

        self._loaded[MODEL_1.key] = LoadedTrafficClassifier(estimator=estimator, meta=meta)
        logger.info(
            "Model 1 traffic classifier loaded: version=%s classes=%s",
            meta.get("model_version"),
            list(estimator.classes_),
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
        if not self.is_ready(key):
            return NOT_TRAINED_VERSION
        loaded = self._loaded.get(key)
        if isinstance(loaded, LoadedTrafficClassifier):
            return str(loaded.meta.get("model_version", "unknown"))
        return "unknown"

    def traffic_classifier(self) -> LoadedTrafficClassifier | None:
        """The loaded Model 1 artifact, or ``None`` if it is not ready.

        Kept separate from the generic ``_loaded`` dict so callers get a
        typed object rather than reaching into registry internals.
        """
        loaded = self._loaded.get(MODEL_1.key)
        return loaded if isinstance(loaded, LoadedTrafficClassifier) else None

    def record_inference(self, key: str, timestamp: str) -> None:
        self._inference_counts[key] = self._inference_counts.get(key, 0) + 1
        self._last_inference[key] = timestamp

    def status(self, slot: ModelSlot) -> ModelStatus:
        state = self.state(slot.key)
        ready_detail = (
            "Artifact loaded and able to run inference. Reused from AQoSE (the team's own "
            "earlier prototype) with a documented feature-vector and class-taxonomy "
            "adaptation; see app.models.aqose_features. Only runs on LIVE packet captures; "
            "SIMULATION has no per-packet data to feed it."
            if slot.key == MODEL_1.key
            else "Artifact loaded and able to run inference."
        )
        detail = {
            ModelState.NOT_CONFIGURED: (
                f"No artifact configured. Designed in docs/ML_SPEC.md and scheduled for "
                f"{slot.implementation_phase} ({slot.round_allocation}). "
                f"This model is not producing any prediction."
            ),
            ModelState.FAILED: self._load_errors.get(slot.key, "Artifact could not be loaded."),
            ModelState.READY: ready_detail,
            ModelState.TRAINING: "Training in progress.",
            ModelState.DISABLED: "Disabled by configuration.",
        }[state]

        traffic_classifier_ready = slot.key == MODEL_1.key and state is ModelState.READY
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
            feature_schema_version=(
                "aqose-packet-features-v1"
                if traffic_classifier_ready
                else (FEATURE_SCHEMA_VERSION if slot.key == MODEL_1.key else None)
            ),
            # Calibration is baked into the saved estimator (temperature
            # scaling, chosen during AQoSE's own training); predict_proba
            # already reflects it, so this is true once the artifact loads.
            calibration_available=traffic_classifier_ready,
            last_inference_at=self._last_inference.get(slot.key),
            inference_count=self._inference_counts.get(slot.key, 0),
            implementation_phase=slot.implementation_phase,
            round_allocation=slot.round_allocation,
            detail=detail,
        )

    def all_statuses(self) -> list[ModelStatus]:
        return [self.status(MODEL_1), self.status(MODEL_2)]
