"""The transcript, and the locator bundle the recorder cannot work without."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from conftest import valid_capability
from src.discovery.transcript import (
    ActionRecord,
    DeclaredCapability,
    DiscoveryStop,
    DiscoveryTranscript,
    EventKind,
    TranscriptEvent,
    hash_observation,
)
from src.models.capability import Assertion, Signal, SignalKind
from src.models.locator import (
    ContainerOrdinalLocator,
    ContainerRef,
    LocatorBundle,
    RoleNameLocator,
)


def bundle(name: str = "Search") -> LocatorBundle:
    return LocatorBundle(
        primary=RoleNameLocator(role="button", name=name), recorded_accessible_name=name
    )


# -- refs never reach a saved bundle --------------------------------------------
def test_a_ref_may_not_survive_into_a_recorded_bundle() -> None:
    """The named test: no ActionRecord.bundle can contain a ref string."""
    with pytest.raises(ValidationError) as exc:
        ActionRecord(
            seq=0,
            action_kind="click",
            ref_used="f1e36",
            bundle=LocatorBundle(primary=RoleNameLocator(role="button", name="f1e36")),
        )
    assert "leaked into the recorded locator bundle" in str(exc.value)


@pytest.mark.parametrize(
    "leaky",
    [
        LocatorBundle(primary=RoleNameLocator(role="button", name="e17")),
        LocatorBundle(
            primary=ContainerOrdinalLocator(
                container=ContainerRef(heading_text="e17", role="table"),
                role="button",
                ordinal=0,
            )
        ),
        LocatorBundle(
            primary=RoleNameLocator(role="button", name="Select"), notes="taken from e17"
        ),
    ],
)
def test_the_ref_check_looks_at_the_whole_bundle_not_just_the_primary_name(
    leaky: LocatorBundle,
) -> None:
    with pytest.raises(ValidationError):
        ActionRecord(seq=0, action_kind="click", ref_used="e17", bundle=leaky)


def test_a_clean_bundle_is_accepted_and_similar_looking_text_is_not_a_false_positive() -> None:
    record = ActionRecord(
        seq=0,
        action_kind="click",
        ref_used="e17",
        bundle=LocatorBundle(
            primary=RoleNameLocator(role="button", name="Save e170 changes")
        ),
    )
    assert record.bundle is not None


# -- actions on a control must have a bundle ------------------------------------
@pytest.mark.parametrize("kind", ["click", "type", "select"])
def test_an_action_on_a_control_must_carry_its_bundle(kind: str) -> None:
    with pytest.raises(ValidationError) as exc:
        ActionRecord(seq=0, action_kind=kind, ref_used="e1")
    assert "must carry the bundle that describe() produced" in str(exc.value)


@pytest.mark.parametrize("kind", ["navigate", "press", "look"])
def test_an_action_with_no_target_needs_no_bundle(kind: str) -> None:
    assert ActionRecord(seq=0, action_kind=kind).bundle is None


# -- observation hashing ---------------------------------------------------------
def test_observation_hashes_distinguish_screen_states() -> None:
    assert hash_observation("- table") == hash_observation("- table")
    assert hash_observation("- table") != hash_observation("- form")


def test_a_record_reports_whether_the_action_changed_anything() -> None:
    unchanged = ActionRecord(
        seq=0, action_kind="click", bundle=bundle(), obs_hash_before="a", obs_hash_after="a"
    )
    changed = ActionRecord(
        seq=1, action_kind="click", bundle=bundle(), obs_hash_before="a", obs_hash_after="b"
    )
    assert not unchanged.changed_the_screen
    assert changed.changed_the_screen


# -- the transcript as a whole ---------------------------------------------------
def transcript(**overrides: object) -> DiscoveryTranscript:
    data: dict[str, object] = {
        "run_id": "run-1",
        "goal": "open a sub-account for member 100001",
        "model": "gemini-3-flash-preview",
        "surface": valid_capability().surface,
        "stop_reason": DiscoveryStop.GAVE_UP,
    }
    data.update(overrides)
    return DiscoveryTranscript(**data)  # type: ignore[arg-type]


def test_a_run_that_reached_its_goal_must_declare_a_capability() -> None:
    with pytest.raises(ValidationError) as exc:
        transcript(stop_reason=DiscoveryStop.GOAL_REACHED)
    assert "no capability to compile" in str(exc.value)


def test_a_run_that_gave_up_need_not_declare_anything() -> None:
    assert transcript().declared is None


def test_a_declared_capability_round_trips() -> None:
    declared = DeclaredCapability(
        capability_name="open-member-subaccount",
        description="Opens a sub-account.",
        checkpoint=Assertion(
            signal=Signal(kind=SignalKind.TEXT_PRESENT, text="Sub-Account Opened"),
            description="the confirmation screen",
        ),
    )
    run = transcript(stop_reason=DiscoveryStop.GOAL_REACHED, declared=declared)
    restored = DiscoveryTranscript.model_validate_json(run.model_dump_json())
    assert restored.declared == declared


def test_actions_and_events_come_back_in_order() -> None:
    run = transcript(
        actions=[
            ActionRecord(seq=2, action_kind="click", bundle=bundle()),
            ActionRecord(seq=0, action_kind="navigate"),
            ActionRecord(seq=1, action_kind="type", bundle=bundle()),
        ],
        events=[
            TranscriptEvent(seq=0, at=datetime(2026, 9, 11, tzinfo=UTC), kind=EventKind.OBSERVATION),
            TranscriptEvent(seq=1, at=datetime(2026, 9, 11, tzinfo=UTC), kind=EventKind.ACTION),
            TranscriptEvent(seq=2, at=datetime(2026, 9, 11, tzinfo=UTC), kind=EventKind.POLICY_BLOCK),
        ],
    )
    assert [a.seq for a in run.actions_in_order()] == [0, 1, 2]
    assert len(run.events_of(EventKind.POLICY_BLOCK)) == 1
