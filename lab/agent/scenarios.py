"""Predefined laboratory scenarios.

A caller names a scenario by identifier and may adjust only the bounded
parameters declared here. Generator commands, ports and schedules are owned by
the agent, so a request cannot introduce traffic the laboratory was not
designed to carry.

These mirror the simulator's scenarios in ``backend/app/simulation/scenarios.py``
so a simulated run and a laboratory run of the same identifier are describing
the same experiment. The generators differ because one is a model and the
other is ``iperf3`` moving real packets.

Ports are fixed per flow and sit inside the range the safety layer permits.
The port is only how the data path recognises a flow once the classifier has
decided its class from behaviour; it is never a classification input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .safety import ValidationError

# Port assignments. Each generated flow gets its own, inside the permitted
# laboratory range.
PORT_CALL_DOWN = 5201
PORT_CALL_UP = 5202
PORT_GAME_DOWN = 5203
PORT_GAME_UP = 5204
PORT_STREAM_1 = 5205
PORT_STREAM_2 = 5206
PORT_STREAM_3 = 5207
PORT_WEB = 5208
PORT_BULK = 5209


@dataclass(frozen=True)
class FlowPlan:
    """One generated flow and when it runs."""

    name: str
    device: str
    port: int
    protocol: str
    direction: str
    traffic_class: str
    start_seconds: float
    rate_mbps: float | None = None
    packet_bytes: int | None = None


@dataclass(frozen=True)
class ImpairmentPlan:
    at_seconds: float
    download_mbps: float | None = None
    upload_mbps: float | None = None
    description: str = ""


@dataclass(frozen=True)
class ParameterBound:
    name: str
    label: str
    unit: str
    minimum: float
    maximum: float
    default: float
    description: str


@dataclass(frozen=True)
class LabScenario:
    scenario_id: str
    name: str
    summary: str
    duration_seconds: float
    download_mbps: float
    upload_mbps: float
    base_rtt_ms: float
    queue_limit_packets: int
    flows: tuple[FlowPlan, ...]
    impairments: tuple[ImpairmentPlan, ...] = ()
    parameters: tuple[ParameterBound, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "summary": self.summary,
            "is_laboratory_emulation": True,
            "duration_seconds": self.duration_seconds,
            "download_mbps": self.download_mbps,
            "upload_mbps": self.upload_mbps,
            "base_rtt_ms": self.base_rtt_ms,
            "queue_limit_packets": self.queue_limit_packets,
            "flows": [
                {
                    "name": f.name,
                    "device": f.device,
                    "port": f.port,
                    "protocol": f.protocol,
                    "direction": f.direction,
                    "traffic_class": f.traffic_class,
                    "start_seconds": f.start_seconds,
                    "rate_mbps": f.rate_mbps,
                }
                for f in self.flows
            ],
            "parameters": [
                {
                    "name": p.name,
                    "label": p.label,
                    "unit": p.unit,
                    "minimum": p.minimum,
                    "maximum": p.maximum,
                    "default": p.default,
                    "description": p.description,
                }
                for p in self.parameters
            ],
        }


def _common_parameters(download: float, upload: float, duration: float) -> tuple[ParameterBound, ...]:
    return (
        ParameterBound(
            "bottleneck_download_mbps", "Bottleneck download rate", "Mbps",
            5.0, 500.0, download, "Emulated downstream rate on the provider namespace.",
        ),
        ParameterBound(
            "bottleneck_upload_mbps", "Bottleneck upload rate", "Mbps",
            2.0, 200.0, upload, "Emulated upstream rate on the provider namespace.",
        ),
        ParameterBound(
            "duration_seconds", "Run duration", "s",
            20.0, 600.0, duration, "Total length of the run.",
        ),
    )


def _call_flows(start: float = 0.0) -> tuple[FlowPlan, ...]:
    """A two-way conversation: steady, small datagrams in both directions."""
    return (
        FlowPlan("call-down", "laptop", PORT_CALL_DOWN, "UDP", "DOWNLOAD",
                 "INTERACTIVE", start, rate_mbps=3.2, packet_bytes=760),
        FlowPlan("call-up", "laptop", PORT_CALL_UP, "UDP", "UPLOAD",
                 "INTERACTIVE", start, rate_mbps=1.6, packet_bytes=720),
    )


def _game_flows(start: float = 0.0) -> tuple[FlowPlan, ...]:
    """Low volume, small packets, frequent updates."""
    return (
        FlowPlan("game-down", "console", PORT_GAME_DOWN, "UDP", "DOWNLOAD",
                 "GAMING_LIKE", start, rate_mbps=0.6, packet_bytes=180),
        FlowPlan("game-up", "console", PORT_GAME_UP, "UDP", "UPLOAD",
                 "GAMING_LIKE", start, rate_mbps=0.35, packet_bytes=150),
    )


ISO_DURING_CALL = LabScenario(
    scenario_id="iso_during_call",
    name="Disc image download during a work call",
    summary=(
        "A call and a game run from the start. A greedy transfer begins part-way "
        "through and fills the provider's buffer. The primary demonstrator."
    ),
    duration_seconds=90.0,
    download_mbps=100.0,
    upload_mbps=20.0,
    base_rtt_ms=18.0,
    queue_limit_packets=900,
    flows=(
        *_call_flows(0.0),
        *_game_flows(0.0),
        FlowPlan("tv1-stream", "tv1", PORT_STREAM_1, "TCP", "DOWNLOAD",
                 "STREAMING_LIKE", 4.0, rate_mbps=8.5),
        FlowPlan("web-phone", "phone", PORT_WEB, "TCP", "DOWNLOAD",
                 "WEB_VOICE", 2.0, rate_mbps=1.1),
        FlowPlan("iso-download", "desktop", PORT_BULK, "TCP", "DOWNLOAD", "BULK", 20.0),
    ),
    parameters=(
        *_common_parameters(100.0, 20.0, 90.0),
        ParameterBound(
            "download_start_after_seconds", "Bulk transfer start", "s",
            0.0, 120.0, 20.0, "Delay before the greedy transfer begins.",
        ),
    ),
)

BOTTLENECK_DROP = LabScenario(
    scenario_id="bottleneck_drop",
    name="Bottleneck drop from 100 Mbps to 20 Mbps",
    summary=(
        "A steady mix runs against a 100 Mbps emulated link that is reduced "
        "part-way through, so the capacity estimator's response can be measured."
    ),
    duration_seconds=90.0,
    download_mbps=100.0,
    upload_mbps=20.0,
    base_rtt_ms=18.0,
    queue_limit_packets=900,
    flows=(
        *_call_flows(0.0),
        FlowPlan("tv1-stream", "tv1", PORT_STREAM_1, "TCP", "DOWNLOAD",
                 "STREAMING_LIKE", 0.0, rate_mbps=9.0),
        FlowPlan("tv2-stream", "tv2", PORT_STREAM_2, "TCP", "DOWNLOAD",
                 "STREAMING_LIKE", 3.0, rate_mbps=8.0),
        FlowPlan("bulk-sync", "desktop", PORT_BULK, "TCP", "DOWNLOAD", "BULK", 6.0),
    ),
    impairments=(
        ImpairmentPlan(
            at_seconds=45.0,
            download_mbps=20.0,
            description="Emulated downstream rate reduced to 20 Mbps.",
        ),
    ),
    parameters=(
        *_common_parameters(100.0, 20.0, 90.0),
        ParameterBound("drop_at_seconds", "Bottleneck drop time", "s",
                       10.0, 400.0, 45.0, "When the emulated rate is reduced."),
        ParameterBound("dropped_download_mbps", "Reduced download rate", "Mbps",
                       5.0, 100.0, 20.0, "Downstream rate after the drop."),
    ),
)

THREE_STREAMS_GAMING = LabScenario(
    scenario_id="three_streams_gaming",
    name="Three television streams plus gaming",
    summary=(
        "Three comparable streams compete with a game. Fairness is computed "
        "across the three comparable streams only."
    ),
    duration_seconds=90.0,
    download_mbps=45.0,
    upload_mbps=12.0,
    base_rtt_ms=20.0,
    queue_limit_packets=700,
    flows=(
        *_game_flows(0.0),
        FlowPlan("tv1-stream", "tv1", PORT_STREAM_1, "TCP", "DOWNLOAD",
                 "STREAMING_LIKE", 0.0, rate_mbps=16.0),
        FlowPlan("tv2-stream", "tv2", PORT_STREAM_2, "TCP", "DOWNLOAD",
                 "STREAMING_LIKE", 5.0, rate_mbps=16.0),
        FlowPlan("tv3-stream", "tv3", PORT_STREAM_3, "TCP", "DOWNLOAD",
                 "STREAMING_LIKE", 10.0, rate_mbps=16.0),
        FlowPlan("web-phone", "phone", PORT_WEB, "TCP", "DOWNLOAD",
                 "WEB_VOICE", 2.0, rate_mbps=1.1),
    ),
    parameters=_common_parameters(45.0, 12.0, 90.0),
)

SCENARIOS: dict[str, LabScenario] = {
    s.scenario_id: s for s in (ISO_DURING_CALL, BOTTLENECK_DROP, THREE_STREAMS_GAMING)
}


def get_scenario(scenario_id: str) -> LabScenario:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise ValidationError(
            f"Unknown scenario '{scenario_id}'. Permitted: {', '.join(SCENARIOS)}."
        )
    return scenario


def resolve_parameters(
    scenario: LabScenario, requested: dict[str, float]
) -> tuple[dict[str, float], list[str]]:
    """Clamp requested parameters to their declared bounds.

    Out-of-range values are clamped rather than rejected so a run still
    starts, and every adjustment is reported so the record says what actually
    ran rather than what was asked for.
    """
    bounds = {p.name: p for p in scenario.parameters}
    resolved = {name: bound.default for name, bound in bounds.items()}
    notes: list[str] = []
    for key, value in (requested or {}).items():
        bound = bounds.get(key)
        if bound is None:
            notes.append(f"Ignored unsupported parameter '{key}'.")
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            notes.append(f"Ignored non-numeric parameter '{key}'.")
            continue
        clamped = min(max(numeric, bound.minimum), bound.maximum)
        if clamped != numeric:
            notes.append(
                f"Clamped '{key}' from {numeric:g} to {clamped:g} {bound.unit}."
            )
        resolved[key] = clamped
    return resolved, notes
