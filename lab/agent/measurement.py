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
import os
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
            entry["packets_per_second"] = (
                round(total / max(0.001, entry["window_end_seconds"] - entry["window_start_seconds"]), 2)
            )
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
