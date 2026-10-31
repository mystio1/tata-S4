# QoSense Implementation Plan

Build in vertical slices. Keep each phase runnable and tested. Do not build a static dashboard first and leave the backend disconnected.

## Phase 0 — Workspace inspection and architecture confirmation

Tasks:
- Inspect the workspace and preserve existing files.
- Confirm Node/Python versions and target Ubuntu environment.
- Read all supplied references.
- Create monorepo folders and shared schemas.
- Create `.env.example`, `.gitignore`, format/lint/test commands and README.

Done when:
- Repository structure is documented.
- Frontend and backend can start independently.
- Shared API types/schema are defined.
- No real network operations are enabled by default.

## Phase 1 — Functional simulation vertical slice

Tasks:
- Build React/TypeScript/Vite shell in Deep Teal & Bronze.
- Build the main dashboard and central 3D topology.
- Build a deterministic scenario simulator.
- Build FastAPI experiment lifecycle endpoints.
- Stream typed events over WebSocket.
- Connect Start/Stop/Reset to backend-owned experiment state.
- Animate packet density, queue depth, RTT/jitter timelines, Model 1/Model 2 states, priority evaluation and simulated enforcement.
- Label every value and event `SIMULATION`.

Done when:
- `iso_during_call` can be started, observed and stopped from the browser.
- All charts and 3D animations are derived from the same simulator state.
- A user can see why the simulator selected a named policy.
- No fake `LIVE` badge or fabricated model confidence exists.
- Unit and integration tests pass.

## Phase 2 — Baseline network lab and metrics

Tasks:
- Create isolated Ubuntu namespaces, veth pairs and a bottleneck topology.
- Add reproducible `netem` conditions and traffic generators.
- Collect real baseline RTT, jitter, loss, throughput and queue statistics.
- Create a reset script and an experiment runner.
- Store experiment configuration, seeds, timestamps and measurements.

Done when:
- Lab reset returns to a known state.
- Traffic traverses the intended namespace topology.
- Measurement units and definitions are documented.
- One baseline experiment produces a reproducible report.

## Phase 3 — Deterministic adaptive QoS baseline

Tasks:
- Implement M3A usable-capacity estimation with data-quality state.
- Implement M4 deterministic policy state machine, hysteresis and fairness constraints.
- Implement M5 upload shaping and download IFB shaping in the isolated lab.
- Implement safety validation, configuration snapshot, apply/read-back and rollback.
- Add independent watchdog/recovery profile.
- Integrate live agent events into dashboard.

Done when:
- Mode 2 runs on the isolated Ubuntu lab.
- Actual `tc` state is read back and logged.
- A failed/invalid update is rejected or rolled back.
- No default/production interface is modified.

## Phase 4 — Model 1 traffic classifier

Tasks:
- Finalize feature schema and supported classes.
- Generate labelled controlled traffic data.
- Split by experiment run/scenario.
- Train LightGBM multiclass model.
- Evaluate macro-F1/per-class precision and recall/confusion matrix.
- Calibrate probabilities if confidence thresholds will drive control.
- Integrate inference into M2.
- Compare deterministic classifier vs ML classifier with same policy/enforcement pipeline.

Done when:
- Model artifact and schema are versioned.
- Held-out evaluation is reproducible.
- Unknown fallback works.
- Model version and inference events reach the UI.
- Mode 3 runs end-to-end.

## Phase 5 — Model 2 congestion predictor

Tasks:
- Define measurable congestion event and future horizon.
- Create time-window dataset with labels strictly in the future.
- Prevent future-data leakage and split by run/time.
- Train LightGBM binary classifier.
- Evaluate precision, recall, PR-AUC where appropriate, calibration, false alarms, missed events and lead time.
- Integrate as advisory input to M4.
- Ensure it cannot bypass the safety guard.
- Compare Mode 3 with Mode 4.

Done when:
- Model 2 provides predictions only when ready and input window is fresh.
- Future target leakage checks pass.
- Mode 4 ablation report demonstrates whether it adds value.
- UI distinguishes predicted risk from current measured congestion.

## Phase 6 — Full dashboard, experiments and reports

Tasks:
- Complete all pages in `UI_SPEC.md`.
- Complete 3D event-driven animations.
- Add four-mode experiments and optional CAKE benchmark.
- Add scenario seeds/repeated runs and exportable reports.
- Add decision trace, user-intent lease and failure/recovery UI.
- Add replay mode for recorded experiments, clearly labelled.

Done when:
- Each major visual event is tied to an API event or measurement.
- All controls work and have loading/error/confirmation states.
- Charts use consistent metric definitions.
- No illustrative values are represented as measured results.

## Phase 7 — Deployment and hardening

Tasks:
- Deploy frontend as Render Static Site or equivalent.
- Deploy non-privileged API if needed.
- Run privileged lab agent on controlled Ubuntu VM.
- Secure API-to-agent communication.
- Add authentication, strict CORS, rate limits and audit trail.
- Add health checks, reconnect/resync and graceful failure.
- Prepare demo mode and offline replay backup.
- Write exact setup/deployment guide.

Done when:
- Public dashboard works from a separate network.
- LIVE mode only appears when the Ubuntu agent is connected and measurements are fresh.
- Simulation/replay are visibly labelled.
- Privileged actions are allow-listed and authenticated.
- Full demo can be reset and repeated.

## Cross-phase quality gates

At each phase:
- Run formatting and linting.
- Run TypeScript checking.
- Run unit and integration tests.
- Add error handling for all external calls.
- Check responsive layout and keyboard access.
- Update `docs/IMPLEMENTATION_STATUS.md`.
- Record unresolved risks.

## Required scripts (names can be adapted)

- `dev`: start frontend/backend development services.
- `test`: run tests.
- `lint`: run linters.
- `typecheck`: validate TypeScript.
- `demo:simulation`: start local simulation scenario.
- `lab:reset`: reset the isolated Ubuntu lab.
- `lab:demo:iso`: run the actual lab ISO-during-call scenario.
- `report`: export an experiment report.

Never make `lab:reset` or an enforcing command target a host/default interface without explicit validated configuration.
