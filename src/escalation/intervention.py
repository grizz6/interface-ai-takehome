"""What a person is given when the run stops, and what they give back.

Section 3.6 of the brief asks for enough context to act on: which capability, which step, what
the screen looks like, why it stopped, and what inputs it had. Each is a field here rather than
text in a log, so the operator page can show them and tests can check them.

Nothing here ever sees a parameter value. `params_redacted` is built from the capability's
declared inputs, not the values passed in, so there is no way for a value to get in.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from src.models.capability import ParamDescriptor
from src.models.common import ResolutionOutcome, RiskClass, StuckReason

STRICT: Final[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CapturedAction(BaseModel):
    """One thing the person did: which element, never what they typed.

    In a banking back office, a typed value is an account number, an amount or a name. That is
    just as sensitive when a person types it, so only the field is recorded.
    """

    model_config = STRICT

    kind: str
    tag: str | None = None
    text: str | None = None
    element_id: str | None = None
    field: str | None = None
    url: str | None = None
    at: str | None = None


class InterventionResolution(BaseModel):
    """What the operator decided, written back onto the request."""

    model_config = STRICT

    outcome: ResolutionOutcome
    operator_note: str = ""
    human_actions: list[CapturedAction] = Field(default_factory=list)
    url_after: str | None = None
    aria_after: str | None = None
    resolved_at: datetime


class InterventionRequest(BaseModel):
    """The handoff request. One JSON file per request, in interventions/."""

    model_config = STRICT

    id: str
    session_id: str
    created_at: datetime
    deadline_at: datetime | None = None

    capability_id: str
    capability_version: str
    goal_text: str

    step_index: int
    step_description: str
    risk: RiskClass

    reason: StuckReason
    why: str

    url: str
    aria_snapshot: str
    screenshot_path: str | None = None

    params_redacted: list[ParamDescriptor] = Field(default_factory=list)

    resolution: InterventionResolution | None = None


def refuse_unsafe_outcome(risk: RiskClass, outcome: ResolutionOutcome) -> str | None:
    """Refuse retry_step on an irreversible step. Used by the operator page and the engine.

    A person who had the browser may already have done the step, and doing an irreversible
    action again could open the account twice. The operator page hides the option, and this
    refuses it anyway in case the answer came from somewhere else. See DECISIONS.md 0032.
    """
    if risk is RiskClass.RISKY_IRREVERSIBLE and outcome is ResolutionOutcome.RETRY_STEP:
        return (
            "retry_step is refused on a risky_irreversible step. A human has had control of "
            "this session, so the action may already have been performed, and repeating it "
            "cannot be undone. Choose approved, completed_manually, or aborted."
        )
    return None


class InterventionStore:
    """The interventions/ folder. One file per request, rewritten when it is resolved."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, intervention_id: str) -> Path:
        return self.root / f"{intervention_id}.json"

    def write(self, request: InterventionRequest) -> Path:
        """Written atomically, like the lease, since the operator page may be reading it."""
        path = self.path_for(request.id)
        handle = tempfile.NamedTemporaryFile(
            mode="w", dir=self.root, prefix=".int-", suffix=".tmp", delete=False
        )
        try:
            with handle as out:
                out.write(request.model_dump_json(indent=2))
                out.flush()
                os.fsync(out.fileno())
            os.replace(handle.name, path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
        return path

    def read(self, intervention_id: str) -> InterventionRequest:
        return InterventionRequest.model_validate_json(
            self.path_for(intervention_id).read_text()
        )

    def resolve(
        self, intervention_id: str, resolution: InterventionResolution
    ) -> InterventionRequest:
        """Rewrite the file with the resolution added.

        Rewritten, not appended, so the file stays plain JSON that json.load can read.
        """
        request = self.read(intervention_id)
        updated = request.model_copy(update={"resolution": resolution})
        self.write(updated)
        return updated

    def open_requests(self) -> list[InterventionRequest]:
        """Everything not yet resolved, newest first."""
        found: list[InterventionRequest] = []
        for path in self.root.glob("*.json"):
            try:
                request = InterventionRequest.model_validate_json(path.read_text())
            except (ValueError, OSError):
                # A broken file should not take the operator page down. Skip it and leave it
                # on disk for someone to look at.
                continue
            if request.resolution is None:
                found.append(request)
        return sorted(found, key=lambda r: r.created_at, reverse=True)

    def all_requests(self) -> list[InterventionRequest]:
        found: list[InterventionRequest] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                found.append(InterventionRequest.model_validate_json(path.read_text()))
            except (ValueError, OSError):
                continue
        return sorted(found, key=lambda r: r.created_at, reverse=True)


def new_intervention_id(run_id: str, step_index: int, reason: StuckReason) -> str:
    """An id you can read in a file listing: which run, which step, what went wrong."""
    stamp = datetime.now(UTC).strftime("%H%M%S")
    return f"{run_id}-s{step_index}-{reason.value}-{stamp}"


def load_actions(raw: object) -> list[CapturedAction]:
    """Parse what the page recorder sent back, carefully.

    This data comes from a page a person has been using, so it cannot be trusted. Anything that
    does not parse is dropped instead of failing the resume, because losing one audit line is
    better than losing the session.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    actions: list[CapturedAction] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            actions.append(CapturedAction.model_validate(item))
        except ValueError:
            continue
    return actions
