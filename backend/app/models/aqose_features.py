"""Feature extraction and class mapping for the AQoSE traffic classifier.

AQoSE is the team's own earlier prototype (see ``docs/COMPETITOR_STUDY.md``),
trained on a 28-feature packet-level schema: the signed sizes of a flow's
first 8 packets, the 7 gaps between them in microseconds, IP version,
transport protocol, a coarse port class for each side, and 9 windowed rate
statistics. The trained artifact is checked in at
``app/models/artifacts/traffic_classifier/``.

Two things this module exists to keep honest:

1. **The feature vector must match training exactly**, in name and order.
   ``FEATURE_NAMES`` is the same list the artifact's own ``meta.json``
   records; ``ModelRegistry`` refuses to load the artifact if they diverge,
   the same check AQoSE's own ``model.py`` makes.
2. **The class remap is lossy and is documented as such.** AQoSE's seven
   classes do not correspond one-to-one with QoSense's six. The mapping below
   is this project's own judgement call, not a measured equivalence, and two
   AQoSE classes collapsing onto one QoSense class means real information is
   discarded at that point.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.domain.enums import TrafficClass

FEATURES_VERSION = 1

# Must match meta.json's "features" list name-for-name, in order. A model
# trained on a different feature list would silently misread every row.
WINDOW_FEATURES: list[str] = [
    "pkts_up_per_s",
    "pkts_down_per_s",
    "bytes_up_per_s",
    "bytes_down_per_s",
    "mean_pkt_size_up",
    "mean_pkt_size_down",
    "pkt_size_spread",
    "rate_cv",
    "burstiness",
]
FEATURE_NAMES: list[str] = (
    [f"size_{i}" for i in range(1, 9)]
    + [f"gap_{i}_us" for i in range(1, 8)]
    + ["ip_version", "protocol", "port_class_initiator", "port_class_responder"]
    + WINDOW_FEATURES
)

# AQoSE's seven-class taxonomy, in the order its artifact was trained on.
AQOSE_CLASSES: tuple[str, ...] = (
    "telephony",
    "realtime_interactive",
    "multimedia_conferencing",
    "multimedia_streaming",
    "standard",
    "high_throughput_data",
    "low_priority_data",
)

# The remap. Documented per entry because each one is a judgement call, not a
# fact. Two AQoSE classes collapse onto WEB_VOICE and two onto BULK; that
# collapsed probability mass is summed, not dropped, when building the
# QoSense-shape probability dict (see `remap_probabilities`).
AQOSE_TO_QOSENSE: dict[str, TrafficClass] = {
    # Pure voice call. QoSense has no voice-only class; closest in behaviour
    # (low, steady datagram rate) is WEB_VOICE, not INTERACTIVE, which in
    # QoSense's own scenarios means a video call.
    "telephony": TrafficClass.WEB_VOICE,
    # Low-latency small-packet traffic: games and similar. Direct match.
    "realtime_interactive": TrafficClass.GAMING_LIKE,
    # Video call with audio: this is what QoSense's own scenarios generate
    # as INTERACTIVE ("call-down"/"call-up"). Direct match.
    "multimedia_conferencing": TrafficClass.INTERACTIVE,
    # Buffered audio/video playback. Direct match.
    "multimedia_streaming": TrafficClass.STREAMING_LIKE,
    # General web browsing. No dedicated QoSense class; folds into
    # WEB_VOICE alongside telephony, which is the lossiest point in this
    # remap — browsing and a voice call are not alike, but QoSense's
    # taxonomy was not built to keep them apart.
    "standard": TrafficClass.WEB_VOICE,
    # Large sustained transfer. Direct match.
    "high_throughput_data": TrafficClass.BULK,
    # Background/best-effort bulk. No dedicated QoSense class; folds into
    # BULK, since both are elastic bulk-like traffic the policy engine
    # should treat similarly.
    "low_priority_data": TrafficClass.BULK,
}


def port_class(port: int) -> int:
    """0 system, 1 registered, 2 ephemeral — IANA's three ranges.

    The coarse range is a feature; the raw port number never is, because for
    QoSense's own lab scenarios the raw port determines the label directly
    and would let the model memorise ports instead of learning behaviour.
    """
    return 0 if port < 1024 else 1 if port < 49_152 else 2


@dataclass(frozen=True)
class PacketWindowStats:
    """The 9 windowed rate statistics AQoSE's feature schema expects.

    ``None`` entries become ``NaN`` in the feature vector, exactly as AQoSE's
    own ``features.vector`` does for a record with no window, which is how a
    gradient-boosted tree is trained to handle a missing statistic.
    """

    pkts_up_per_s: float | None
    pkts_down_per_s: float | None
    bytes_up_per_s: float | None
    bytes_down_per_s: float | None
    mean_pkt_size_up: float | None
    mean_pkt_size_down: float | None
    pkt_size_spread: float | None
    rate_cv: float | None
    burstiness: float | None

    def as_list(self) -> list[float]:
        return [
            v if v is not None else np.nan
            for v in (
                self.pkts_up_per_s,
                self.pkts_down_per_s,
                self.bytes_up_per_s,
                self.bytes_down_per_s,
                self.mean_pkt_size_up,
                self.mean_pkt_size_down,
                self.pkt_size_spread,
                self.rate_cv,
                self.burstiness,
            )
        ]


@dataclass(frozen=True)
class PacketLevelRecord:
    """One flow's capture, in the shape the feature vector needs.

    ``pkt_sizes`` are the first up to 8 packets, signed positive for the
    initiator-to-responder direction and negative the other way, as bytes.
    ``pkt_gaps_us`` are the up to 7 gaps between them, in microseconds.
    Shorter than 8 packets is padded with zeros, matching AQoSE's own
    ``MIN_PACKETS`` convention that a flow this short is not classified with
    confidence; the model still runs, but on a mostly-zero vector.
    """

    flow_id: str
    pkt_sizes: list[float]
    pkt_gaps_us: list[float]
    ip_version: int
    protocol: int
    src_port: int
    dst_port: int
    window: PacketWindowStats | None


def feature_vector(record: PacketLevelRecord) -> list[float]:
    """Build one row in ``FEATURE_NAMES`` order. Training and inference must
    call this same function so they can never silently diverge."""
    sizes = (record.pkt_sizes + [0.0] * 8)[:8]
    gaps = (record.pkt_gaps_us + [0.0] * 7)[:7]
    window = record.window.as_list() if record.window else [np.nan] * len(WINDOW_FEATURES)
    return [
        *sizes,
        *gaps,
        float(record.ip_version),
        float(record.protocol),
        float(port_class(record.src_port)),
        float(port_class(record.dst_port)),
        *window,
    ]


def feature_matrix(records: list[PacketLevelRecord]) -> np.ndarray:
    return np.asarray([feature_vector(r) for r in records], dtype=np.float64).reshape(
        -1, len(FEATURE_NAMES)
    )


def remap_probabilities(aqose_probs: dict[str, float]) -> dict[TrafficClass, float]:
    """Fold AQoSE's seven-class probabilities onto QoSense's six classes.

    Where two AQoSE classes map to the same QoSense class, their probability
    mass is summed, not dropped. A QoSense class with no AQoSE class mapping
    onto it (UNKNOWN) gets none directly; UNKNOWN is reserved for the
    low-confidence fallback QoSense's own threshold applies afterward.
    """
    remapped: dict[TrafficClass, float] = dict.fromkeys(TrafficClass, 0.0)
    for aqose_class, probability in aqose_probs.items():
        target = AQOSE_TO_QOSENSE.get(aqose_class)
        if target is not None:
            remapped[target] += probability
    return {cls: p for cls, p in remapped.items() if p > 0}
