"""An expired session, which the only honest recovery for is to start the flow again.

Drives the live app. The session_expired fault is armed through the developer console in the
same browser, exactly as tests/test_replay.py arms the other faults, and then the run is left to
cope. DECISIONS.md 0046.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.evidence.writer import EvidenceWriter
from src.models.capability import Capability
from src.models.common import StuckReason
from src.models.results import NeedsHumanResult, SuccessResult
from src.policy.gate import PolicyGate
from src.policy.redaction import Redactor
from src.replay.engine import replay

LOOKUP = Path("capabilities/lookup-member-savings-balance-1.2.0.json")
SUBACCOUNT = Path("capabilities/open-member-subaccount-1.1.0.json")


def _reauthenticate(detect_text: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": "reauthenticate_after_session_expiry",
        "detect": {"kind": "text_present", "text": detect_text},
        "action": "reauthenticate",
        "max_attempts": 1,
        **extra,
    }


def _with_rule(path: Path, live_app: str, rule: dict[str, Any]) -> Capability:
    data = json.loads(path.read_text())
    data["surface"]["base_url"] = live_app
    data["recoveries"] = [*data["recoveries"], rule]
    return Capability.model_validate(data)


def _arm(surface: Any, base_url: str, fault: str) -> None:
    page = surface.page
    page.goto(f"{base_url}/dev/faults")
    label = "Clear Armed Fault" if fault == "clear" else f"Arm {fault}"
    page.get_by_role("button", name=label, exact=True).click()


@pytest.fixture(autouse=True)
def clear_faults(surface: Any, live_app: str) -> Iterator[None]:
    yield
    _arm(surface, live_app, "clear")


def _run(capability: Capability, params: dict[str, str], surface: Any, policy: Any,
         tmp_path: Path) -> tuple[Any, Path]:
    writer = EvidenceWriter("session", Redactor({}), root=tmp_path)
    result = replay(capability, params, surface, policy,
                    evidence=lambda: writer.ref, sink=writer, allow_draft=True)
    return result, writer.directory / "run.jsonl"


def test_an_expired_session_restarts_the_flow_and_still_succeeds(
    live_app: str, surface: Any, policy_config: Any, tmp_path: Path
) -> None:
    # The committed 1.3.0 artifact, unmodified: this is the rule that ships.
    data = json.loads(Path("capabilities/lookup-member-savings-balance-1.3.0.json").read_text())
    data["surface"]["base_url"] = live_app
    capability = Capability.model_validate(data)
    _arm(surface, live_app, "session_expired")

    result, log = _run(capability, {"member_id": "100001"}, surface, policy_config, tmp_path)

    assert isinstance(result, SuccessResult), result
    assert result.outputs["savings_balance"] == 4182.55
    assert result.recoveries_applied == ["reauthenticate_after_session_expiry"]
    kinds = [json.loads(line)["kind"] for line in log.read_text().splitlines()]
    assert kinds.count("restart_flow") == 1


def test_a_restart_that_keeps_coming_back_escalates_instead_of_looping(
    live_app: str, surface: Any, policy_config: Any, tmp_path: Path
) -> None:
    """A detect signal that is always on screen would restart forever without the budget."""
    capability = _with_rule(LOOKUP, live_app, _reauthenticate("Member Services Console"))

    result, log = _run(capability, {"member_id": "100001"}, surface, policy_config, tmp_path)

    assert isinstance(result, NeedsHumanResult), result
    assert result.reason is StuckReason.RECOVERY_EXHAUSTED
    kinds = [json.loads(line)["kind"] for line in log.read_text().splitlines()]
    assert kinds.count("restart_flow") == 1
    assert "restart_refused" in kinds


def test_no_restart_once_an_irreversible_step_has_run(
    live_app: str, surface: Any, policy_config: Any, tmp_path: Path
) -> None:
    """Starting over after Confirm could open the account twice, so it escalates instead.

    The rule detects the confirmation page itself, which only exists after the irreversible
    step ran. Risky steps are flagged rather than held for approval here so step 5 actually
    runs; the surface enforces the gate too, so its gate is swapped for the test and restored.
    """
    flag = policy_config.model_copy(update={"risky_action_policy": "flag"})
    capability = _with_rule(
        SUBACCOUNT, live_app,
        _reauthenticate("The sub-account has been opened.", applies_to_steps=[5]),
    )
    original_gate = surface._gate
    surface._gate = PolicyGate(flag)
    try:
        result, log = _run(
            capability,
            {"member_id": "100001", "account_type": "Savings", "nickname": "Vacation",
             "initial_deposit": "250.00"},
            surface, flag, tmp_path,
        )
    finally:
        surface._gate = original_gate

    assert isinstance(result, NeedsHumanResult), result
    assert result.reason is StuckReason.RECOVERY_EXHAUSTED
    assert result.step_index == 5
    kinds = [json.loads(line)["kind"] for line in log.read_text().splitlines()]
    assert "restart_refused" in kinds
    assert "restart_flow" not in kinds
