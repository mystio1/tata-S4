# QoSense laboratory

The isolated Linux testbed and the privileged agent that drives it.

This is the only part of QoSense that performs privileged network operations.
It runs on a dedicated Linux host, separately from the public dashboard and
API, and exposes a narrow typed interface rather than anything resembling a
shell.

## The testbed

Four network namespaces joined by virtual Ethernet pairs:

```
 qos-home              qos-gw                   qos-isp                qos-wan
 (devices)        (QoSense gateway)         (emulated link)          (Internet)
   qos-h0 <------> qos-g0     qos-g1 <------> qos-i0    qos-i1 <------> qos-w0
 10.200.1.2      10.200.1.1  10.200.2.1     10.200.2.2  10.200.3.1    10.200.3.2
```

**Why four namespaces and not three.** The emulated bottleneck and the policy
shaping must not sit on the same interface, or they contend for one queue and
neither can be reasoned about. Giving the bottleneck its own namespace
separates them the way reality does: `qos-isp` is the link you do not control
and `qos-gw` is the gateway you do. The gateway then shapes *below* the
bottleneck rate, which pulls the standing queue out of the link it cannot
manage and into the one it can. That is the entire mechanism behind the
product, so the testbed models it directly.

The bottleneck is `netem` with a rate, a delay and a finite packet limit,
giving genuine drop-tail behaviour. **The controller never reads its
configuration**: the capacity estimator has to discover the rate from
delivered throughput and delay, exactly as it would against a real
connection.

## Measured result

A greedy download against a 100 Mbps emulated link with an 18 ms base
round-trip time, measured by `ping` and the kernel's own queue counters:

| | round-trip time | added delay | jitter | bottleneck queue |
|---|---|---|---|---|
| Idle | 18.34 ms | — | — | 0 packets |
| Bulk download, no shaping | 311.09 ms | +292.75 ms | 51.95 ms | 302 packets |
| Bulk download, shaped | 18.41 ms | +0.07 ms | 0.25 ms | 10 packets |

The queue did not disappear; it moved. Unshaped it sits in the provider's
drop-tail buffer where the gateway cannot touch it. Shaped, the gateway
becomes the slowest point on the path, so the queue forms there instead and
Fair Queuing Controlled Delay keeps it short.

Reproduce it with `sudo .venv/bin/python scripts/demo_before_after.py`.

## Running it

Needs a Linux host with root and a kernel carrying `sch_htb`, `sch_netem`,
`sch_fq_codel`, `sch_ingress`, `act_mirred` and `ifb`. Developed against
Ubuntu 24.04 on the Windows Subsystem for Linux version 2, kernel 6.6, which
has all of them.

```bash
sudo apt-get install -y iperf3 iproute2 iputils-ping python3-venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

sudo scripts/lab-reset.sh                     # build and prove it forwards

export QOSENSE_LAB_AGENT_TOKEN="$(openssl rand -hex 24)"
sudo -E scripts/run-agent.sh                  # bind to loopback, port 8100
```

Point the API at it:

```bash
export QOSENSE_LAB_AGENT_URL=http://127.0.0.1:8100
export QOSENSE_LAB_AGENT_TOKEN=...            # the same token
export QOSENSE_ENABLE_LIVE_ENFORCEMENT=true
```

`LIVE` then appears in the dashboard's supported data modes. Until all three
are set and the agent answers, the API refuses to create a live run and says
why.

## The safety boundary

`docs/SAFETY_SECURITY.md` has the full model. What the code enforces:

- **No shell, anywhere.** Commands are argument arrays passed to executable
  paths resolved once at start-up. There is no string interpolation into a
  shell for a crafted identifier to escape into.
- **No caller names an interface or a namespace.** Callers reference a
  scenario or a policy profile by identifier; the names are agent constants
  carrying the `qos-` prefix, and every one is checked against that fixed
  list. A request cannot reach the host's real networking even if it tries.
- **Every number is bounded** before it is formatted into an argument: rates,
  delays, durations, queue limits and ports.
- **Ports are restricted** to the laboratory generator range, so a policy
  cannot be made to match traffic outside the experiment.
- **Bearer-token authentication** on every operation except `/health`, and
  the agent refuses to start without a token unless told it is a local
  development run.
- **Loopback binding** by default.
- **A structured audit log** of every privileged command with its exit status,
  readable at `/audit`.

Tests assert the published interface contains no command endpoint and that no
request body accepts an interface, namespace or queue discipline name.

## Enforcement

Both paths from `docs/ARCHITECTURE.md`:

```
upload    home device → qos-g0 → [ HTB class on qos-g1 egress → FQ-CoDel ] → provider
download  provider → qos-g1 ingress → mirred redirect
                   → [ HTB class on qos-ifb0 egress → FQ-CoDel ] → qos-g0 → device
```

Every change is a transaction: snapshot, apply a named profile, read the
kernel's state back, compare, and restore on mismatch. **`APPLIED` is returned
only when the read-back matched.** There is no code path that constructs that
status otherwise.

A class the policy did not mention falls through to a default class with the
full link as its ceiling, so a shaping gap can never black-hole a flow.

## Measurement

Everything is kernel truth or a real probe:

- **Per-flow throughput and packet size** from counting filters with a pass
  action. Download is counted on the local interface's *egress* hook, which
  is after the shaper, so the figure is what the device received rather than
  what the provider sent. Upload is counted on ingress and is therefore
  offered load; the distinction is reported as `measures`.
- **Per-class throughput** differenced from the Hierarchical Token Bucket
  byte counters.
- **Queue depth and drops** from the queue discipline counters.
- **Round-trip time and jitter** from one continuous `ping`, read as a
  trailing window so a metrics call returns immediately instead of waiting a
  second for a fresh probe.

Measured packet size matters more than it looks: the classifier leads with it,
so substituting an assumed constant quietly decides the classifier's answer
for every flow. An early version did exactly that and labelled everything
`BULK`.

A value with no sample yet is `null`, never zero.

## Tests

```bash
.venv/bin/python -m pytest tests/test_safety.py tests/test_service.py   # 111, no root
sudo .venv/bin/python -m pytest tests/test_laboratory.py                # 18, needs root
```

The first group tests the validation layer and the agent's interface and runs
anywhere. The second builds namespaces, moves real traffic and installs real
traffic control, including the test that asserts shaping below the bottleneck
actually removes the standing queue.

## Known limitations

1. **`iperf3` is an imperfect generator for web and voice traffic.** It sends
   maximum-sized segments regardless of the configured bitrate, so the
   web/voice flow looks like large packets at a low rate. A real browser
   session would show varied packet sizes.
2. **Shaping margin is a tuning problem.** The gateway must shape
   meaningfully below the bottleneck to hold the queue. At a 5 percent margin
   the provider's buffer still builds transiently under bursts.
3. **Upload per-flow figures are offered load, not delivered.** Counting
   delivered upload would need a hook on the provider side.
4. **CAKE is not available** on this kernel, so the Round 2 queue-management
   benchmark cannot run here.
5. **One run at a time.** The agent refuses a second concurrent scenario.
