"""M3B - future congestion-risk prediction (Model 2).

Model 2 is a separately trained LightGBM binary classifier scheduled for
Phase 5 / Round 2. It is not trained in this build, so this module exists to
hold the boundary rather than to produce numbers: every call returns
``risk_band = UNAVAILABLE`` and ``decision = IGNORED``.

Two rules matter here and are enforced by the surrounding code rather than
stated as a comment only:

* A prediction describes a future event in ``(t, t + horizon]``. The currently
  measured state lives in ``MetricsSnapshot.congestion_state`` and is computed
  from measurements alone. Nothing copies one into the other.
* A prediction is advisory. It reaches the policy engine as one input among
  several and cannot bypass the Safety Guard.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.enums import PredictionDecision, RiskBand
from app.domain.metrics import CongestionPrediction
from app.models.registry import MODEL_2, ModelRegistry


class CongestionPredictor:
    """Wrapper around the Model 2 artifact slot."""

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    @property
    def is_available(self) -> bool:
        return self._registry.is_ready(MODEL_2.key)

    def predict_congestion(
        self,
        experiment_id: str,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> CongestionPrediction:
        """Return a prediction, or an explicit unavailability record."""
        if not self.is_available:
            return CongestionPrediction(
                experiment_id=experiment_id,
                model_version=self._registry.version(MODEL_2.key),
                window_start=window_start,
                window_end=window_end,
                prediction_horizon_seconds=None,
                congestion_probability=None,
                risk_band=RiskBand.UNAVAILABLE,
                decision=PredictionDecision.IGNORED,
                reason="MODEL_NOT_READY",
                inference_ms=None,
            )
        raise NotImplementedError(
            "Model 2 inference is not implemented. The registry must not report "
            "READY until the artifact is trained, evaluated and integrated."
        )
