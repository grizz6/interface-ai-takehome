"""The gate is a pure function of config plus action, so it needs no browser to test."""
from __future__ import annotations

import pytest

from src.models.common import ActionType, RiskClass
from src.models.locator import LocatorBundle, RoleNameLocator
from src.models.policy import PolicyConfig
from src.policy.gate import Allowed, Blocked, PolicyGate
from src.surface.actions import ClickAction, NavigateAction, PressAction

BASE = "http://127.0.0.1:8080"


def config(**overrides: object) -> PolicyConfig:
    data: dict[str, object] = {
        "allowed_hosts": ["127.0.0.1"],
        "allowed_path_patterns": [r"^/"],
        "denied_path_patterns": [r"^/dev/"],
        "allowed_actions": [ActionType.NAVIGATE, ActionType.CLICK, ActionType.TYPE],
        "risky_action_policy": "require_approval",
        "risky_control_names": ["Confirm"],
    }
    data.update(overrides)
    return PolicyConfig(**data)  # type: ignore[arg-type]


def bundle(name: str) -> LocatorBundle:
    return LocatorBundle(
        primary=RoleNameLocator(role="button", name=name), recorded_accessible_name=name
    )


@pytest.mark.parametrize("path", ["/search", "/member/100001", "/member/100001/subaccount"])
def test_ordinary_application_routes_are_allowed(path: str) -> None:
    decision = PolicyGate(config()).check(NavigateAction(url=BASE + path))
    assert isinstance(decision, Allowed)


@pytest.mark.parametrize("path", ["/dev/faults", "/dev/reset"])
def test_developer_routes_are_blocked(path: str) -> None:
    decision = PolicyGate(config()).check(NavigateAction(url=BASE + path))
    assert isinstance(decision, Blocked)
    assert decision.rule == "denied_path_patterns"


def test_denied_beats_allowed_even_when_both_match() -> None:
    """The precedence documented on PolicyConfig, asserted rather than assumed."""
    gate = PolicyGate(config(allowed_path_patterns=[r"^/dev/"], denied_path_patterns=[r"^/dev/"]))
    decision = gate.check(NavigateAction(url=BASE + "/dev/faults"))
    assert isinstance(decision, Blocked)
    assert "denied always wins" in decision.reason


def test_a_host_outside_the_allowlist_is_blocked() -> None:
    decision = PolicyGate(config()).check(NavigateAction(url="http://example.test/search"))
    assert isinstance(decision, Blocked)
    assert decision.rule == "allowed_hosts"


def test_a_path_matching_no_allowed_pattern_is_blocked() -> None:
    gate = PolicyGate(config(allowed_path_patterns=[r"^/member/"]))
    decision = gate.check(NavigateAction(url=BASE + "/search"))
    assert isinstance(decision, Blocked)
    assert decision.rule == "allowed_path_patterns"


def test_an_action_kind_absent_from_allowed_actions_is_blocked() -> None:
    decision = PolicyGate(config()).check(PressAction(key="Enter"))
    assert isinstance(decision, Blocked)
    assert decision.rule == "allowed_actions"
    assert "press" in decision.reason


def test_a_risky_control_name_needs_approval_even_when_the_route_is_fine() -> None:
    decision = PolicyGate(config()).check(ClickAction(bundle=bundle("Confirm")))
    assert isinstance(decision, Blocked)
    assert decision.rule == "risky_action_policy:require_approval"


def test_an_ordinary_control_name_is_not_risky() -> None:
    assert isinstance(PolicyGate(config()).check(ClickAction(bundle=bundle("Search"))), Allowed)


def test_flag_policy_allows_but_marks_the_action() -> None:
    gate = PolicyGate(config(risky_action_policy="flag"))
    decision = gate.check(ClickAction(bundle=bundle("Confirm")))
    assert isinstance(decision, Allowed)
    assert decision.risky is True


def test_block_policy_refuses_outright() -> None:
    gate = PolicyGate(config(risky_action_policy="block"))
    decision = gate.check(ClickAction(bundle=bundle("Confirm")))
    assert isinstance(decision, Blocked)
    assert decision.rule == "risky_action_policy:block"


def test_a_recorded_risk_classification_makes_an_action_risky_without_a_name_match() -> None:
    """During replay the risk comes from the recorded Step, not from the control name."""
    decision = PolicyGate(config()).check(
        ClickAction(bundle=bundle("Submit Request")), risk=RiskClass.RISKY_IRREVERSIBLE
    )
    assert isinstance(decision, Blocked)
