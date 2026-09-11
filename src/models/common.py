"""Enums and small value types shared across the artifact and result models.

Schema only. Nothing here has behaviour, imports a driver, or knows what a browser is.

Every model in this package sets `extra="forbid"`. A capability artifact is meant to be
hand reviewed and hand edited, and a misspelled field that is silently ignored is exactly
the failure mode that makes a recorded flow drift away from what it claims to do.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

STRICT = ConfigDict(extra="forbid", frozen=True)
"""Shared config for models that record facts. Frozen per design rules section 9."""


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
