# QoSense

An adaptive, intent-aware home Quality of Service (QoS) engine.

QoSense observes network flow behaviour without reading application content,
estimates how much of the link is usable, selects a bounded and fair policy,
applies it, and then measures whether the result actually improved anything.
If it did not, it puts the previous configuration back.

**What this repository currently is.** A working engineering prototype with
two data modes. `SIMULATION` runs a deterministic simulator entirely in
process. `LIVE` runs the same controller against an isolated Linux testbed of
network namespaces, applying **real traffic control** and confirming every
change by reading the kernel's own state back.

**What it is not yet.** Neither machine-learning model is trained, so Modes 3
and 4 fall back to a safe `UNKNOWN` class and are marked degraded. Nothing in
the interface claims otherwise. See [Honest status](#honest-status).

---

## Quick start

Requires Node.js 20 or newer and Python 3.11 or newer. The versions this was
developed against are Node 22.17 and Python 3.12.

```bash
# Backend, in its own terminal
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows; use "source .venv/bin/activate" elsewhere
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend, in a second terminal
cd frontend
npm install
npm run dev
```

Then open the address Vite prints, normally <http://localhost:5173>.

With both set up, the root scripts run them together:

```bash
npm install            # installs the small root tooling only
npm run dev            # starts the API and the dashboard side by side
npm run check          # lint, type check and the full test suite
```

Copy `.env.example` to `.env` to change ports or bounds. The defaults need no
configuration.

### What to do first

1. Open the dashboard. Every value reads as an em dash, because nothing has
   been measured yet. That is deliberate.
2. Go to **Experiments**, keep the default scenario (*Disc image download
   during a work call*) and mode (*Mode 2, Traditional adaptive*), and press
   **Configure and start**.
3. Watch the **Network Overview**. For the first twenty seconds the link is
   quiet. Then the bulk transfer starts, the queue beside the gateway fills,
   round-trip time climbs past 100 ms and the congestion state turns red.
4. The controller evaluates its candidates, the Safety Guard checks the
   selected one, shaping is installed and the queue drains. The verification
   panel shows the before-and-after measurement that justified keeping it.
5. Run the same scenario again in **Mode 1, No QoS** and compare the reports.
   That difference is the point of the project, and it is measured rather than
   asserted.

---

## Repository layout

```
backend/                FastAPI service, simulator and control plane
  app/domain/           Typed schemas shared by every layer
  app/simulation/       Deterministic scenario simulator
  app/control/          M1 observer, M2 classifier, M3A capacity, M3B predictor,
                        M4 policy engine, Safety Guard, M5 enforcement,
                        M6 verification and watchdog, intent leases
  app/runtime/          Event bus and experiment orchestration
  app/api/              REST and WebSocket surface
  tests/                142 tests
frontend/               React, TypeScript and Vite dashboard
  src/scene/            Three-dimensional topology and its 2D fallback
  src/panels/           The engineering panels of the decision pipeline
  src/pages/            The eight pages
  src/store/            Normalised event and metric state
lab/                    Isolated Linux testbed and the privileged agent
  agent/                Topology, enforcement, measurement, scenarios, service
  tests/                129 tests (18 need root on Linux)
docs/                   Specifications (read these before changing behaviour)
references/             Case study, proposal and the approved visual reference
```

The laboratory is documented separately in [lab/README.md](lab/README.md),
including how to run it and the safety boundary it enforces.

---

## How it works

### The decision path

```
flows ─▶ M1 observer ─▶ feature window ─▶ M2 classifier ─┐
                                                          ├─▶ M4 policy engine
link measurements ─▶ M3A capacity estimator ──────────────┤        │
                  └▶ M3B congestion predictor (advisory) ─┘        ▼
                                                            Safety Guard
                                                                   │
                                                     ┌─────────────┴────────────┐
                                                 rejected                   accepted
                                                     │                          ▼
                                            keep safe state            M5 enforcement
                                                                       (snapshot, apply,
                                                                        read back)
                                                                                │
                                                                                ▼
                                                                        M6 verification
                                                                                │
                                                              ┌─────────────────┴────────┐
                                                          acceptable                 degraded
                                                              │                          ▼
                                                        keep policy                  rollback
```

### The simulator is one coupled model

Every number in `SIMULATION` mode comes out of a single per-tick state update,
so the charts, the panels and the three-dimensional scene cannot contradict
each other. Queue growth is offered load minus service rate, queueing delay is
backlog divided by link rate, and round-trip time is the idle path delay plus
the queueing delay of both directions. Greedy flows are congestion controlled,
which is what makes a bulk transfer fill an unmanaged buffer and produce the
bufferbloat the project exists to address.

The same scenario, parameters and seed always produce the same run, regardless
of wall-clock timing or animation frame rate.

### Two things worth knowing about the design

**The shaper follows the controller's class, not the generator's.** When the
classifier mislabels a flow, the data plane treats it as the wrong class and
the consequence is measured. If the simulator shaped by ground truth, a
misclassification would cost nothing and comparing a rule set against a model
would measure nothing.

**Classification leads with packet size, not byte rate.** Byte rate is the
obvious discriminator and the wrong one to lean on, because the shaper controls
it: throttle a class and its measured rate falls, which can reclassify it
downward, which throttles it further. Packet size and duty cycle are set by the
application rather than by how much capacity it was granted, so they stay
stable under shaping.

---

## Honest status

This is the part worth reading carefully before the dashboard is shown to
anyone.

| Capability | Status | Evidence |
|---|---|---|
| Deterministic simulator | Implemented and tested | `backend/tests/test_simulation_engine.py` |
| M1 flow observer and feature windows | Implemented (simulation) | `app/control/observer.py` |
| M2 classification, deterministic rules | Implemented and tested | `tests/test_control_plane.py::TestRuleClassifier` |
| M2 classification, **Model 1** | **Not trained.** Designed only | `docs/ML_SPEC.md`, Phase 4 |
| M3A usable-capacity estimator | Implemented and tested | `tests/test_control_plane.py::TestCapacityEstimator` |
| M3B **Model 2** congestion predictor | **Not trained.** Designed only | `docs/ML_SPEC.md`, Phase 5, Round 2 |
| M4 policy engine with scored candidates | Implemented and tested | `tests/test_control_plane.py::TestPolicyEngine` |
| Safety Guard | Implemented and tested | `tests/test_control_plane.py::TestSafetyGuard` |
| M5 enforcement, **simulated** | Implemented and tested | reported as `SIMULATED`, never `APPLIED` |
| M5 enforcement, **real Linux traffic control** | Implemented and tested | HTB + FQ-CoDel with IFB download path; `lab/tests/test_laboratory.py` |
| Isolated Linux testbed | Implemented and tested | four namespaces, netem bottleneck, resettable |
| Privileged laboratory agent | Implemented and tested | allow-listed operations, token authentication, audit log |
| M6 verification and rollback | Implemented and tested | `tests/test_control_plane.py::TestVerification` |
| Independent watchdog | Implemented and tested | `tests/test_control_plane.py::TestWatchdog` |
| Temporary intent leases | Implemented and tested | server-side expiry |
| Dashboard, 3D scene, eight pages | Implemented and tested | `frontend/src/test/` |
| `LIVE` data mode | Implemented | offered only when an agent is reachable and enabled |
| `REPLAY` data mode | **Not implemented** | Phase 6 |

The rules the code actually enforces, not just documents:

- A simulated policy application reports `SIMULATED`. `APPLIED` is returned
  only after a verified read-back of real Linux queue discipline state, and a
  test asserts the simulator never produces it.
- `LIVE` is refused with an explanation unless a laboratory agent is
  configured, reachable and enabled. `REPLAY` is refused outright.
- Both models report `NOT_CONFIGURED`. Modes 3 and 4 are selectable but are
  marked degraded, every flow takes the safe `UNKNOWN` fallback, and the
  interface says those runs must not be presented as working results.
- A deterministic rule set produces a class, not a probability. The confidence
  column reads "rule-based" rather than showing a number nothing computed.
- An unmeasured value renders as an em dash, never as zero.
- The emulated bottleneck rate is never published as a measurement. The
  capacity estimator has to discover it from delivered rate and delay, which is
  what makes the bottleneck-drop scenario a real test of it.

### Measured results

**Real Linux laboratory.** A greedy download against a 100 Mbps emulated link
with an 18 ms base round-trip time, measured by `ping` and the kernel's own
queue counters:

| | round-trip time | added delay | jitter | bottleneck queue |
|---|---|---|---|---|
| Idle | 18.34 ms | - | - | 0 packets |
| Bulk download, no shaping | 311.09 ms | +292.75 ms | 51.95 ms | 302 packets |
| Bulk download, shaped | 18.41 ms | +0.07 ms | 0.25 ms | 10 packets |

The queue did not disappear; it moved out of the provider's buffer, which the
gateway cannot manage, into the gateway's own Fair Queuing Controlled Delay
queue, which it can. Reproduce with
`sudo .venv/bin/python lab/scripts/demo_before_after.py`.

**Simulation.** Mode 1 against Mode 2, same scenario, same seed, 70 seconds:

| | No QoS | Traditional adaptive |
|---|---|---|
| Added delay over baseline, median | 54.2 ms | 0.40 ms |
| Jitter, median | 14.1 ms | 0.47 ms |
| Packet loss, median | 0.47 % | 0.00 % |
| Bulk transferred | 713 MB | 633 MB |

Delay-sensitive traffic is protected and the bulk transfer still makes
progress. These are laboratory emulations, not measurements of a real Internet
service provider connection.

---

## Metric definitions

Defined once and used everywhere, because interchanging them quietly is how
performance claims stop meaning anything.

- **Round-trip time** — probe round-trip time. Not one-way application latency.
- **Added delay under load** — loaded round-trip time minus the idle baseline,
  measured with the same probe method.
- **Jitter** — packet delay variation: the exponentially weighted moving
  average of the absolute difference between consecutive probe round-trip
  times.
- **Packet loss** — dropped packets over offered packets for the interval.
- **Throughput** — measured delivered bits per second.
- **Queue depth** — simulator queue state here, clearly labelled; read from the
  Linux queue discipline once a laboratory agent is connected.
- **Jain's fairness index** — computed across comparable flows only, meaning
  flows in the same class with the same service entitlement, over bytes
  delivered during the run.

---

## Safety boundary

The browser is untrusted and the API is not a privileged shell. There is no
endpoint that accepts a shell command, a queue discipline string or an
interface name, and a test asserts the published API surface contains none.

Privileged Linux networking will live behind a separate agent on a dedicated
Ubuntu host, reachable only through authenticated, allow-listed, typed
operations. The `LabAgentEnforcer` in `app/control/enforcement.py` is that
boundary; it currently refuses every request because no agent exists yet.
`docs/SAFETY_SECURITY.md` has the full model.

---

## Testing

```bash
npm run check          # everything
npm run test:api       # 150 backend tests
npm run test:web       # 37 frontend tests
```

The laboratory has its own suite, on a Linux host:

```bash
cd lab
.venv/bin/python -m pytest tests/test_safety.py tests/test_service.py   # 111
sudo .venv/bin/python -m pytest tests/test_laboratory.py                # 18
```

The backend suite covers the simulator's physics and determinism, each control
component, the Safety Guard's rejections, verification and rollback, the
experiment lifecycle and the API contract. The frontend suite covers the store
reducer, the formatting rules, particle aggregation, the panels and a
whole-application smoke test that walks every page.

---

## Next phases

Phases 2 to 7 are described in `docs/IMPLEMENTATION_PLAN.md`, and the Round 1
and Round 2 split is in `docs/ROUND_BOUNDARY.md`. In short:

1. **Phase 2** — Ubuntu namespace laboratory, real measurements. **Done.**
2. **Phase 3** — real traffic control enforcement, snapshot, read-back and
   rollback. **Done**, apart from watchdog fault injection.
3. **Phase 4** — train and integrate Model 1, then compare Mode 2 with Mode 3.
4. **Phase 5** — define the congestion event and horizon, train Model 2, then
   compare Mode 3 with Mode 4.
5. **Phases 6 and 7** — the full experiment matrix, replay mode and deployment.
