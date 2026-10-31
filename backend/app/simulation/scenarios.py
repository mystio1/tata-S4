"""Predefined laboratory scenarios.

Clients reference a scenario by identifier only. Numeric overrides are limited
to the declared ``ParameterBound`` entries and are clamped server-side.

Every scenario here is a laboratory emulation. None of these values are
measurements of a real Internet service provider connection.
"""

from __future__ import annotations

from app.domain.enums import DataMode, DeviceKind, Direction, Protocol, TrafficClass
from app.domain.experiment import (
    FlowSpec,
    ImpairmentStep,
    ParameterBound,
    ScenarioDefinition,
)
from app.domain.topology import Topology, TopologyDevice, TopologyLink

# Device catalogue shared by all scenarios. Scene coordinates are in
# gateway-relative units: x is left/right, z is near/far.
DEVICE_CATALOGUE: dict[str, tuple[str, DeviceKind, str, float, float]] = {
    "gateway": ("QoSense Gateway", DeviceKind.GATEWAY, "Shaping point under test", 0.0, 0.0),
    "internet": ("Internet", DeviceKind.INTERNET, "Wide area network peer", 0.0, -5.4),
    "laptop": ("Laptop", DeviceKind.LAPTOP, "Video call endpoint", -4.6, 1.2),
    "console": ("Gaming Console", DeviceKind.GAMING_CONSOLE, "Online game client", -4.2, 4.2),
    "desktop": ("Desktop", DeviceKind.DESKTOP, "Bulk transfer client", -1.6, 5.6),
    "tv1": ("Smart TV 1", DeviceKind.SMART_TV, "Streaming client", 4.6, 1.2),
    "tv2": ("Smart TV 2", DeviceKind.SMART_TV, "Streaming client", 4.8, 3.6),
    "tv3": ("Smart TV 3", DeviceKind.SMART_TV, "Streaming client", 2.4, 5.8),
    "phone": ("Smartphone", DeviceKind.SMARTPHONE, "Web and voice client", 4.0, 5.8),
}


def _common_parameters(
    download_default: float, upload_default: float, duration_default: float
) -> list[ParameterBound]:
    return [
        ParameterBound(
            name="bottleneck_download_mbps",
            label="Bottleneck download rate",
            unit="Mbps",
            minimum=5.0,
            maximum=500.0,
            default=download_default,
            description="Emulated downstream bottleneck rate applied by the scenario.",
        ),
        ParameterBound(
            name="bottleneck_upload_mbps",
            label="Bottleneck upload rate",
            unit="Mbps",
            minimum=2.0,
            maximum=200.0,
            default=upload_default,
            description="Emulated upstream bottleneck rate applied by the scenario.",
        ),
        ParameterBound(
            name="duration_seconds",
            label="Run duration",
            unit="s",
            minimum=10.0,
            maximum=600.0,
            default=duration_default,
            description="Total length of the run.",
        ),
    ]


def _video_call_flows(start: float = 0.0) -> list[FlowSpec]:
    """Interactive-like two-way call: steady small packets in both directions."""
    return [
        FlowSpec(
            flow_id="call-down",
            device_id="laptop",
            direction=Direction.DOWNLOAD,
            protocol=Protocol.UDP,
            true_class=TrafficClass.INTERACTIVE,
            demand_model="CONSTANT_BITRATE",
            start_seconds=start,
            nominal_demand_mbps=3.2,
            mean_packet_bytes=760.0,
            packet_bytes_jitter=90.0,
        ),
        FlowSpec(
            flow_id="call-up",
            device_id="laptop",
            direction=Direction.UPLOAD,
            protocol=Protocol.UDP,
            true_class=TrafficClass.INTERACTIVE,
            demand_model="CONSTANT_BITRATE",
            start_seconds=start,
            nominal_demand_mbps=1.6,
            mean_packet_bytes=720.0,
            packet_bytes_jitter=80.0,
        ),
    ]


def _gaming_flows(start: float = 0.0) -> list[FlowSpec]:
    """Gaming-like: low volume, small packets, frequent updates."""
    return [
        FlowSpec(
            flow_id="game-down",
            device_id="console",
            direction=Direction.DOWNLOAD,
            protocol=Protocol.UDP,
            true_class=TrafficClass.GAMING_LIKE,
            demand_model="CONSTANT_BITRATE",
            start_seconds=start,
            nominal_demand_mbps=0.6,
            mean_packet_bytes=180.0,
            packet_bytes_jitter=40.0,
        ),
        FlowSpec(
            flow_id="game-up",
            device_id="console",
            direction=Direction.UPLOAD,
            protocol=Protocol.UDP,
            true_class=TrafficClass.GAMING_LIKE,
            demand_model="CONSTANT_BITRATE",
            start_seconds=start,
            nominal_demand_mbps=0.35,
            mean_packet_bytes=150.0,
            packet_bytes_jitter=35.0,
        ),
    ]


def _streaming_flow(
    flow_id: str, device_id: str, demand_mbps: float, start: float, period: float
) -> FlowSpec:
    """Streaming-like: adaptive bitrate player filling its buffer in bursts."""
    return FlowSpec(
        flow_id=flow_id,
        device_id=device_id,
        direction=Direction.DOWNLOAD,
        protocol=Protocol.TCP,
        true_class=TrafficClass.STREAMING_LIKE,
        demand_model="BURSTY",
        start_seconds=start,
        nominal_demand_mbps=demand_mbps,
        mean_packet_bytes=1420.0,
        packet_bytes_jitter=60.0,
        burst_period_seconds=period,
        burst_duty_cycle=0.55,
    )


def _web_voice_flow(start: float) -> FlowSpec:
    return FlowSpec(
        flow_id="web-phone",
        device_id="phone",
        direction=Direction.DOWNLOAD,
        protocol=Protocol.TCP,
        true_class=TrafficClass.WEB_VOICE,
        demand_model="BURSTY",
        start_seconds=start,
        nominal_demand_mbps=1.1,
        mean_packet_bytes=980.0,
        packet_bytes_jitter=220.0,
        burst_period_seconds=7.0,
        burst_duty_cycle=0.3,
    )


def _bulk_flow(flow_id: str, start: float, stop: float | None = None) -> FlowSpec:
    """Greedy transfer: takes whatever the link will give it."""
    return FlowSpec(
        flow_id=flow_id,
        device_id="desktop",
        direction=Direction.DOWNLOAD,
        protocol=Protocol.TCP,
        true_class=TrafficClass.BULK,
        demand_model="GREEDY",
        start_seconds=start,
        stop_seconds=stop,
        nominal_demand_mbps=400.0,
        mean_packet_bytes=1460.0,
        packet_bytes_jitter=30.0,
    )


ISO_DURING_CALL = ScenarioDefinition(
    scenario_id="iso_during_call",
    name="Disc image download during a work call",
    summary=(
        "An interactive-like video call and a gaming-like flow run from the start. "
        "A greedy bulk transfer begins part-way through and drives queueing delay up. "
        "This is the primary demonstrator scenario."
    ),
    default_duration_seconds=120.0,
    default_download_mbps=100.0,
    default_upload_mbps=20.0,
    default_base_rtt_ms=18.0,
    queue_limit_packets=900,
    flows=[
        *_video_call_flows(start=0.0),
        *_gaming_flows(start=0.0),
        _streaming_flow("tv1-stream", "tv1", 8.5, start=4.0, period=6.0),
        _web_voice_flow(start=2.0),
        _bulk_flow("iso-download", start=20.0),
    ],
    parameters=[
        *_common_parameters(100.0, 20.0, 120.0),
        ParameterBound(
            name="download_start_after_seconds",
            label="Bulk transfer start",
            unit="s",
            minimum=0.0,
            maximum=120.0,
            default=20.0,
            description="Delay before the greedy bulk transfer begins.",
        ),
    ],
)

BOTTLENECK_DROP = ScenarioDefinition(
    scenario_id="bottleneck_drop",
    name="Bottleneck drop from 100 Mbps to 20 Mbps",
    summary=(
        "A steady traffic mix runs against a 100 Mbps emulated bottleneck. "
        "The bottleneck is reduced part-way through so the response of the capacity "
        "estimator and the policy engine can be measured."
    ),
    default_duration_seconds=120.0,
    default_download_mbps=100.0,
    default_upload_mbps=20.0,
    default_base_rtt_ms=18.0,
    queue_limit_packets=900,
    flows=[
        *_video_call_flows(start=0.0),
        _streaming_flow("tv1-stream", "tv1", 9.0, start=0.0, period=6.0),
        _streaming_flow("tv2-stream", "tv2", 8.0, start=3.0, period=6.5),
        _bulk_flow("bulk-sync", start=6.0),
    ],
    impairments=[
        ImpairmentStep(
            at_seconds=45.0,
            download_mbps=20.0,
            description="Emulated downstream bottleneck reduced to 20 Mbps.",
        )
    ],
    parameters=[
        *_common_parameters(100.0, 20.0, 120.0),
        ParameterBound(
            name="drop_at_seconds",
            label="Bottleneck drop time",
            unit="s",
            minimum=10.0,
            maximum=400.0,
            default=45.0,
            description="When the emulated bottleneck is reduced.",
        ),
        ParameterBound(
            name="dropped_download_mbps",
            label="Reduced download rate",
            unit="Mbps",
            minimum=5.0,
            maximum=100.0,
            default=20.0,
            description="Downstream rate after the drop.",
        ),
    ],
)

THREE_STREAMS_GAMING = ScenarioDefinition(
    scenario_id="three_streams_gaming",
    name="Three television streams plus gaming",
    summary=(
        "Three comparable streaming-like flows compete with a gaming-like flow. "
        "Jain's fairness index is computed across the three comparable streams only."
    ),
    default_duration_seconds=120.0,
    default_download_mbps=45.0,
    default_upload_mbps=12.0,
    default_base_rtt_ms=20.0,
    queue_limit_packets=700,
    flows=[
        *_gaming_flows(start=0.0),
        _streaming_flow("tv1-stream", "tv1", 16.0, start=0.0, period=6.0),
        _streaming_flow("tv2-stream", "tv2", 16.0, start=5.0, period=6.0),
        _streaming_flow("tv3-stream", "tv3", 16.0, start=10.0, period=6.0),
        _web_voice_flow(start=2.0),
    ],
    parameters=_common_parameters(45.0, 12.0, 120.0),
)

CUSTOM_BOUNDED = ScenarioDefinition(
    scenario_id="custom_bounded",
    name="Custom bounded mix",
    summary=(
        "A call, a game, one stream and a bulk transfer with adjustable bounded "
        "parameters. Use this to probe conditions outside the fixed scenarios."
    ),
    default_duration_seconds=90.0,
    default_download_mbps=60.0,
    default_upload_mbps=15.0,
    default_base_rtt_ms=22.0,
    queue_limit_packets=800,
    flows=[
        *_video_call_flows(start=0.0),
        *_gaming_flows(start=0.0),
        _streaming_flow("tv1-stream", "tv1", 10.0, start=2.0, period=6.0),
        _bulk_flow("bulk-custom", start=15.0),
    ],
    parameters=[
        *_common_parameters(60.0, 15.0, 90.0),
        ParameterBound(
            name="base_rtt_ms",
            label="Baseline round-trip time",
            unit="ms",
            minimum=2.0,
            maximum=200.0,
            default=22.0,
            description="Idle path round-trip time before any queueing delay.",
        ),
        ParameterBound(
            name="download_start_after_seconds",
            label="Bulk transfer start",
            unit="s",
            minimum=0.0,
            maximum=300.0,
            default=15.0,
            description="Delay before the greedy bulk transfer begins.",
        ),
    ],
)

SCENARIOS: dict[str, ScenarioDefinition] = {
    s.scenario_id: s
    for s in (ISO_DURING_CALL, BOTTLENECK_DROP, THREE_STREAMS_GAMING, CUSTOM_BOUNDED)
}


def get_scenario(scenario_id: str) -> ScenarioDefinition:
    try:
        return SCENARIOS[scenario_id]
    except KeyError:
        raise KeyError(f"Unknown scenario '{scenario_id}'") from None


def clamp_parameters(
    scenario: ScenarioDefinition, requested: dict[str, float]
) -> tuple[dict[str, float], list[str]]:
    """Resolve scenario parameters against their declared bounds.

    Unknown keys are dropped and reported. Out-of-range values are clamped
    rather than rejected so a run still starts, but every adjustment is recorded
    so the user interface can show what actually ran.
    """
    bounds = {p.name: p for p in scenario.parameters}
    resolved = {name: bound.default for name, bound in bounds.items()}
    notes: list[str] = []

    for key, value in requested.items():
        bound = bounds.get(key)
        if bound is None:
            notes.append(f"Ignored unsupported parameter '{key}'.")
            continue
        numeric = float(value)
        clamped = min(max(numeric, bound.minimum), bound.maximum)
        if clamped != numeric:
            notes.append(
                f"Clamped '{key}' from {numeric:g} to {clamped:g} {bound.unit} "
                f"(allowed {bound.minimum:g} to {bound.maximum:g})."
            )
        resolved[key] = clamped

    return resolved, notes


def build_topology(scenario: ScenarioDefinition, data_mode: DataMode) -> Topology:
    """Build the device and link graph for the scene from the scenario's flows."""
    used = {"gateway", "internet"} | {f.device_id for f in scenario.flows}
    devices = [
        TopologyDevice(
            device_id=device_id,
            label=label,
            kind=kind,
            description=description,
            scene_x=x,
            scene_z=z,
        )
        for device_id, (label, kind, description, x, z) in DEVICE_CATALOGUE.items()
        if device_id in used
    ]
    links = [
        TopologyLink(
            link_id="wan",
            source_id="gateway",
            target_id="internet",
            description="Wide area network link carrying the emulated bottleneck",
        )
    ]
    links += [
        TopologyLink(
            link_id=f"lan-{d.device_id}",
            source_id=d.device_id,
            target_id="gateway",
            description="Local area network link",
        )
        for d in devices
        if d.device_id not in ("gateway", "internet")
    ]
    return Topology(
        topology_id=f"{scenario.scenario_id}-topology",
        data_mode=data_mode,
        description=(
            "Emulated home topology. Device positions are fixed so the scene stays "
            "readable between runs."
        ),
        devices=devices,
        links=links,
    )
