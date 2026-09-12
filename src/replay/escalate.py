"""What automation does when it gets the session back.

Split out of engine.py because it is the part worth reading on its own. The rule underneath
all of it is one sentence: trust the page, not the report. An operator saying "I did it" is a
claim about a screen, and the screen is right there, so the claim is checked rather than
believed. See DECISIONS.md 0031.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from src.escalation.intervention import InterventionResolution, refuse_unsafe_outcome
from src.models.capability import Capability, Step
from src.models.common import ActionType, FailureClass, ResolutionOutcome, StuckReason
from src.models.results import EvidenceRef, FailureResult, NeedsHumanResult, StepTrace


class ResumeAction(StrEnum):
    """What the engine should do with the step it stopped on."""

    PERFORM = "perform"
    SKIP = "skip"


def verify_after_return(surface: Any, capability: Capability, step: Step) -> bool:
    """Re-observe and ask whether the step's checkpoint holds now.

    The step's own postcondition if it declares one, the capability checkpoint otherwise. A
    step with neither has nothing to verify against, and that returns False rather than True:
    absence of a check is not evidence that a thing happened.
    """
    signal = step.postcondition.signal if step.postcondition else capability.checkpoint.signal
    if signal is None:
        return False
    return bool(surface.evaluate(signal))


def decide_resume(
    resolution: InterventionResolution,
    step: Step,
    *,
    postcondition_held: bool,
    intervention_id: str,
    traces: list[StepTrace],
    evidence: EvidenceRef,
) -> ResumeAction | FailureResult | NeedsHumanResult:
    """Turn the operator's answer plus the state of the page into one next move."""
    outcome = resolution.outcome

    # The safety rule runs here as well as in the console, because a resolution file can be
    # written by hand, by a script, or by a future second console. The UI hiding an option is
    # a courtesy; this is the enforcement.
    refusal = refuse_unsafe_outcome(step.risk, outcome)
    if refusal is not None:
        return FailureResult(
            error_class=FailureClass.INTERNAL,
            step_index=step.index,
            action=step.action,
            expected="an outcome that is safe for an irreversible step",
            observed=refusal,
            steps=traces,
            evidence=evidence,
        )

    if outcome is ResolutionOutcome.ABORTED:
        return NeedsHumanResult(
            intervention_id=intervention_id,
            reason=StuckReason.UNKNOWN_STATE,
            step_index=step.index,
            steps=traces,
            evidence=evidence,
        )

    if outcome is ResolutionOutcome.COMPLETED_MANUALLY:
        if not postcondition_held:
            expected = (
                step.postcondition.description
                if step.postcondition
                else "the capability checkpoint"
            )
            return FailureResult(
                error_class=FailureClass.CHECKPOINT_FAILED,
                step_index=step.index,
                action=step.action,
                expected=expected,
                observed=(
                    f"the operator reported step {step.index} completed manually "
                    f"(note: {resolution.operator_note or 'none'}), but the page does not "
                    "show it. The run stops here rather than continuing from a state nobody "
                    "has established."
                ),
                steps=traces,
                evidence=evidence,
            )
        return ResumeAction.SKIP

    # approved and retry_step both mean automation drives the step. They differ only in what
    # produced the intervention, which the engine has already recorded.
    return ResumeAction.PERFORM


def needs_human_on_expiry(
    intervention_id: str,
    step: Step,
    reason: StuckReason,
    traces: list[StepTrace],
    evidence: EvidenceRef,
) -> NeedsHumanResult:
    """Nobody answered. The id goes in the result so the caller can see it was raised."""
    return NeedsHumanResult(
        intervention_id=intervention_id,
        reason=reason,
        step_index=step.index,
        steps=traces,
        evidence=evidence,
    )


__all__ = [
    "ActionType",
    "ResumeAction",
    "decide_resume",
    "needs_human_on_expiry",
    "verify_after_return",
]
