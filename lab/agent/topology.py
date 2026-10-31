"""The isolated laboratory topology.

Four network namespaces joined by virtual Ethernet pairs::

    qos-home            qos-gw                 qos-isp              qos-wan
    (devices)         (QoSense gateway)      (emulated link)      (Internet)
      qos-h0 <------> qos-g0    qos-g1 <----> qos-i0   qos-i1 <--> qos-w0
    10.200.1.2      10.200.1.1 10.200.2.1   10.200.2.2 10.200.3.1  10.200.3.2

Why four namespaces and not three
---------------------------------

The emulated bottleneck and the policy shaping must not sit on the same
interface, or they fight over the same queue and neither can be reasoned
about. Giving the bottleneck its own namespace separates them the way reality
does: ``qos-isp`` is the link you do not control, and ``qos-gw`` is the
gateway you do. The gateway then shapes *below* the bottleneck rate, which
pulls the standing queue out of the link it cannot manage and into the one it
can. That is the entire mechanism behind the product, so the testbed models it
directly rather than approximating it.

The bottleneck lives on ``qos-isp`` as ``netem`` with a rate, a delay and a
finite packet limit, giving genuine drop-tail behaviour. Nothing in the
controller may read its configuration: the capacity estimator has to discover
the rate from delivered throughput and delay, exactly as it would against a
real connection.

Isolation
---------

Every interface and namespace name is a constant from :mod:`.safety` carrying
the ``qos-`` prefix, and every command that names one is validated against
that list. The host's real interfaces are not reachable from any code path in
this agent, and the laboratory has no route to anything outside itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .commands import in_namespace, run
from .safety import (
    ADDR_GW_LAN,
    ADDR_GW_WAN,
    ADDR_HOME,
    ADDR_ISP_GW,
    ADDR_ISP_WAN,
    ADDR_WAN,
    DELAY_MS,
    IF_GW_LAN,
    IF_GW_WAN,
    IF_HOME_LAN,
    IF_IFB,
    IF_ISP_GW,
    IF_ISP_WAN,
    IF_WAN,
    IP_GW_LAN,
    IP_GW_WAN,
    IP_HOME,
    IP_ISP_GW,
    IP_ISP_WAN,
    IP_WAN,
    LOSS_PERCENT,
    NAMESPACES,
    NET_HOME,
    NET_ISP_WAN,
    NS_GATEWAY,
    NS_HOME,
    NS_ISP,
    NS_WAN,
    QUEUE_PACKETS,
    RATE_MBPS,
    format_delay,
)

logger = logging.getLogger("qosense.lab.topology")

REQUIRED_MODULES = ("ifb", "sch_htb", "sch_netem", "sch_fq_codel", "sch_ingress", "act_mirred")


class LabUnavailable(RuntimeError):
    """The host cannot run the laboratory, for example a missing kernel module."""

# Representative packet size used to turn a buffer expressed in packets into
# the delay it can hold. Matches the simulator's assumption so the two agree.
REPRESENTATIVE_PACKET_BYTES = 1400


@dataclass(frozen=True)
class LinkProfile:
    """The emulated connection. Hidden from the controller by design."""

    download_mbps: float
    upload_mbps: float
    base_rtt_ms: float
    loss_percent: float = 0.0
    queue_limit_packets: int = 900

    def validated(self) -> LinkProfile:
        return LinkProfile(
            download_mbps=RATE_MBPS.validate(self.download_mbps, "download rate"),
            upload_mbps=RATE_MBPS.validate(self.upload_mbps, "upload rate"),
            base_rtt_ms=DELAY_MS.validate(self.base_rtt_ms, "base round-trip time"),
            loss_percent=LOSS_PERCENT.validate(self.loss_percent, "loss"),
            queue_limit_packets=int(
                QUEUE_PACKETS.validate(self.queue_limit_packets, "queue limit")
            ),
        )


DEFAULT_LINK = LinkProfile(download_mbps=100.0, upload_mbps=20.0, base_rtt_ms=18.0)


def load_modules() -> list[str]:
    """Load the queueing modules the testbed needs. Returns the ones missing."""
    missing: list[str] = []
    for module in REQUIRED_MODULES:
        result = run(["modprobe", module], check=False)
        if not result.ok:
            missing.append(module)
    return missing


def namespace_exists(namespace: str) -> bool:
    result = run(["ip", "netns", "list"], check=False)
    return any(line.split()[0] == namespace for line in result.stdout.splitlines() if line.strip())


def teardown() -> None:
    """Remove the laboratory.

    Deleting a namespace takes its virtual Ethernet pairs with it, so this is
    enough to return the host to its previous state. It is safe to call when
    nothing exists.
    """
    for namespace in NAMESPACES:
        run(["ip", "netns", "del", namespace], check=False)
    logger.info("laboratory namespaces removed")


def _create_pair(left: str, right: str) -> None:
    run(["ip", "link", "add", left, "type", "veth", "peer", "name", right])


def build(link: LinkProfile | None = None) -> LinkProfile:
    """Create the laboratory from scratch and return the profile applied."""
    profile = (link or DEFAULT_LINK).validated()

    missing = load_modules()
    if missing:
        raise LabUnavailable(
            "This kernel cannot load the queueing modules the testbed needs: "
            + ", ".join(missing)
            + ". The laboratory cannot run here."
        )

    teardown()

    for namespace in NAMESPACES:
        run(["ip", "netns", "add", namespace])

    # Virtual Ethernet pairs, created in the root namespace then moved.
    _create_pair(IF_HOME_LAN, IF_GW_LAN)
    _create_pair(IF_GW_WAN, IF_ISP_GW)
    _create_pair(IF_ISP_WAN, IF_WAN)

    placement = [
        (IF_HOME_LAN, NS_HOME),
        (IF_GW_LAN, NS_GATEWAY),
        (IF_GW_WAN, NS_GATEWAY),
        (IF_ISP_GW, NS_ISP),
        (IF_ISP_WAN, NS_ISP),
        (IF_WAN, NS_WAN),
    ]
    for interface, namespace in placement:
        run(["ip", "link", "set", interface, "netns", namespace])

    addresses = [
        (NS_HOME, IF_HOME_LAN, ADDR_HOME),
        (NS_GATEWAY, IF_GW_LAN, ADDR_GW_LAN),
        (NS_GATEWAY, IF_GW_WAN, ADDR_GW_WAN),
        (NS_ISP, IF_ISP_GW, ADDR_ISP_GW),
        (NS_ISP, IF_ISP_WAN, ADDR_ISP_WAN),
        (NS_WAN, IF_WAN, ADDR_WAN),
    ]
    for namespace, interface, address in addresses:
        in_namespace(namespace, ["ip", "addr", "add", address, "dev", interface])
        in_namespace(namespace, ["ip", "link", "set", interface, "up"])

    for namespace in NAMESPACES:
        in_namespace(namespace, ["ip", "link", "set", "lo", "up"])

    # Forwarding on the two transit namespaces only.
    for namespace in (NS_GATEWAY, NS_ISP):
        in_namespace(namespace, ["sysctl", "-q", "-w", "net.ipv4.ip_forward=1"])

    # Routes. The laboratory is a closed world; none of these reach the host.
    in_namespace(NS_HOME, ["ip", "route", "add", "default", "via", IP_GW_LAN])
    in_namespace(NS_GATEWAY, ["ip", "route", "add", NET_ISP_WAN, "via", IP_ISP_GW])
    in_namespace(NS_ISP, ["ip", "route", "add", NET_HOME, "via", IP_GW_WAN])
    in_namespace(NS_WAN, ["ip", "route", "add", "default", "via", IP_ISP_WAN])

    # The Intermediate Functional Block device the download path redirects to.
    in_namespace(NS_GATEWAY, ["ip", "link", "add", IF_IFB, "type", "ifb"])
    in_namespace(NS_GATEWAY, ["ip", "link", "set", IF_IFB, "up"])

    apply_bottleneck(profile)
    logger.info(
        "laboratory built: %.1f Mbps down, %.1f Mbps up, %.1f ms base round-trip time",
        profile.download_mbps,
        profile.upload_mbps,
        profile.base_rtt_ms,
    )
    return profile


def apply_bottleneck(link: LinkProfile) -> LinkProfile:
    """Install the emulated connection on the provider namespace.

    Download and upload are shaped on opposite ends of the provider, so each
    direction gets its own rate. Half the base round-trip time is applied in
    each direction, so a probe measures the whole of it.

    ``netem``'s ``limit`` is the buffer in packets. It is finite and
    drop-tail, which is what produces real bufferbloat when a greedy transfer
    fills it, and therefore what the gateway's shaping has to solve.
    """
    profile = link.validated()
    one_way_delay = profile.base_rtt_ms / 2.0

    for interface, rate in (
        (IF_ISP_GW, profile.download_mbps),
        (IF_ISP_WAN, profile.upload_mbps),
    ):
        in_namespace(NS_ISP, ["tc", "qdisc", "del", "dev", interface, "root"], check=False)
        argv = [
            "tc", "qdisc", "add", "dev", interface, "root", "netem",
            "rate", f"{rate:.3f}mbit",
            "delay", format_delay(one_way_delay),
            "limit", str(profile.queue_limit_packets),
        ]
        if profile.loss_percent > 0:
            argv += ["loss", f"{profile.loss_percent:.3f}%"]
        in_namespace(NS_ISP, argv)

    return profile


def reset(link: LinkProfile | None = None) -> LinkProfile:
    """Return the laboratory to a known baseline.

    Rebuilds from nothing rather than trying to undo whatever state a run left
    behind, because a reset that depends on knowing the previous state is the
    one that fails when it is most needed.
    """
    return build(link)


def status() -> dict[str, object]:
    """Describe what currently exists, without changing anything."""
    present = {namespace: namespace_exists(namespace) for namespace in NAMESPACES}
    built = all(present.values())
    interfaces: dict[str, list[str]] = {}
    if built:
        for namespace in NAMESPACES:
            result = in_namespace(namespace, ["ip", "-br", "link", "show"], check=False)
            interfaces[namespace] = [
                line.split()[0].split("@")[0]
                for line in result.stdout.splitlines()
                if line.strip()
            ]
    return {
        "built": built,
        "namespaces": present,
        "interfaces": interfaces,
    }


def connectivity_check() -> dict[str, object]:
    """Prove the topology forwards, end to end, before any experiment runs."""
    result = in_namespace(
        NS_HOME,
        ["ping", "-c", "3", "-i", "0.2", "-W", "2", "-q", IP_WAN],
        check=False,
        timeout=15.0,
    )
    return {
        "reachable": result.ok,
        "from": "qos-home",
        "to": IP_WAN,
        "detail": (result.stdout or result.stderr).strip()[:500],
    }
