# QoSense — Explicit Round 1 / Round 2 Delivery Boundary

This document is authoritative for implementation prioritization. The architecture may show the complete intended final system, but the public submission and implementation status must clearly distinguish what is working now from what is planned.

## Guiding rule

- **Round 1 must demonstrate one real, end-to-end, measurable QoS control path.** A static dashboard or a simulation-only UI is not sufficient evidence of real enforcement.
- **Round 2 expands predictive intelligence, robustness and evaluation.** Do not postpone safety mechanisms needed by any live control feature.
- The full architecture may display both LightGBM models from the beginning, but Model 2 must be visibly labelled `PLANNED FOR ROUND 2` until it is trained, tested and integrated.
- Never describe a planned feature as implemented.

## Round 1 — Minimum credible working prototype

### What must be implemented

1. **Frontend and dashboard**
   - React, TypeScript and Vite.
   - Deep Teal & Bronze design.
   - 3D topology and event-driven visualizations.
   - Start, stop and reset experiment controls connected to backend state.
   - Data mode clearly labelled: `SIMULATION`, `LIVE` or `REPLAY`.
   - A functioning decision trace and experiment result view.

2. **Backend and experiment orchestration**
   - FastAPI service.
   - Typed REST and WebSocket contracts.
   - One reproducible primary scenario: large ISO-like bulk transfer during an interactive-like call.
   - Experiment IDs, run configuration, seeds, timestamps, event logs and report export.

3. **M1 — Flow observation**
   - Ubuntu namespace testbed and a documented observation adapter.
   - Flow aggregation and feature windows (not model inference on every packet).
   - Candidate flow features: packet sizes, arrival intervals, direction, duration, byte rate, packet rate and burst statistics.
   - No payload capture/decryption.

4. **M3A — Usable-capacity estimator**
   - Deterministic delivery-rate and delay-trend estimator.
   - EWMA smoothing only after the parameter and behaviour are documented.
   - Freshness/data-quality flag and conservative fallback.
   - Never read the hidden `netem` configured bottleneck as the estimator output.

5. **M4 — Deterministic adaptive policy baseline**
   - A deterministic state machine with hysteresis, cooldown and named candidate actions.
   - Implement a simple, explainable impact-aware candidate score if it can be validated: interactive-delay risk, bulk-progress risk, fairness penalty and policy-churn penalty.
   - Hard safety/fairness constraints override every score.
   - AQoSE is the team's own prior work; its damage table/weights may be reused directly or adapted as a starting point, with our own version recorded.

6. **Safety Guard and M5 — Real Linux enforcement**
   - Isolated Ubuntu network namespaces, virtual Ethernet pairs and a controlled bottleneck.
   - Actual upload shaping and download shaping through IFB.
   - Primary proposed configuration: HTB classes with FQ-CoDel queues, subject to validation on the chosen kernel/iproute2 version.
   - nftables/connection marks only where needed and verified.
   - Strict allow-listed lab-agent operations; no browser-to-shell command path.
   - Snapshot current qdisc/class/filter state, apply a named profile, read back state, and roll back if verification fails.
   - Independent basic watchdog and a known-safe recovery profile before enabling live enforcement.

7. **M6 — Verification and baseline evidence**
   - Mode 1: No QoS.
   - Mode 2: Traditional deterministic adaptive QoS.
   - Mode 3: LightGBM classifier with the same capacity estimator, policy engine, enforcer and verifier as Mode 2.
   - Measure RTT/added delay, jitter, loss, throughput/goodput, queue depth, bulk progress, policy-application time and CPU overhead.
   - Run repeated experiments with the same scenario settings and seeds.
   - Train and integrate Model 1 (LightGBM multiclass traffic classifier), with run-based held-out evaluation, macro-F1, per-class precision/recall, confusion matrix and probability calibration if probabilities drive policy.

### Round 1 acceptance gate

Round 1 is ready only when all of the following evidence exists:

- A public dashboard URL opens from a separate network.
- A public GitHub repository contains reproducible setup instructions and source code.
- `SIMULATION` mode works and is labelled honestly.
- The Ubuntu lab can be reset to a known state.
- The primary scenario sends traffic through the intended virtual topology.
- Mode 1 and Mode 2 run in the isolated Linux lab.
- Mode 3 uses an actually trained and loaded Model 1 artifact; if Model 1 is not ready, show it as unavailable and do not claim the mode is operational.
- Linux enforcement is verified by reading back actual qdisc/class/filter state.
- Safety guard, snapshot, rollback and watchdog recovery have tests.
- Before/after and three-mode reports are based on actual repeated measurements.
- README, architecture, limitations and demo steps are complete.

A hosted web UI alone does not satisfy this acceptance gate. If the public backend cannot safely reach the Ubuntu lab, provide a clearly labelled replay/demo path and disclose that live enforcement is available only in the controlled lab.

## Round 2 — Complete predictive QoSense prototype

### What should be added or strengthened

1. **M3B — Model 2 congestion-risk predictor**
   - A separately trained LightGBM binary classifier.
   - Define a future congestion event and prediction horizon before training.
   - Use time-windowed inputs available at or before time `t` to predict an event in `(t, t+H]`.
   - Prevent future-data leakage; split by experiment run and chronology.
   - Evaluate precision, recall, PR-AUC where suitable, false alarms, missed events, calibration and prediction lead time.
   - Model output is advisory only; it cannot bypass M4 or the Safety Guard.

2. **Mode 4 — Complete QoSense**
   - Model 1 traffic classification plus Model 2 congestion prediction.
   - Compare Mode 3 versus Mode 4 to isolate the incremental value of Model 2.
   - Show current measured congestion separately from predicted future congestion.

3. **Improve impact-aware policy selection**
   - Tune/validate the deterministic candidate-scoring method using held-out experiments.
   - Investigate a measured impact/damage matrix inspired by the general problem AQoSE addresses, but design our own method and data.
   - Evaluate expected interactive harm, bulk progress, fairness, safety and policy churn.
   - Hard constraints always take precedence over score.

4. **Expanded safety and reliability**
   - Fault injection for controller/model/agent failure.
   - Watchdog heartbeat-loss tests.
   - Partial-apply and failed-read-back tests.
   - Rollback verification and recovery-time measurements.
   - Stale measurement and stale prediction handling.

5. **Expanded evaluation**
   - Three-TVs-plus-gaming scenario.
   - Bandwidth drop from 100 Mbps to 20 Mbps.
   - Held-out combinations of bandwidth, delay, loss, traffic mix and ports.
   - Additional randomized scenarios and seeds.
   - Model ablation: Mode 2 vs 3 vs 4.
   - Compare HTB + FQ-CoDel against CAKE as a queue-management benchmark if time and testbed allow. Keep this separate from the ML comparison.
   - Report medians and uncertainty intervals; do not claim superiority unless measurements support it.

6. **Broader deployment hardening**
   - IPv6 validation where supported.
   - Authentication, API-to-agent encryption, strict authorization and audit logs.
   - Reconnect/resynchronization, rate limits, resource monitoring and deployment guide.
   - Optional Raspberry Pi/OpenWrt or physical-router investigation only if hardware is available; do not claim it works without testing.

### Round 2 acceptance gate

- Model 2 has a versioned artifact and reproducible held-out evaluation.
- Future-event label and prediction horizon are documented.
- No future-data leakage is detected by tests/review.
- Mode 4 works end-to-end and its incremental effect over Mode 3 is measured.
- The controller safely ignores stale or unavailable predictions.
- Expanded scenarios and robustness tests are reproducible.
- CAKE benchmark results are included if benchmarked; otherwise state it as future work.
- Complete architecture and results report distinguish measured outcomes from targets.

## Mapping implementation phases to competition rounds

| Existing implementation phase | Main contents | Round allocation |
|---|---|---|
| Phase 0 | Workspace and schemas | Before / Round 1 foundation |
| Phase 1 | Functional simulation vertical slice | Round 1 |
| Phase 2 | Ubuntu baseline network lab and metrics | Round 1 |
| Phase 3 | Deterministic QoS, Safety Guard, live enforcement, rollback and watchdog | Round 1 |
| Phase 4 | Model 1 traffic classifier | Round 1 |
| Phase 5 | Model 2 future congestion predictor | Round 2 |
| Phase 6 | Full four-mode comparison, extended scenarios, CAKE benchmark and detailed reports | Primarily Round 2; retain basic dashboard/reporting in Round 1 |
| Phase 7 | Deployment and hardening | Basic public deployment in Round 1; advanced hardening in Round 2 |

## Features learned from AQoSE and how we use them

| AQoSE-documented idea to investigate | QoSense decision | Round |
|---|---|---|
| LightGBM traffic classification | Use our own feature schema, labels and dataset; do not copy their feature set blindly | R1 |
| Probability calibration | Add validation-set calibration if confidence drives policy; report calibration | R1 basic, improve R2 |
| Expected-damage / impact-aware queue selection | Implement our own transparent candidate-impact scoring with hard constraints; no copied weights/table | R1 basic, tune R2 |
| Active capacity/rate control | Use observable delivery-rate and delay evidence with safe smoothing/fallback; compare methods | R1 |
| CAKE DiffServ treatment | Benchmark CAKE against our HTB + FQ-CoDel configuration; choose based on evidence | R2 |
| Safety shield / bounded changes | Implement Safety Guard and bounded changes before live enforcement | R1 |
| Transactional apply and read-back | Snapshot, apply named profile, read back, restore on mismatch | R1 |
| Independent watchdog | Basic independent safe recovery before live enforcement; fault-injection expansion later | R1 basic, R2 strengthen |
| Automated namespace-based experiments | Build resettable lab and experiment runner | R1 basic, R2 expanded |
| Repeated runs and reports | Repeat primary scenario in R1; expand runs, seeds and unseen scenarios in R2 | R1/R2 |
| User-intent lease | Implement server-side expiry, fairness checks and logging | R1 basic; R2 extend |

## Explicit exclusions

- Do not claim QoSense is better based on having two ML models.
- Do not defer essential safety until Round 2 if live enforcement is enabled in Round 1.
- Do not claim Model 2 is implemented in Round 1 unless it is trained, validated and integrated.
- Do not call a simulated policy application `LIVE` or `APPLIED`.
