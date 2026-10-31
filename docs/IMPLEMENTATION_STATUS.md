# QoSense Implementation Status

Updated after Phase 1. Status values are `NOT_STARTED`, `IN_PROGRESS`,
`IMPLEMENTED_SIMULATION`, `IMPLEMENTED_LIVE`, `TESTED` and `BLOCKED`.

`IMPLEMENTED_LIVE` is not used anywhere below and must not be until the
capability has been tested against a real Ubuntu laboratory agent.

## Phase 0 — Workspace and schemas · TESTED

| Capability | Status | Evidence | Limitations |
|---|---|---|---|
| Monorepo scaffold | TESTED | `backend/`, `frontend/`, `docs/`, `references/` | — |
| Typed domain schemas | TESTED | `backend/app/domain/` | Shared by hand-written mirrors in `frontend/src/api/types.ts`; not generated |
| Configuration and bounds | TESTED | `backend/app/config.py`, `.env.example` | — |
| Format, lint, type check, test commands | TESTED | root `package.json`, `npm run check` | — |
| Independent start-up of both services | TESTED | `npm run dev:api`, `npm run dev:web` | — |

## Phase 1 — Functional simulation vertical slice · TESTED

### Simulation and observation

| Capability | Status | Evidence | Limitations |
|---|---|---|---|
| Deterministic scenario simulator | TESTED | `tests/test_simulation_engine.py` (20 tests) | Simulated physics; not a measurement of any real link |
| Queue growth as offered load minus service rate | TESTED | `TestQueueDynamics` | — |
| Bufferbloat under drop-tail, recovery under shaping | TESTED | `TestBufferbloatAndRecovery` | — |
| Reproducibility from scenario, parameters and seed | TESTED | `TestDeterminism` | — |
| Four scenarios | IMPLEMENTED_SIMULATION | `app/simulation/scenarios.py` | Laboratory emulations only |
| M1 flow observer and feature windows | IMPLEMENTED_SIMULATION | `app/control/observer.py` | Observes the simulator, not a real capture adapter |
| Feature schema `flow-features-v1` | IMPLEMENTED_SIMULATION | `app/domain/metrics.py` | Will need revalidation against real capture |

### Analytics

| Capability | Status | Evidence | Limitations |
|---|---|---|---|
| M2 classification, deterministic rule set | TESTED | `TestRuleClassifier` (8 tests) | Rules produce a class, never a probability. Mode 2 baseline only |
| M2 **Model 1**, LightGBM multiclass | NOT_STARTED | Designed in `ML_SPEC.md` | **Not trained.** Reports `NOT_CONFIGURED`. Modes 3 and 4 fall back to `UNKNOWN` and are marked degraded |
| M3A usable-capacity estimator | TESTED | `TestCapacityEstimator` (7 tests) | — |
| Estimator tracks a hidden bottleneck drop | TESTED | `test_tracks_a_real_bottleneck_drop_without_reading_the_configuration` | Never reads the configured rate |
| M3B **Model 2**, congestion predictor | NOT_STARTED | Designed in `ML_SPEC.md` | **Not trained.** Scheduled for Phase 5 / Round 2. Always returns `UNAVAILABLE` / `IGNORED` |

### Control

| Capability | Status | Evidence | Limitations |
|---|---|---|---|
| M4 policy engine, five named candidates | TESTED | `TestPolicyEngine` (12 tests) | — |
| Candidate scoring from measurable costs | TESTED | `TestPolicyEngine` | Weights `qosense-policy-weights-v1` are our own and are untuned; Round 2 should validate them against held-out runs |
| Rates derived from capacity and demand | TESTED | `test_rates_are_derived_from_capacity_and_demand_not_fixed_percentages` | No fixed percentage split anywhere |
| Hysteresis, minimum hold and cooldown | TESTED | `test_honours_the_minimum_hold_time` | — |
| Bounded rate step per evaluation | TESTED | `test_bounds_how_far_one_evaluation_moves_a_ceiling` | — |
| Safety Guard | TESTED | `TestSafetyGuard` (11 tests) | — |
| M5 enforcement, simulated | TESTED | `TestEnforcement` (6 tests) | Reports `SIMULATED`. `APPLIED` is never produced |
| M5 enforcement, real Linux traffic control | NOT_STARTED | `LabAgentEnforcer` refuses every call | **Phase 3.** Needs a dedicated Ubuntu host |
| Transactional snapshot, apply, read-back, restore | IMPLEMENTED_SIMULATION | `app/control/enforcement.py` | Exercises the real transaction shape against the simulator |
| M6 verification with before and after windows | TESTED | `TestVerification` (5 tests) | — |
| Rollback on a degraded outcome | TESTED | `test_a_policy_change_is_snapshotted_applied_read_back_and_verified` | — |
| Independent watchdog | TESTED | `TestWatchdog` (4 tests) | Heartbeat and recovery request only; fault injection is Round 2 |
| Temporary intent leases | TESTED | `TestIntentLeases` (6 tests) | Server-side expiry enforced |

### Interface and transport

| Capability | Status | Evidence | Limitations |
|---|---|---|---|
| FastAPI REST contract | TESTED | `tests/test_api.py` (30 tests) | — |
| WebSocket event stream | TESTED | `TestWebSocket`, verified against a live server | Bounded queues, drop-oldest, sequence numbers |
| Experiment lifecycle | TESTED | `tests/test_experiment_lifecycle.py` (32 tests) | — |
| Deep Teal and Bronze dashboard | TESTED | `frontend/src/test/` (37 tests) | — |
| Three-dimensional topology | IMPLEMENTED_SIMULATION | `frontend/src/scene/` | Event-driven; instanced particles; fixed camera with limited orbit |
| Two-dimensional fallback | TESTED | `test/qosense.test.tsx` | Used when WebGL is unavailable or the renderer fails |
| Reduced motion and hidden-tab pause | IMPLEMENTED_SIMULATION | `scene/useSceneMotion.ts` | Does not affect measurement |
| Eight pages | TESTED | `test/app.test.tsx` walks all of them | — |
| Experiment report and export | IMPLEMENTED_SIMULATION | Experiments page | Medians and interquartile ranges from one run; repeated-run aggregation is Phase 6 |

### Data-mode honesty

| Rule | Status | Evidence |
|---|---|---|
| Every response and event carries `data_mode` | TESTED | `test_every_event_and_snapshot_is_labelled_simulation` |
| `LIVE` is refused while no agent is connected | TESTED | `test_live_mode_is_refused` |
| `REPLAY` is refused until implemented | TESTED | `test_replay_mode_is_refused` |
| Simulated enforcement never reports `APPLIED` | TESTED | `test_enforcement_is_never_reported_as_applied` |
| The configured bottleneck is not published as a measurement | TESTED | `test_the_configured_bottleneck_is_not_published_as_a_measurement` |
| Degraded modes say so and do not claim results | TESTED | `TestModelDegradedModes` |
| No command-execution endpoint exists | TESTED | `test_there_is_no_command_execution_endpoint` |
| Unmeasured values render as an em dash, not zero | TESTED | `frontend` formatting tests |

## Phases 2 to 7 — NOT_STARTED

| Phase | Contents | Blocker |
|---|---|---|
| Phase 2 | Ubuntu namespace laboratory, real baseline measurements | Needs a dedicated Ubuntu host or virtual machine |
| Phase 3 | Real traffic control enforcement, rollback and watchdog on that laboratory | Depends on Phase 2 |
| Phase 4 | Train Model 1, compare Mode 2 against Mode 3 | Needs labelled data from Phase 2 |
| Phase 5 | Define the congestion event and horizon, train Model 2, compare Mode 3 against Mode 4 | Round 2; needs Phase 4 |
| Phase 6 | Full experiment matrix, repeated runs, replay mode, CAKE benchmark | Depends on Phase 2 |
| Phase 7 | Deployment, authentication, hardening | — |

## Known limitations and risks

1. **No live enforcement.** Nothing in this build has touched Linux traffic
   control. The transaction shape is exercised against the simulator so that
   Phase 3 replaces one component rather than inventing the whole path.
2. **Neither model is trained.** Modes 3 and 4 run, but every flow takes the
   safe fallback. Those runs must not be presented as Mode 3 or Mode 4 results.
3. **Policy weights are untuned.** They are documented, versioned and
   defensible, but they have not been validated against held-out experiments.
   That is Round 2 work.
4. **The rule classifier is a baseline, with known failure modes.** A streaming
   buffer fill and a bulk transfer look alike over a short window; the observer
   carries a smoothed activity ratio across windows to separate them, which
   helps but does not eliminate the confusion. Closing that gap is Model 1's
   job and is how its value should be measured.
5. **Simulated results are not predictions.** The Mode 1 against Mode 2
   difference shows the control loop works as designed. It says nothing about
   what the Linux implementation will achieve.
6. **Frontend types are mirrored by hand** from the Python schemas. They are
   covered by contract tests, but generating them would remove a class of
   drift.
7. **No persistence.** Experiment state is in memory, so a backend restart
   loses it. SQLite is planned.
