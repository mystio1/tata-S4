# AQoSE — Prior Work Reference and QoSense Improvement Opportunities

## Status: same team, not a competitor

AQoSE was previously tracked as external competitor work with a strict
no-copy rule. That rule is **lifted**: AQoSE is this team's own earlier
prototype, published under a different repository name/account. Its source,
datasets, feature schema, trained artifacts and configuration may be reused,
adapted or ported into QoSense directly, same as any other first-party code
the team owns.

## Reference

AQoSE — Adaptive Quality of Service Engine:
https://github.com/shreyashsri79/AQoSE-Adaptive-Quality-of-Service-Engine-

Review the current repository contents before relying on these notes; they
were compiled from a single README fetch and have not been independently
verified against the full file tree.

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

Verify specifics against the current repository sources before relying on them in documentation. The no-copy restriction no longer applies (own prior work); reuse of source, diagrams, training data and configurations is permitted.

## QoSense improvement priorities

- Impact-aware deterministic policy candidate scoring with hard fairness/safety constraints.
- Calibrated Model 1 class probabilities and unknown handling.
- Model 2 predictive lead-time validation and ablation.
- Independent watchdog and safe recovery.
- Transactional Linux policy application with read-back.
- Reproducible four-mode experiment matrix.
- Clearly explained decision trace in the dashboard.
