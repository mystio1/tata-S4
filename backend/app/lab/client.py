"""Client for the privileged Ubuntu laboratory agent.

The public interface never performs privileged networking itself. It asks the
agent, over an authenticated connection, to run one of a fixed set of named
operations. This module is the whole of that conversation.

Nothing here can send a command, an interface name or a queue discipline
string, because the agent has no operation that accepts one. The strongest
thing a caller can express is "apply this validated policy profile", and the
agent re-validates it before touching the kernel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("qosense.lab.client")

DEFAULT_TIMEOUT_SECONDS = 10.0
# Applying a policy reads state back afterwards, so it is allowed longer.
APPLY_TIMEOUT_SECONDS = 30.0
RESET_TIMEOUT_SECONDS = 60.0


class LabAgentError(RuntimeError):
    """The laboratory agent could not be reached, or refused the request."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class LabAgentHealth:
    reachable: bool
    detail: str
    agent_version: str | None = None
    laboratory_built: bool = False
    tooling: dict[str, bool] | None = None


class LabAgentClient:
    """Typed calls to the agent's allow-listed operations."""

    def __init__(self, base_url: str, token: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(
                method,
                url,
                json=body,
                headers=self._headers,
                timeout=timeout or self._timeout,
            )
        except httpx.HTTPError as exc:
            raise LabAgentError(
                f"The laboratory agent at {self.base_url} could not be reached: {exc}"
            ) from exc

        if response.status_code >= 400:
            detail: Any
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise LabAgentError(
                f"The laboratory agent refused the request: {detail}",
                status_code=response.status_code,
            )
        return response.json()

    # -- operations ----------------------------------------------------

    def health(self) -> LabAgentHealth:
        """Check the agent without authenticating, so a missing token shows up."""
        try:
            payload = self._request("GET", "/health", timeout=4.0)
        except LabAgentError as exc:
            return LabAgentHealth(reachable=False, detail=str(exc))
        return LabAgentHealth(
            reachable=True,
            detail="Laboratory agent reachable.",
            agent_version=payload.get("agent_version"),
            laboratory_built=bool(payload.get("laboratory_built")),
            tooling=payload.get("tooling"),
        )

    def list_scenarios(self) -> list[dict[str, Any]]:
        return self._request("GET", "/scenarios")

    def reset_lab(self, **link: float | None) -> dict[str, Any]:
        body = {key: value for key, value in link.items() if value is not None}
        return self._request("POST", "/reset", body, timeout=RESET_TIMEOUT_SECONDS)

    def start_scenario(
        self, scenario_id: str, experiment_id: str, parameters: dict[str, float]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/scenario/start",
            {
                "scenario_id": scenario_id,
                "experiment_id": experiment_id,
                "parameters": parameters,
            },
            timeout=RESET_TIMEOUT_SECONDS,
        )

    def stop_scenario(self, experiment_id: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/scenario/stop", {"experiment_id": experiment_id})

    def scenario_state(self) -> dict[str, Any]:
        return self._request("GET", "/scenario/state")

    def get_metrics(self, window_seconds: float = 2.0) -> dict[str, Any]:
        return self._request("GET", f"/metrics?window_seconds={window_seconds:g}")

    def apply_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/policy/apply", payload, timeout=APPLY_TIMEOUT_SECONDS)

    def restore_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return self._request(
            "POST", "/policy/restore", {"snapshot_id": snapshot_id}, timeout=APPLY_TIMEOUT_SECONDS
        )

    def clear_policy(self) -> dict[str, Any]:
        return self._request("POST", "/policy/clear", timeout=APPLY_TIMEOUT_SECONDS)

    def read_tc_state(self) -> dict[str, Any]:
        return self._request("GET", "/tc-state")

    def audit(self, limit: int = 50) -> dict[str, Any]:
        return self._request("GET", f"/audit?limit={limit}")
