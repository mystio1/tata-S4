# QoSense Experiment Plan

## 1. Principles

- All test network conditions are controlled laboratory emulations unless explicitly stated otherwise.
- The same traffic scenario, seed, link settings and duration should be used across modes.
- Repeated experiments should be grouped by scenario/seed.
- Keep raw measurements, summary metrics and configuration files.
- Report median and uncertainty (for example, interquartile range or a justified confidence interval).
- Never claim one mode wins unless the measured data supports it.
- Separate the contribution of each ML model using ablation.

## 2. Scenarios

### A. ISO download during a work call

- Create one interactive-like flow and one bulk-transfer flow.
- Start the bulk transfer at a configured time.
- Observe queue growth, RTT, jitter, loss, call-probe performance and bulk goodput.
- Compare all four QoS modes.

### B. Bottleneck drop

- Begin with a configured 100 Mbps bottleneck.
- At a defined time, reduce the bottleneck to 20 Mbps using the lab impairment tool.
- Measure capacity-estimator response, congestion-prediction lead time, policy-application time and QoS-recovery time.
- Clearly distinguish the hidden configured bottleneck from the estimator's observed output.

### C. Three streams plus gaming

- Create three comparable streaming-like flows and one gaming-like flow.
- Measure per-stream goodput, gaming probe RTT/jitter/loss and Jain's fairness index across the three comparable streams.
- Check that minimum bulk/service guarantees are respected where configured.

### D. Unseen condition stress test

- Hold out selected bandwidth/delay/loss/traffic combinations from training.
- Change ports and flow identifiers.
- Evaluate classification generalization and Model 2 prediction lead time.

## 3. Modes

1. `NO_QOS`: no special traffic handling.
2. `TRADITIONAL_ADAPTIVE`: deterministic classification with common capacity estimator, policy constraints, enforcement and verification.
3. `CLASSIFIER_ONLY`: Model 1 classification with other comparable components unchanged.
4. `FULL_QOSENSE`: Model 1 plus Model 2 congestion prediction, with other comparable components unchanged.

Optional benchmark: CAKE-based configuration versus HTB + FQ-CoDel. Keep it as a queue-management benchmark, not a model comparison.

## 4. Metric definitions

- RTT: probe round-trip time. Do not call it one-way call latency.
- Added delay under load: use a consistent loaded RTT statistic minus idle baseline RTT.
- Jitter: use one documented packet-delay-variation method consistently.
- Packet loss: lost probes/packets divided by sent probes/packets for the same flow/class and interval.
- Throughput: measured transfer rate. Goodput should exclude protocol overhead/retransmitted bytes if the measurement source supports it.
- Queue depth: read from the Linux queue discipline when available; otherwise label simulated queue state.
- Fairness: Jain's index across comparable streams only.
- Bulk progress: goodput and zero-progress windows.
- Policy application time: time from accepted policy request to verified Linux state.
- QoS recovery time: time until measured interactive metrics recover and remain stable for a defined period.
- Model 1: macro-F1, per-class precision/recall, confusion matrix, unknown rate, calibration.
- Model 2: precision/recall, PR-AUC where suitable, false alarms, missed events, lead time and calibration.
- Overhead: inference latency, controller CPU and memory on named hardware.

## 5. Data and reproducibility

Each run stores:
- Experiment ID.
- Scenario ID.
- Mode.
- Seed.
- Traffic-generator versions/configuration.
- Bottleneck and impairment parameters.
- Start/end timestamps.
- Model versions.
- Feature-schema versions.
- Policy profile/version.
- Raw metric samples.
- Event/decision log.
- Summary report.

## 6. Model ablations

- Mode 2 vs Mode 3 estimates the contribution of ML traffic classification.
- Mode 3 vs Mode 4 estimates the incremental contribution of the congestion predictor.
- Evaluate both predictive metrics and downstream QoS outcomes. A better classifier score alone does not prove better QoS.
