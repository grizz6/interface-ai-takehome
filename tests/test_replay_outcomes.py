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

SUBACCOUNT = Path("capabilities/open-member-subaccount-1.1.0.json")


def _subaccount(live_app: str) -> Capability:
    data = json.loads(SUBACCOUNT.read_text())
    data["surface"]["base_url"] = live_app
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
    capability = _subaccount(live_app)

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
    capability = _subaccount(live_app)

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


def test_the_validation_signal_matches_every_error_the_form_can_show() -> None:
    """1.0.0 looked for "Correct the highlighted fields", which the app never shows.

    Checked against the app's own message table rather than a copy of it, so a reworded message
    fails here instead of silently turning a rejected form back into a timeout.
    """
    import re
    import sys

    sys.path.insert(0, "target_app")
    from seed import VARIANTS

    detect = next(
        o.detect for o in Capability.model_validate_json(SUBACCOUNT.read_text()).known_outcomes
        if o.code == "validation_rejected"
    )
    assert detect.pattern is not None
    form_errors = {k: v for k, v in VARIANTS["a"]["errors"].items() if k != "member_id_required"}
    for key, message in form_errors.items():
        assert re.search(detect.pattern, message, re.IGNORECASE), f"{key}: {message!r}"
