"""Scenario execution inside the laboratory.

One run at a time. The runner starts generators on their schedule, applies
scheduled impairments, and answers ``get_metrics`` with whatever the kernel
and the probes currently report.

The runner does not decide policy. It measures and it enforces what it is
told to enforce; the controller on the other side of the agent boundary makes
every decision. That split is why this file has no thresholds in it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import enforcement as enf
from . import measurement as m
from . import topology
from .safety import IF_GW_WAN, IF_IFB, ValidationError, validate_identifier
from .scenarios import LabScenario, get_scenario, resolve_parameters

logger = logging.getLogger("qosense.lab.runner")


@dataclass
class RunState:
    experiment_id: str
    scenario: LabScenario
    parameters: dict[str, float]
    notes: list[str]
    started_at: float
    duration_seconds: float
    link: topology.LinkProfile
    started_flows: set[str] = field(default_factory=set)
    applied_impairments: set[int] = field(default_factory=set)
    stopped: bool = False
    stopped_at: float | None = None

    @property
    def elapsed_seconds(self) -> float:
        end = self.stopped_at if self.stopped_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    @property
    def finished(self) -> bool:
        return self.stopped or self.elapsed_seconds >= self.duration_seconds


class LabRunner:
    """Owns the single active run and the background schedule thread."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._run: RunState | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -- state ---------------------------------------------------------

    @property
    def active(self) -> RunState | None:
        return self._run

    def describe(self) -> dict[str, Any]:
        run = self._run
        if run is None:
            return {"running": False, "experiment_id": None}
        return {
            "running": not run.finished,
            "experiment_id": run.experiment_id,
            "scenario_id": run.scenario.scenario_id,
            "elapsed_seconds": round(run.elapsed_seconds, 3),
            "duration_seconds": run.duration_seconds,
            "parameters": run.parameters,
            "notes": run.notes,
            "generators": m.active_generators(),
        }

    # -- lifecycle -----------------------------------------------------

    def reset(self, link: topology.LinkProfile | None = None) -> dict[str, Any]:
        """Return the laboratory to a known baseline.

        Stops every generator, rebuilds the namespaces from nothing and
        proves the result forwards traffic before reporting success.
        """
        with self._lock:
            self._halt_thread()
            m.stop_generators()
            m.reset_counters()
            m.reset_log_directory()
            profile = topology.reset(link)
            m.start_probe()
            self._run = None
            check = topology.connectivity_check()
            return {
                "reset": True,
                "link": {
                    "download_mbps": profile.download_mbps,
                    "upload_mbps": profile.upload_mbps,
                    "base_rtt_ms": profile.base_rtt_ms,
                    "queue_limit_packets": profile.queue_limit_packets,
                },
                "connectivity": check,
                "reset_at": datetime.now(UTC).isoformat(),
            }

    def start(
        self,
        scenario_id: str,
        experiment_id: str,
        parameters: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._run is not None and not self._run.finished:
                raise ValidationError(
                    f"Experiment '{self._run.experiment_id}' is still running. "
                    f"Stop it before starting another."
                )
            scenario = get_scenario(scenario_id)
            checked_id = validate_identifier(experiment_id, "experiment identifier")
            resolved, notes = resolve_parameters(scenario, parameters or {})

            link = topology.LinkProfile(
                download_mbps=resolved.get("bottleneck_download_mbps", scenario.download_mbps),
                upload_mbps=resolved.get("bottleneck_upload_mbps", scenario.upload_mbps),
                base_rtt_ms=scenario.base_rtt_ms,
                queue_limit_packets=scenario.queue_limit_packets,
            )

            # Always rebuild before a run. A run that inherits leftover state
            # is not reproducible, which defeats the point of a seed.
            m.stop_generators()
            m.reset_counters()
            m.reset_log_directory()
            topology.reset(link)
            enf.clear_shaping()
            # Counting filters go in before any traffic, so the observer sees
            # a flow's real packet sizes from its very first window.
            m.install_flow_counters(
                [
                    {
                        "name": flow.name,
                        "port": flow.port,
                        "protocol": flow.protocol,
                        "direction": flow.direction,
                    }
                    for flow in scenario.flows
                ]
            )
            # The probe runs continuously for the whole experiment, so its
            # sampling rate is independent of how often the controller asks.
            m.start_probe()

            self._run = RunState(
                experiment_id=checked_id,
                scenario=scenario,
                parameters=resolved,
                notes=notes,
                started_at=time.monotonic(),
                duration_seconds=resolved.get("duration_seconds", scenario.duration_seconds),
                link=link,
            )
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._schedule_loop, name="qosense-lab-schedule", daemon=True
            )
            self._thread.start()
            logger.info("started %s running scenario %s", checked_id, scenario_id)
            return self.describe()

    def stop(self, experiment_id: str | None = None) -> dict[str, Any]:
        """Stop the run and return the data plane to its unshaped baseline."""
        with self._lock:
            run = self._run
            if run is None:
                return {"stopped": False, "reason": "No experiment is running."}
            if experiment_id is not None and experiment_id != run.experiment_id:
                raise ValidationError(
                    f"Experiment '{experiment_id}' is not the active run."
                )
            self._halt_thread()
            m.stop_generators()
            enf.clear_shaping()
            run.stopped = True
            run.stopped_at = time.monotonic()
            logger.info("stopped %s", run.experiment_id)
            return {
                "stopped": True,
                "experiment_id": run.experiment_id,
                "elapsed_seconds": round(run.elapsed_seconds, 3),
            }

    def _halt_thread(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None

    # -- schedule ------------------------------------------------------

    def _schedule_loop(self) -> None:
        """Start flows and apply impairments at their scheduled times."""
        while not self._stop_event.is_set():
            run = self._run
            if run is None or run.finished:
                break
            elapsed = run.elapsed_seconds

            for flow in run.scenario.flows:
                start_at = flow.start_seconds
                if flow.traffic_class == "BULK":
                    start_at = run.parameters.get(
                        "download_start_after_seconds", flow.start_seconds
                    )
                if flow.name in run.started_flows or elapsed < start_at:
                    continue
                remaining = max(2.0, run.duration_seconds - elapsed)
                try:
                    m.start_flow(
                        name=flow.name,
                        port=flow.port,
                        protocol=flow.protocol,
                        traffic_class=flow.traffic_class,
                        direction=flow.direction,
                        duration_seconds=remaining,
                        rate_mbps=flow.rate_mbps,
                        packet_bytes=flow.packet_bytes,
                    )
                except Exception:  # pragma: no cover - a generator failing is not fatal
                    logger.exception("could not start generator %s", flow.name)
                run.started_flows.add(flow.name)

            for index, impairment in enumerate(run.scenario.impairments):
                at = run.parameters.get("drop_at_seconds", impairment.at_seconds)
                if index in run.applied_impairments or elapsed < at:
                    continue
                download = run.parameters.get(
                    "dropped_download_mbps", impairment.download_mbps
                )
                profile = topology.LinkProfile(
                    download_mbps=download or run.link.download_mbps,
                    upload_mbps=impairment.upload_mbps or run.link.upload_mbps,
                    base_rtt_ms=run.link.base_rtt_ms,
                    queue_limit_packets=run.link.queue_limit_packets,
                )
                topology.apply_bottleneck(profile)
                run.link = profile
                run.applied_impairments.add(index)
                run.notes.append(impairment.description)
                logger.info("applied impairment: %s", impairment.description)

            self._stop_event.wait(0.25)

        run = self._run
        if run is not None and not run.stopped:
            m.stop_generators()
            logger.info("run %s reached its duration", run.experiment_id)

    # -- measurement ---------------------------------------------------

    def metrics(self, window_seconds: float = 2.0) -> dict[str, Any]:
        """One measurement sample. Everything here came from the kernel.

        This returns immediately: the round-trip figures are read out of the
        continuous probe's recent window rather than by starting a new ping,
        so a controller sampling once a second is not held up for a second.

        The emulated bottleneck's configured rate is deliberately absent. The
        capacity estimator has to discover it from delivered throughput and
        delay, which is the whole point of having one.
        """
        run = self._run
        probe = m.recent_probe(window_seconds=window_seconds)
        queues = m.queue_stats()
        counters = m.flow_counters()

        return {
            "experiment_id": run.experiment_id if run else None,
            "elapsed_seconds": round(run.elapsed_seconds, 3) if run else 0.0,
            "running": bool(run and not run.finished),
            "measured_at": datetime.now(UTC).isoformat(),
            "data_mode": "LIVE",
            "probe": probe,
            "probe_running": m.probe_running(),
            "flows": _merge_flow_views(m.flow_rates(), counters),
            "classes": {
                "download": m.class_throughput(IF_IFB),
                "upload": m.class_throughput(IF_GW_WAN),
            },
            "queues": queues,
        }


RUNNER = LabRunner()


def _merge_flow_views(
    reported: dict[str, Any], counted: dict[str, Any]
) -> dict[str, Any]:
    """Combine what a generator says about itself with what the kernel saw.

    The kernel's counters win for throughput and packet size, because they
    are what actually crossed the interface. The generator still contributes
    the datagram statistics it alone can report, such as one-way loss.
    """
    merged: dict[str, Any] = {}
    for name, entry in reported.items():
        counter = counted.get(name, {})
        merged[name] = {
            **entry,
            "throughput_mbps": (
                counter.get("throughput_mbps")
                if counter.get("throughput_mbps") is not None
                else entry.get("throughput_mbps")
            ),
            "packets_per_second": (
                counter.get("packets_per_second")
                if counter.get("packets_per_second") is not None
                else entry.get("packets_per_second")
            ),
            "mean_packet_bytes": counter.get("mean_packet_bytes"),
            "total_bytes": counter.get("total_bytes"),
            "measured_by": "kernel counters" if counter else "generator report",
        }
    # A flow the generator has not started yet can still have counters.
    for name, counter in counted.items():
        if name not in merged:
            merged[name] = {**counter, "running": False, "measured_by": "kernel counters"}
    return merged
