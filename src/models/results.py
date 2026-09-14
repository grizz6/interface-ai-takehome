"""Run results. Every run ends as exactly one of five types.

A business outcome is an answer the caller wanted, a failure is something broken, and a policy
block is a refusal. Keeping them apart is the whole point.

There is no type for a recovered problem. A recovery is something that happened on the way to
a result, so it is listed in `recoveries_applied` on a success.
"""
from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, Field

from src.models.common import STRICT, ActionType, FailureClass, StuckReason


class StepTrace(BaseModel):
    """What happened on one step, including which locator tier found the control.

    `locator_strategy_used` is how you notice an app changing. A flow that slowly slides from
    role_name to css_fallback over many runs is an early warning, and you only see it if every
    step records it.
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
    """The run stopped and asked for a person instead of guessing."""

    model_config = STRICT

    kind: Literal["needs_human"] = "needs_human"
    intervention_id: str
    reason: StuckReason
    step_index: int
    steps: list[StepTrace] = Field(default_factory=list)
    evidence: EvidenceRef


class PolicyBlockedResult(BaseModel):
    """The policy refused an action. That is a refusal, not something broken."""

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
"""One exit code per result type.

Ten apart, so a related code can be added later without renumbering. A shell script can tell a
business outcome from a failure without reading any output.
"""
