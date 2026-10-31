"""API contract tests, including request validation and the event stream."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.domain.enums import DataMode, ExperimentMode, ModelState, RiskBand


def _create(client: TestClient, **overrides) -> dict:
    body = {
        "scenario_id": "iso_during_call",
        "mode": ExperimentMode.TRADITIONAL_ADAPTIVE.value,
        "data_mode": DataMode.SIMULATION.value,
        "seed": 42,
        "duration_seconds": 30,
    }
    body.update(overrides)
    response = client.post("/api/v1/experiments", json=body)
    assert response.status_code == 201, response.text
    return response.json()


class TestSystemEndpoints:
    def test_health(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_system_status_is_honest_about_what_is_available(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/v1/system/status").json()
        assert body["data_mode"] == DataMode.SIMULATION.value
        assert body["supported_data_modes"] == [DataMode.SIMULATION.value]
        assert body["lab_agent"]["reachable"] is False
        assert body["lab_agent"]["live_enforcement_enabled"] is False
        assert all(m["state"] == ModelState.NOT_CONFIGURED.value for m in body["models"])

    def test_mode_availability_flags_the_degraded_modes(self, client: TestClient) -> None:
        modes = {m["mode"]: m for m in client.get("/api/v1/system/status").json()["modes"]}
        assert modes[ExperimentMode.NO_QOS.value]["degraded"] is False
        assert modes[ExperimentMode.TRADITIONAL_ADAPTIVE.value]["degraded"] is False
        assert modes[ExperimentMode.CLASSIFIER_ONLY.value]["degraded"] is True
        assert modes[ExperimentMode.FULL_QOSENSE.value]["degraded"] is True

    def test_scenarios_declare_bounded_parameters(self, client: TestClient) -> None:
        scenarios = client.get("/api/v1/scenarios").json()
        assert {s["scenario_id"] for s in scenarios} == {
            "iso_during_call",
            "bottleneck_drop",
            "three_streams_gaming",
            "custom_bounded",
        }
        for scenario in scenarios:
            assert scenario["is_laboratory_emulation"] is True
            for parameter in scenario["parameters"]:
                assert parameter["minimum"] <= parameter["default"] <= parameter["maximum"]

    def test_model_status_reports_both_models_with_their_phase(
        self, client: TestClient
    ) -> None:
        models = client.get("/api/v1/models/status").json()
        assert len(models) == 2
        by_key = {m["key"]: m for m in models}
        assert by_key["traffic_classifier"]["round_allocation"] == "Round 1"
        assert by_key["congestion_predictor"]["round_allocation"] == "Round 2"
        assert all(m["version"] == "not-trained" for m in models)

    def test_there_is_no_command_execution_endpoint(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        joined = " ".join(paths).lower()
        for forbidden in ("shell", "exec", "command", "tc", "qdisc", "iface", "interface"):
            assert f"/{forbidden}" not in joined


class TestExperimentEndpoints:
    def test_create_start_stop_and_report(self, client: TestClient) -> None:
        created = _create(client)
        experiment_id = created["config"]["experiment_id"]
        assert created["status"] == "CREATED"

        started = client.post(f"/api/v1/experiments/{experiment_id}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "RUNNING"

        stopped = client.post(f"/api/v1/experiments/{experiment_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "STOPPED"

        report = client.get(f"/api/v1/experiments/{experiment_id}/report")
        assert report.status_code == 200
        body = report.json()
        assert body["data_mode"] == DataMode.SIMULATION.value
        assert body["limitations"]

    def test_reset_produces_a_fresh_run(self, client: TestClient) -> None:
        experiment_id = _create(client)["config"]["experiment_id"]
        client.post(f"/api/v1/experiments/{experiment_id}/start")
        body = client.post(f"/api/v1/experiments/{experiment_id}/reset").json()
        assert body["status"] == "CREATED"
        assert body["config"]["experiment_id"] != experiment_id
        assert body["config"]["seed"] == 42

    def test_pause_and_resume(self, client: TestClient) -> None:
        experiment_id = _create(client)["config"]["experiment_id"]
        client.post(f"/api/v1/experiments/{experiment_id}/start")
        assert client.post(f"/api/v1/experiments/{experiment_id}/pause").json()["status"] == (
            "PAUSED"
        )
        assert client.post(f"/api/v1/experiments/{experiment_id}/resume").json()["status"] == (
            "RUNNING"
        )

    def test_starting_a_finished_run_is_a_conflict(self, client: TestClient) -> None:
        experiment_id = _create(client)["config"]["experiment_id"]
        client.post(f"/api/v1/experiments/{experiment_id}/start")
        client.post(f"/api/v1/experiments/{experiment_id}/stop")
        response = client.post(f"/api/v1/experiments/{experiment_id}/start")
        assert response.status_code == 409

    def test_unknown_experiment_is_not_found(self, client: TestClient) -> None:
        assert client.get("/api/v1/experiments/exp-9999").status_code == 404
        assert client.post("/api/v1/experiments/exp-9999/reset").status_code == 404

    def test_telemetry_before_any_experiment_is_not_found(self, client: TestClient) -> None:
        assert client.get("/api/v1/metrics/current").status_code == 404


class TestValidation:
    def test_an_unknown_scenario_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/experiments",
            json={"scenario_id": "rm -rf /", "mode": ExperimentMode.NO_QOS.value},
        )
        assert response.status_code == 404

    def test_live_data_mode_is_refused_with_an_explanation(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/experiments",
            json={
                "scenario_id": "iso_during_call",
                "mode": ExperimentMode.NO_QOS.value,
                "data_mode": DataMode.LIVE.value,
            },
        )
        assert response.status_code == 422
        assert "laboratory agent" in response.text

    def test_an_unknown_mode_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/experiments",
            json={"scenario_id": "iso_during_call", "mode": "GOD_MODE"},
        )
        assert response.status_code == 422

    def test_unexpected_fields_are_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/experiments",
            json={
                "scenario_id": "iso_during_call",
                "mode": ExperimentMode.NO_QOS.value,
                "interface": "eth0",
            },
        )
        assert response.status_code == 422

    def test_out_of_range_parameters_are_clamped_server_side(
        self, client: TestClient
    ) -> None:
        created = _create(client, parameters={"bottleneck_download_mbps": 10_000})
        assert created["config"]["parameters"]["bottleneck_download_mbps"] == 500.0
        assert any("Clamped" in note for note in created["notes"])

    def test_a_negative_seed_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/experiments",
            json={
                "scenario_id": "iso_during_call",
                "mode": ExperimentMode.NO_QOS.value,
                "seed": -1,
            },
        )
        assert response.status_code == 422


class TestTelemetryEndpoints:
    def _running(self, client: TestClient) -> str:
        experiment_id = _create(client)["config"]["experiment_id"]
        client.post(f"/api/v1/experiments/{experiment_id}/start")
        state: dict = client.app.state.qosense  # type: ignore[assignment]
        runtime = client.app.state.qosense.manager.get(experiment_id)
        for _ in range(200):
            runtime.tick()
        assert state is not None
        return experiment_id

    def test_metrics_snapshot_shape(self, client: TestClient) -> None:
        experiment_id = self._running(client)
        body = client.get(f"/api/v1/metrics/current?experiment_id={experiment_id}").json()
        assert body["data_mode"] == DataMode.SIMULATION.value
        assert body["rtt_ms"] is not None
        assert body["added_delay_ms"] is not None
        assert body["congestion_prediction"] == RiskBand.UNAVAILABLE.value
        directions = {d["direction"] for d in body["per_direction"]}
        assert directions == {"DOWNLOAD", "UPLOAD"}
        for direction in body["per_direction"]:
            # The configured bottleneck stays hidden: the estimator has to find it.
            assert direction["link_rate_mbps"] is None
            assert direction["queue_delay_ms"] >= 0

    def test_flows_are_listed_with_their_class_source(self, client: TestClient) -> None:
        experiment_id = self._running(client)
        flows = client.get(f"/api/v1/flows?experiment_id={experiment_id}").json()
        assert flows
        for flow in flows:
            assert flow["data_mode"] == DataMode.SIMULATION.value
            assert flow["classification_source"] in (
                "DETERMINISTIC_RULES",
                "SAFE_FALLBACK",
                "MODEL_1",
            )

    def test_topology_describes_the_emulated_home(self, client: TestClient) -> None:
        experiment_id = self._running(client)
        body = client.get(f"/api/v1/topology?experiment_id={experiment_id}").json()
        kinds = {d["kind"] for d in body["devices"]}
        assert {"GATEWAY", "INTERNET"} <= kinds
        assert body["links"]

    def test_events_can_be_filtered_by_type(self, client: TestClient) -> None:
        experiment_id = self._running(client)
        body = client.get(
            f"/api/v1/events?experiment_id={experiment_id}"
            f"&event_type=metrics.snapshot&limit=20"
        ).json()
        assert body["events"]
        assert all(e["type"] == "metrics.snapshot" for e in body["events"])

    def test_policy_view_separates_measured_from_predicted_congestion(
        self, client: TestClient
    ) -> None:
        experiment_id = self._running(client)
        body = client.get(f"/api/v1/policies/current?experiment_id={experiment_id}").json()
        assert body["measured_congestion"] in (
            "UNKNOWN",
            "CLEAR",
            "BUILDING",
            "CONGESTED",
            "BUFFERBLOAT",
        )
        assert body["predicted_congestion"]["risk_band"] == RiskBand.UNAVAILABLE.value
        assert body["predicted_congestion"]["congestion_probability"] is None


class TestIntentEndpoints:
    def _running(self, client: TestClient) -> str:
        experiment_id = _create(client)["config"]["experiment_id"]
        client.post(f"/api/v1/experiments/{experiment_id}/start")
        runtime = client.app.state.qosense.manager.get(experiment_id)
        for _ in range(50):
            runtime.tick()
        return experiment_id

    def test_create_and_cancel_a_lease(self, client: TestClient) -> None:
        experiment_id = self._running(client)
        created = client.post(
            "/api/v1/intents",
            json={
                "target_type": "DEVICE",
                "target_id": "laptop",
                "intent_type": "PRIORITIZE_INTERACTIVE",
                "duration_seconds": 120,
                "reason": "Work call",
                "experiment_id": experiment_id,
            },
        )
        assert created.status_code == 201, created.text
        lease = created.json()
        assert lease["status"] == "ACTIVE"

        cancelled = client.delete(
            f"/api/v1/intents/{lease['intent_id']}?experiment_id={experiment_id}"
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"

    def test_an_unknown_target_is_rejected(self, client: TestClient) -> None:
        experiment_id = self._running(client)
        response = client.post(
            "/api/v1/intents",
            json={
                "target_type": "DEVICE",
                "target_id": "neighbours-router",
                "intent_type": "PRIORITIZE_INTERACTIVE",
                "duration_seconds": 60,
                "experiment_id": experiment_id,
            },
        )
        assert response.status_code == 422
        assert "not known" in response.text

    def test_an_excessive_duration_is_rejected(self, client: TestClient) -> None:
        experiment_id = self._running(client)
        response = client.post(
            "/api/v1/intents",
            json={
                "target_type": "DEVICE",
                "target_id": "laptop",
                "intent_type": "PRIORITIZE_INTERACTIVE",
                "duration_seconds": 999_999,
                "experiment_id": experiment_id,
            },
        )
        assert response.status_code == 422


class TestWebSocket:
    def test_the_stream_sends_a_snapshot_then_live_events(self, client: TestClient) -> None:
        experiment_id = _create(client)["config"]["experiment_id"]
        client.post(f"/api/v1/experiments/{experiment_id}/start")
        runtime = client.app.state.qosense.manager.get(experiment_id)

        with client.websocket_connect(
            f"/api/v1/ws/experiments/{experiment_id}"
        ) as websocket:
            hello = json.loads(websocket.receive_text())
            assert hello["type"] == "experiment.status"
            assert hello["payload"]["connected"] is True

            for _ in range(5):
                runtime.tick()

            seen = [json.loads(websocket.receive_text()) for _ in range(5)]
            assert all(e["data_mode"] == DataMode.SIMULATION.value for e in seen)
            sequences = [e["sequence"] for e in seen]
            assert sequences == sorted(sequences)
