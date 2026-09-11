"""Step validators. The shape of a step has to match the action it claims to perform."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from conftest import assert_error, role_name_bundle, text_signal, wait_for
from src.models import (
    ActionType,
    Assertion,
    ParamBinding,
    RiskClass,
    Step,
    WaitSpec,
)

FIXED = WaitSpec(condition="fixed", timeout_ms=100)


def test_navigate_requires_a_url() -> None:
    with pytest.raises(ValidationError) as exc:
        Step(
            index=0, action=ActionType.NAVIGATE, description="go",
            risk=RiskClass.SAFE_REVERSIBLE, wait=FIXED,
        )
    assert_error(exc, "navigate step requires url")


def test_navigate_may_not_carry_a_target() -> None:
    with pytest.raises(ValidationError) as exc:
        Step(
            index=0, action=ActionType.NAVIGATE, description="go", url="/x",
            target=role_name_bundle("button", "Go"),
            risk=RiskClass.SAFE_REVERSIBLE, wait=FIXED,
        )
    assert_error(exc, "navigate step must not carry a target")


def test_click_requires_a_target() -> None:
    with pytest.raises(ValidationError) as exc:
        Step(
            index=0, action=ActionType.CLICK, description="click",
            risk=RiskClass.SAFE_REVERSIBLE, wait=FIXED,
        )
    assert_error(exc, "click step requires target")


def test_type_requires_a_target() -> None:
    with pytest.raises(ValidationError) as exc:
        Step(
            index=0, action=ActionType.TYPE, description="type",
            value=ParamBinding(param="x"),
            risk=RiskClass.SAFE_REVERSIBLE, wait=FIXED,
        )
    assert_error(exc, "type step requires target")


def test_select_requires_a_value() -> None:
    with pytest.raises(ValidationError) as exc:
        Step(
            index=0, action=ActionType.SELECT, description="select",
            target=role_name_bundle("combobox", "Account Type"),
            risk=RiskClass.SAFE_REVERSIBLE, wait=FIXED,
        )
    assert_error(exc, "select step requires value")


def test_an_irreversible_step_must_prove_what_it_did() -> None:
    with pytest.raises(ValidationError) as exc:
        Step(
            index=0, action=ActionType.CLICK, description="confirm",
            target=role_name_bundle("button", "Confirm"),
            risk=RiskClass.RISKY_IRREVERSIBLE, wait=FIXED,
        )
    assert_error(exc, "risky_irreversible step requires a postcondition")


def test_an_irreversible_step_with_a_postcondition_is_accepted() -> None:
    step = Step(
        index=0, action=ActionType.CLICK, description="confirm",
        target=role_name_bundle("button", "Confirm"),
        risk=RiskClass.RISKY_IRREVERSIBLE,
        wait=wait_for("Sub-Account Opened"),
        postcondition=Assertion(
            signal=text_signal("Sub-Account Opened"), description="proves it happened"
        ),
    )
    assert step.postcondition is not None
