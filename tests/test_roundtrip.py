"""The valid fixture must survive a JSON round trip without losing anything."""
from __future__ import annotations

import json
from typing import get_args

from pydantic import TypeAdapter

from conftest import valid_capability
from src.models import (
    EXIT_CODES,
    ActionType,
    BusinessOutcomeResult,
    Capability,
    EvidenceRef,
    RunResult,
    StepTrace,
    SuccessResult,
)


def test_capability_round_trips_through_json() -> None:
    original = valid_capability()
    restored = Capability.model_validate_json(original.model_dump_json())
    assert restored == original


def test_round_trip_preserves_the_parts_that_are_easy_to_lose() -> None:
    restored = Capability.model_validate_json(valid_capability().model_dump_json())

    # discriminated unions keep their concrete type
    assert restored.steps[1].target is not None
    assert restored.steps[1].target.primary.strategy == "container_ordinal"
    assert restored.steps[2].target is not None
    assert restored.steps[2].target.primary.strategy == "label_relation"

    # frame path, risk class and the postcondition on the irreversible step
    assert restored.steps[1].target.frame_path == ["maincontent"]
    assert restored.steps[5].risk == "risky_irreversible"
    assert restored.steps[5].postcondition is not None

    # variant override keys survive as ints, not as the strings JSON turns them into
    assert set(restored.overrides["b"].step_overrides) == {5}


def test_capability_is_frozen() -> None:
    capability = valid_capability()
    try:
        capability.name = "something else"  # type: ignore[misc]
    except ValueError:
        return
    raise AssertionError("Capability should be frozen")


def test_sensitive_input_carries_no_value_into_the_serialized_artifact() -> None:
    """design rule 6, asserted on the bytes that would hit disk."""
    payload = json.loads(valid_capability().model_dump_json())
    member_id = next(p for p in payload["inputs"] if p["name"] == "member_id")
    assert member_id["sensitivity"] == "pii"
    assert member_id["example"] is None


def test_exit_codes_are_distinct_and_cover_every_result_kind() -> None:
    assert len(set(EXIT_CODES.values())) == len(EXIT_CODES) == 5
    assert EXIT_CODES["success"] == 0
    assert all(code > 0 for kind, code in EXIT_CODES.items() if kind != "success")


def test_run_result_union_discriminates_on_kind() -> None:
    adapter = TypeAdapter(RunResult)
    evidence = EvidenceRef(run_id="r1", directory="evidence/r1", log_path="evidence/r1/log.jsonl")
    trace = StepTrace(index=0, action=ActionType.NAVIGATE, description="go", duration_ms=12)

    success = SuccessResult(outputs={"a": 1}, steps=[trace], evidence=evidence, duration_ms=99)
    outcome = BusinessOutcomeResult(
        code="member_not_found", message="no match", detected_at_step=0, evidence=evidence
    )
    for original in (success, outcome):
        restored = adapter.validate_json(adapter.dump_json(original))
        assert type(restored) is type(original)
        assert restored == original


def test_exit_codes_cover_every_kind_in_the_run_result_union() -> None:
    """Derived from the union itself, so adding a sixth result kind fails this test."""
    members = get_args(get_args(RunResult)[0])
    kinds = {member.model_fields["kind"].default for member in members}
    assert kinds == set(EXIT_CODES), "EXIT_CODES and the RunResult union have diverged"
    assert len(set(EXIT_CODES.values())) == len(EXIT_CODES), "exit codes must be distinct"

