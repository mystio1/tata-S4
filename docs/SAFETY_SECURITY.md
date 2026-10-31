# QoSense Safety, Security and Linux Lab-Agent Boundary

## 1. Trust boundaries

The browser is untrusted. The public API is not a privileged shell. The Ubuntu lab agent is the only component allowed to perform privileged Linux network operations, and it must expose a narrow, typed interface.

Recommended topology:

- React dashboard: Render Static Site or equivalent.
- Ordinary FastAPI API: Render Web Service or a controlled host; no privileged `tc` operations here.
- Ubuntu lab agent: dedicated Ubuntu VM/host with required network-administration permissions, isolated network namespaces and controlled test interfaces.
- Authenticated encrypted connection between API and lab agent.

Do not expose the lab VM directly to arbitrary Internet traffic. Use authentication, firewall rules, private networking or a secure outbound connection pattern as appropriate.

## 2. Allowed operations

The lab agent may expose only predefined operations, for example:
- `health_check`
- `list_scenarios`
- `reset_lab`
- `start_scenario(scenario_id, validated_parameters)`
- `stop_scenario(experiment_id)`
- `get_metrics(experiment_id)`
- `apply_policy(profile_id, validated_profile_version)`
- `restore_snapshot(snapshot_id)`
- `read_tc_state`

Each operation must validate identifiers, numeric bounds, target namespaces/interfaces and authorization. Never accept shell command text, Python expressions, arbitrary qdisc strings or arbitrary interface names from a browser request.

## 3. Linux process execution

- Use fixed executable paths and argument arrays, not shell interpolation.
- Validate every argument against an allow-list.
- Use least privilege; grant only the network-administration capabilities required by the lab agent.
- Run experiments inside dedicated namespaces with veth pairs and controlled bottlenecks.
- Never apply test rules to the host's production/default network interface.
- Keep a known-safe lab reset command and independent recovery mechanism.
- Log commands by safe structured representation, exit code, stdout/stderr summary and post-apply state. Do not log secrets.

## 4. Policy transaction

For each live policy update:
1. Authenticate and authorize request.
2. Validate policy profile and configuration limits.
3. Check current agent health and last-known-good state.
4. Snapshot qdisc/class/filter/mark state.
5. Apply a bounded named configuration.
6. Read back actual Linux state.
7. Compare expected and actual state.
8. Commit the new policy only if verification passes.
9. Otherwise restore snapshot and verify restore.
10. If restore cannot be verified, activate the independent watchdog recovery path and raise an alert.

Use transaction IDs and idempotency keys to prevent duplicate application.

## 5. Watchdog

A separate process/service should monitor the controller/agent heartbeat. It must not rely solely on the process it monitors. Its only authority should be to activate a predefined safe lab profile or isolate/reset the lab. Record heartbeat loss and recovery actions.

## 6. Temporary user intent

- Server-side expiry, not only a frontend countdown.
- Maximum permitted duration configurable.
- Target device/flow must be known to the lab.
- Intent does not bypass safety/fairness constraints.
- Conflicting requests must have deterministic handling.
- Expiry/cancellation triggers re-evaluation of the ordinary policy.
- Every intent creation, denial, change and expiry is logged.

## 7. Secrets and privacy

- Never commit tokens, keys, passwords or personal network credentials.
- Use `.env.example` with fictional placeholders and `.gitignore` for local `.env` files.
- Do not collect application payloads or private browsing content.
- Store only metadata needed for experiments.
- Provide data retention and experiment cleanup controls.
- Use fake/example IP addresses in documentation unless a controlled lab address is required.

## 8. API/web security

- HTTPS in deployment.
- Authentication for live controls.
- Role/permission checks for actions that alter network policy.
- Rate limiting and request-size limits.
- Strict CORS allow-list.
- Pydantic validation and bounded scenario parameters.
- CSRF protection where cookie-based authentication is used.
- Audit logs for all privileged requests.
- No arbitrary command endpoint.

## 9. Data-mode honesty

Every telemetry response and event includes `data_mode`. `SIMULATION`, `REPLAY` and `LIVE` must look different in the UI. A simulated policy application must say `SIMULATED`, not `APPLIED`. Only verified Linux read-back permits a LIVE `APPLIED` status.
