"""M5 — real Linux traffic control enforcement.

This installs the two shaping paths from ``docs/ARCHITECTURE.md`` on the
gateway namespace:

**Upload** — Hierarchical Token Bucket classes on the wide area network
interface's egress, each holding a Fair Queuing Controlled Delay queue::

    home device → qos-g0 → [ HTB class on qos-g1 egress → FQ-CoDel ] → provider

**Download** — the wide area network interface has no egress for inbound
traffic, so inbound packets are redirected to an Intermediate Functional Block
device and shaped on *its* egress::

    provider → qos-g1 ingress → mirred redirect → [ HTB class on qos-ifb0
    egress → FQ-CoDel ] → qos-g0 → home device

Why shaping below the bottleneck is the whole trick
---------------------------------------------------

The gateway cannot drain a queue that forms in the provider's buffer. By
shaping slightly below the rate the provider will deliver, the gateway becomes
the slowest point on the path, so the standing queue forms *here* instead,
where Fair Queuing Controlled Delay can keep it short. If the policy's total
were set at or above the real link rate, the queue would stay in the provider
and nothing would improve. The capacity estimator's headroom factor is what
keeps the total below the line.

Transaction shape
-----------------

Every policy change is snapshot, apply, read back, compare, and restore on
mismatch. ``APPLIED`` is returned only when the kernel's own reported state
matches what was asked for. A read-back that disagrees is a failure, not a
warning.
"""

from __future__ import annotations

import itertools
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .commands import CommandError, in_namespace
from .safety import (
    CLASS_MINORS,
    DEFAULT_CLASS,
    IF_GW_WAN,
    IF_IFB,
    NS_GATEWAY,
    PROTOCOL_NUMBERS,
    RATE_MBPS,
    ValidationError,
    class_minor_hex,
    format_rate,
    validate_identifier,
    validate_port,
    validate_profile,
    validate_protocol,
    validate_traffic_class,
)

logger = logging.getLogger("qosense.lab.enforcement")

_transaction_counter = itertools.count(1)
_snapshot_counter = itertools.count(1)

# A read-back rate may differ from the request by this much and still count as
# a match. Traffic control stores rates in bytes per second internally, so a
# small rounding difference is expected and is not a mismatch.
READ_BACK_TOLERANCE_MBPS = 0.05
READ_BACK_TOLERANCE_FRACTION = 0.01

# Fair Queuing Controlled Delay settings. The target is the standing queue
# delay it aims to hold; the interval is the window over which it decides.
CODEL_TARGET_MS = 5
CODEL_INTERVAL_MS = 100

# Filter preference for the ingress redirect. Counting filters occupy the
# lower preferences so a packet is counted before it is redirected.
REDIRECT_PREFERENCE = 900

_RATE_PATTERN = re.compile(r"(?P<value>[\d.]+)(?P<unit>[KMGT]?)(?:bit|bps)", re.IGNORECASE)
_UNIT_SCALE = {"": 1.0, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}


def parse_rate_to_mbps(text: str) -> float | None:
    """Parse a traffic-control rate such as ``55Mbit`` into megabits."""
    match = _RATE_PATTERN.search(text)
    if not match:
        return None
    value = float(match.group("value"))
    scale = _UNIT_SCALE.get(match.group("unit").upper(), 1.0)
    return value * scale / 1e6


@dataclass(frozen=True)
class ClassRate:
    traffic_class: str
    ceiling_mbps: float
    floor_mbps: float

    def validated(self) -> ClassRate:
        validate_traffic_class(self.traffic_class)
        ceiling = RATE_MBPS.validate(self.ceiling_mbps, f"{self.traffic_class} ceiling")
        floor = RATE_MBPS.validate(max(self.floor_mbps, 0.05), f"{self.traffic_class} floor")
        if floor > ceiling:
            raise ValidationError(
                f"{self.traffic_class} floor {floor:g} Mbps exceeds its ceiling "
                f"{ceiling:g} Mbps."
            )
        return ClassRate(self.traffic_class, ceiling, floor)


@dataclass(frozen=True)
class FlowSelector:
    """Which packets belong to which class.

    The classifier decided the class from behaviour; this only says how to
    recognise that flow's packets in the data path. A real gateway would carry
    the same decision as a connection mark.
    """

    port: int
    protocol: str
    traffic_class: str

    def validated(self) -> FlowSelector:
        return FlowSelector(
            port=validate_port(self.port),
            protocol=validate_protocol(self.protocol),
            traffic_class=validate_traffic_class(self.traffic_class),
        )


@dataclass(frozen=True)
class DirectionProfile:
    total_mbps: float
    classes: tuple[ClassRate, ...]

    def validated(self) -> DirectionProfile:
        total = RATE_MBPS.validate(self.total_mbps, "total rate")
        classes = tuple(c.validated() for c in self.classes)
        if not classes:
            raise ValidationError("A shaping direction must define at least one class.")
        floors = sum(c.floor_mbps for c in classes)
        if floors > total * 1.001:
            raise ValidationError(
                f"Guaranteed floors total {floors:.2f} Mbps, which exceeds the shaped "
                f"total of {total:.2f} Mbps."
            )
        return DirectionProfile(total, classes)


@dataclass(frozen=True)
class PolicyRequest:
    policy_id: str
    profile: str
    download: DirectionProfile
    upload: DirectionProfile
    flows: tuple[FlowSelector, ...] = ()

    def validated(self) -> PolicyRequest:
        return PolicyRequest(
            policy_id=validate_identifier(self.policy_id, "policy identifier"),
            profile=validate_profile(self.profile),
            download=self.download.validated(),
            upload=self.upload.validated(),
            flows=tuple(f.validated() for f in self.flows),
        )


@dataclass
class TrafficControlState:
    """What the kernel reports for one shaped device."""

    interface: str
    qdisc: str
    classes: str
    filters: str
    class_rates_mbps: dict[str, float] = field(default_factory=dict)
    class_ceilings_mbps: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "interface": self.interface,
            "qdisc": self.qdisc,
            "classes": self.classes,
            "filters": self.filters,
            "class_rates_mbps": self.class_rates_mbps,
            "class_ceilings_mbps": self.class_ceilings_mbps,
        }


# ---------------------------------------------------------------------------
# Reading state back
# ---------------------------------------------------------------------------

_MINOR_TO_CLASS = {minor: name for name, minor in CLASS_MINORS.items()}


def read_state(interface: str) -> TrafficControlState:
    """Read the kernel's own view of one shaped device.

    This is the evidence a policy application is judged against. Nothing here
    reports what was requested; every value comes from ``tc``.
    """
    qdisc = in_namespace(
        NS_GATEWAY, ["tc", "-s", "qdisc", "show", "dev", interface], check=False
    ).stdout
    classes = in_namespace(
        NS_GATEWAY, ["tc", "-s", "class", "show", "dev", interface], check=False
    ).stdout
    filters = in_namespace(
        NS_GATEWAY, ["tc", "filter", "show", "dev", interface], check=False
    ).stdout

    rates: dict[str, float] = {}
    ceilings: dict[str, float] = {}
    for line in classes.splitlines():
        if "htb" not in line or "class" not in line:
            continue
        handle = re.search(r"class htb 1:([0-9a-f]+)", line)
        if not handle:
            continue
        minor = int(handle.group(1), 16)
        name = _MINOR_TO_CLASS.get(minor)
        if name is None:
            continue
        rate_match = re.search(r"\brate (\S+)", line)
        ceil_match = re.search(r"\bceil (\S+)", line)
        if rate_match:
            parsed = parse_rate_to_mbps(rate_match.group(1))
            if parsed is not None:
                rates[name] = round(parsed, 4)
        if ceil_match:
            parsed = parse_rate_to_mbps(ceil_match.group(1))
            if parsed is not None:
                ceilings[name] = round(parsed, 4)

    return TrafficControlState(
        interface=interface,
        qdisc=qdisc.strip(),
        classes=classes.strip(),
        filters=filters.strip(),
        class_rates_mbps=rates,
        class_ceilings_mbps=ceilings,
    )


def read_all_state() -> dict[str, Any]:
    """Read both shaped devices. Used by the ``read_tc_state`` operation."""
    return {
        "upload": read_state(IF_GW_WAN).as_dict(),
        "download": read_state(IF_IFB).as_dict(),
        "read_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Installing and clearing
# ---------------------------------------------------------------------------


def clear_shaping() -> None:
    """Remove the shaping this agent installs, and nothing else.

    Two things are deliberately left alone. The emulated bottleneck belongs
    to the provider namespace and is not the gateway's to remove. The ingress
    qdiscs carry the per-flow measurement counters, so tearing them down with
    the shaping would blind the observer every time a policy changed; only
    the redirect filter is removed from them.
    """
    in_namespace(NS_GATEWAY, ["tc", "qdisc", "del", "dev", IF_GW_WAN, "root"], check=False)
    in_namespace(NS_GATEWAY, ["tc", "qdisc", "del", "dev", IF_IFB, "root"], check=False)
    in_namespace(
        NS_GATEWAY,
        [
            "tc", "filter", "del", "dev", IF_GW_WAN, "parent", "ffff:",
            "prio", str(REDIRECT_PREFERENCE),
        ],
        check=False,
    )


def _install_htb(interface: str, direction: DirectionProfile) -> None:
    """Build the class hierarchy on one device."""
    default_minor = class_minor_hex(DEFAULT_CLASS)
    in_namespace(
        NS_GATEWAY,
        [
            "tc", "qdisc", "add", "dev", interface, "root", "handle", "1:",
            "htb", "default", default_minor,
        ],
    )
    total = format_rate(direction.total_mbps)
    in_namespace(
        NS_GATEWAY,
        [
            "tc", "class", "add", "dev", interface, "parent", "1:", "classid", "1:1",
            "htb", "rate", total, "ceil", total,
        ],
    )

    present = {c.traffic_class for c in direction.classes}
    classes = list(direction.classes)
    if DEFAULT_CLASS not in present:
        # Always provide the default class, or unmatched traffic has nowhere
        # to go and the kernel drops it.
        classes.append(
            ClassRate(DEFAULT_CLASS, ceiling_mbps=direction.total_mbps, floor_mbps=0.05)
        )

    for entry in classes:
        minor = class_minor_hex(entry.traffic_class)
        in_namespace(
            NS_GATEWAY,
            [
                "tc", "class", "add", "dev", interface, "parent", "1:1",
                "classid", f"1:{minor}", "htb",
                "rate", format_rate(entry.floor_mbps),
                "ceil", format_rate(entry.ceiling_mbps),
            ],
        )
        # One Fair Queuing Controlled Delay queue per class keeps the standing
        # queue short inside the class and shares it fairly between flows.
        in_namespace(
            NS_GATEWAY,
            [
                "tc", "qdisc", "add", "dev", interface, "parent", f"1:{minor}",
                "handle", f"{minor}0:", "fq_codel",
                "target", f"{CODEL_TARGET_MS}ms", "interval", f"{CODEL_INTERVAL_MS}ms",
            ],
        )


def _install_filters(
    interface: str, flows: tuple[FlowSelector, ...], match_source_port: bool
) -> None:
    """Direct each known flow's packets into its class.

    ``match_source_port`` picks which end of the connection identifies the
    flow. Inbound traffic from a generator carries its port as the source;
    outbound traffic carries it as the destination.
    """
    for flow in flows:
        protocol_number = PROTOCOL_NUMBERS[flow.protocol]
        port_selector = "sport" if match_source_port else "dport"
        in_namespace(
            NS_GATEWAY,
            [
                "tc", "filter", "add", "dev", interface, "protocol", "ip",
                "parent", "1:", "prio", "1", "u32",
                "match", "ip", "protocol", str(protocol_number), "0xff",
                "match", "ip", port_selector, str(flow.port), "0xffff",
                "flowid", f"1:{class_minor_hex(flow.traffic_class)}",
            ],
        )


def _install_ingress_redirect() -> None:
    """Redirect inbound traffic to the Intermediate Functional Block device.

    The ingress qdisc already exists: the topology creates it so the
    measurement counters can live there permanently. This adds only the
    redirect, at a preference after the counting filters so each packet is
    counted before it is moved.
    """
    in_namespace(
        NS_GATEWAY,
        [
            "tc", "filter", "add", "dev", IF_GW_WAN, "parent", "ffff:",
            "protocol", "ip", "prio", str(REDIRECT_PREFERENCE), "u32",
            "match", "u32", "0", "0",
            "action", "mirred", "egress", "redirect", "dev", IF_IFB,
        ],
    )


def install(request: PolicyRequest) -> None:
    """Install a validated policy on both directions."""
    clear_shaping()

    # Upload: ordinary egress shaping on the wide area network interface.
    _install_htb(IF_GW_WAN, request.upload)
    _install_filters(IF_GW_WAN, request.flows, match_source_port=False)

    # Download: redirect inbound traffic and shape it on the virtual device.
    _install_ingress_redirect()
    _install_htb(IF_IFB, request.download)
    _install_filters(IF_IFB, request.flows, match_source_port=True)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@dataclass
class Snapshot:
    snapshot_id: str
    taken_at: str
    upload: TrafficControlState
    download: TrafficControlState
    had_shaping: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "taken_at": self.taken_at,
            "had_shaping": self.had_shaping,
            "upload": self.upload.as_dict(),
            "download": self.download.as_dict(),
        }


_SNAPSHOTS: dict[str, Snapshot] = {}
# The last successfully applied request, so a restore can rebuild it exactly.
_APPLIED: dict[str, PolicyRequest] = {}


def take_snapshot() -> Snapshot:
    upload = read_state(IF_GW_WAN)
    download = read_state(IF_IFB)
    snapshot = Snapshot(
        snapshot_id=f"snap-{next(_snapshot_counter):04d}",
        taken_at=datetime.now(UTC).isoformat(),
        upload=upload,
        download=download,
        had_shaping=bool(upload.class_rates_mbps or download.class_rates_mbps),
    )
    _SNAPSHOTS[snapshot.snapshot_id] = snapshot
    return snapshot


def _rates_match(intended: DirectionProfile, observed: dict[str, float]) -> list[str]:
    """Compare what was asked for against what the kernel reports."""
    mismatches: list[str] = []
    for entry in intended.classes:
        actual = observed.get(entry.traffic_class)
        if actual is None:
            mismatches.append(f"{entry.traffic_class}: no class found in the read-back")
            continue
        tolerance = max(
            READ_BACK_TOLERANCE_MBPS, entry.ceiling_mbps * READ_BACK_TOLERANCE_FRACTION
        )
        if abs(actual - entry.ceiling_mbps) > tolerance:
            mismatches.append(
                f"{entry.traffic_class}: asked for {entry.ceiling_mbps:.3f} Mbps, "
                f"kernel reports {actual:.3f} Mbps"
            )
    return mismatches


def apply_policy(request: PolicyRequest) -> dict[str, Any]:
    """Apply a policy transactionally and verify it against the kernel.

    Returns ``APPLIED`` only when the read-back matches. On any mismatch or
    error the snapshot is restored and the restore is itself verified.
    """
    validated = request.validated()
    transaction_id = f"txn-{next(_transaction_counter):05d}"
    requested_at = datetime.now(UTC)
    started = time.perf_counter()

    snapshot = take_snapshot()

    def finish(status: str, error: str | None, mismatches: list[str]) -> dict[str, Any]:
        upload_state = read_state(IF_GW_WAN)
        download_state = read_state(IF_IFB)
        return {
            "policy_id": validated.policy_id,
            "profile": validated.profile,
            "transaction_id": transaction_id,
            "snapshot_id": snapshot.snapshot_id,
            "status": status,
            "data_mode": "LIVE",
            "requested_at": requested_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "apply_duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "error": error,
            "mismatches": mismatches,
            "read_back": {
                "source": "Linux traffic control on the gateway namespace",
                "matches_intent": status == "APPLIED",
                "upload": upload_state.as_dict(),
                "download": download_state.as_dict(),
                "observed_class_rates_mbps": download_state.class_ceilings_mbps,
                "upload_path": [
                    "home device",
                    "qos-g0 ingress",
                    "classified by traffic-control filter",
                    "Hierarchical Token Bucket class on qos-g1 egress",
                    "Fair Queuing Controlled Delay queue",
                    "provider",
                ],
                "download_path": [
                    "provider",
                    "qos-g1 ingress",
                    "classified by traffic-control filter",
                    "redirect to Intermediate Functional Block qos-ifb0",
                    "Hierarchical Token Bucket class on qos-ifb0 egress",
                    "Fair Queuing Controlled Delay queue",
                    "home device",
                ],
            },
        }

    try:
        install(validated)
    except (CommandError, ValidationError) as exc:
        restore_snapshot(snapshot.snapshot_id)
        return finish("FAILED_ROLLED_BACK", str(exc), [])

    upload_state = read_state(IF_GW_WAN)
    download_state = read_state(IF_IFB)
    mismatches = _rates_match(validated.upload, upload_state.class_ceilings_mbps)
    mismatches += _rates_match(validated.download, download_state.class_ceilings_mbps)

    if mismatches:
        logger.warning("read-back mismatch, rolling back: %s", mismatches)
        restore = restore_snapshot(snapshot.snapshot_id)
        status = "FAILED_ROLLED_BACK" if restore["restored"] else "FAILED_SAFE_MODE"
        return finish(status, "Read-back did not match the requested state.", mismatches)

    _APPLIED[validated.policy_id] = validated
    return finish("APPLIED", None, [])


def restore_snapshot(snapshot_id: str) -> dict[str, Any]:
    """Restore a snapshot and verify the restore itself."""
    validate_identifier(snapshot_id, "snapshot identifier")
    snapshot = _SNAPSHOTS.get(snapshot_id)
    started = time.perf_counter()

    if snapshot is None:
        return {
            "snapshot_id": snapshot_id,
            "restored": False,
            "verified": False,
            "error": f"Snapshot '{snapshot_id}' is not known to this agent.",
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    try:
        clear_shaping()
        if snapshot.had_shaping:
            # Rebuild from the request that produced that state. A snapshot of
            # text output cannot be replayed into the kernel, so the agent
            # keeps the validated request that created it.
            previous = next(
                (
                    request
                    for policy_id, request in reversed(list(_APPLIED.items()))
                    if policy_id
                ),
                None,
            )
            if previous is not None:
                install(previous)
    except (CommandError, ValidationError) as exc:
        return {
            "snapshot_id": snapshot_id,
            "restored": False,
            "verified": False,
            "error": str(exc),
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    after_upload = read_state(IF_GW_WAN)
    after_download = read_state(IF_IFB)
    expected_empty = not snapshot.had_shaping
    now_empty = not (after_upload.class_ceilings_mbps or after_download.class_ceilings_mbps)
    verified = now_empty if expected_empty else not now_empty

    return {
        "snapshot_id": snapshot_id,
        "restored": True,
        "verified": verified,
        "error": None if verified else "Restore could not be confirmed by read-back.",
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "state": {
            "upload": after_upload.as_dict(),
            "download": after_download.as_dict(),
        },
    }


def known_safe_profile(download_mbps: float, upload_mbps: float) -> PolicyRequest:
    """The configuration the watchdog falls back to.

    One class covering everything at the full shaped rate with Fair Queuing
    Controlled Delay underneath. It prioritises nothing, which is the point:
    it is the state that cannot be wrong, only unhelpful.
    """
    return PolicyRequest(
        policy_id="policy-known-safe",
        profile="CONSERVATIVE_SAFE",
        download=DirectionProfile(
            total_mbps=download_mbps,
            classes=(ClassRate(DEFAULT_CLASS, download_mbps, 0.05),),
        ),
        upload=DirectionProfile(
            total_mbps=upload_mbps,
            classes=(ClassRate(DEFAULT_CLASS, upload_mbps, 0.05),),
        ),
        flows=(),
    )
