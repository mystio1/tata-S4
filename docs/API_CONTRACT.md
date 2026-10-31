# QoSense API and Real-Time Event Contract

This document defines a suggested first contract. Keep schemas typed and versioned. Claude Code may refine details during implementation, but frontend/backend names and semantics must remain consistent.

## 1. API conventions

- Base path: `/api/v1`
- JSON request/response bodies.
- UTC ISO-8601 timestamps.
- Every experiment/event has an `experiment_id`.
- Every live-lab action has an `actor`, `request_id` and audit record.
- Every response exposes `data_mode`: `SIMULATION`, `LIVE` or `REPLAY`.
- All values must carry units in field names or schema documentation.
- Use explicit enums rather than free-form shell commands or policy strings.

## 2. Core endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | API health and build version |
| GET | `/api/v1/system/status` | Controller, models, lab agent and data mode |
| GET | `/api/v1/topology` | Devices, gateway, interfaces and links |
| GET | `/api/v1/metrics/current` | Current metric snapshot |
| GET | `/api/v1/flows` | Active flow list and latest classification |
| GET | `/api/v1/models/status` | Status/version of both models |
| GET | `/api/v1/policies/current` | Current named policy and applied state |
| GET | `/api/v1/policies/history` | Policy proposals, approval and enforcement history |
| POST | `/api/v1/experiments` | Create an experiment from a predefined scenario/mode |
| POST | `/api/v1/experiments/{id}/start` | Start a created experiment |
| POST | `/api/v1/experiments/{id}/stop` | Stop an experiment safely |
| POST | `/api/v1/experiments/{id}/reset` | Reset the isolated lab to a known baseline |
| GET | `/api/v1/experiments/{id}` | Experiment state and configuration |
| GET | `/api/v1/experiments/{id}/report` | Results/report when available |
| POST | `/api/v1/intents` | Create a bounded temporary priority lease |
| DELETE | `/api/v1/intents/{id}` | Cancel an intent lease |
| POST | `/api/v1/policies/{profile}/apply` | Request a predefined profile; server revalidates it |
| GET | `/api/v1/events` | Recent audit and system events |

Do not expose an endpoint such as `/run-shell`, `/tc-command` or any arbitrary command interface.

## 3. Experiment creation example

```json
{
  "scenario_id": "iso_during_call",
  "mode": "QOSENSE_FULL",
  "data_mode": "SIMULATION",
  "seed": 42,
  "duration_seconds": 120,
  "parameters": {
    "bottleneck_mbps": 100,
    "download_start_after_seconds": 15,
    "download_duration_seconds": 90
  }
}
```

Allowed `mode` values:
- `NO_QOS`
- `TRADITIONAL_ADAPTIVE`
- `CLASSIFIER_ONLY`
- `FULL_QOSENSE`

Allowed `data_mode` values:
- `SIMULATION`
- `LIVE`
- `REPLAY`

The backend must validate scenario parameters against a schema and configured bounds. Do not accept arbitrary interface names or shell arguments from the client.

## 4. Flow feature window

```json
{
  "experiment_id": "exp-001",
  "flow_id": "flow-001",
  "observed_at": "2026-01-01T00:00:05Z",
  "window_ms": 2000,
  "direction": "DOWNLOAD",
  "protocol": "UDP",
  "features": {
    "mean_packet_bytes": 740.0,
    "std_packet_bytes": 120.0,
    "mean_interarrival_ms": 8.2,
    "std_interarrival_ms": 2.4,
    "packets_per_second": 120.0,
    "bytes_per_second": 88800.0,
    "flow_duration_ms": 5200.0,
    "up_bytes": 1200,
    "down_bytes": 180000,
    "up_packets": 4,
    "down_packets": 240,
    "burst_count": 3
  }
}
```

Values are illustrative schema examples, not measured results. Maintain a versioned feature schema.

## 5. Classification result

```json
{
  "flow_id": "flow-001",
  "model_name": "traffic_classifier",
  "model_version": "not-trained",
  "predicted_class": "UNKNOWN",
  "probabilities": {},
  "calibrated_confidence": null,
  "calibration_status": "NOT_CALIBRATED",
  "decision": "SAFE_FALLBACK",
  "reason": "MODEL_NOT_READY",
  "inference_ms": null
}
```

Never populate probability values with made-up example numbers in live mode.

## 6. Congestion prediction result

```json
{
  "experiment_id": "exp-001",
  "model_name": "congestion_predictor",
  "model_version": "not-trained",
  "window_end": "2026-01-01T00:00:05Z",
  "prediction_horizon_seconds": null,
  "congestion_probability": null,
  "risk_band": "UNAVAILABLE",
  "decision": "IGNORED",
  "reason": "MODEL_NOT_READY"
}
```

A separate current-state measurement must represent actual observed congestion. Do not use the future risk prediction as a current congestion measurement.

## 7. Policy proposal

```json
{
  "policy_id": "policy-001",
  "profile": "PROTECT_INTERACTIVE",
  "created_at": "2026-01-01T00:00:06Z",
  "reason_codes": ["INTERACTIVE_FLOW_PRESENT", "CONGESTION_RISK_ELEVATED"],
  "source_event_ids": ["evt-101", "evt-102"],
  "class_rates_mbps": {},
  "valid_until": null,
  "safety_checks": {
    "capacity_fresh": false,
    "fairness_ok": false,
    "rate_change_within_limit": false,
    "intent_valid": true,
    "rollback_available": false
  },
  "status": "REJECTED"
}
```

`class_rates_mbps` must be populated only from a validated policy proposal. The API must not assume fixed universal percentages. The safety guard evaluates the complete allocation and configured link rate.

## 8. Temporary intent request

```json
{
  "target_type": "DEVICE",
  "target_id": "device-laptop-01",
  "intent_type": "PRIORITIZE_INTERACTIVE",
  "duration_seconds": 3600,
  "reason": "Work call"
}
```

The backend must validate target ownership, maximum duration, fairness and allowed intent types. Expiry must be enforced server-side, not only by a frontend timer.

## 9. WebSocket

Suggested endpoint: `/api/v1/ws/experiments/{experiment_id}`.

Event envelope:

```json
{
  "event_id": "evt-0001",
  "experiment_id": "exp-001",
  "timestamp": "2026-01-01T00:00:05Z",
  "data_mode": "SIMULATION",
  "type": "metrics.snapshot",
  "sequence": 1,
  "payload": {}
}
```

Suggested event types:
- `experiment.status`
- `flow.detected`
- `flow.updated`
- `flow.classified`
- `capacity.updated`
- `congestion.predicted`
- `policy.evaluating`
- `policy.proposed`
- `policy.rejected`
- `policy.applied`
- `enforcement.verified`
- `metrics.snapshot`
- `verification.completed`
- `rollback.started`
- `rollback.completed`
- `watchdog.alert`
- `experiment.completed`
- `system.error`

Use bounded queues, event sequence numbers and reconnect/resync behaviour. The UI should handle lost events by requesting a fresh snapshot.

## 10. Metrics snapshot

```json
{
  "timestamp": "2026-01-01T00:00:05Z",
  "data_mode": "SIMULATION",
  "download_mbps": null,
  "upload_mbps": null,
  "usable_capacity_mbps": null,
  "rtt_ms": null,
  "added_delay_ms": null,
  "jitter_ms": null,
  "packet_loss_percent": null,
  "queue_packets": null,
  "queue_occupancy_percent": null,
  "active_flows": 0,
  "congestion_state": "UNKNOWN",
  "congestion_prediction": "UNAVAILABLE",
  "policy_id": null
}
```

Use `null` when a value is not measured. Never replace unavailable metrics with attractive example values.
