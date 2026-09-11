"""config/policy.json must load, validate, and mean what the docstring says it means."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models.common import ActionType, Sensitivity
from src.policy.gate import Allowed, Blocked, PolicyGate
from src.policy.loading import PolicyConfigError, load_policy_config


def test_the_shipped_policy_loads_and_validates() -> None:
    config = load_policy_config()
    assert set(config.allowed_actions) == set(ActionType)
    assert config.risky_action_policy == "require_approval"
    assert "Confirm" in config.risky_control_names
    assert config.redact_sensitivities == [Sensitivity.PII, Sensitivity.SECRET]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", True),
        ("/search", True),
        ("/member/100001", True),
        ("/member/100001/subaccount", True),
        ("/member/100001/loan-servicing", True),
        ("/maintenance", True),
        ("/maintenance/continue", True),
        ("/session-expired", True),
        ("/dev/faults", False),
        ("/dev/reset", False),
    ],
)
def test_the_shipped_policy_admits_exactly_what_it_should(path: str, expected: bool) -> None:
    decision = PolicyGate(load_policy_config()).check_url("http://127.0.0.1:8080" + path)
    assert isinstance(decision, Allowed) is expected


def test_the_error_screens_are_allowed_so_recovery_is_not_self_blocked() -> None:
    """Denying these would make the gate block the system's own interstitial recovery."""
    gate = PolicyGate(load_policy_config())
    for path in ("/maintenance", "/maintenance/continue", "/session-expired"):
        assert isinstance(gate.check_url("http://127.0.0.1:8080" + path), Allowed), path


def test_the_allowlist_pins_the_port_not_just_the_host() -> None:
    gate = PolicyGate(load_policy_config())
    assert isinstance(gate.check_url("http://127.0.0.1:8080/search"), Allowed)
    blocked = gate.check_url("http://127.0.0.1:9999/search")
    assert isinstance(blocked, Blocked)
    assert blocked.rule == "allowed_hosts"


def test_patterns_are_anchored_so_the_allowlist_is_not_decorative() -> None:
    """A bare "/" would match every path, since the gate matches with re.search."""
    gate = PolicyGate(load_policy_config())
    for path in ("/member-notes", "/x/member/1", "/searching"):
        assert isinstance(gate.check_url("http://127.0.0.1:8080" + path), Blocked), path


# -- malformed configs are rejected at load, not at first use --------------------
@pytest.mark.parametrize(
    ("broken", "fragment"),
    [
        ({"allowed_actions": ["fly"]}, "allowed_actions"),
        ({"risky_action_policy": "maybe"}, "risky_action_policy"),
        ({"allowed_hosts": "127.0.0.1"}, "allowed_hosts"),
        ({"redact_sensitivities": ["gossip"]}, "redact_sensitivities"),
        ({"nonsense_field": True}, "nonsense_field"),
    ],
)
def test_a_malformed_policy_is_rejected_at_load(
    tmp_path: Path, broken: dict[str, object], fragment: str
) -> None:
    data = json.loads(Path("config/policy.json").read_text())
    data.update(broken)
    bad = tmp_path / "policy.json"
    bad.write_text(json.dumps(data))

    with pytest.raises(PolicyConfigError) as exc:
        load_policy_config(bad)
    assert "rejected at load rather than at first use" in str(exc.value)
    assert fragment in str(exc.value)


def test_invalid_json_is_reported_as_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "policy.json"
    bad.write_text("{not json")
    with pytest.raises(PolicyConfigError) as exc:
        load_policy_config(bad)
    assert "not valid JSON" in str(exc.value)


def test_a_missing_policy_file_is_reported_clearly(tmp_path: Path) -> None:
    with pytest.raises(PolicyConfigError) as exc:
        load_policy_config(tmp_path / "absent.json")
    assert "cannot read policy file" in str(exc.value)
