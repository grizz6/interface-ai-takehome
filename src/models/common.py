"""Enums and small value types used by the capability and result models.

No behaviour here, and nothing that knows about a browser.

Every model in this package sets `extra="forbid"`. People read and edit capability files by
hand, and a misspelled field that gets silently ignored is how a saved flow ends up doing
something other than what it says.
"""
from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationInfo

STRICT = ConfigDict(extra="forbid", frozen=True)
"""Shared config for models that record facts, which should not change after the fact."""


def _reject_uncompilable_regex(value: str | None, info: ValidationInfo) -> str | None:
    """Compile a user-supplied regex when the model is validated, not during replay.

    A pattern that only fails when replay first uses it has already been through review and
    approval by then. See DECISIONS.md 0004.
    """
    if value is None:
        return value
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(
            f"{info.field_name} is not a valid regular expression, so the artifact was "
            f"rejected at record time rather than at replay time: {value!r} does not "
            f"compile ({exc})"
        ) from exc
    return value


RegexPattern = Annotated[str | None, AfterValidator(_reject_uncompilable_regex)]
"""A user-supplied regular expression, compiled during validation. None is allowed."""


class SurfaceKind(StrEnum):
    WEB = "web"
    LEGACY_WEB = "legacy_web"
    DESKTOP = "desktop"


class ActionType(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    PRESS = "press"
    WAIT_FOR = "wait_for"


class RiskClass(StrEnum):
    SAFE_REVERSIBLE = "safe_reversible"
    RISKY_IRREVERSIBLE = "risky_irreversible"


class Sensitivity(StrEnum):
    NONE = "none"
    PII = "pii"
    SECRET = "secret"


class ValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    CURRENCY = "currency"
    BOOLEAN = "boolean"
    DATE = "date"


class ApprovalStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class SignalKind(StrEnum):
    TEXT_PRESENT = "text_present"
    TEXT_ABSENT = "text_absent"
    URL_MATCHES = "url_matches"
    ELEMENT_PRESENT = "element_present"
    ELEMENT_ABSENT = "element_absent"
    ARIA_MATCHES = "aria_matches"


class RecoveryAction(StrEnum):
    DISMISS = "dismiss"
    WAIT_RETRY = "wait_retry"
    RELOAD = "reload"
    REAUTHENTICATE = "reauthenticate"


class StuckReason(StrEnum):
    LOCATOR_AMBIGUOUS = "locator_ambiguous"
    LOCATOR_UNRESOLVED = "locator_unresolved"
    UNKNOWN_STATE = "unknown_state"
    RISKY_ACTION_REQUIRES_APPROVAL = "risky_action_requires_approval"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    STEP_TIMEOUT = "step_timeout"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"


class Holder(StrEnum):
    """Who is driving the browser right now."""

    AUTOMATION = "automation"
    HUMAN = "human"
    NONE = "none"


class LeaseState(StrEnum):
    """Where a session is in the handoff. See src/escalation/lease.py."""

    RUNNING = "running"
    PAUSED = "paused"
    HUMAN_CONTROL = "human_control"
    RESUMING = "resuming"
    CLOSED = "closed"


class ResolutionOutcome(StrEnum):
    """What the operator did, which decides what the run does with the step."""

    APPROVED = "approved"
    COMPLETED_MANUALLY = "completed_manually"
    RETRY_STEP = "retry_step"
    ABORTED = "aborted"


class FailureClass(StrEnum):
    LOCATOR_UNRESOLVED = "locator_unresolved"
    CHECKPOINT_FAILED = "checkpoint_failed"
    TIMEOUT = "timeout"
    APP_ERROR = "app_error"
    EXTRACTION_FAILED = "extraction_failed"
    SURFACE_UNAVAILABLE = "surface_unavailable"
    INTERNAL = "internal"


class Rect(BaseModel):
    """A recorded bounding box. A hint for a human looking at evidence, never a locator."""

    model_config = STRICT

    x: int
    y: int
    width: int
    height: int
