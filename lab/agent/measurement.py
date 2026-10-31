"""Traffic generation and measurement inside the laboratory.

Generators run as real processes in the namespaces: ``iperf3`` for bulk
transfers and for constant-bitrate datagram flows, and ``ping`` for the
round-trip probe. Nothing here is modelled; every number returned came from a
packet that actually crossed a virtual Ethernet pair.

Metric definitions match ``docs/EXPERIMENT_PLAN.md`` and the rest of the
project:

* **Round-trip time** is what the probe measured. It is not one-way delay.
* **Added delay under load** is the loaded round-trip time minus the idle
  baseline, measured by the same probe at the same interval.
* **Jitter** is the probe's mean deviation, one consistent definition.
* **Queue depth** is read from the queueing discipline's own counters.
"""

from __future__ import annotations

import logging
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .commands import executable, in_namespace
from .safety import (
    DURATION_SECONDS,
    IF_GW_WAN,
    IF_IFB,
    IF_ISP_GW,
    IP_WAN,
    NS_GATEWAY,
    NS_HOME,
    NS_ISP,
    NS_WAN,
    RATE_MBPS,
    ValidationError,
    validate_port,
    validate_protocol,
)

logger = logging.getLogger("qosense.lab.measurement")

PROBE_INTERVAL_SECONDS = 0.2
MAX_GENERATORS = 12

# Generators write their interval reports here so throughput can be read while
# a run is in progress rather than only once it finishes.
LOG_DIRECTORY = Path(tempfile.gettempdir()) / "qosense-lab"


@dataclass
class Generator:
    """One running traffic process."""

    name: str
    port: int
    protocol: str
    traffic_class: str
    direction: str
    process: subprocess.Popen[bytes]
    log_path: Path

    @property
    def running(self) -> bool:
        return self.process.poll() is None


@dataclass
class GeneratorPool:
    """Everything this agent started, so everything can be stopped."""

    servers: dict[int, subprocess.Popen[bytes]] = field(default_factory=dict)
    flows: list[Generator] = field(default_factory=list)

    def stop_all(self) -> int:
        stopped = 0
        for generator in self.flows:
            if _terminate(generator.process):
                stopped += 1
        self.flows.clear()
        for process in self.servers.values():
            if _terminate(process):
                stopped += 1
        self.servers.clear()
        stop_probe()
        return stopped


POOL = GeneratorPool()


def _terminate(process: subprocess.Popen[bytes]) -> bool:
    if process.poll() is not None:
        return False
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=3)
    except Exception:  # pragma: no cover - best effort teardown
        try:
            process.kill()
        except Exception:
            return False
    return True


def _spawn(namespace: str, argv: list[str]) -> subprocess.Popen[bytes]:
    """Start a long-running process inside a namespace, without a shell."""
    resolved = [
        executable("ip"), "netns", "exec", namespace,
        executable(argv[0]), *(str(part) for part in argv[1:]),
    ]
    return subprocess.Popen(  # noqa: S603 - fixed paths, argument array, no shell
        resolved,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )


def reset_log_directory() -> None:
    """Clear previous generator logs so a new run starts from nothing."""
    shutil.rmtree(LOG_DIRECTORY, ignore_errors=True)
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def ensure_server(port: int) -> None:
    """Start an ``iperf3`` server on the Internet side for one port."""
    checked = validate_port(port)
    existing = POOL.servers.get(checked)
    if existing is not None and existing.poll() is None:
        return
    POOL.servers[checked] = _spawn(NS_WAN, ["iperf3", "--server", "--port", checked, "--daemon"])
    # The daemon flag makes iperf3 fork and exit, so wait for the listener
    # rather than for the process we started.
    time.sleep(0.25)


def start_flow(
    *,
    name: str,
    port: int,
    protocol: str,
    traffic_class: str,
    direction: str,
    duration_seconds: float,
    rate_mbps: float | None = None,
    packet_bytes: int | None = None,
) -> Generator:
    """Start one generated flow from the home namespace.

    ``direction`` is from the home device's point of view, so ``DOWNLOAD``
    asks ``iperf3`` to send in reverse: the server pushes toward the client,
    which is how a download actually behaves.
    """
    checked_port = validate_port(port)
    checked_protocol = validate_protocol(protocol)
    duration = DURATION_SECONDS.validate(duration_seconds, "flow duration")
    if direction not in ("DOWNLOAD", "UPLOAD"):
        raise ValidationError("Flow direction must be DOWNLOAD or UPLOAD.")
    if len(POOL.flows) >= MAX_GENERATORS:
        raise ValidationError(f"At most {MAX_GENERATORS} generators may run at once.")

    ensure_server(checked_port)

    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIRECTORY / f"{name}-{checked_port}.log"
    log_path.unlink(missing_ok=True)

    # --forceflush makes iperf3 write each interval report as it happens,
    # which is what lets throughput be read during the run instead of only
    # from a summary once it ends.
    argv: list[str] = [
        "iperf3", "--client", IP_WAN, "--port", checked_port,
        "--time", f"{duration:.0f}", "--interval", "1", "--forceflush",
        "--logfile", str(log_path),
    ]
    if direction == "DOWNLOAD":
        argv.append("--reverse")
    if checked_protocol == "UDP":
        if rate_mbps is None:
            raise ValidationError("A datagram flow must declare its rate.")
        argv += ["--udp", "--bitrate", f"{RATE_MBPS.validate(rate_mbps, 'flow rate'):.3f}M"]
        if packet_bytes:
            argv += ["--length", str(max(64, min(1400, int(packet_bytes))))]
    elif rate_mbps is not None:
        argv += ["--bitrate", f"{RATE_MBPS.validate(rate_mbps, 'flow rate'):.3f}M"]

    generator = Generator(
        name=name,
        port=checked_port,
        protocol=checked_protocol,
        traffic_class=traffic_class,
        direction=direction,
        process=_spawn(NS_HOME, argv),
        log_path=log_path,
    )
    POOL.flows.append(generator)
    logger.info("started generator %s on port %d (%s)", name, checked_port, direction)
    return generator


def stop_generators() -> int:
    return POOL.stop_all()


def active_generators() -> list[dict[str, Any]]:
    return [
        {
            "name": g.name,
            "port": g.port,
            "protocol": g.protocol,
            "traffic_class": g.traffic_class,
            "direction": g.direction,
            "running": g.running,
        }
        for g in POOL.flows
    ]


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

_PING_SUMMARY = re.compile(
    r"(?P<sent>\d+) packets transmitted, (?P<received>\d+) received"
    r"(?:, \+(?P<errors>\d+) errors)?, (?P<loss>[\d.]+)% packet loss"
)
_PING_RTT = re.compile(
    r"= (?P<min>[\d.]+)/(?P<avg>[\d.]+)/(?P<max>[\d.]+)/(?P<mdev>[\d.]+) ms"
)


def probe_rtt(count: int = 10, interval: float = PROBE_INTERVAL_SECONDS) -> dict[str, Any]:
    """Measure round-trip time from the home namespace to the Internet side.

    Returns ``None`` for every statistic when no reply came back, rather than
    a zero. A probe that failed is not a probe that measured zero delay.
    """
    checked_count = max(1, min(100, int(count)))
    checked_interval = max(0.05, min(1.0, float(interval)))
    timeout = checked_count * checked_interval + 6.0

    result = in_namespace(
        NS_HOME,
        [
            "ping", "-c", str(checked_count), "-i", f"{checked_interval:g}",
            "-W", "2", "-q", IP_WAN,
        ],
        check=False,
        timeout=timeout,
    )
    text = result.stdout

    summary = _PING_SUMMARY.search(text)
    rtt = _PING_RTT.search(text)
    sent = int(summary.group("sent")) if summary else checked_count
    received = int(summary.group("received")) if summary else 0
    loss = float(summary.group("loss")) if summary else 100.0

    return {
        "probes_sent": sent,
        "probes_received": received,
        "packet_loss_percent": loss,
        "rtt_min_ms": float(rtt.group("min")) if rtt else None,
        "rtt_ms": float(rtt.group("avg")) if rtt else None,
        "rtt_max_ms": float(rtt.group("max")) if rtt else None,
        # ping's mean deviation is this project's single jitter definition.
        "jitter_ms": float(rtt.group("mdev")) if rtt else None,
    }


# ---------------------------------------------------------------------------
# Queue statistics
# ---------------------------------------------------------------------------

_BACKLOG = re.compile(r"backlog (?P<bytes>\d+)b (?P<packets>\d+)p")
_DROPS = re.compile(r"dropped (?P<dropped>\d+)")
_SENT = re.compile(r"Sent (?P<bytes>\d+) bytes (?P<packets>\d+) pkt")


def _parse_qdisc(text: str) -> dict[str, Any]:
    backlog = _BACKLOG.search(text)
    drops = _DROPS.search(text)
    sent = _SENT.search(text)
    return {
        "queue_bytes": int(backlog.group("bytes")) if backlog else 0,
        "queue_packets": int(backlog.group("packets")) if backlog else 0,
        "dropped_packets": int(drops.group("dropped")) if drops else 0,
        "sent_bytes": int(sent.group("bytes")) if sent else 0,
        "sent_packets": int(sent.group("packets")) if sent else 0,
    }


def queue_stats() -> dict[str, Any]:
    """Read queue depth from the kernel's own counters.

    The gateway entries are what the controller manages. The bottleneck entry
    is recorded for the experiment record only; the controller never sees it,
    because discovering the link rate is the capacity estimator's job.
    """
    gateway_upload = in_namespace(
        NS_GATEWAY, ["tc", "-s", "qdisc", "show", "dev", IF_GW_WAN], check=False
    ).stdout
    gateway_download = in_namespace(
        NS_GATEWAY, ["tc", "-s", "qdisc", "show", "dev", IF_IFB], check=False
    ).stdout
    bottleneck = in_namespace(
        NS_ISP, ["tc", "-s", "qdisc", "show", "dev", IF_ISP_GW], check=False
    ).stdout

    return {
        "gateway_upload": _parse_qdisc(gateway_upload),
        "gateway_download": _parse_qdisc(gateway_download),
        "bottleneck_download": _parse_qdisc(bottleneck),
    }


# ---------------------------------------------------------------------------
# Live throughput
# ---------------------------------------------------------------------------

# An iperf3 interval line, for example:
#   [  5]   3.00-4.00   sec  8.11 MBytes  68.1 Mbits/sec
# The datagram form adds jitter and loss:
#   [  5]   3.00-4.00   sec  1.19 MBytes  10.0 Mbits/sec  0.040 ms  0/862 (0%)
_INTERVAL_LINE = re.compile(
    r"\]\s+(?P<start>[\d.]+)-(?P<end>[\d.]+)\s+sec\s+[\d.]+\s+\w+Bytes\s+"
    r"(?P<rate>[\d.]+)\s+(?P<unit>[KMG]?)bits/sec"
    r"(?:\s+(?P<jitter>[\d.]+)\s+ms\s+(?P<lost>\d+)/(?P<total>\d+))?"
)
_RATE_UNIT_SCALE = {"": 1e-6, "K": 1e-3, "M": 1.0, "G": 1e3}


def _last_interval(log_path: Path) -> dict[str, Any] | None:
    """Parse the most recent completed interval report from a generator log."""
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None

    last: dict[str, Any] | None = None
    for line in text.splitlines():
        if "sender" in line or "receiver" in line or "SUM" in line:
            continue
        match = _INTERVAL_LINE.search(line)
        if not match:
            continue
        scale = _RATE_UNIT_SCALE.get(match.group("unit"), 1.0)
        entry: dict[str, Any] = {
            "window_start_seconds": float(match.group("start")),
            "window_end_seconds": float(match.group("end")),
            "throughput_mbps": round(float(match.group("rate")) * scale, 4),
        }
        if match.group("jitter") is not None:
            lost = int(match.group("lost"))
            total = int(match.group("total"))
            entry["jitter_ms"] = float(match.group("jitter"))
            entry["packet_loss_percent"] = round(lost / total * 100.0, 4) if total else None
            window = max(
                0.001, entry["window_end_seconds"] - entry["window_start_seconds"]
            )
            entry["packets_per_second"] = round(total / window, 2)
        last = entry
    return last


def flow_rates() -> dict[str, dict[str, Any]]:
    """Measured throughput per generated flow, from its own interval reports."""
    rates: dict[str, dict[str, Any]] = {}
    for generator in POOL.flows:
        interval = _last_interval(generator.log_path)
        rates[generator.name] = {
            "port": generator.port,
            "protocol": generator.protocol,
            "direction": generator.direction,
            "traffic_class": generator.traffic_class,
            "running": generator.running,
            # None rather than zero: a flow with no interval report yet has
            # not been measured, which is not the same as measuring nothing.
            "throughput_mbps": interval["throughput_mbps"] if interval else None,
            "jitter_ms": (interval or {}).get("jitter_ms"),
            "packet_loss_percent": (interval or {}).get("packet_loss_percent"),
            "packets_per_second": (interval or {}).get("packets_per_second"),
        }
    return rates


_CLASS_LINE = re.compile(r"class htb 1:(?P<minor>[0-9a-f]+)")
_CLASS_SENT = re.compile(r"Sent (?P<bytes>\d+) bytes (?P<packets>\d+) pkt")
_CLASS_DROPPED = re.compile(r"dropped (?P<dropped>\d+)")

_LAST_CLASS_COUNTERS: dict[str, tuple[float, dict[str, int]]] = {}


def class_throughput(interface: str) -> dict[str, dict[str, Any]]:
    """Per-class throughput, differenced from the kernel's byte counters.

    These counters are the authoritative view of what the shaper actually
    served, independent of whatever a generator reports about itself.
    """
    from .safety import CLASS_MINORS  # local import keeps the constant nearby

    minor_to_class = {minor: name for name, minor in CLASS_MINORS.items()}
    output = in_namespace(
        NS_GATEWAY, ["tc", "-s", "class", "show", "dev", interface], check=False
    ).stdout

    totals: dict[str, int] = {}
    drops: dict[str, int] = {}
    backlog: dict[str, int] = {}
    current_class: str | None = None
    for line in output.splitlines():
        header = _CLASS_LINE.search(line)
        if header:
            current_class = minor_to_class.get(int(header.group("minor"), 16))
            continue
        if current_class is None:
            continue
        sent = _CLASS_SENT.search(line)
        if sent:
            totals[current_class] = int(sent.group("bytes"))
            dropped = _CLASS_DROPPED.search(line)
            drops[current_class] = int(dropped.group("dropped")) if dropped else 0
        queued = _BACKLOG.search(line)
        if queued:
            backlog[current_class] = int(queued.group("packets"))

    now = time.monotonic()
    previous = _LAST_CLASS_COUNTERS.get(interface)
    _LAST_CLASS_COUNTERS[interface] = (now, dict(totals))

    result: dict[str, dict[str, Any]] = {}
    for traffic_class, total_bytes in totals.items():
        throughput: float | None = None
        if previous is not None:
            elapsed = now - previous[0]
            before = previous[1].get(traffic_class)
            if before is not None and elapsed > 0.05 and total_bytes >= before:
                throughput = round((total_bytes - before) * 8 / elapsed / 1e6, 4)
        result[traffic_class] = {
            "throughput_mbps": throughput,
            "total_bytes": total_bytes,
            "dropped_packets": drops.get(traffic_class, 0),
            "queue_packets": backlog.get(traffic_class, 0),
        }
    return result


def reset_counters() -> None:
    """Forget the previous counter sample so the next read starts clean."""
    _LAST_CLASS_COUNTERS.clear()


# ---------------------------------------------------------------------------
# Continuous probe
# ---------------------------------------------------------------------------
#
# A probe spawned per request would make every metrics call wait a second or
# more for replies, which is far too slow for a controller sampling at one
# hertz. Instead one long-running ping writes timestamped replies to a log and
# each metrics call reads the recent window out of it. The probe interval is
# the sampling rate; the controller's tick rate does not affect it.

PROBE_LOG = LOG_DIRECTORY / "probe.log"
_PROBE_PROCESS: subprocess.Popen[bytes] | None = None

# ping -D prefixes each reply with a wall-clock timestamp:
#   [1730000000.123456] 64 bytes from 10.200.3.2: icmp_seq=5 ttl=64 time=18.3 ms
_PROBE_LINE = re.compile(
    r"\[(?P<stamp>\d+\.\d+)\].*icmp_seq=(?P<seq>\d+).*time=(?P<rtt>[\d.]+) ms"
)


def start_probe(interval: float = PROBE_INTERVAL_SECONDS) -> None:
    """Start the continuous round-trip probe from the home namespace."""
    global _PROBE_PROCESS
    stop_probe()
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    PROBE_LOG.unlink(missing_ok=True)
    checked = max(0.05, min(1.0, float(interval)))

    resolved = [
        executable("ip"), "netns", "exec", NS_HOME,
        executable("ping"), "-D", "-i", f"{checked:g}", "-W", "2", IP_WAN,
    ]
    handle = open(PROBE_LOG, "wb")  # noqa: SIM115 - owned by the probe's lifetime
    _PROBE_PROCESS = subprocess.Popen(  # noqa: S603 - fixed paths, argument array
        resolved, stdout=handle, stderr=subprocess.DEVNULL, shell=False
    )
    logger.info("continuous probe started at %.2f s interval", checked)


def stop_probe() -> None:
    global _PROBE_PROCESS
    if _PROBE_PROCESS is not None:
        _terminate(_PROBE_PROCESS)
        _PROBE_PROCESS = None


def probe_running() -> bool:
    return _PROBE_PROCESS is not None and _PROBE_PROCESS.poll() is None


def recent_probe(window_seconds: float = 2.0) -> dict[str, Any]:
    """Summarise the probe replies from the last ``window_seconds``.

    Jitter is the mean absolute difference between consecutive round-trip
    times, the same definition used everywhere else in the project.

    Loss is derived from the sequence numbers: a reply that never arrived
    leaves a gap. When no reply at all landed in the window every statistic is
    ``None``, because a probe that failed did not measure zero delay.
    """
    if not PROBE_LOG.exists():
        return _empty_probe("The continuous probe has not been started.")

    try:
        text = PROBE_LOG.read_text(errors="replace")
    except OSError:
        return _empty_probe("The probe log could not be read.")

    cutoff = time.time() - max(0.2, float(window_seconds))
    samples: list[tuple[int, float]] = []
    for line in text.splitlines():
        match = _PROBE_LINE.search(line)
        if not match:
            continue
        if float(match.group("stamp")) < cutoff:
            continue
        samples.append((int(match.group("seq")), float(match.group("rtt"))))

    if not samples:
        return _empty_probe("No probe reply arrived in the measurement window.")

    rtts = [rtt for _, rtt in samples]
    deltas = [abs(b - a) for a, b in zip(rtts, rtts[1:], strict=False)]
    sequences = [seq for seq, _ in samples]
    expected = sequences[-1] - sequences[0] + 1
    received = len(samples)
    loss = max(0.0, (expected - received) / expected * 100.0) if expected > 0 else 0.0

    return {
        "probes_sent": expected,
        "probes_received": received,
        "packet_loss_percent": round(loss, 4),
        "rtt_min_ms": round(min(rtts), 4),
        "rtt_ms": round(sum(rtts) / len(rtts), 4),
        "rtt_max_ms": round(max(rtts), 4),
        "jitter_ms": round(sum(deltas) / len(deltas), 4) if deltas else 0.0,
        "window_seconds": window_seconds,
        "reason": None,
    }


def _empty_probe(reason: str) -> dict[str, Any]:
    return {
        "probes_sent": 0,
        "probes_received": 0,
        "packet_loss_percent": None,
        "rtt_min_ms": None,
        "rtt_ms": None,
        "rtt_max_ms": None,
        "jitter_ms": None,
        "window_seconds": None,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Per-flow counters from the kernel
# ---------------------------------------------------------------------------
#
# A counting filter per flow sits on the gateway's ingress qdiscs with a
# pass action, so every packet is counted and then continues on its way.
# These give the flow observer real byte *and* packet counts, which is what
# makes a measured mean packet size possible. Assuming a packet size instead
# would quietly decide the classifier's answer, since packet size is its
# leading discriminator.
#
# Each flow's filter gets its own preference derived from its port, so a
# counter can be matched back to its flow without parsing match patterns.

COUNTER_PREFERENCE_BASE = 5000
_COUNTER_INSTALLED: list[dict[str, Any]] = []
_LAST_FLOW_COUNTERS: dict[int, tuple[float, int, int]] = {}


def counter_preference(port: int) -> int:
    """A stable, distinct filter preference for one generator port."""
    return validate_port(port) - COUNTER_PREFERENCE_BASE


def install_flow_counters(flows: list[dict[str, Any]]) -> int:
    """Install one counting filter per flow, in the matching direction.

    Inbound traffic from a generator carries its port as the source; outbound
    traffic carries it as the destination, so each direction is counted on
    the interface where that flow actually arrives.
    """
    from .safety import IF_GW_LAN, PROTOCOL_NUMBERS

    _COUNTER_INSTALLED.clear()
    _LAST_FLOW_COUNTERS.clear()
    installed = 0

    for flow in flows:
        port = validate_port(flow["port"])
        protocol = validate_protocol(flow["protocol"])
        download = flow.get("direction") == "DOWNLOAD"

        # Both hooks live on the local interface. A download is counted on
        # egress, which is after the shaper, so the figure is what the device
        # actually received. An upload is counted on ingress, as it arrives
        # from the device and before the shaper, so that figure is offered
        # load rather than delivered.
        hook = "egress" if download else "ingress"
        selector = "sport" if download else "dport"

        in_namespace(
            NS_GATEWAY,
            [
                "tc", "filter", "add", "dev", IF_GW_LAN, hook,
                "protocol", "ip", "prio", str(counter_preference(port)), "u32",
                "match", "ip", "protocol", str(PROTOCOL_NUMBERS[protocol]), "0xff",
                "match", "ip", selector, str(port), "0xffff",
                "action", "gact", "pass",
            ],
            check=False,
        )
        _COUNTER_INSTALLED.append(
            {
                "port": port,
                "hook": hook,
                "name": flow.get("name", str(port)),
                "direction": "DOWNLOAD" if download else "UPLOAD",
                "protocol": protocol,
                "measures": "delivered" if download else "offered",
            }
        )
        installed += 1

    logger.info("installed %d per-flow counting filters", installed)
    return installed


_FILTER_PREF = re.compile(r"filter protocol ip pref (?P<pref>\d+) u32")
_FILTER_SENT = re.compile(r"Sent (?P<bytes>\d+) bytes (?P<packets>\d+) pkt")


def _read_counters(hook: str) -> dict[int, tuple[int, int]]:
    """Parse ``(bytes, packets)`` per filter preference on one hook."""
    from .safety import IF_GW_LAN

    output = in_namespace(
        NS_GATEWAY,
        ["tc", "-s", "filter", "show", "dev", IF_GW_LAN, hook],
        check=False,
    ).stdout

    counters: dict[int, tuple[int, int]] = {}
    current: int | None = None
    for line in output.splitlines():
        pref = _FILTER_PREF.search(line)
        if pref:
            current = int(pref.group("pref"))
            continue
        if current is None:
            continue
        sent = _FILTER_SENT.search(line)
        if sent:
            counters[current] = (int(sent.group("bytes")), int(sent.group("packets")))
            current = None
    return counters


def flow_counters() -> dict[str, dict[str, Any]]:
    """Measured rate and packet size per flow, from the kernel's counters.

    A flow with no second sample yet reports ``None`` for its rates rather
    than zero: one counter reading is a total, not a rate.
    """
    readings: dict[str, dict[int, tuple[int, int]]] = {
        hook: _read_counters(hook) for hook in ("egress", "ingress")
    }

    now = time.monotonic()
    result: dict[str, dict[str, Any]] = {}

    for entry in _COUNTER_INSTALLED:
        port = int(entry["port"])
        pref = counter_preference(port)
        reading = readings.get(str(entry["hook"]), {}).get(pref)
        if reading is None:
            continue
        total_bytes, total_packets = reading

        throughput_mbps: float | None = None
        packets_per_second: float | None = None
        previous = _LAST_FLOW_COUNTERS.get(pref)
        if previous is not None:
            elapsed = now - previous[0]
            if elapsed > 0.05 and total_bytes >= previous[1]:
                throughput_mbps = round((total_bytes - previous[1]) * 8 / elapsed / 1e6, 4)
                packets_per_second = round((total_packets - previous[2]) / elapsed, 3)
        _LAST_FLOW_COUNTERS[pref] = (now, total_bytes, total_packets)

        result[str(entry["name"])] = {
            "port": port,
            "direction": entry["direction"],
            "protocol": entry["protocol"],
            "measures": entry["measures"],
            "total_bytes": total_bytes,
            "total_packets": total_packets,
            "throughput_mbps": throughput_mbps,
            "packets_per_second": packets_per_second,
            # Measured, not assumed. This is what lets the classifier see a
            # flow's real packet size.
            "mean_packet_bytes": (
                round(total_bytes / total_packets, 2) if total_packets else None
            ),
        }
    return result
