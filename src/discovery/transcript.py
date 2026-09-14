"""What a discovery run produced, in a form the recorder can compile into a Capability.

The field that matters most is `ActionRecord.bundle`.

A ref stops meaning anything once the next snapshot is taken. By the end of a run every ref
the model used is useless, so a transcript that only says "clicked e36" cannot be replayed.
That is why `describe(ref)` runs before the action, while the snapshot is still current, and
its result is saved here.

An ActionRecord whose bundle still contains the ref it came from is rejected when it is built.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.models.capability import (
    Assertion,
    OutputSpec,
    ParamSpec,
    SurfaceDescriptor,
)
from src.models.locator import LocatorBundle

STRICT = ConfigDict(extra="forbid", frozen=True)

TARGETED_ACTIONS = {"click", "type", "select"}
"""Actions that act on a control, and therefore must carry a bundle."""


class DiscoveryStop(StrEnum):
    """Why a discovery run ended."""

    GOAL_REACHED = "goal_reached"
    GAVE_UP = "gave_up"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    POLICY_BLOCKED = "policy_blocked"
    NEEDS_HUMAN = "needs_human"
    ERROR = "error"


class EventKind(StrEnum):
    OBSERVATION = "observation"
    MODEL_TEXT = "model_text"
    ACTION = "action"
    TOOL_RESULT = "tool_result"
    POLICY_BLOCK = "policy_block"
    VERIFICATION = "verification"
    STOP = "stop"


def hash_observation(aria_yaml: str) -> str:
    """A short hash of a snapshot, to tell one screen from another.

    Shows whether an action changed anything. The same hash before and after a click means
    the click did nothing.
    """
    return hashlib.sha256(aria_yaml.encode()).hexdigest()[:16]


class ActionRecord(BaseModel):
    """One action, with the locator that was built at the moment it ran."""

    model_config = STRICT

    seq: int
    action_kind: str
    ref_used: str | None = None
    bundle: LocatorBundle | None = None
    literal_value: str | None = None
    tier_resolved: str | None = None
    outcome_ok: bool = True
    note: str | None = None
    obs_hash_before: str | None = None
    obs_hash_after: str | None = None
    duration_ms: int = 0

    @property
    def changed_the_screen(self) -> bool:
        if self.obs_hash_before is None or self.obs_hash_after is None:
            return False
        return self.obs_hash_before != self.obs_hash_after

    @model_validator(mode="after")
    def _targeted_actions_carry_a_bundle(self) -> ActionRecord:
        """Without a bundle the action cannot be replayed, and the recorder would find out too late."""
        if self.action_kind in TARGETED_ACTIONS and self.bundle is None:
            raise ValueError(
                f"action {self.action_kind!r} acts on a control and must carry the bundle "
                "that describe() produced before it ran. Without it there is nothing to "
                "compile into a step"
            )
        return self

    @model_validator(mode="after")
    def _no_ref_survives_into_the_bundle(self) -> ActionRecord:
        """Refuse a bundle that still contains the snapshot ref it was built from."""
        if self.bundle is None or not self.ref_used:
            return self
        serialized = self.bundle.model_dump_json()
        if re.search(rf"\b{re.escape(self.ref_used)}\b", serialized):
            raise ValueError(
                f"ref {self.ref_used!r} leaked into the recorded locator bundle. Refs are "
                "valid only for the snapshot that issued them, so one stored in an artifact "
                "is a locator that can never resolve again"
            )
        return self


class TranscriptEvent(BaseModel):
    """One thing that happened, in order. What a person reads when debugging a run."""

    model_config = STRICT

    seq: int
    at: datetime
    kind: EventKind
    payload: dict[str, Any] = Field(default_factory=dict)


class DeclaredCapability(BaseModel):
    """What the model declared when it called finish.

    Kept separate from the Capability. It is what the model claimed, and the recorder decides
    whether the run backs it up.
    """

    model_config = STRICT

    capability_name: str
    description: str
    checkpoint: Assertion
    inputs: list[ParamSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)


class DiscoveryTranscript(BaseModel):
    """The full record of one run.

    Not a Capability. This keeps everything, dead ends included, while a Capability only keeps
    the flow that worked.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    goal: str
    model: str
    surface: SurfaceDescriptor
    events: list[TranscriptEvent] = Field(default_factory=list)
    actions: list[ActionRecord] = Field(default_factory=list)
    stop_reason: DiscoveryStop = DiscoveryStop.ERROR
    declared: DeclaredCapability | None = None

    @model_validator(mode="after")
    def _a_reached_goal_declares_something(self) -> DiscoveryTranscript:
        if self.stop_reason is DiscoveryStop.GOAL_REACHED and self.declared is None:
            raise ValueError(
                "a run that reached its goal must carry what finish declared; without it "
                "there is no capability to compile"
            )
        return self

    def actions_in_order(self) -> list[ActionRecord]:
        return sorted(self.actions, key=lambda a: a.seq)

    def events_of(self, kind: EventKind) -> list[TranscriptEvent]:
        return [e for e in self.events if e.kind is kind]
