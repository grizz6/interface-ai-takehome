"""One failing test per cross field validator on Capability.

These are the rules that involve more than one field, and they keep a capability consistent
with itself. A capability that passes field validation but uses an input nobody declared would
otherwise fail during a replay instead of when it is reviewed.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from conftest import (
    assert_error,
    role_name_bundle,
    text_signal,
    valid_capability,
    valid_inputs,
    valid_outcomes,
    valid_steps,
    wait_for,
)
from src.models import (
    ActionType,
    BusinessOutcomeSpec,
    ExtractionSpec,
    InsertedStep,
    ParamBinding,
    ParamSpec,
    RecoveryAction,
    RecoveryRule,
    RiskClass,
    Step,
    StepPatch,
    ValueType,
    VariantOverride,
)


def test_the_fixture_itself_is_valid() -> None:
    assert valid_capability().capability_id == "open-member-subaccount"


# -- 1. steps non-empty and contiguous from zero --------------------------------
def test_a_capability_needs_at_least_one_step() -> None:
    with pytest.raises(ValidationError) as exc:
        valid_capability(steps=[])
    assert_error(exc, "capability requires at least one step")


def test_step_indices_must_be_contiguous_from_zero() -> None:
    steps = valid_steps()
    steps[3] = steps[3].model_copy(update={"index": 9})
    with pytest.raises(ValidationError) as exc:
        valid_capability(steps=steps)
    assert_error(exc, "step indices must be contiguous starting at 0")


# -- 2. param bindings name declared inputs -------------------------------------
def test_a_step_may_not_bind_an_undeclared_input() -> None:
    steps = valid_steps()
    steps[2] = steps[2].model_copy(update={"value": ParamBinding(param="not_declared")})
    with pytest.raises(ValidationError) as exc:
        valid_capability(steps=steps)
    assert_error(exc, "step 2 binds undeclared input 'not_declared'")


# -- 3. url templates name declared inputs --------------------------------------
def test_a_url_template_may_not_name_an_undeclared_input() -> None:
    steps = valid_steps()
    steps[0] = steps[0].model_copy(update={"url": "/member/{mystery_id}"})
    with pytest.raises(ValidationError) as exc:
        valid_capability(steps=steps)
    assert_error(exc, "step 0 url references undeclared input 'mystery_id'")


def test_an_entry_path_template_may_not_name_an_undeclared_input() -> None:
    capability = valid_capability()
    surface = capability.surface.model_copy(update={"entry_path": "/member/{whoever}"})
    with pytest.raises(ValidationError) as exc:
        valid_capability(surface=surface)
    assert_error(exc, "surface.entry_path references undeclared input 'whoever'")


# -- 4. names and codes are unique ----------------------------------------------
def test_input_names_must_be_unique() -> None:
    inputs = valid_inputs()
    inputs.append(
        ParamSpec(name="nickname", type=ValueType.STRING, description="a second one")
    )
    with pytest.raises(ValidationError) as exc:
        valid_capability(inputs=inputs)
    assert_error(exc, "duplicate input names: nickname")


def test_outcome_codes_must_be_unique() -> None:
    outcomes = valid_outcomes()
    outcomes.append(
        BusinessOutcomeSpec(
            code="member_not_found",
            description="declared twice",
            detect=text_signal("something else"),
        )
    )
    with pytest.raises(ValidationError) as exc:
        valid_capability(known_outcomes=outcomes)
    assert_error(exc, "duplicate outcome codes: member_not_found")


# -- 5. partial outputs name declared outputs -----------------------------------
def test_partial_outputs_must_name_a_declared_output() -> None:
    outcomes = valid_outcomes()
    outcomes[0] = outcomes[0].model_copy(update={"partial_outputs": ["ghost_field"]})
    with pytest.raises(ValidationError) as exc:
        valid_capability(known_outcomes=outcomes)
    assert_error(exc, "lists undeclared partial output 'ghost_field'")


# -- 6. overrides target existing steps -----------------------------------------
def test_a_step_override_must_patch_an_existing_step() -> None:
    override = VariantOverride(
        variant_id="b", description="bad patch", step_overrides={42: StepPatch()}
    )
    with pytest.raises(ValidationError) as exc:
        valid_capability(overrides={"b": override})
    assert_error(exc, "patches nonexistent step index 42")


def test_an_inserted_step_must_attach_to_an_existing_step() -> None:
    override = VariantOverride(
        variant_id="b",
        description="bad insert",
        inserted_steps=[
            InsertedStep(
                after_index=42,
                step=Step(
                    index=0,
                    action=ActionType.CLICK,
                    description="extra",
                    target=role_name_bundle("button", "Acknowledge"),
                    risk=RiskClass.SAFE_REVERSIBLE,
                    wait=wait_for("done"),
                ),
            )
        ],
    )
    with pytest.raises(ValidationError) as exc:
        valid_capability(overrides={"b": override})
    assert_error(exc, "inserts after nonexistent step index 42")


# -- 7. every required input is actually used -----------------------------------
def test_a_required_input_that_nothing_uses_is_a_schema_error() -> None:
    inputs = valid_inputs()
    inputs.append(
        ParamSpec(
            name="branch_code",
            type=ValueType.STRING,
            description="Never referenced by any step.",
        )
    )
    with pytest.raises(ValidationError) as exc:
        valid_capability(inputs=inputs)
    assert_error(exc, "required input 'branch_code' is never referenced")


def test_an_optional_input_may_go_unused() -> None:
    inputs = valid_inputs()
    inputs.append(
        ParamSpec(
            name="branch_code",
            type=ValueType.STRING,
            required=False,
            description="Reserved for a variant that needs it.",
        )
    )
    assert len(valid_capability(inputs=inputs).inputs) == 4


# -- 8. override keys name declared outputs and outcomes ------------------------
def test_an_output_override_must_name_a_declared_output() -> None:
    override = VariantOverride(
        variant_id="b",
        description="bad output override",
        output_overrides={
            "not_an_output": ExtractionSpec(
                locator=role_name_bundle("cell", "x"), source="text", parse="raw"
            )
        },
    )
    with pytest.raises(ValidationError) as exc:
        valid_capability(overrides={"b": override})
    assert_error(exc, "overrides undeclared output 'not_an_output'")


def test_an_outcome_override_must_name_a_declared_outcome() -> None:
    override = VariantOverride(
        variant_id="b",
        description="bad outcome override",
        outcome_overrides={"not_an_outcome": text_signal("whatever")},
    )
    with pytest.raises(ValidationError) as exc:
        valid_capability(overrides={"b": override})
    assert_error(exc, "overrides undeclared outcome 'not_an_outcome'")


# -- field level patterns on the capability itself ------------------------------
def test_capability_id_must_be_a_slug() -> None:
    with pytest.raises(ValidationError) as exc:
        valid_capability(capability_id="Open_Member_SubAccount")
    assert_error(exc, "String should match pattern")


def test_version_must_be_semver() -> None:
    with pytest.raises(ValidationError) as exc:
        valid_capability(version="1.0")
    assert_error(exc, "String should match pattern")


# -- 9. outcome checkpoints name existing steps ---------------------------------
def test_an_outcome_may_not_check_after_a_nonexistent_step() -> None:
    outcomes = valid_outcomes()
    outcomes[0] = outcomes[0].model_copy(update={"check_after_step": 42})
    with pytest.raises(ValidationError) as exc:
        valid_capability(known_outcomes=outcomes)
    assert_error(exc, "outcome 'member_not_found' checks after nonexistent step index 42")


def test_an_outcome_may_check_after_an_existing_step() -> None:
    outcomes = valid_outcomes()
    outcomes[0] = outcomes[0].model_copy(update={"check_after_step": 0})
    assert valid_capability(known_outcomes=outcomes).known_outcomes[0].check_after_step == 0


# -- 10. recovery scopes name existing steps ------------------------------------
def test_a_recovery_may_not_scope_to_a_nonexistent_step() -> None:
    rule = RecoveryRule(
        name="dismiss_maintenance_notice",
        detect=text_signal("scheduled maintenance"),
        action=RecoveryAction.DISMISS,
        action_target=role_name_bundle("link", "Continue"),
        applies_to_steps=[0, 42],
    )
    with pytest.raises(ValidationError) as exc:
        valid_capability(recoveries=[rule])
    assert_error(
        exc, "recovery 'dismiss_maintenance_notice' applies to nonexistent step index 42"
    )


def test_a_recovery_with_no_scope_applies_everywhere() -> None:
    assert valid_capability().recoveries[0].applies_to_steps is None

