# Competitor Study — AQoSE and QoSense Improvement Opportunities

## Reference

AQoSE — Adaptive Quality of Service Engine:
https://github.com/shreyashsri79/AQoSE-Adaptive-Quality-of-Service-Engine-

Review the current repository contents before relying on these notes. Repository claims and results are developer-reported and should not be treated as independently verified.

## Publicly documented AQoSE design elements to investigate

- LightGBM-based broad traffic classification using packet/header-derived features.
- Class probabilities and calibration.
- CAKE DiffServ queue treatment.
- Expected-damage / impact-aware selection of a queue lane based on classification probabilities and the cost of misclassification.
- Active capacity/rate control using delay and rate observations.
- Safety shield and bounded policy updates.
- Transactional application and state read-back.
- Independent watchdog and rollback.
- Automated namespace-based experiments and repeated runs.

## What QoSense should learn from it

1. Do not let a classifier's highest-probability label blindly determine queue treatment. Consider the cost of being wrong and the consequences for interactive traffic and bulk progress.
2. Calibrate probabilities if the policy uses probability thresholds.
3. Add a safety guard between policy decision and network enforcement.
4. Snapshot, apply, read back and restore if state does not match.
5. Use an independent watchdog for controller failure.
6. Build repeatable experiments and publish configuration/seeds/logs.
7. Benchmark HTB + FQ-CoDel against CAKE rather than claiming one is universally better.

## Potential QoSense differentiator

QoSense proposes a second LightGBM model for future congestion-risk prediction. This is only a differentiator if it predicts a measurable future event with useful lead time and improves QoS beyond the classifier-only/reactive baseline. The model must not simply relabel current high RTT or queue occupancy as future prediction.

## Required comparison method

For each potential feature, verify it in current source code/docs and classify it:
- Present and implemented in AQoSE.
- Present in QoSense design.
- Planned but not implemented in QoSense.
- Not found / not established.
- Recommended QoSense action: adopt, adapt, benchmark or reject.

Do not make claims about competitor internals without inspecting current repository sources. Do not copy AQoSE source code, diagrams, training data or numerical configurations. Check its license before any code reuse.

## QoSense improvement priorities

- Impact-aware deterministic policy candidate scoring with hard fairness/safety constraints.
- Calibrated Model 1 class probabilities and unknown handling.
- Model 2 predictive lead-time validation and ablation.
- Independent watchdog and safe recovery.
- Transactional Linux policy application with read-back.
- Reproducible four-mode experiment matrix.
- Clearly explained decision trace in the dashboard.
