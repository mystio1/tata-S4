# QoSense Product and Build Specification

## 1. Product summary

QoSense is an adaptive, intent-aware home Quality of Service (QoS) engine. It observes network-flow behaviour without reading private application content, predicts broad traffic categories, estimates usable bandwidth, predicts future congestion risk, selects a safe and fair policy, applies it through Linux traffic control, and verifies whether the result improved network performance.

The system must protect delay-sensitive activities such as video calls and online gaming while allowing streaming, browsing and large downloads to continue making progress.

## 2. Core user story

A home connection is carrying a video call, an online game, three streaming devices and a large file download. The download increases queueing delay and causes the call and game to lag. QoSense observes the flows, classifies broad traffic behaviour, estimates usable bandwidth, predicts whether congestion is likely to develop, and gives the policy engine enough information to choose a bounded policy. Linux applies the policy. QoSense measures the outcome and either keeps the policy or safely restores the previous configuration.

This scenario is the primary demonstrator. Other scenarios include a sudden bandwidth drop from a configured 100 Mbps bottleneck to 20 Mbps, and three television streams competing with gaming traffic. These are laboratory emulations, not real ISP measurements.

## 3. Scope and truth labels

The product has three data modes:

- `SIMULATION`: reproducible generated events and metrics from the local experiment simulator.
- `LIVE`: measurements and enforcement state received from the configured Ubuntu lab agent.
- `REPLAY`: previously recorded experiment events replayed for review.

Every page, chart and animation must show the active mode. Never label simulation or replay data as live.

The complete intended architecture contains two separately trained LightGBM models. The implementation must expose each model's actual state (`NOT_CONFIGURED`, `TRAINING`, `READY`, `FAILED`, `DISABLED`) and must not show an operational badge until the model can perform inference and pass tests.

## 4. Product modules

### M1 — Flow Observer

Collect flow-level metadata from the controlled Linux testbed. Candidate features include packet-size summaries, packet arrival intervals, direction, flow duration, bytes per second, packets per second, burst statistics and permitted protocol/header metadata. Do not decrypt or persist application payloads. Prefer a flow/time-window abstraction; do not run a model on every packet.

Outputs: flow identifier, start/end time, direction, protocol metadata, feature-window statistics, byte/packet counters and capture-health status.

### M2 — LightGBM Traffic Classifier

A supervised multiclass LightGBM model predicts broad traffic-behaviour classes. Initial class candidates: `INTERACTIVE`, `GAMING_LIKE`, `STREAMING_LIKE`, `BULK`, `WEB_VOICE`, `UNKNOWN`. Final classes must match the labels that the controlled data-generation system can reliably produce.

Outputs: predicted class, class probabilities, calibrated confidence if calibration has been implemented, model version, inference time and fallback reason. Low-confidence or unsupported flows become `UNKNOWN` and receive safe best-effort treatment.

### M3A — Usable Capacity Estimator

A deterministic estimator uses delivery-rate observations, loaded-versus-idle delay trends and a smoothing method such as Exponentially Weighted Moving Average (EWMA). It must expose data freshness and confidence/quality flags. Under low demand, available capacity may not be observable; retain a validated safe estimate or use a bounded probe only if configured. Never read the hidden simulator bottleneck setting and pass it off as an estimate.

Outputs: estimated usable rate, sample window, trend, quality flag, last update time and safe fallback rate.

### M3B — LightGBM Congestion-Risk Predictor

A separate supervised binary LightGBM model uses recent time-windowed measurements to predict whether a precisely defined congestion event will occur within a configurable future horizon. Features may include utilization, delivery-rate trend, RTT inflation, jitter, packet loss, queue depth/growth, active-flow count and burst/demand changes when observable.

Outputs: probability, risk band, prediction horizon, model version and inference time. Model 2 must be trained with time-aware data, with no future-data leakage. A high-risk prediction is advisory; it cannot independently authorize an aggressive policy change.

### M4 — Adaptive Policy Engine

A deterministic, testable state machine consumes:
- M2 traffic class and confidence;
- M3A usable-capacity estimate and quality flag;
- M3B congestion probability and horizon;
- current delay, jitter, loss and queue state;
- active user intent and expiry;
- previous policy;
- fairness and minimum-service constraints.

Candidate actions: `KEEP_CURRENT`, `PROTECT_INTERACTIVE`, `LIMIT_BULK`, `REBALANCE_CLASSES`, `CONSERVATIVE_SAFE`. Implement explicit action bounds, hysteresis, minimum hold time, cooldown and reason codes.

Investigate impact-aware policy selection. It may score candidate actions against interactive-delay risk, bulk-progress risk, fairness violations, congestion risk and policy churn. The scoring method must be documented, deterministic, tested and based on measurable or defensible quantities. Do not invent damage weights or show fictional scores.

### Safety Guard

Validate every candidate policy before M5. Reject stale measurements, unsupported classes, excessive rate changes, invalid allocations, expired intent and any action that breaks minimum-service or fairness constraints. A safety guard rejection must be visible in logs and UI.

### M5 — Linux QoS Enforcer

Primary proposed path: Linux traffic control with Hierarchical Token Bucket (HTB) classes and Fair Queuing Controlled Delay (FQ-CoDel) queues, plus nftables/connection marks as required. Use Intermediate Functional Block (IFB) redirection to shape download traffic on a virtual interface. Keep upload and download paths separate. Benchmark Common Applications Kept Enhanced (CAKE) as an alternative where practical; do not combine incompatible or redundant queue configurations without justification.

The privileged lab agent accepts only named, validated scenarios and policy profiles. It must never accept arbitrary shell commands or arbitrary `tc` arguments from the browser/API.

### M6 — Verifier, rollback and watchdog

Collect post-enforcement measurements. Compare against a defined baseline and check safety constraints. Keep the policy only when required metrics improve or remain within acceptable bounds. Otherwise restore the previous configuration and verify that restoration succeeded. A separate watchdog monitors the controller/agent heartbeat and can trigger a predefined known-safe recovery profile.

Record the full decision trace: input snapshot, model outputs, candidate actions, selected action, safety result, actual Linux state, measurement result and rollback outcome.

## 5. User-facing pages

1. **Network Overview** — 3D topology, active devices/flows, bandwidth, network health, current policy and active data mode.
2. **Live Traffic** — flow table, class, confidence, direction, rates, duration, queue and filtering.
3. **AI Intelligence** — separate cards for Model 1 and Model 2, model versions, readiness, feature summary, predictions and confidence/calibration status.
4. **QoS Controller** — capacity estimate, congestion signal, current policy, candidate action, reason, fairness constraints, temporary intent and policy history.
5. **Experiments** — scenario setup, four modes, start/stop/reset, repeat count, seed and experiment comparison.
6. **Performance** — RTT/added delay, jitter, loss, throughput/goodput, queue depth, fairness and adaptation/recovery time.
7. **Safety & Logs** — safety rejections, enforcement transactions, watchdog health, rollback history and audit events.
8. **Settings** — lab connection, thresholds, data mode, model versions and safe configuration. Destructive or privileged actions require confirmation and role checks.

## 6. Four experiment modes

- Mode 1: No special QoS; reference condition.
- Mode 2: Traditional adaptive QoS with deterministic traffic classification, the same capacity estimator, same policy constraints, same enforcement and same verification.
- Mode 3: LightGBM traffic classification with the same capacity estimator, policy engine, enforcement and verification as Mode 2.
- Mode 4: Model 1 plus LightGBM congestion prediction, with all other comparable components held constant.

Use the same traffic scenarios, network impairments, seeds and durations. Mode 2 vs Mode 3 isolates the classification contribution. Mode 3 vs Mode 4 isolates the incremental contribution of congestion prediction. Add a CAKE benchmark if feasible and clearly identify it as a queue-management comparison, not an AI model comparison.

## 7. Evaluation metrics

Define each metric in code and documentation before displaying it:
- RTT: round-trip time measured by probes; do not call it one-way application latency.
- Added delay under load: a documented loaded RTT statistic minus idle baseline RTT, using the same probe method.
- Jitter: choose and document one consistent packet-delay-variation method; do not interchange unrelated definitions.
- Packet loss: lost probe or traffic packets divided by sent packets for the defined flow/class and interval.
- Throughput: transferred bits per second; goodput excludes protocol overhead/retransmissions where measured.
- Queue depth/occupancy: actual qdisc or lab measurement when available; otherwise clearly labelled simulator metric.
- Fairness: Jain's fairness index across comparable flows with the same service entitlement, not across intentionally different priority classes.
- Bulk progress: goodput and count/duration of zero-progress windows.
- Adaptation time: define policy-application time separately from QoS-recovery/stability time.
- Model 1: macro-F1, per-class precision/recall, confusion matrix, unknown rate and probability calibration.
- Model 2: precision/recall, PR-AUC where suitable, false alarms, missed events, lead time and calibration.
- Overhead: model inference latency, controller CPU and overall resource use on the stated hardware.

Never invent experiment results. Targets must be explicitly labelled as targets until measured.

## 8. Acceptance criteria for the first end-to-end slice

The initial local vertical slice is complete only when:
- The frontend loads and shows the Deep Teal & Bronze theme.
- The central 3D network topology renders and labels devices and traffic categories.
- A user can select a scenario and start, stop and reset it.
- The FastAPI backend owns experiment state and emits typed events.
- Simulation events update packet density, queue occupancy, jitter/latency charts and priority-decision UI.
- The interface visibly labels all such values as `SIMULATION`.
- A decision trace explains the current policy selection.
- UI actions have working callbacks and loading/error states.
- Tests cover event validation, experiment lifecycle and basic calculations.

The initial slice does not count as real Linux enforcement or real ML inference. Those capabilities must be implemented and tested in later phases before being labelled `LIVE`.
