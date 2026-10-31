"""Laboratory topology description consumed by the 3D scene."""

from __future__ import annotations

from pydantic import Field

from app.domain.enums import DataMode, DeviceKind
from app.domain.metrics import StrictModel


class TopologyDevice(StrictModel):
    device_id: str
    label: str
    kind: DeviceKind
    description: str
    # Scene placement in gateway-relative units; the frontend maps these onto
    # the isometric layout so device positions stay stable between renders.
    scene_x: float
    scene_z: float


class TopologyLink(StrictModel):
    link_id: str
    source_id: str
    target_id: str
    description: str


class Topology(StrictModel):
    topology_id: str
    data_mode: DataMode
    description: str
    devices: list[TopologyDevice] = Field(default_factory=list)
    links: list[TopologyLink] = Field(default_factory=list)
