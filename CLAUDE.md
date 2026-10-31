# QoSense — Claude Code Project Instructions

You are the senior implementation engineer for **QoSense**, an adaptive, intent-aware home Quality of Service (QoS) system. Build a working, testable engineering prototype—not a static mockup.

## Read these files before coding

1. `START_HERE.md` — the first task and expected working method.
2. `docs/BUILD_SPEC.md` — complete product and engineering requirements.
3. `docs/ARCHITECTURE.md` — detailed module-by-module and packet-flow architecture.
4. `docs/ML_SPEC.md` — the two LightGBM models, data design, evaluation and safeguards.
5. `docs/API_CONTRACT.md` — frontend/backend/lab-agent interfaces and event formats.
6. `docs/UI_SPEC.md` — visual system, pages, 3D scene and event-driven animations.
7. `docs/SAFETY_SECURITY.md` — security and Linux network-control boundaries.
8. `docs/IMPLEMENTATION_PLAN.md` — implementation order and definition of done.
9. `docs/COMPETITOR_STUDY.md` — notes on AQoSE, the team's own earlier prototype; safe to reuse directly.

Reference files are in `references/`:
- `Case_Study_3_Original.pdf` is the authoritative problem statement.
- `QoSense_V3_Submission.pdf` records the team's current proposal.
- `QoSense_Deep_Teal_Bronze_UI_Reference.png` is the approved visual direction, not a source of valid telemetry values.

## Non-negotiable engineering rules

- Build the complete intended architecture around **two separately trained LightGBM models**:
  - Model 1: broad traffic-behaviour classification (multiclass).
  - Model 2: future congestion-event prediction (binary classification).
- Keep capacity estimation, policy selection, enforcement and verification deterministic and observable. Do not add ML models without a measurable reason.
- Model 2 is a proposed final capability. Implement and validate it as a distinct phase. Do not mark it operational until it passes its tests.
- Never invent live metrics, model predictions, confidence values, policy decisions or performance improvements. The UI must clearly distinguish `LIVE`, `SIMULATION` and `REPLAY` data modes.
- Do not show a successful policy application until the Ubuntu lab agent has applied the named configuration and verified the actual Linux state.
- Do not allow the browser, public API or Render service to run arbitrary shell commands or submit arbitrary `tc` arguments.
- Only predefined, validated scenarios and policy profiles may be executed by the privileged Ubuntu lab agent.
- Linux network enforcement must run on a suitably configured Ubuntu host/VM with the required network-administration capability. Do not assume an ordinary Render web service can create namespaces or change host traffic control.
- Use the lab-agent boundary described in `docs/SAFETY_SECURITY.md`.
- AQoSE is the team's own prior prototype (same authors, different repository name). Its code, datasets, feature schema and configuration may be reused and adapted directly; it is not third-party or competitor work.
- Do not claim application identification is guaranteed from encrypted flow metadata. Use broad classes and an `unknown` class.
- Distinguish RTT from one-way delay. Define jitter consistently. Calculate fairness only across comparable flows.
- Use full forms for abbreviations at first use in user-facing documentation. Keep the language simple and direct.
- Do not over-design the UI. 3D motion must explain actual system events and must not obstruct data readability.

## Preferred stack

- Frontend: React, TypeScript and Vite.
- 3D: Three.js with React Three Fiber and Drei; use GSAP only where timeline sequencing is needed.
- Charts: Apache ECharts or Recharts (choose one and use it consistently).
- Backend API and orchestration: Python + FastAPI.
- Machine learning: LightGBM + scikit-learn tooling.
- Lab: Ubuntu Linux, network namespaces, `tc`, `netem`, `iperf3`, `ping` or timestamped UDP probes, and queue statistics.
- State/logging: start with SQLite for local experiments; keep the repository structured so PostgreSQL can be introduced if necessary.
- Frontend hosting: Render Static Site is acceptable.
- General API hosting: Render Web Service may host non-privileged API functions if required.
- Privileged network control: separate Ubuntu VM/lab agent only.

Before adding dependencies, check compatibility and current documentation. Pin versions in lockfiles. Do not use alpha packages for the core application without a clear reason.

## Working method

1. Inspect the existing workspace before creating or replacing files. Preserve useful work.
2. Read all project documents listed above.
3. Produce a concise implementation plan and note any design contradictions before coding.
4. Build in vertical slices. The first slice must work end-to-end in a clearly labelled local simulation mode.
5. Keep frontend, backend, ML and privileged lab-agent code separated.
6. Add unit tests for calculations, API validation, policy safety and model inference.
7. Add integration tests for experiment lifecycle and policy application using a mocked lab agent before using real `tc`.
8. Add a real Ubuntu lab mode only after the safe mock integration works.
9. Run formatting, linting, type checking and tests after each major phase.
10. Maintain `docs/IMPLEMENTATION_STATUS.md` with `Implemented`, `In progress`, `Planned` and `Blocked` status. Update it honestly.
11. Do not stop after generating a plan. Begin implementing the first vertical slice unless a genuine blocker requires a decision.
12. At the end of each phase, report files changed, commands run, test results, remaining limitations and the next phase.

## Initial implementation priorities

Build in this order:

1. Repository scaffold, typed domain models and configuration.
2. Deep Teal & Bronze dashboard with accurate information hierarchy and a functioning 3D topology using clearly labelled simulation data.
3. FastAPI API contracts and WebSocket event stream.
4. Experiment runner with repeatable scenario state and event generation.
5. Network metrics collection and baseline mode.
6. Deterministic adaptive QoS baseline and safe policy engine.
7. Model 1 training/inference pipeline and integration.
8. Ubuntu lab-agent and safe `tc` enforcement.
9. Model 2 dataset, training, calibration, prediction and ablation evaluation.
10. Four-mode comparison, reports, hardening and deployment.

Do not show the dashboard's illustrative values as live data. Use neutral placeholders or explicit `SIMULATION` labels until the data source is connected.
