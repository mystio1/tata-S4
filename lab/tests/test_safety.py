"""Validation tests for the privileged agent.

These run anywhere, with no root and no Linux, because they test the layer
that decides whether a request is allowed to reach a privileged command at
all. That layer is the reason the rest of the agent can be trusted, so it is
tested without needing the laboratory to exist.
"""

from __future__ import annotations

import pytest

from agent import enforcement as enf
from agent import scenarios
from agent.safety import (
    INTERFACES,
    NAMESPACES,
    PORT_MAXIMUM,
    PORT_MINIMUM,
    RATE_MBPS,
    SUPPORTED_CLASSES,
    ValidationError,
    class_handle,
    format_rate,
    validate_class_rates,
    validate_identifier,
    validate_interface,
    validate_namespace,
    validate_port,
    validate_profile,
    validate_protocol,
    validate_traffic_class,
)


class TestIsolation:
    """Nothing a caller can say may reach the host's own networking."""

    @pytest.mark.parametrize(
        "name",
        ["eth0", "lo", "wlan0", "docker0", "", "qos-", "../eth0", "eth0; rm -rf /"],
    )
    def test_host_interfaces_are_unreachable(self, name: str) -> None:
        with pytest.raises(ValidationError):
            validate_interface(name)

    @pytest.mark.parametrize("name", list(INTERFACES))
    def test_laboratory_interfaces_are_permitted(self, name: str) -> None:
        assert validate_interface(name) == name

    def test_every_permitted_interface_carries_the_laboratory_prefix(self) -> None:
        assert all(name.startswith("qos-") for name in INTERFACES)

    @pytest.mark.parametrize("name", ["default", "host", "", "qos", "/proc/1/ns/net"])
    def test_other_namespaces_are_unreachable(self, name: str) -> None:
        with pytest.raises(ValidationError):
            validate_namespace(name)

    @pytest.mark.parametrize("name", list(NAMESPACES))
    def test_laboratory_namespaces_are_permitted(self, name: str) -> None:
        assert validate_namespace(name) == name


class TestIdentifiers:
    @pytest.mark.parametrize(
        "value",
        [
            "../../etc/passwd",
            "a b",
            "x" * 65,
            "",
            "$(whoami)",
            "a;b",
            "a|b",
            "a&b",
            "a\nb",
            "`id`",
        ],
    )
    def test_dangerous_identifiers_are_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError):
            validate_identifier(value)

    @pytest.mark.parametrize("value", ["exp-0001", "iso_during_call", "policy.v1", "a:b"])
    def test_ordinary_identifiers_are_accepted(self, value: str) -> None:
        assert validate_identifier(value) == value

    @pytest.mark.parametrize("value", ["protect interactive", "lowercase", "x", "A" * 40])
    def test_profile_names_are_constrained(self, value: str) -> None:
        with pytest.raises(ValidationError):
            validate_profile(value)

    def test_a_real_profile_name_is_accepted(self) -> None:
        assert validate_profile("PROTECT_INTERACTIVE") == "PROTECT_INTERACTIVE"


class TestBounds:
    @pytest.mark.parametrize(
        "port", [0, 1, 80, 443, 22, PORT_MINIMUM - 1, PORT_MAXIMUM + 1, 65_535]
    )
    def test_ports_outside_the_laboratory_range_are_rejected(self, port: int) -> None:
        with pytest.raises(ValidationError):
            validate_port(port)

    def test_laboratory_ports_are_accepted(self) -> None:
        assert validate_port(PORT_MINIMUM) == PORT_MINIMUM
        assert validate_port(PORT_MAXIMUM) == PORT_MAXIMUM

    @pytest.mark.parametrize("rate", [-1.0, 0.0, 1e9, 100_000.0])
    def test_rates_outside_bounds_are_rejected(self, rate: float) -> None:
        with pytest.raises(ValidationError):
            RATE_MBPS.validate(rate, "rate")

    @pytest.mark.parametrize("protocol", ["ICMP", "SCTP", "", "tcp"])
    def test_unsupported_protocols_are_rejected(self, protocol: str) -> None:
        with pytest.raises(ValidationError):
            validate_protocol(protocol)

    @pytest.mark.parametrize("name", ["VIDEO", "", "bulk", "INTERACTIVE "])
    def test_unknown_traffic_classes_are_rejected(self, name: str) -> None:
        with pytest.raises(ValidationError):
            validate_traffic_class(name)

    @pytest.mark.parametrize("name", list(SUPPORTED_CLASSES))
    def test_supported_traffic_classes_are_accepted(self, name: str) -> None:
        assert validate_traffic_class(name) == name

    def test_class_rate_tables_are_validated_entry_by_entry(self) -> None:
        with pytest.raises(ValidationError):
            validate_class_rates({"BULK": 10.0, "NONSENSE": 5.0})
        with pytest.raises(ValidationError):
            validate_class_rates({})
        assert validate_class_rates({"BULK": 10.0}) == {"BULK": 10.0}


class TestTrafficControlFormatting:
    def test_class_handles_are_hexadecimal(self) -> None:
        """Traffic control parses class minors as hexadecimal.

        Formatting them any other way makes the number asked for and the
        number the kernel prints disagree, and a read-back can then never be
        matched to its class.
        """
        assert class_handle("INTERACTIVE") == "1:10"
        assert class_handle("UNKNOWN") == "1:60"

    def test_rates_are_expressed_in_kilobits(self) -> None:
        assert format_rate(55.0) == "55000kbit"
        assert format_rate(0.05) == "50kbit"

    def test_a_tiny_rate_never_formats_to_zero(self) -> None:
        assert format_rate(0.0) == "1kbit"


class TestPolicyRequestValidation:
    def _direction(self, **overrides) -> enf.DirectionProfile:
        defaults = {
            "total_mbps": 95.0,
            "classes": (enf.ClassRate("BULK", 55.0, 2.0),),
        }
        defaults.update(overrides)
        return enf.DirectionProfile(**defaults)  # type: ignore[arg-type]

    def test_a_sound_request_validates(self) -> None:
        request = enf.PolicyRequest(
            policy_id="policy-0001",
            profile="PROTECT_INTERACTIVE",
            download=self._direction(),
            upload=self._direction(total_mbps=19.0),
            flows=(enf.FlowSelector(5201, "TCP", "BULK"),),
        )
        assert request.validated().policy_id == "policy-0001"

    def test_a_floor_above_its_ceiling_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exceeds its ceiling"):
            enf.ClassRate("BULK", 5.0, 50.0).validated()

    def test_floors_exceeding_the_shaped_total_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="exceeds the shaped total"):
            enf.DirectionProfile(
                total_mbps=10.0,
                classes=(
                    enf.ClassRate("BULK", 9.0, 8.0),
                    enf.ClassRate("INTERACTIVE", 9.0, 8.0),
                ),
            ).validated()

    def test_an_empty_direction_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least one class"):
            enf.DirectionProfile(total_mbps=10.0, classes=()).validated()

    def test_a_flow_selector_outside_the_laboratory_ports_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            enf.FlowSelector(443, "TCP", "BULK").validated()

    def test_the_known_safe_profile_validates(self) -> None:
        request = enf.known_safe_profile(100.0, 20.0).validated()
        assert request.profile == "CONSERVATIVE_SAFE"
        assert request.flows == ()


class TestRateParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("55Mbit", 55.0),
            ("55000Kbit", 55.0),
            ("1Gbit", 1000.0),
            ("500Kbit", 0.5),
        ],
    )
    def test_kernel_rate_strings_parse_back_to_megabits(
        self, text: str, expected: float
    ) -> None:
        assert enf.parse_rate_to_mbps(text) == pytest.approx(expected)

    def test_unparseable_text_returns_nothing(self) -> None:
        assert enf.parse_rate_to_mbps("no rate here") is None


class TestScenarios:
    def test_scenarios_are_named_the_same_as_the_simulator(self) -> None:
        """A laboratory run and a simulated run must describe one experiment."""
        assert {"iso_during_call", "bottleneck_drop", "three_streams_gaming"} <= set(
            scenarios.SCENARIOS
        )

    def test_an_unknown_scenario_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unknown scenario"):
            scenarios.get_scenario("../../etc/passwd")

    def test_every_generated_flow_uses_a_laboratory_port(self) -> None:
        for scenario in scenarios.SCENARIOS.values():
            for flow in scenario.flows:
                assert PORT_MINIMUM <= flow.port <= PORT_MAXIMUM
                validate_traffic_class(flow.traffic_class)
                validate_protocol(flow.protocol)

    def test_ports_are_unique_within_a_scenario(self) -> None:
        for scenario in scenarios.SCENARIOS.values():
            ports = [flow.port for flow in scenario.flows]
            assert len(ports) == len(set(ports)), scenario.scenario_id

    def test_out_of_range_parameters_are_clamped_and_reported(self) -> None:
        scenario = scenarios.get_scenario("iso_during_call")
        resolved, notes = scenarios.resolve_parameters(
            scenario, {"bottleneck_download_mbps": 99_999, "nonsense": 1}
        )
        assert resolved["bottleneck_download_mbps"] == 500.0
        assert any("Clamped" in note for note in notes)
        assert any("nonsense" in note for note in notes)

    def test_parameters_default_within_their_own_bounds(self) -> None:
        for scenario in scenarios.SCENARIOS.values():
            for parameter in scenario.parameters:
                assert parameter.minimum <= parameter.default <= parameter.maximum
