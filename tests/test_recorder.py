"""The recorder: compiling a transcript into a Capability.

Every test here is offline. The transcripts are built in code or read from evidence on disk,
and nothing touches a browser or a model.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import valid_capability
from src.discovery.transcript import (
    ActionRecord,
    DeclaredCapability,
    DiscoveryStop,
    DiscoveryTranscript,
    EventKind,
    TranscriptEvent,
)
from src.models.capability import Assertion, ParamSpec, Signal
from src.models.common import SignalKind, ValueType
from src.models.locator import LocatorBundle, RoleNameLocator
from src.policy.loading import load_policy_config
from src.recorder.compile import CompileFailure, compile_capability

POLICY = load_policy_config("config/policy.json")
# The curated copy, which is tracked. The original run directory is gitignored, so
# pointing at it meant these two tests silently skipped in every clone but mine.
REAL = Path("evidence/curated/01-discovery-real/transcript.json")


def bundle(name: str) -> LocatorBundle:
    return LocatorBundle(
        primary=RoleNameLocator(role="button", name=name), recorded_accessible_name=name
    )


def declared(**over: object) -> DeclaredCapability:
    data: dict[str, object] = {
        "capability_name": "lookup-member-savings-balance",
        "description": "Look up a member and read the savings balance.",
        "checkpoint": Assertion(
            signal=Signal(kind=SignalKind.TEXT_PRESENT, text="Member Detail"),
            description="the detail screen is showing",
        ),
        "inputs": [
            ParamSpec(
                name="member_id", type=ValueType.STRING, description="Member to look up."
            )
        ],
        "outputs": [],
    }
    data.update(over)
    return DeclaredCapability(**data)  # type: ignore[arg-type]


def transcript(**over: object) -> DiscoveryTranscript:
    data: dict[str, object] = {
        "run_id": "run-1",
        "goal": "look up member 100001",
        "model": "scripted",
        "surface": valid_capability().surface.model_copy(update={"entry_path": "/search"}),
        "stop_reason": DiscoveryStop.GOAL_REACHED,
        "declared": declared(),
        "events": [
            TranscriptEvent(
                seq=1,
                at=datetime(2026, 9, 12, tzinfo=UTC),
                kind=EventKind.OBSERVATION,
                payload={"url": "http://localhost:8080/search"},
            ),
            TranscriptEvent(
                seq=2,
                at=datetime(2026, 9, 12, tzinfo=UTC),
                kind=EventKind.OBSERVATION,
                payload={"url": "http://localhost:8080/member/100001"},
            ),
        ],
        "actions": [
            ActionRecord(seq=0, action_kind="navigate", duration_ms=10),
            ActionRecord(
                seq=1, action_kind="type", ref_used="e1", bundle=bundle("Member ID"),
                literal_value="100001", tier_resolved="role_name", duration_ms=5,
            ),
            ActionRecord(
                seq=2, action_kind="click", ref_used="e2", bundle=bundle("Search"),
                tier_resolved="role_name", duration_ms=20,
            ),
        ],
    }
    data.update(over)
    return DiscoveryTranscript(**data)  # type: ignore[arg-type]


# -- 1. preconditions -------------------------------------------------------------
@pytest.mark.parametrize(
    "stop",
    [DiscoveryStop.MAX_STEPS, DiscoveryStop.GAVE_UP, DiscoveryStop.POLICY_BLOCKED,
     DiscoveryStop.NEEDS_HUMAN, DiscoveryStop.TIMEOUT, DiscoveryStop.ERROR],
)
def test_a_run_that_did_not_verify_a_finish_refuses_to_compile(stop: DiscoveryStop) -> None:
    outcome = compile_capability(transcript(stop_reason=stop, declared=None), POLICY)
    assert outcome.capability is None
    assert outcome.error is not None
    assert outcome.error.reason is CompileFailure.NOT_A_VERIFIED_FINISH


def test_a_verified_finish_compiles_to_a_capability_that_validates() -> None:
    outcome = compile_capability(transcript(), POLICY)
    assert outcome.error is None, outcome.error
    capability = outcome.capability
    assert capability is not None
    assert capability.schema_version == "1.0"
    assert capability.version == "1.0.0"
    assert capability.status == "draft", "nothing that has replayed zero times is approved"


# -- 3. no ref survives -----------------------------------------------------------
def test_the_compiled_capability_contains_no_ref_string() -> None:
    out = compile_capability(transcript(), POLICY)
    assert out.capability is not None
    blob = out.capability.model_dump_json()
    for ref in ("e1", "e2"):
        assert not re.search(rf"\b{ref}\b", blob), f"ref {ref} survived into the artifact"


# -- 4. parameterization ----------------------------------------------------------
def test_a_declared_input_becomes_a_param_binding_and_the_literal_disappears() -> None:
    out = compile_capability(transcript(), POLICY)
    assert out.capability is not None
    typed = next(s for s in out.capability.steps if s.action == "type")
    assert typed.value is not None
    assert typed.value.source == "param"
    assert typed.value.param == "member_id"
    assert "100001" not in out.capability.model_dump_json()


def test_an_input_matching_no_recorded_literal_is_a_compile_error() -> None:
    """No step typed anything at all, so the declared input consumes nothing."""
    spec = ParamSpec(name="branch_code", type=ValueType.STRING, description="Never typed.")
    actions = [
        ActionRecord(seq=0, action_kind="navigate", duration_ms=10),
        ActionRecord(seq=1, action_kind="click", ref_used="e2", bundle=bundle("Search"),
                     tier_resolved="role_name", duration_ms=20),
    ]
    outcome = compile_capability(
        transcript(declared=declared(inputs=[spec]), actions=actions), POLICY
    )
    assert outcome.capability is None
    assert outcome.error is not None
    assert outcome.error.reason is CompileFailure.INPUT_MATCHES_NO_LITERAL
    assert "branch_code" in outcome.error.detail


def test_a_navigate_url_containing_the_value_becomes_a_template() -> None:
    events = [
        TranscriptEvent(
            seq=1, at=datetime(2026, 9, 12, tzinfo=UTC), kind=EventKind.OBSERVATION,
            payload={"url": "http://localhost:8080/member/100001"},
        )
    ]
    out = compile_capability(transcript(events=events), POLICY)
    assert out.capability is not None
    navigate = next(s for s in out.capability.steps if s.action == "navigate")
    # A path, not a URL: the host lives on the surface descriptor so a caller can repoint it.
    assert navigate.url == "/member/{member_id}"


def test_an_unbound_literal_is_reported_without_printing_its_value() -> None:
    actions = list(transcript().actions) + [
        ActionRecord(
            seq=3, action_kind="type", ref_used="e3", bundle=bundle("Nickname"),
            literal_value="Vacation", tier_resolved="role_name", duration_ms=4,
        )
    ]
    out = compile_capability(transcript(actions=actions), POLICY)
    assert out.capability is not None
    assert out.report.unbound == ["a value typed into 'Nickname'"]
    assert "Vacation" not in "".join(out.report.lines())


# -- 5. risk ----------------------------------------------------------------------
def test_a_risky_control_compiles_as_irreversible_and_carries_a_postcondition() -> None:
    actions = list(transcript().actions) + [
        ActionRecord(
            seq=3, action_kind="click", ref_used="e9", bundle=bundle("Confirm"),
            tier_resolved="role_name", duration_ms=30,
        )
    ]
    out = compile_capability(transcript(actions=actions), POLICY)
    assert out.capability is not None, out.error
    confirm = out.capability.steps[-1]
    assert confirm.risk == "risky_irreversible"
    assert confirm.postcondition is not None
    assert confirm.postcondition.signal.kind is SignalKind.URL_MATCHES


def test_ordinary_controls_are_safe_reversible() -> None:
    out = compile_capability(transcript(), POLICY)
    assert out.capability is not None
    assert all(s.risk == "safe_reversible" for s in out.capability.steps)


# -- 2. step selection ------------------------------------------------------------
def test_failed_actions_are_dropped_and_indices_stay_contiguous() -> None:
    actions = [
        ActionRecord(seq=0, action_kind="navigate", duration_ms=10),
        ActionRecord(
            seq=1, action_kind="click", ref_used="e5", bundle=bundle("Broken"),
            outcome_ok=False, duration_ms=9,
        ),
        ActionRecord(
            seq=2, action_kind="type", ref_used="e1", bundle=bundle("Member ID"),
            literal_value="100001", duration_ms=5,
        ),
    ]
    out = compile_capability(transcript(actions=actions), POLICY)
    assert out.capability is not None, out.error
    assert [s.index for s in out.capability.steps] == [0, 1]
    assert not any("Broken" in (s.target.recorded_accessible_name or "")
                   for s in out.capability.steps if s.target)
    assert any("did not succeed" in d for d in out.report.dropped)


def test_an_action_that_changed_nothing_and_was_retried_is_dropped() -> None:
    actions = [
        ActionRecord(seq=0, action_kind="navigate", duration_ms=10),
        ActionRecord(
            seq=1, action_kind="click", ref_used="e2", bundle=bundle("Search"),
            obs_hash_before="same", obs_hash_after="same", duration_ms=9,
        ),
        ActionRecord(
            seq=2, action_kind="click", ref_used="e2", bundle=bundle("Search"),
            obs_hash_before="same", obs_hash_after="moved", duration_ms=9,
        ),
        ActionRecord(
            seq=3, action_kind="type", ref_used="e1", bundle=bundle("Member ID"),
            literal_value="100001", duration_ms=5,
        ),
    ]
    out = compile_capability(transcript(actions=actions), POLICY)
    assert out.capability is not None, out.error
    assert len(out.capability.steps) == 3
    assert any("changed nothing and was retried" in d for d in out.report.dropped)
    assert out.report.raw_actions == 4 and out.report.kept == 3


# -- 7, 8, 9 ----------------------------------------------------------------------
def test_known_outcomes_are_left_empty_and_the_absence_is_reported() -> None:
    out = compile_capability(transcript(), POLICY)
    assert out.capability is not None
    assert out.capability.known_outcomes == []
    assert any("fabrication" in n for n in out.report.notes)


def test_provenance_records_raw_versus_compiled_counts() -> None:
    out = compile_capability(transcript(), POLICY)
    assert out.capability is not None
    assert out.capability.provenance.raw_step_count == 3
    assert len(out.capability.steps) == 3


def test_a_compiled_capability_round_trips() -> None:
    from src.models.capability import Capability

    out = compile_capability(transcript(), POLICY)
    assert out.capability is not None
    restored = Capability.model_validate_json(out.capability.model_dump_json())
    assert restored == out.capability


# -- against the real recorded run ------------------------------------------------
@pytest.mark.skipif(not REAL.exists(), reason="the real transcript is not on disk")
def test_the_real_transcript_compiles() -> None:
    real = DiscoveryTranscript.model_validate_json(REAL.read_text())
    out = compile_capability(real, POLICY)
    assert out.error is None, out.error
    capability = out.capability
    assert capability is not None
    assert capability.capability_id == "lookup-member-savings-balance"
    assert [s.index for s in capability.steps] == [0, 1, 2, 3]
    assert capability.outputs[0].name == "savings_balance"


@pytest.mark.skipif(not REAL.exists(), reason="the real transcript is not on disk")
def test_the_real_compiled_capability_leaks_neither_ref_nor_member_id() -> None:
    raw = json.loads(REAL.read_text())
    refs = {a["ref_used"] for a in raw["actions"] if a.get("ref_used")}
    real = DiscoveryTranscript.model_validate_json(REAL.read_text())
    out = compile_capability(real, POLICY)
    assert out.capability is not None
    blob = out.capability.model_dump_json()
    for ref in refs:
        assert not re.search(rf"\b{re.escape(ref)}\b", blob), ref
    assert "100001" not in blob, "the pii value survived into the artifact"


def test_a_navigate_step_never_carries_the_recorded_host() -> None:
    """An absolute URL pins the artifact to the machine that recorded it.

    The same application at another host, port or tenant subdomain is exactly the case the
    brief calls heterogeneity, and a baked in host makes it unreachable without editing the
    artifact by hand.
    """
    events = [
        TranscriptEvent(
            seq=1, at=datetime(2026, 9, 12, tzinfo=UTC), kind=EventKind.OBSERVATION,
            payload={"url": "http://localhost:8080/"},
        )
    ]
    out = compile_capability(transcript(events=events), POLICY)
    assert out.capability is not None
    for step in out.capability.steps:
        assert step.url is None or step.url.startswith("/"), step.url
