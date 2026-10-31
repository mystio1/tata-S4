# QoSense UI and 3D Animation Specification

## 1. Approved visual direction

Use **Theme 05 — Deep Teal & Bronze** from the supplied reference image `references/QoSense_Deep_Teal_Bronze_UI_Reference.png`.

The UI should look like a serious, premium industrial network operations centre. Avoid the generic blue-neon cyber dashboard look. Preserve the reference composition: left navigation, top metric strip, large central 3D network topology, right-side metrics/charts, and functional engineering panels below the 3D scene.

### Core palette

| Role | Colour | Hex |
|---|---|---|
| Main background | Deep teal-black | `#081C1D` |
| Secondary surface | Dark teal | `#12302D` |
| Raised surface | Graphite teal | `#1C3331` |
| Primary accent | Bronze | `#C69A5B` |
| Highlight | Champagne | `#E9E2D5` |
| Success | Mint green | `#58B99D` |
| Interactive traffic | Cyan | `#4FCBD0` |
| Gaming traffic | Violet | `#A78BDA` |
| Streaming traffic | Amber | `#E79A47` |
| Bulk traffic | Muted blue | `#5796C8` |
| Warning | Orange | `#E58A45` |
| Critical | Red | `#D85B52` |
| Muted text | Grey-green | `#AAB9B2` |

Do not recolour semantic traffic classes just to match the theme. Keep the legend consistent across all pages. Bronze is the brand accent, not a replacement for class colours.

## 2. Main dashboard layout

### Left sidebar
- QoSense identity and subtitle.
- Network Overview.
- Live Traffic.
- AI Intelligence.
- QoS Controller.
- Experiments.
- Performance.
- Safety & Logs.
- Settings.

### Top metric strip
- Download rate.
- Upload rate.
- Usable-capacity estimate.
- Congestion risk.
- Data mode badge (`SIMULATION`, `LIVE`, `REPLAY`).
- Current experiment/mode.

### Central region
A large isometric 3D home-network topology:
- Central QoSense gateway/router.
- Internet/WAN node.
- Laptop (work/video call).
- Gaming console.
- Desktop (bulk download).
- Smart TVs (streaming).
- Smartphone (web/voice).
- Separate links for upload/download direction where useful.
- Queue visualization close to the gateway.
- Device labels and per-flow telemetry.

### Right region
- Network health gauges.
- Live throughput chart.
- Latency and jitter chart.
- Packet-loss and queue-occupancy indicators.
- Recent alerts.

### Lower engineering panels
1. Traffic Flow Detected (M1).
2. AI Traffic Classification (Model 1).
3. Congestion Prediction (Model 2).
4. Priority Decision Centre (M4).
5. Policy Enforcement (M5).
6. Verification / Final Decision (M6).
7. Recent Events.
8. Start Experiment controls.

The composition must remain readable. Allow responsive resizing and a simplified 2D view on smaller or lower-performance screens.

## 3. Pages

### Network Overview
Show the 3D topology and summary status. Make the 3D scene useful and clickable: selecting a device/flow opens a detail panel with its current measured or simulated state.

### Live Traffic
Flow table with ID, device, direction, class, confidence, bytes/s, packets/s, RTT/flow delay where measurable, queue/class, source mode and last update. Filters by class, direction, device and confidence.

### AI Intelligence
Two distinct model cards:
- Model 1: LightGBM traffic classifier (multiclass).
- Model 2: LightGBM congestion-risk predictor (binary).

Show model readiness, version, last inference, inputs summary, output, probability/calibration status and fallback state. Do not show a model as active if it is not trained/loaded. Explain predictions in plain language.

### QoS Controller
Show capacity estimate and quality, congestion prediction separate from measured congestion, candidate policy, decision explanation, safety checks, applied policy, bandwidth classes, fairness floors and temporary priority lease.

### Experiments
Scenario selector:
- ISO download during work call.
- Bandwidth drop (100 Mbps to 20 Mbps emulated).
- Three TVs streaming + gaming.
- Custom bounded scenario.

Mode selector:
- No QoS.
- Traditional adaptive QoS.
- Classifier only.
- Full QoSense.

Controls: configure, start, pause if supported, stop safely, reset, repeat, seed and export report. Clearly identify laboratory emulation.

### Performance
Plots and tables for RTT, added delay, jitter, packet loss, throughput/goodput, queue depth, bulk progress, Jain fairness for comparable flows, adaptation time and CPU overhead.

### Safety & Logs
Chronological event trace, safety rejections, policy transactions, read-back results, rollback, watchdog heartbeat, model failures and measurement errors.

## 4. Event-driven 3D animation rules

The scene must be driven by typed backend events/measurements. Do not animate a successful outcome based only on a button click.

### Packet streams
- Packet/particle emission density should reflect packet rate or a documented aggregated rate.
- Flow thickness/brightness may reflect throughput but must not imply packet speed.
- Particle travel speed should represent a separate configured visual transit concept, not be conflated with throughput.
- Upload particles travel from device to gateway to Internet.
- Download particles travel from Internet to gateway to device.
- Use aggregation/instancing; do not create one heavyweight Three.js object per real packet.

### Congestion/queue
Drive queue blocks/occupancy from measured qdisc/queue statistics in LIVE mode, or the deterministic simulator's queue model in SIMULATION mode. As occupancy grows, show a short queue becoming longer, a warning colour change and a bufferbloat label only when defined conditions are met.

### Latency and jitter
- Latency: show a probe request/response pulse and update the RTT chart from measured values.
- Jitter: show an arrival timeline with evenly spaced packets when variation is low and irregular spacing when measured delay variation rises.
- Do not use particle volume to represent jitter.
- Label RTT separately from one-way delay.

### Packet loss
Only animate a packet as lost when a measurement or configured simulation event records loss. Do not randomly drop visual particles in LIVE mode.

### Model 1 classification
When a classification event arrives, highlight the corresponding flow, briefly show the observed feature summary, predicted class, probability and calibrated confidence status. Low-confidence/unknown flows become muted grey and show safe fallback.

### Model 2 congestion prediction
Show a separate risk indicator and its prediction horizon. Visually distinguish predicted future congestion from currently measured congestion. A prediction must not be represented as an already-occurring queue failure.

### Priority Decision Centre
This is a key differentiator. When a policy evaluation starts:
1. Show incoming class, confidence, capacity, congestion risk, current metrics and user intent.
2. Show the candidate actions being evaluated (keep current, protect interactive, limit bulk, rebalance, conservative safe).
3. Show only real computed scores/reason codes from the policy engine. If the engine does not calculate a numeric score, show the qualitative checks rather than fake scores.
4. Animate the selected candidate being highlighted.
5. Show Safety Guard pass/reject.
6. Show the actual policy version and enforcement result.

Do not use arbitrary percentage allocations as defaults. Show only rates returned by the current policy. If no policy exists, show "Not configured".

### Enforcement
Animate connection marking, upload egress queue and download IFB path. Show "Applied" only after the lab agent returns success and read-back verification matches the intended state. Otherwise show rejected, failed or rollback state.

### Verification and rollback
Show before/after measured cards. On acceptable outcome, retain the policy. On degraded outcome, animate rollback only after the backend starts it and show restored state only after read-back confirms it.

### Temporary intent
Show a visible expiring priority lease, remaining time and cancellation. When it expires, show policy re-evaluation rather than silently changing the display.

## 5. Simulation engine

Create a deterministic local simulator for the first UI vertical slice. It must generate plausible, internally consistent events from a scenario and seed. It should model:
- Flow arrivals/departures.
- Packet/byte rates.
- Link bottleneck.
- Queue growth/servicing.
- Probe RTT and jitter.
- Optional packet loss.
- Classification events.
- Congestion-risk events when a simulation model/state is available.
- Policy proposals and simulated enforcement outcomes.

Simulation outputs must carry `data_mode: SIMULATION` and be visibly labelled. Use consistent equations/state updates rather than unrelated random values for each widget. For example, queue growth must relate to offered load minus service rate; throughput and queue charts must reflect the same experiment state.

Do not use fabricated live values. The supplied image contains example UI numbers, but those numbers are visual references only and must not be copied as live telemetry.

## 6. Motion quality and performance

- Smooth, restrained transitions; no constant camera shaking or excessive flashes.
- Use a fixed/controlled camera with optional limited orbit controls.
- Avoid generic floating holograms and decorative AI brains.
- Use instanced particles and reusable geometries/materials.
- Pause expensive animations when the tab is hidden.
- Respect reduced-motion preference.
- Provide a 2D fallback when WebGL is unavailable.
- Keep charts and controls usable even if 3D rendering fails.
- Animation frame rate must not affect metric sampling or experiment timing.
- Support keyboard navigation, readable contrast and responsive layout.

## 7. Component organization

Prefer reusable components such as:
- `AppShell`, `Sidebar`, `TopMetricStrip`.
- `NetworkTopology3D`, `GatewayModel`, `DeviceNode`, `PacketStream`, `QueueStack`, `InternetNode`.
- `NetworkHealthPanel`, `MetricChart`, `FlowTable`.
- `TrafficClassifierPanel`, `CongestionPredictorPanel`.
- `PriorityDecisionCentre`, `SafetyGuardPanel`, `EnforcementPanel`, `VerificationPanel`.
- `ExperimentRunner`, `ModeSelector`, `ScenarioForm`, `EventTimeline`.

Keep scene state and dashboard state driven by normalized event/metric stores. Do not build the entire application as one large component.
