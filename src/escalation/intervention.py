"""What a human is handed when automation stops, and what they hand back.

Section 3.6 asks for enough context to act on. That is five things: which capability, which
step, what state the surface is in, why it stopped, and what it was working with. All five are
fields here rather than prose in a log, so the operator console can render them and a test can
assert they are present.

Nothing in this module ever sees a parameter value. `params_redacted` is built from the
capability's declared inputs, not from the params dict, so invariant 6 holds structurally: the
code path that would leak a value does not exist.
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
    """One thing the human did, recorded by identity and never by content.

    A typed value in a back office banking console is an account number, a dollar amount or a
    member's name. Invariant 6 does not stop applying because a person typed it rather than a
    model, so the recorder stores which field changed and never what went into it.
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
    """The handoff packet. One JSON file per intervention, under interventions/."""

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
    """The safety rule, in one function so both the console and the engine call the same code.

    A human who has been inside the session may already have performed the step. Re-performing
    an irreversible action opens the account twice, and no note in a text box makes that
    recoverable. The console hides the option; this refuses it even when the option is not
    what produced the request. See DECISIONS.md 0032.
    """
    if risk is RiskClass.RISKY_IRREVERSIBLE and outcome is ResolutionOutcome.RETRY_STEP:
        return (
            "retry_step is refused on a risky_irreversible step. A human has had control of "
            "this session, so the action may already have been performed, and repeating it "
            "cannot be undone. Choose approved, completed_manually, or aborted."
        )
    return None


class InterventionStore:
    """The interventions/ directory. One file per request, rewritten when resolved."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, intervention_id: str) -> Path:
        return self.root / f"{intervention_id}.json"

    def write(self, request: InterventionRequest) -> Path:
        """Atomic, for the same reason the lease is: the console may be reading it."""
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
        """Rewrite the file with the resolution attached.

        Rewritten rather than appended as a second JSON document, so the file stays something
        json.load can read. An intervention record that needs a custom parser is a record
        nobody will look at during an incident.
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
                # A half written or hand mangled file must not take the console down. It is
                # skipped and stays on disk for a human to look at.
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
    """Readable in a directory listing: which run, which step, what went wrong."""
    stamp = datetime.now(UTC).strftime("%H%M%S")
    return f"{run_id}-s{step_index}-{reason.value}-{stamp}"


def load_actions(raw: object) -> list[CapturedAction]:
    """Parse what came back from the injected page recorder, defensively.

    This is the one place untrusted shaped data enters the model layer: the array is read out
    of a page that a human has been driving. Anything that does not parse is dropped rather
    than failing the resume, because losing a line of the audit trail is better than losing
    the session.
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
