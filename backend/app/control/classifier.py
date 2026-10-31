"""M2 - traffic classification.

Two sources can produce a traffic class, and the result always says which one
did:

``DETERMINISTIC_RULES``
    A transparent rule set over the same feature window the model would see.
    This is the Mode 2 (``TRADITIONAL_ADAPTIVE``) baseline. A rule set has no
    probability distribution, so ``probabilities`` stays empty and
    ``calibrated_confidence`` stays ``None``. Reporting a made-up number here
    would make the Mode 2 against Mode 3 comparison meaningless.

``MODEL_1``
    The trained LightGBM multiclass artifact. Not available in this build, so
    every request routes to ``SAFE_FALLBACK`` with reason ``MODEL_NOT_READY``.

The rules below are deliberately written against behaviour, not against ports
or the scenario's ground-truth label. ``FlowSpec.true_class`` is never read
here.

Why packet size leads and byte rate follows
-------------------------------------------

Byte rate is the obvious discriminator and the wrong one to lean on, because
the shaper controls it. Throttle a class and its measured rate falls, which can
reclassify it into a lower class, which throttles it further: classification
and enforcement form a feedback loop through the link itself.

Packet size and the duty cycle of a flow are set by the application, not by
how much capacity it is granted, so they stay stable under shaping. The rules
therefore lead with packet size and activity structure and use rate only as a
secondary bound. A model trained on these windows will face the same loop, so
the same reasoning applies to its feature set.
"""

from __future__ import annotations

import time

import numpy as np

from app.domain.enums import (
    CalibrationStatus,
    ClassificationDecision,
    ClassificationSource,
    ExperimentMode,
    Protocol,
    TrafficClass,
)
from app.domain.metrics import ClassificationResult, FlowFeatureWindow
from app.models.aqose_features import PacketLevelRecord, feature_matrix, remap_probabilities
from app.models.registry import MODEL_1, ModelRegistry

# Minimum calibrated probability for the top class to be accepted. Below this
# the model is not confident enough to act on its prediction, so UNKNOWN is
# returned and policy falls back to the conservative handling for that class.
MODEL_CONFIDENCE_THRESHOLD = 0.45

RULES_VERSION = "deterministic-rules-v1"

# Rule thresholds. These are configuration, not learned parameters, and they
# are versioned with ``RULES_VERSION`` so a Mode 2 run can be reproduced.
# A flow using consistently large segments is moving a file or a video, not
# carrying a conversation.
LARGE_PACKET_BYTES = 1100.0
BULK_MIN_ACTIVE_RATIO = 0.75

GAMING_MAX_PACKET_BYTES = 320.0
GAMING_MIN_PACKETS_PER_SECOND = 80.0
GAMING_MAX_MBPS = 2.0

INTERACTIVE_MIN_MBPS = 0.4
INTERACTIVE_MAX_MBPS = 8.0
INTERACTIVE_PACKET_BYTES_RANGE = (320.0, 1100.0)
INTERACTIVE_MAX_INTERARRIVAL_STD_MS = 6.0

STREAMING_MAX_ACTIVE_RATIO = 0.9

WEB_VOICE_MAX_MBPS = 6.0
WEB_VOICE_MAX_ACTIVE_RATIO = 0.75


def _mbps(window: FlowFeatureWindow) -> float:
    return window.features.bytes_per_second * 8.0 / 1_000_000.0


def classify_with_rules(window: FlowFeatureWindow) -> tuple[TrafficClass, str]:
    """Deterministic behaviour rules. Returns the class and the rule that fired."""
    f = window.features
    rate_mbps = _mbps(window)
    large_packets = f.mean_packet_bytes >= LARGE_PACKET_BYTES

    # Small, frequent datagrams at a low rate: a game client.
    if (
        f.mean_packet_bytes <= GAMING_MAX_PACKET_BYTES
        and f.packets_per_second >= GAMING_MIN_PACKETS_PER_SECOND
        and rate_mbps <= GAMING_MAX_MBPS
    ):
        return TrafficClass.GAMING_LIKE, "RULE_GAMING_SMALL_FREQUENT_PACKETS"

    # Medium datagrams arriving at a steady cadence: a live conversation.
    low, high = INTERACTIVE_PACKET_BYTES_RANGE
    if (
        window.protocol is Protocol.UDP
        and low <= f.mean_packet_bytes < high
        and INTERACTIVE_MIN_MBPS <= rate_mbps <= INTERACTIVE_MAX_MBPS
        and f.std_interarrival_ms <= INTERACTIVE_MAX_INTERARRIVAL_STD_MS
    ):
        return TrafficClass.INTERACTIVE, "RULE_INTERACTIVE_STEADY_DATAGRAMS"

    # Large segments separate the two bulk-ish behaviours from everything else.
    # Continuous means a file transfer; an idle gap every cycle means a player
    # topping up a buffer it has already filled.
    if large_packets:
        if f.active_time_ratio >= BULK_MIN_ACTIVE_RATIO:
            return TrafficClass.BULK, "RULE_BULK_SUSTAINED_LARGE_SEGMENTS"
        if f.active_time_ratio <= STREAMING_MAX_ACTIVE_RATIO:
            return TrafficClass.STREAMING_LIKE, "RULE_STREAMING_BURSTY_BUFFER_FILL"

    if (
        not large_packets
        and rate_mbps <= WEB_VOICE_MAX_MBPS
        and f.active_time_ratio <= WEB_VOICE_MAX_ACTIVE_RATIO
    ):
        return TrafficClass.WEB_VOICE, "RULE_WEB_VOICE_LOW_RATE_INTERMITTENT"

    return TrafficClass.UNKNOWN, "RULE_NO_MATCH"


class TrafficClassifier:
    """Routes a feature window to the source appropriate for the run mode."""

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def _classify_with_model1(
        self, window: FlowFeatureWindow, packet_record: PacketLevelRecord
    ) -> ClassificationResult:
        """Run the AQoSE LightGBM artifact and remap its output to QoSense classes."""
        started = time.perf_counter()
        loaded = self._registry.traffic_classifier()
        # Registry guards ensure this is not None when is_ready() is True.
        assert loaded is not None

        X = feature_matrix([packet_record])
        raw_probs = loaded.estimator.predict_proba(X)[0]
        aqose_classes = list(loaded.estimator.classes_)
        aqose_prob_dict = dict(zip(aqose_classes, raw_probs.tolist(), strict=False))

        qosense_probs = remap_probabilities(aqose_prob_dict)
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 4)

        self._registry.record_inference(MODEL_1.key, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

        if not qosense_probs:
            return ClassificationResult(
                flow_id=window.flow_id,
                model_version=self._registry.version(MODEL_1.key),
                source=ClassificationSource.SAFE_FALLBACK,
                predicted_class=TrafficClass.UNKNOWN,
                probabilities={},
                calibrated_confidence=None,
                calibration_status=CalibrationStatus.CALIBRATED,
                decision=ClassificationDecision.SAFE_FALLBACK,
                reason="REMAP_EMPTY",
                inference_ms=elapsed_ms,
            )

        best_class = max(qosense_probs, key=lambda c: qosense_probs[c])
        confidence = qosense_probs[best_class]

        prob_dict = dict(qosense_probs)

        if confidence < MODEL_CONFIDENCE_THRESHOLD:
            return ClassificationResult(
                flow_id=window.flow_id,
                model_version=self._registry.version(MODEL_1.key),
                source=ClassificationSource.MODEL_1,
                predicted_class=TrafficClass.UNKNOWN,
                probabilities=prob_dict,
                calibrated_confidence=round(confidence, 4),
                calibration_status=CalibrationStatus.CALIBRATED,
                decision=ClassificationDecision.SAFE_FALLBACK,
                reason=f"LOW_CONFIDENCE_{round(confidence, 3)}",
                inference_ms=elapsed_ms,
            )

        return ClassificationResult(
            flow_id=window.flow_id,
            model_version=self._registry.version(MODEL_1.key),
            source=ClassificationSource.MODEL_1,
            predicted_class=best_class,
            probabilities=prob_dict,
            calibrated_confidence=round(confidence, 4),
            calibration_status=CalibrationStatus.CALIBRATED,
            decision=ClassificationDecision.ACCEPTED,
            reason="MODEL_1_ACCEPTED",
            inference_ms=elapsed_ms,
        )

    def classify_flow(
        self,
        window: FlowFeatureWindow,
        mode: ExperimentMode,
        packet_record: PacketLevelRecord | None = None,
    ) -> ClassificationResult:
        """Classify one feature window.

        ``mode`` decides which source is *allowed*, not which source is
        pretended. A mode that requires Model 1 returns a safe fallback while
        Model 1 is unavailable rather than quietly using the rule set, because
        substituting the rules would destroy the Mode 2 against Mode 3
        comparison the experiment plan depends on.
        """
        started = time.perf_counter()

        if mode in (ExperimentMode.CLASSIFIER_ONLY, ExperimentMode.FULL_QOSENSE):
            if not self._registry.is_ready(MODEL_1.key):
                return ClassificationResult(
                    flow_id=window.flow_id,
                    model_version=self._registry.version(MODEL_1.key),
                    source=ClassificationSource.SAFE_FALLBACK,
                    predicted_class=TrafficClass.UNKNOWN,
                    probabilities={},
                    calibrated_confidence=None,
                    calibration_status=CalibrationStatus.UNAVAILABLE,
                    decision=ClassificationDecision.SAFE_FALLBACK,
                    reason="MODEL_NOT_READY",
                    inference_ms=None,
                )
            if packet_record is None:
                # Model 1 needs per-packet positional features (first-8
                # sizes and 7 gaps) that windowed aggregates do not carry.
                # SIMULATION mode has no packet capture, so this path is
                # expected there. LIVE mode will supply a PacketLevelRecord
                # once the capture pipeline is wired in.
                return ClassificationResult(
                    flow_id=window.flow_id,
                    model_version=self._registry.version(MODEL_1.key),
                    source=ClassificationSource.SAFE_FALLBACK,
                    predicted_class=TrafficClass.UNKNOWN,
                    probabilities={},
                    calibrated_confidence=None,
                    calibration_status=CalibrationStatus.UNAVAILABLE,
                    decision=ClassificationDecision.SAFE_FALLBACK,
                    reason="NO_PACKET_RECORD",
                    inference_ms=None,
                )
            return self._classify_with_model1(window, packet_record)

        traffic_class, rule = classify_with_rules(window)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        accepted = traffic_class is not TrafficClass.UNKNOWN
        return ClassificationResult(
            flow_id=window.flow_id,
            model_version=RULES_VERSION,
            source=ClassificationSource.DETERMINISTIC_RULES,
            predicted_class=traffic_class,
            probabilities={},
            calibrated_confidence=None,
            calibration_status=CalibrationStatus.UNAVAILABLE,
            decision=(
                ClassificationDecision.ACCEPTED
                if accepted
                else ClassificationDecision.SAFE_FALLBACK
            ),
            reason=rule,
            inference_ms=round(elapsed_ms, 4),
        )
