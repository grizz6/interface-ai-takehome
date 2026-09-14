"""Declared business outcomes that arrive in place of the screen a step was waiting for.

These drive the sub-account capability against the live app. Unlike the lookup capability, its
steps wait for specific text, so a screen replaced by an answer used to surface as a wait timeout
before any declared outcome was consulted. DECISIONS.md 0045.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.evidence.writer import EvidenceWriter
from src.models.capability import Capability
from src.models.results import BusinessOutcomeResult
from src.policy.redaction import Redactor
from src.replay.engine import replay

SUBACCOUNT = Path("capabilities/open-member-subaccount-1.0.0.json")

# The error messages the target app really shows on the sub-account form.
FORM_ERRORS = (
    r"Select an account type\.|Nickname is required\.|"
    r"Initial deposit (is required|must be a number|must be greater than zero)\."
)


def _subaccount(live_app: str, *, real_validation_signal: bool) -> Capability:
    data = json.loads(SUBACCOUNT.read_text())
    data["surface"]["base_url"] = live_app
    if real_validation_signal:
        for outcome in data["known_outcomes"]:
            if outcome["code"] == "validation_rejected":
                outcome["detect"]["text"] = None
                outcome["detect"]["pattern"] = FORM_ERRORS
    return Capability.model_validate(data)


def _params(**overrides: str) -> dict[str, str]:
    return {
        "member_id": "100001", "account_type": "Savings",
        "nickname": "Vacation", "initial_deposit": "250.00", **overrides,
    }


def _run(capability: Capability, params: dict[str, str], surface: Any, policy: Any,
         tmp_path: Path) -> Any:
    writer = EvidenceWriter("outcome", Redactor({}), root=tmp_path)
    return replay(capability, params, surface, policy,
                  evidence=lambda: writer.ref, sink=writer, allow_draft=True)


def test_a_rejected_form_is_a_business_outcome_even_though_the_wait_times_out(
    live_app: str, surface: Any, policy_config: Any, tmp_path: Path
) -> None:
    """Step 4 waits for the review screen. A rejected deposit never shows it."""
    capability = _subaccount(live_app, real_validation_signal=True)

    result = _run(capability, _params(initial_deposit="-50.00"), surface, policy_config, tmp_path)

    assert isinstance(result, BusinessOutcomeResult), result
    assert result.code == "validation_rejected"
    assert result.detected_at_step == 4
    assert [s.index for s in result.steps] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    ("member_id", "code"),
    [("100003", "member_restricted"), ("999999", "member_not_found")],
)
def test_a_member_outcome_on_the_entry_screen_is_not_mistaken_for_drift(
    member_id: str, code: str, live_app: str, surface: Any, policy_config: Any, tmp_path: Path
) -> None:
    """The fingerprint check used to stop these at pre-flight with surface_unavailable."""
    capability = _subaccount(live_app, real_validation_signal=False)

    result = _run(capability, _params(member_id=member_id), surface, policy_config, tmp_path)

    assert isinstance(result, BusinessOutcomeResult), result
    assert result.code == code
    assert result.detected_at_step == 0


def test_a_real_title_change_still_stops_at_pre_flight(
    live_app: str, surface: Any, policy_config: Any, tmp_path: Path
) -> None:
    """The exemption is for declared outcomes only. Real drift still stops the run."""
    data = json.loads(SUBACCOUNT.read_text())
    data["surface"]["base_url"] = live_app
    data["surface"]["fingerprint"]["title"] = "A Different Vendor Console"
    capability = Capability.model_validate(data)

    result = _run(capability, _params(), surface, policy_config, tmp_path)

    assert result.kind == "failure", result
    assert result.error_class.value == "surface_unavailable"
    assert result.step_index == -1
