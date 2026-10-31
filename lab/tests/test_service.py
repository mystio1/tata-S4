"""Tests for the agent's network interface.

These exercise authentication, the shape of each request body and the absence
of anything resembling a command interface. They do not need root, because
every test here is rejected before a privileged command would run.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("QOSENSE_LAB_AGENT_TOKEN", "test-token")
    import agent.service as service

    importlib.reload(service)
    return TestClient(service.create_app())


AUTH = {"Authorization": "Bearer test-token"}


class TestAuthentication:
    def test_health_needs_no_token(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["data_mode"] == "LIVE"

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/scenarios"),
            ("get", "/metrics"),
            ("get", "/tc-state"),
            ("get", "/audit"),
            ("post", "/reset"),
            ("post", "/policy/clear"),
        ],
    )
    def test_privileged_operations_require_a_token(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = getattr(client, method)(path)
        assert response.status_code == 401

    def test_a_wrong_token_is_refused(self, client: TestClient) -> None:
        response = client.get("/scenarios", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401

    def test_a_valid_token_is_accepted(self, client: TestClient) -> None:
        assert client.get("/scenarios", headers=AUTH).status_code == 200


class TestNoCommandInterface:
    def test_the_published_interface_exposes_no_command_endpoint(
        self, client: TestClient
    ) -> None:
        paths = " ".join(client.get("/openapi.json").json()["paths"]).lower()
        for forbidden in ("shell", "exec", "command", "run", "qdisc", "iface", "sudo"):
            assert f"/{forbidden}" not in paths

    def test_no_request_body_accepts_an_interface_name(self, client: TestClient) -> None:
        """Interface names are agent constants; a caller cannot supply one."""
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        text = str(schemas).lower()
        for forbidden in ("interface", "namespace", "device", "qdisc", "command"):
            assert forbidden not in text

    def test_extra_fields_are_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/scenario/start",
            headers=AUTH,
            json={
                "scenario_id": "iso_during_call",
                "experiment_id": "exp-1",
                "interface": "eth0",
            },
        )
        assert response.status_code == 422


class TestRequestValidation:
    def test_an_unknown_scenario_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/scenario/start",
            headers=AUTH,
            json={"scenario_id": "../../etc/passwd", "experiment_id": "exp-1"},
        )
        assert response.status_code == 422
        assert "Unknown scenario" in response.text

    def test_a_reset_rate_outside_bounds_is_rejected(self, client: TestClient) -> None:
        response = client.post("/reset", headers=AUTH, json={"download_mbps": 99_999})
        assert response.status_code == 422

    def test_a_policy_naming_an_unknown_class_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/policy/apply",
            headers=AUTH,
            json={
                "policy_id": "policy-1",
                "profile": "PROTECT_INTERACTIVE",
                "download": {
                    "total_mbps": 95.0,
                    "classes": [
                        {"traffic_class": "NONSENSE", "ceiling_mbps": 10, "floor_mbps": 1}
                    ],
                },
                "upload": {
                    "total_mbps": 19.0,
                    "classes": [
                        {"traffic_class": "BULK", "ceiling_mbps": 10, "floor_mbps": 1}
                    ],
                },
                "flows": [],
            },
        )
        assert response.status_code == 422

    def test_a_policy_naming_a_port_outside_the_laboratory_is_rejected(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/policy/apply",
            headers=AUTH,
            json={
                "policy_id": "policy-1",
                "profile": "PROTECT_INTERACTIVE",
                "download": {
                    "total_mbps": 95.0,
                    "classes": [
                        {"traffic_class": "BULK", "ceiling_mbps": 10, "floor_mbps": 1}
                    ],
                },
                "upload": {
                    "total_mbps": 19.0,
                    "classes": [
                        {"traffic_class": "BULK", "ceiling_mbps": 10, "floor_mbps": 1}
                    ],
                },
                "flows": [{"port": 443, "protocol": "TCP", "traffic_class": "BULK"}],
            },
        )
        assert response.status_code == 422

    def test_too_many_classes_are_rejected(self, client: TestClient) -> None:
        classes = [
            {"traffic_class": "BULK", "ceiling_mbps": 1, "floor_mbps": 0.1}
            for _ in range(20)
        ]
        response = client.post(
            "/policy/apply",
            headers=AUTH,
            json={
                "policy_id": "policy-1",
                "profile": "PROTECT_INTERACTIVE",
                "download": {"total_mbps": 95.0, "classes": classes},
                "upload": {"total_mbps": 19.0, "classes": classes},
                "flows": [],
            },
        )
        assert response.status_code == 422


class TestScenarioCatalogue:
    def test_scenarios_declare_bounded_parameters(self, client: TestClient) -> None:
        for scenario in client.get("/scenarios", headers=AUTH).json():
            assert scenario["is_laboratory_emulation"] is True
            for parameter in scenario["parameters"]:
                assert parameter["minimum"] <= parameter["default"] <= parameter["maximum"]

    def test_health_reports_the_tooling_it_actually_found(self, client: TestClient) -> None:
        tooling = client.get("/health").json()["tooling"]
        assert set(tooling) == {"ip", "tc", "ping", "iperf3"}
        assert all(isinstance(value, bool) for value in tooling.values())
