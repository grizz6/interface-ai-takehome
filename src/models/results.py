"""The result contract. Every run returns exactly one of five kinds.

The separation is the point, and it is design rules section 7. A business outcome is an answer
the caller asked for, a failure is a defect, and a policy block is a refusal. Collapsing any
of them into the others is the design mistake this contract exists to prevent.

Recoverable conditions are deliberately absent from this union. They are not outcomes, they
are things that happened on the way to one, so they ride on a Success as `recoveries_applied`.
"""
from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field

from src.models.common import STRICT, ActionType, FailureClass, StuckReason


class StepTrace(BaseModel):
    """What actually happened on one step, including which locator tier resolved it.

    `locator_strategy_used` is telemetry, not decoration. A flow quietly degrading from
    role_name to css_fallback over successive runs is the early warning that a surface has
    drifted, and it is invisible unless it is recorded per step.
    """

    model_config = STRICT

    index: int
    action: ActionType
    description: str
    locator_strategy_used: str | None = None
    attempt_count: int = 1
    duration_ms: int
    recovered_by: str | None = None


class EvidenceRef(BaseModel):
    """Where the evidence for this run landed. Paths only, never content."""

    model_config = STRICT

    run_id: str
    directory: str
    log_path: str
    screenshot_paths: list[str] = Field(default_factory=list)


class SuccessResult(BaseModel):
    """The goal was reached and the checkpoint held."""

    model_config = STRICT

    kind: Literal["success"] = "success"
    outputs: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepTrace] = Field(default_factory=list)
    recoveries_applied: list[str] = Field(default_factory=list)
    evidence: EvidenceRef
    duration_ms: int


class BusinessOutcomeResult(BaseModel):
    """A declared, legitimate answer. Not a failure, and never raised."""

    model_config = STRICT

    kind: Literal["business_outcome"] = "business_outcome"
    code: str
    message: str
    detected_at_step: int
    partial_outputs: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepTrace] = Field(default_factory=list)
    evidence: EvidenceRef


class NeedsHumanResult(BaseModel):
    """The run stopped and asked for a person, rather than guessing."""

    model_config = STRICT

    kind: Literal["needs_human"] = "needs_human"
    intervention_id: str
    reason: StuckReason
    step_index: int
    steps: list[StepTrace] = Field(default_factory=list)
    evidence: EvidenceRef


class PolicyBlockedResult(BaseModel):
    """The policy gate refused an action. A refusal is not a malfunction."""

    model_config = STRICT

    kind: Literal["policy_blocked"] = "policy_blocked"
    rule: str
    attempted_action: ActionType
    step_index: int
    evidence: EvidenceRef


class FailureResult(BaseModel):
    """Something broke. Carries enough to debug it without rerunning."""

    model_config = STRICT

    kind: Literal["failure"] = "failure"
    error_class: FailureClass
    step_index: int
    action: ActionType
    expected: str
    observed: str
    steps: list[StepTrace] = Field(default_factory=list)
    evidence: EvidenceRef


RunResult = Annotated[
    SuccessResult
    | BusinessOutcomeResult
    | NeedsHumanResult
    | PolicyBlockedResult
    | FailureResult,
    Field(discriminator="kind"),
]

EXIT_CODES: Final[dict[str, int]] = {
    "success": 0,
    "business_outcome": 10,
    "needs_human": 20,
    "policy_blocked": 30,
    "failure": 40,
}
"""Distinct process exit code per result kind.

Spaced by ten so a caller can branch on the decade without parsing JSON, and so a related
code can be added later without renumbering. A shell invoking a capability can tell a
business outcome from a failure without reading stdout at all.
"""
