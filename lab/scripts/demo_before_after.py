"""Before-and-after demonstration on the real Linux laboratory.

Runs the primary scenario's core question: a bulk download fills the
bottleneck buffer and ruins delay-sensitive traffic, and shaping below that
bottleneck moves the queue somewhere it can be managed.

Every number printed came from ``ping`` and from the kernel's own queue
counters. Nothing here is simulated.

Run as root inside the laboratory host::

    sudo python3 lab/scripts/demo_before_after.py
"""

from __future__ import annotations

import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import enforcement as enf  # noqa: E402
from agent import measurement as m  # noqa: E402
from agent import topology  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(message)s")

DOWNLOAD_MBPS = 100.0
UPLOAD_MBPS = 20.0
BASE_RTT_MS = 18.0
BULK_PORT = 5201


def line(tag: str, probe: dict, queues: dict) -> None:
    rtt = probe["rtt_ms"]
    jitter = probe["jitter_ms"]
    print(
        f"  {tag:<28} rtt {rtt if rtt is None else format(rtt, '7.2f')} ms"
        f"   jitter {jitter if jitter is None else format(jitter, '6.2f')} ms"
        f"   loss {probe['packet_loss_percent']:5.1f}%"
        f"   bottleneck queue {queues['bottleneck_download']['queue_packets']:>4}p"
        f"   gateway queue {queues['gateway_download']['queue_packets']:>3}p"
    )


def sample(tag: str, rounds: int = 3) -> dict:
    probes = []
    for index in range(rounds):
        probe = m.probe_rtt(count=10)
        queues = m.queue_stats()
        line(f"{tag} {index + 1}", probe, queues)
        probes.append(probe)
    measured = [p["rtt_ms"] for p in probes if p["rtt_ms"] is not None]
    jitters = [p["jitter_ms"] for p in probes if p["jitter_ms"] is not None]
    losses = [p["packet_loss_percent"] for p in probes]
    return {
        "rtt_ms": statistics.median(measured) if measured else None,
        "jitter_ms": statistics.median(jitters) if jitters else None,
        "packet_loss_percent": statistics.median(losses),
    }


def main() -> int:
    print("Building the isolated laboratory...")
    topology.build(
        topology.LinkProfile(
            download_mbps=DOWNLOAD_MBPS,
            upload_mbps=UPLOAD_MBPS,
            base_rtt_ms=BASE_RTT_MS,
        )
    )
    check = topology.connectivity_check()
    if not check["reachable"]:
        print("Laboratory is not forwarding traffic:", check["detail"])
        return 1
    m.stop_generators()
    enf.clear_shaping()

    print()
    print("1. Idle baseline, no load and no shaping")
    idle = sample("idle", rounds=2)
    idle_rtt = idle["rtt_ms"]
    if idle_rtt is None:
        print("No probe replies; cannot continue.")
        return 1

    print()
    print("2. Bulk download running, no shaping")
    print("   A greedy transfer fills the provider's drop-tail buffer.")
    m.start_flow(
        name="bulk",
        port=BULK_PORT,
        protocol="TCP",
        traffic_class="BULK",
        direction="DOWNLOAD",
        duration_seconds=40,
    )
    time.sleep(6)
    unshaped = sample("under load")

    print()
    print("3. Applying a policy that shapes below the bottleneck")
    request = enf.PolicyRequest(
        policy_id="policy-demo-0001",
        profile="PROTECT_INTERACTIVE",
        download=enf.DirectionProfile(
            total_mbps=92.0,
            classes=(
                enf.ClassRate("INTERACTIVE", 8.0, 4.0),
                enf.ClassRate("BULK", 70.0, 2.0),
            ),
        ),
        upload=enf.DirectionProfile(
            total_mbps=18.0,
            classes=(enf.ClassRate("BULK", 14.0, 1.0),),
        ),
        flows=(enf.FlowSelector(BULK_PORT, "TCP", "BULK"),),
    )
    result = enf.apply_policy(request)
    print(
        f"   status {result['status']}"
        f" | read-back matches intent: {result['read_back']['matches_intent']}"
        f" | applied in {result['apply_duration_ms']:.0f} ms"
    )
    if result["status"] != "APPLIED":
        print("   mismatches:", result["mismatches"], result["error"])
        m.stop_generators()
        return 1

    time.sleep(5)
    shaped = sample("after shaping")

    m.stop_generators()
    enf.clear_shaping()

    added_unshaped = unshaped["rtt_ms"] - idle_rtt
    added_shaped = shaped["rtt_ms"] - idle_rtt
    reduction = (1 - added_shaped / added_unshaped) * 100 if added_unshaped > 0 else 0.0

    print()
    print("Result, measured on real Linux traffic control")
    print(f"   idle baseline round-trip time : {idle_rtt:7.2f} ms")
    print(
        f"   loaded, no shaping            : {unshaped['rtt_ms']:7.2f} ms"
        f"   (added {added_unshaped:6.2f} ms, jitter {unshaped['jitter_ms']:.2f} ms,"
        f" loss {unshaped['packet_loss_percent']:.1f}%)"
    )
    print(
        f"   loaded, shaped                : {shaped['rtt_ms']:7.2f} ms"
        f"   (added {added_shaped:6.2f} ms, jitter {shaped['jitter_ms']:.2f} ms,"
        f" loss {shaped['packet_loss_percent']:.1f}%)"
    )
    print(f"   added delay reduced by        : {reduction:5.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
