"""Allow-lists, bounds and validation for the privileged laboratory agent.

This module is the reason the rest of the agent can be read without anxiety.
Every identifier that reaches a privileged command is checked here first, and
nothing that fails a check proceeds.

Three rules hold throughout the agent and are enforced in this file:

1. **No caller ever names an interface or a namespace.** Callers reference a
   scenario or a policy profile by identifier. The interface and namespace
   names are constants owned by the agent, so a request cannot reach the
   host's real networking even if it tries.
2. **No shell, ever.** Commands are argument arrays passed to fixed executable
   paths resolved at start-up. There is no string interpolation into a shell,
   so there is nothing for a crafted identifier to escape into.
3. **Every numeric value is bounded.** Rates, delays and durations are clamped
   to ranges defined here before they are formatted into an argument.

See ``docs/SAFETY_SECURITY.md`` for the trust boundary this implements.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# The laboratory topology. These names are constants, never caller input.
# ---------------------------------------------------------------------------

PREFIX = "qos-"

NS_HOME = "qos-home"
NS_GATEWAY = "qos-gw"
NS_ISP = "qos-isp"
NS_WAN = "qos-wan"

NAMESPACES: tuple[str, ...] = (NS_HOME, NS_GATEWAY, NS_ISP, NS_WAN)

# Interface names, one per end of each virtual Ethernet pair.
IF_HOME_LAN = "qos-h0"  # in qos-home, faces the gateway
IF_GW_LAN = "qos-g0"  # in qos-gw, faces the home
IF_GW_WAN = "qos-g1"  # in qos-gw, faces the Internet service provider
IF_ISP_GW = "qos-i0"  # in qos-isp, faces the gateway
IF_ISP_WAN = "qos-i1"  # in qos-isp, faces the wide area network
IF_WAN = "qos-w0"  # in qos-wan
IF_IFB = "qos-ifb0"  # Intermediate Functional Block, in qos-gw

INTERFACES: tuple[str, ...] = (
    IF_HOME_LAN,
    IF_GW_LAN,
    IF_GW_WAN,
    IF_ISP_GW,
    IF_ISP_WAN,
    IF_WAN,
    IF_IFB,
)

# Addresses. A documentation-style private range that will not collide with a
# home network the developer is actually using.
ADDR_HOME = "10.200.1.2/24"
ADDR_GW_LAN = "10.200.1.1/24"
ADDR_GW_WAN = "10.200.2.1/24"
ADDR_ISP_GW = "10.200.2.2/24"
ADDR_ISP_WAN = "10.200.3.1/24"
ADDR_WAN = "10.200.3.2/24"

IP_HOME = "10.200.1.2"
IP_GW_LAN = "10.200.1.1"
IP_GW_WAN = "10.200.2.1"
IP_ISP_GW = "10.200.2.2"
IP_ISP_WAN = "10.200.3.1"
IP_WAN = "10.200.3.2"

NET_HOME = "10.200.1.0/24"
NET_GW_ISP = "10.200.2.0/24"
NET_ISP_WAN = "10.200.3.0/24"

# Traffic class identifiers in the Hierarchical Token Bucket hierarchy.
#
# Traffic control parses class minor numbers as hexadecimal, so these are
# written as hexadecimal literals and always formatted with ``:x``. That makes
# the number this agent asks for and the number ``tc class show`` prints the
# same string, which is what lets a read-back be matched to a class by
# identifier instead of by guesswork.
CLASS_MINORS: dict[str, int] = {
    "INTERACTIVE": 0x10,
    "GAMING_LIKE": 0x20,
    "WEB_VOICE": 0x30,
    "STREAMING_LIKE": 0x40,
    "BULK": 0x50,
    "UNKNOWN": 0x60,
}
# Unmatched traffic lands here. A shaping gap must never black-hole a flow.
DEFAULT_CLASS = "UNKNOWN"

# Internet protocol numbers for the two transports the laboratory generates.
PROTOCOL_NUMBERS: dict[str, int] = {"TCP": 6, "UDP": 17}

# Ports the laboratory generators are allowed to use. A flow selector naming
# anything outside this range is rejected, so a policy cannot be made to match
# traffic that is not part of the experiment.
PORT_MINIMUM = 5200
PORT_MAXIMUM = 5399

SUPPORTED_CLASSES: tuple[str, ...] = tuple(CLASS_MINORS)

# ---------------------------------------------------------------------------
# Bounds. A caller cannot widen these.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bound:
    minimum: float
    maximum: float
    unit: str

    def clamp(self, value: float) -> float:
        return min(max(float(value), self.minimum), self.maximum)

    def validate(self, value: float, name: str) -> float:
        numeric = float(value)
        if numeric != numeric:  # not a number
            raise ValidationError(f"{name} is not a number.")
        if not (self.minimum <= numeric <= self.maximum):
            raise ValidationError(
                f"{name} must be between {self.minimum:g} and {self.maximum:g} "
                f"{self.unit}; got {numeric:g}."
            )
        return numeric


RATE_MBPS = Bound(0.05, 1000.0, "Mbps")
DELAY_MS = Bound(0.0, 2000.0, "ms")
LOSS_PERCENT = Bound(0.0, 50.0, "%")
DURATION_SECONDS = Bound(1.0, 900.0, "s")
QUEUE_PACKETS = Bound(4, 20_000, "packets")

# Identifiers accepted from a caller: scenario ids, experiment ids, profile
# ids and snapshot ids. Deliberately narrow.
IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
PROFILE_NAME = re.compile(r"^[A-Z_]{3,32}$")


class ValidationError(ValueError):
    """A request failed validation and must not reach a privileged command."""


def validate_identifier(value: str, name: str = "identifier") -> str:
    if not isinstance(value, str) or not IDENTIFIER.match(value):
        raise ValidationError(
            f"{name} must be 1 to 64 characters of letters, digits, dot, colon, "
            f"underscore or hyphen."
        )
    return value


def validate_profile(value: str) -> str:
    if not isinstance(value, str) or not PROFILE_NAME.match(value):
        raise ValidationError("Policy profile must be an upper-case name.")
    return value


def validate_namespace(value: str) -> str:
    """A namespace must be one this agent created. Nothing else is reachable."""
    if value not in NAMESPACES:
        raise ValidationError(
            f"Namespace '{value}' is not part of the QoSense laboratory. "
            f"Permitted: {', '.join(NAMESPACES)}."
        )
    return value


def validate_interface(value: str) -> str:
    """An interface must be one this agent created.

    This is the check that keeps the agent away from the host's real
    networking. The permitted set is a constant, so no request can extend it.
    """
    if value not in INTERFACES:
        raise ValidationError(
            f"Interface '{value}' is not part of the QoSense laboratory. "
            f"Permitted: {', '.join(INTERFACES)}."
        )
    if not value.startswith(PREFIX):
        raise ValidationError("Interface names must carry the laboratory prefix.")
    return value


def validate_traffic_class(value: str) -> str:
    if value not in CLASS_MINORS:
        raise ValidationError(
            f"Traffic class '{value}' is not supported. "
            f"Permitted: {', '.join(SUPPORTED_CLASSES)}."
        )
    return value


def validate_class_rates(rates: dict[str, float]) -> dict[str, float]:
    """Validate a per-class rate table from a policy proposal."""
    if not isinstance(rates, dict):
        raise ValidationError("Class rates must be an object keyed by traffic class.")
    if not rates:
        raise ValidationError("A shaping profile must name at least one class.")
    if len(rates) > len(CLASS_MINORS):
        raise ValidationError("A shaping profile names more classes than exist.")
    checked: dict[str, float] = {}
    for traffic_class, rate in rates.items():
        validate_traffic_class(traffic_class)
        checked[traffic_class] = RATE_MBPS.validate(rate, f"rate for {traffic_class}")
    return checked


def format_rate(mbps: float) -> str:
    """Format a rate for traffic control, in kilobits per second.

    Kilobits avoid the floating point surprises that megabit strings produce
    at small rates, and the kernel reports back in the same units.
    """
    return f"{max(1, int(round(mbps * 1000)))}kbit"


def format_delay(ms: float) -> str:
    return f"{DELAY_MS.clamp(ms):.3f}ms"


def validate_port(value: int, name: str = "port") -> int:
    """A flow selector may only name a laboratory generator port."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must be a whole number.") from None
    if not (PORT_MINIMUM <= port <= PORT_MAXIMUM):
        raise ValidationError(
            f"{name} must be between {PORT_MINIMUM} and {PORT_MAXIMUM}; got {port}."
        )
    return port


def validate_protocol(value: str) -> str:
    if value not in PROTOCOL_NUMBERS:
        raise ValidationError(
            f"Protocol '{value}' is not supported. Permitted: "
            + ", ".join(PROTOCOL_NUMBERS)
        )
    return value


def class_handle(traffic_class: str) -> str:
    """The traffic-control class identifier for a traffic class."""
    return f"1:{CLASS_MINORS[validate_traffic_class(traffic_class)]:x}"


def class_minor_hex(traffic_class: str) -> str:
    return f"{CLASS_MINORS[validate_traffic_class(traffic_class)]:x}"
