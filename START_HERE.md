# Start building QoSense with Claude Code

## How to use this pack

1. Extract this entire folder into a new project directory, for example `qosense/`.
2. Open that directory in Visual Studio Code.
3. Open Claude Code in that directory.
4. Claude Code should automatically use the root `CLAUDE.md`. If it does not, ask it to read `CLAUDE.md` first.
5. Give Claude Code the instruction below.

## First instruction to Claude Code

> Read `CLAUDE.md` and every document in `docs/` before making changes. Inspect the existing workspace and the supplied reference files. Then start implementing QoSense according to the specifications. Do not only write a plan or create a static UI mockup. First create a clean monorepo scaffold and implement a working vertical slice: React + TypeScript + Vite dashboard in the Deep Teal & Bronze theme, a central 3D home-network topology, event-driven packet/queue/jitter/priority animations, a FastAPI backend, typed API contracts, and an experiment simulation mode whose values are clearly labelled `SIMULATION`. Make the UI functional: scenario selection and Start/Stop Experiment must change backend state and stream events to the browser. Build real components with reusable code, tests and error handling. Do not claim the Linux enforcement or either ML model is live until it is implemented and validated. After the first vertical slice works, continue through the phases in `docs/IMPLEMENTATION_PLAN.md`. Keep `docs/IMPLEMENTATION_STATUS.md` updated. Do not ask me to make routine design decisions; use the supplied specifications. Ask only if an actual blocker or destructive choice requires my input.

## Suggested terminal setup

Use a current stable Node.js LTS and Python 3.11 or a currently supported Python version confirmed against the selected LightGBM and FastAPI dependencies. Pin exact versions after checking compatibility.

Typical commands after Claude Code creates the scaffold will be similar to:

```bash
# Frontend
cd frontend
npm install
npm run dev

# Backend, in a separate terminal
cd backend
python -m venv .venv
# Activate the environment, then install the pinned requirements
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Exact commands may change based on the generated project structure; keep them documented in the root README.

## Important deployment note

The public dashboard and ordinary API can be deployed separately from the Linux network lab. Actual Linux network namespaces and `tc` enforcement must run on a controlled Ubuntu VM or host with the necessary network-administration permissions. Do not try to solve this by exposing shell access through a public Render endpoint.
