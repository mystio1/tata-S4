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
import signal
import subprocess
import time
from dataclasses import dataclass, field
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


@dataclass
class Generator:
    """One running traffic process."""

    name: str
    port: int
    protocol: str
    traffic_class: str
    direction: str
    process: subprocess.Popen[bytes]

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

    argv: list[str] = [
        "iperf3", "--client", IP_WAN, "--port", checked_port,
        "--time", f"{duration:.0f}", "--interval", "1",
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
