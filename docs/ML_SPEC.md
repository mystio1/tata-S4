# QoSense Machine-Learning Specification

## 1. Overview

The intended final prototype contains two separate LightGBM models. They use the same algorithm family but have different targets, data pipelines and evaluation metrics.

- Model 1: multiclass traffic-behaviour classification.
- Model 2: binary prediction of a future congestion event.

Neither model applies Linux configuration directly. Both provide evidence to the deterministic policy engine.

## 2. Model 1 — traffic classification

### Task
Supervised multiclass classification of broad flow-behaviour categories.

### Candidate classes
`INTERACTIVE`, `GAMING_LIKE`, `STREAMING_LIKE`, `BULK`, `WEB_VOICE`, `UNKNOWN`.

Use only classes that the controlled traffic generators and labelled traces support. Avoid claiming exact application identity from encrypted metadata.

### Candidate features
Feature names, types and units must be versioned in a shared schema. Candidate features:

- `mean_packet_bytes`
- `median_packet_bytes`
- `std_packet_bytes`
- `min_packet_bytes`
- `max_packet_bytes`
- `mean_interarrival_ms`
- `std_interarrival_ms`
- `packets_per_second`
- `bytes_per_second`
- `flow_duration_ms`
- `up_bytes`
- `down_bytes`
- `up_packets`
- `down_packets`
- `burst_count`
- `active_time_ratio`
- `transport_protocol` (only if it adds legitimate generalizable value)
- direction and permitted header-derived fields

Do not use fixed source/destination ports as the sole or dominant identity signal. Include randomized ports and unseen port combinations in evaluation. Do not include ground-truth labels, scenario names or hidden simulator settings as features.

### Data generation
Use controlled generators for interactive-like UDP traffic, gaming-like low-volume small-packet traffic, streaming-like steady/bursty flows, bulk TCP/UDP transfers and web/voice-like cases. Record ground truth from the generator configuration, not from inferred predictions. Save experiment ID, seed, class label, feature-window timestamps and generator version.

### Training/evaluation
- Split by complete experiment runs/scenarios, not random packet rows from the same run.
- Keep a final held-out set of unseen combinations and port values.
- Report macro-F1, per-class precision/recall, confusion matrix, unknown rate and probability calibration.
- Consider class weighting or balanced sampling only after inspecting class imbalance.
- Tune depth/leaves/regularization to prevent overfitting.
- Calibrate probabilities on a validation set if calibrated confidence is used for control.
- Persist model version, training dataset version, feature schema, hyperparameters and evaluation report.

### Inference output example
This is a schema example only; values are not real results:

```json
{
  "model_name": "traffic_classifier",
  "model_version": "not-trained",
  "flow_id": "sim-flow-001",
  "predicted_class": "UNKNOWN",
  "probabilities": {},
  "calibrated_confidence": null,
  "calibration_status": "NOT_CALIBRATED",
  "decision": "SAFE_FALLBACK",
  "reason": "MODEL_NOT_READY",
  "inference_ms": null
}
```

Never return fake confidence values when no model artifact is loaded.

## 3. Model 2 — future congestion-risk prediction

### Task
Supervised binary classification: predict whether a defined congestion event will occur within a future horizon after an observation window.

### Define the event before training
Create an explicit configurable target definition based on observable conditions, for example a sustained threshold crossing in loaded delay/queue growth together with available evidence of link saturation. The exact thresholds and persistence interval must be established experimentally and documented. Do not label congestion solely from one transient RTT spike.

### Candidate features at time t
- utilization and recent utilization slope;
- recent delivery-rate samples and slope;
- idle RTT baseline and current/loaded RTT inflation;
- jitter/delay variation;
- packet-loss trend;
- queue depth and queue-growth trend, if observable;
- active flow count;
- per-class demand and burst statistics;
- recent bytes/packets per second.

Use only values available at or before time `t`. Do not include future RTT, future queue depth, future congestion labels or hidden emulator settings in input features.

### Prediction horizon
Make the horizon configurable, but choose it using experiments. Evaluate several candidate horizons if needed and select one that provides useful lead time without unacceptable false alarms. Never hard-code an arbitrary 30-second claim solely for the UI.

### Dataset creation
For each experiment, create timestamped observation windows. Label each window based on whether the defined event occurs in `(t, t + H]`. Use run-based and chronological train/validation/test splits. Keep windows from one experiment run in one split to prevent leakage.

### Evaluation
- Precision and recall for the positive congestion class.
- PR-AUC if events are rare/imbalanced.
- False alarm rate and missed-event rate.
- Calibration/Brier score where probability is consumed by the controller.
- Prediction lead time for correctly predicted events.
- Results on unseen combinations of bandwidth, delay, loss, traffic mix and ports.
- Ablation: compare reactive deterministic control, Model 1 only and Model 1 + Model 2.

### Inference output example
Schema example only:

```json
{
  "model_name": "congestion_predictor",
  "model_version": "not-trained",
  "window_start": "2026-01-01T00:00:00Z",
  "window_end": "2026-01-01T00:00:05Z",
  "prediction_horizon_seconds": null,
  "congestion_probability": null,
  "risk_band": "UNAVAILABLE",
  "decision": "IGNORED",
  "reason": "MODEL_NOT_READY",
  "inference_ms": null
}
```

## 4. Capacity estimator is not a third ML model

M3A is deterministic. Use a documented estimator with sample validity checks, delivery-rate observations, idle/loaded delay trend and EWMA smoothing. Define the smoothing factor in configuration and test sensitivity. Expose a data-quality state. A low-demand link may not reveal maximum capacity; do not fabricate an estimate.

## 5. Model lifecycle

Keep training separate from live inference. A deployed model is an immutable, versioned artifact. User corrections are recorded as review labels and enter a later controlled retraining dataset; they do not silently mutate the live model. Retraining requires a new evaluation report and explicit model-version promotion.

## 6. Model service interface

Provide separate interfaces:
- `classify_flow(feature_window) -> ClassificationResult`
- `predict_congestion(measurement_window) -> CongestionPrediction`

Each interface must validate schema, timestamp freshness, finite numeric values and model readiness. Return explicit unavailability/fallback reasons. Log model version and inference duration.

## 7. No unearned AI claims

- LightGBM probabilities are not automatically calibrated confidence.
- A high risk score is not proof that congestion is currently present.
- A model cannot identify every encrypted application.
- A model is not useful merely because it runs; it must improve an appropriate measured outcome compared with a deterministic baseline.
- Do not display invented model accuracy, confidence, inference speed or predicted lead time.
