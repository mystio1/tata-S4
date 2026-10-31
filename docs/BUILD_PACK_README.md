# QoSense Claude Code Build Pack

This pack gives Claude Code the product requirements, detailed architecture, machine-learning specifications, API contracts, user-interface and animation design, safety requirements, implementation phases and visual reference needed to begin building QoSense.

## Contents

- `CLAUDE.md` — persistent instructions Claude Code should follow.
- `START_HERE.md` — exact first instruction to start implementation.
- `docs/BUILD_SPEC.md` — complete product scope and acceptance criteria.
- `docs/ARCHITECTURE.md` — detailed operational flow, branches and packet paths.
- `docs/ML_SPEC.md` — Model 1, Model 2, datasets and evaluation.
- `docs/API_CONTRACT.md` — API and WebSocket data contracts.
- `docs/UI_SPEC.md` — Deep Teal & Bronze visual system and event-driven 3D animation.
- `docs/SAFETY_SECURITY.md` — safety, security and privileged lab-agent boundary.
- `docs/IMPLEMENTATION_PLAN.md` — phased build order and definition of done.
- `docs/COMPETITOR_STUDY.md` — AQoSE lessons to investigate and improve upon.
- `references/` — original case study, current QoSense V3 PDF and selected UI reference image.

## Important project decisions

- Final intended system: two separate LightGBM models (traffic classification and future congestion-risk prediction).
- Non-ML components: capacity estimation, deterministic policy engine, Linux enforcement, verification and rollback.
- UI palette: Deep Teal & Bronze.
- Frontend: React + TypeScript + Vite + Three.js / React Three Fiber.
- Backend: FastAPI.
- Real network enforcement: separate Ubuntu lab VM/agent, not a standard public web container.
- Simulation, replay and live modes must always be identified honestly.

This is an implementation specification, not a prebuilt application. Claude Code should use it to create and implement the application in the project workspace.
