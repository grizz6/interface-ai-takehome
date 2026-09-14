"""Deterministic replay. The path an agent actually invokes in production.

NO MODEL IS REACHABLE FROM HERE. Not the SDK, not the discovery package, not by any transitive
import. design rule 2 calls that a claim the reviewer will check, so it is checked
mechanically in tests/test_replay_isolation.py rather than asserted here.

The order of classification inside a step is the load bearing decision in this module, and it
is deliberately not the obvious one. A business outcome is evaluated BEFORE the postcondition,
because a "no such member" screen fails the postcondition too, and asking "did this step work"
before asking "did the application give me a legitimate answer" reports a real answer as a
crash. That is the confusion invariant 5 exists to prevent. See DECISIONS.md 0023.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.models.capability import (
    BusinessOutcomeSpec,
    Capability,
    OutputSpec,
    RecoveryRule,
    Step,
)
from src.models.common import (
    ActionType,
    FailureClass,
    RecoveryAction,
    RiskClass,
    Sensitivity,
    StuckReason,
    ValueType,
)
from src.models.policy import PolicyConfig
from src.models.results import (
    BusinessOutcomeResult,
    EvidenceRef,
    FailureResult,
    NeedsHumanResult,
    PolicyBlockedResult,
    RunResult,
    StepTrace,
    SuccessResult,
)
from src.policy.gate import Blocked, PolicyGate
from src.replay.preflight import (
    COERCERS,
    check_approval,
    check_fingerprint,
    check_parameters,
)
from src.surface.actions import (
    ClickAction,
    NavigateAction,
    PressAction,
    SelectAction,
    TypeAction,
)
from src.escalation.session import EscalationContext
from src.evidence.failure import write_failure_artifacts
from src.replay.escalate import (
    ResumeAction,
    decide_resume,
    needs_human_on_expiry,
    verify_after_return,
)
from src.surface.extraction import extract_value
from src.surface.protocol import (
    ActionTimeout,
    LocatorAmbiguous,
    LocatorUnresolved,
    PolicyViolation,
)


@runtime_checkable
class EvidenceSink(Protocol):
    """What replay needs from an evidence writer.

    A protocol rather than an import of EvidenceWriter, so the engine owns no I/O and the
    isolation proof has one fewer edge to worry about.
    """

    def event(self, kind: str, **payload: Any) -> None: ...
    def screenshot(self, png: bytes) -> Any: ...
    def snapshot(self, name: str, text: str) -> Any: ...


@dataclass
class _Run:
    """Mutable state for one replay."""

    capability: Capability
    params: dict[str, Any]
    surface: Any
    gate: PolicyGate
    policy: PolicyConfig
    evidence_source: EvidenceRef | Callable[[], EvidenceRef]
    sink: EvidenceSink | None = None
    traces: list[StepTrace] = field(default_factory=list)
    recoveries: list[str] = field(default_factory=list)
    started: float = 0.0
    session: Any = None
    # Steps a human has explicitly approved. Approval waives the approval rule for one step
    # and nothing else: the allowlist, the denied paths and the action list still apply.
    approved_steps: set[int] = field(default_factory=set)

    @property
    def evidence(self) -> EvidenceRef:
        source = self.evidence_source
        return source() if callable(source) else source

    def note(self, kind: str, **payload: Any) -> None:
        if self.sink is not None:
            self.sink.event(kind, **payload)


def _capture_failure(run: _Run, step_index: int) -> None:
    """A screenshot at the moment a step went wrong, for the run timeline.

    The full post mortem is written once, at the end, by `_write_failure_artifacts`. Capturing
    it here as well would mean a directory with one failure/ per thing that went wrong, and
    the interesting one is always the state the run actually ended in.
    """
    if run.sink is None:
        return
    try:
        observation = run.surface.observe()
    except Exception:  # noqa: BLE001
        # Evidence capture must never mask the failure it is describing.
        run.note("evidence_capture_failed", step_index=step_index)
        return
    if observation.screenshot_png:
        run.sink.screenshot(observation.screenshot_png)


def _capture_outcome(run: _Run) -> None:
    """One screenshot of the screen the run was judged on.

    Failures already get the richer capture. A run that succeeded or that returned a business
    outcome produces no other visual record, and "the balance was 4182.55" is a much weaker
    claim without the screen it was read from.
    """
    if run.sink is None:
        return
    try:
        observation = run.surface.observe()
    except Exception:  # noqa: BLE001
        run.note("evidence_capture_failed", step_index=-1)
        return
    if observation.screenshot_png:
        run.sink.screenshot(observation.screenshot_png)


def _pii_bundles(capability: Capability) -> list[Any]:
    """Every locator whose field receives a value the schema calls pii or secret.

    Read off the declared inputs rather than off the supplied values, the same way
    params_redacted is, so the set is the same whether or not a value was passed.
    """
    sensitive = {
        spec.name for spec in capability.inputs
        if spec.sensitivity in (Sensitivity.PII, Sensitivity.SECRET)
    }
    return [
        step.target
        for step in capability.steps
        if step.target is not None
        and step.value is not None
        and getattr(step.value, "param", None) in sensitive
    ]


def _bind(step: Step, params: dict[str, Any]) -> str | None:
    if step.value is None:
        return None
    if step.value.source == "param":
        return str(params.get(step.value.param, ""))
    return step.value.value


def _template(url: str, params: dict[str, Any]) -> str:
    for name, value in params.items():
        url = url.replace("{" + name + "}", str(value))
    return url


def _action_for(step: Step, params: dict[str, Any], base_url: str = "") -> Any:
    if step.action is ActionType.NAVIGATE:
        # The step holds a path; the surface descriptor holds the host. Joining them here is
        # what lets one artifact run against a second deployment of the same application.
        target = _template(step.url or "", params)
        if target.startswith("/"):
            target = base_url.rstrip("/") + target
        return NavigateAction(url=target)
    if step.action is ActionType.PRESS:
        return PressAction(key=_bind(step, params) or "Enter")
    assert step.target is not None
    if step.action is ActionType.CLICK:
        return ClickAction(bundle=step.target)
    if step.action is ActionType.TYPE:
        return TypeAction(bundle=step.target, text=_bind(step, params) or "")
    return SelectAction(bundle=step.target, value=_bind(step, params) or "")


def _retry_budget(step: Step) -> int:
    """Zero for anything irreversible, whatever the WaitSpec says.

    A transient timeout and a completed action that simply did not report look identical from
    out here. Retrying a click that opens an account opens it twice. See DECISIONS.md 0024.
    """
    if step.risk is RiskClass.RISKY_IRREVERSIBLE:
        return 0
    return step.wait.retry_on_timeout


def _matching_outcome(
    run: _Run, index: int
) -> BusinessOutcomeSpec | None:
    for outcome in run.capability.known_outcomes:
        if outcome.check_after_step not in (None, index):
            continue
        if run.surface.evaluate(outcome.detect):
            return outcome
    return None


def _outcome_result(run: _Run, outcome: BusinessOutcomeSpec, index: int) -> RunResult:
    run.note("business_outcome", code=outcome.code, step_index=index)
    _capture_outcome(run)
    return BusinessOutcomeResult(
        code=outcome.code,
        message=outcome.description,
        detected_at_step=index,
        partial_outputs=_extract(run, only=outcome.partial_outputs)[0],
        steps=run.traces,
        evidence=run.evidence,
    )


def _apply_recoveries(run: _Run, index: int) -> str | None:
    """Fire any recovery whose detect signal matches, bounded by max_attempts.

    A recovery that fires is metadata on whatever result follows. It never becomes a result
    kind of its own, per design rules section 7.

    Returns the name of a rule whose condition is STILL present after its attempts are spent.
    That is the recovery_exhausted case: the interruption is real, it was recognised, and the
    declared remedy did not clear it. Continuing from there means judging the flow against
    whatever is covering it, so the caller escalates instead.
    """
    for rule in run.capability.recoveries:
        if rule.applies_to_steps is not None and index not in rule.applies_to_steps:
            continue
        fired = False
        for _attempt in range(rule.max_attempts):
            if not run.surface.evaluate(rule.detect):
                break
            _perform_recovery(run, rule)
            run.recoveries.append(rule.name)
            run.note("recovery", rule=rule.name, step_index=index)
            fired = True
        else:
            if fired and run.surface.evaluate(rule.detect):
                run.note("recovery_exhausted", rule=rule.name, step_index=index)
                return rule.name
    return None


def _perform_recovery(run: _Run, rule: RecoveryRule) -> None:
    if rule.action is RecoveryAction.DISMISS and rule.action_target is not None:
        run.surface.act(ClickAction(bundle=rule.action_target))
    elif rule.action is RecoveryAction.RELOAD:
        page = getattr(run.surface, "page", None)
        if page is not None:
            page.reload()
    elif rule.action is RecoveryAction.WAIT_RETRY:
        page = getattr(run.surface, "page", None)
        if page is not None:
            page.wait_for_timeout(500)


def _coerce_output(spec: OutputSpec, raw: str) -> Any:
    try:
        return COERCERS[spec.type](raw)
    except (TypeError, ValueError):
        return raw if spec.type is ValueType.STRING else None


def _escalate(
    run: _Run, step: Step, escalation: _Escalation
) -> ResumeAction | RunResult:
    """Hand the session to a human, wait, then decide what to do with what comes back.

    With no session wired up there is nobody to escalate to, so this degrades to the honest
    answer: a NeedsHumanResult naming the reason, exactly as phase 6 returned.
    """
    if run.session is None:
        return NeedsHumanResult(
            intervention_id=f"{run.evidence.run_id}-{escalation.reason.value}-{step.index}",
            reason=escalation.reason,
            step_index=step.index,
            steps=run.traces,
            evidence=run.evidence,
        )

    context = EscalationContext.from_capability(
        run.capability,
        run.params,
        step_index=step.index,
        step_description=step.description,
        risk=step.risk,
        why=escalation.why,
        run_id=run.evidence.run_id,
    )
    intervention_id = run.session.escalate(escalation.reason, context)
    run.note("escalated", step_index=step.index, reason=escalation.reason.value,
             intervention_id=intervention_id)

    resolution = run.session.await_return(intervention_id)
    if resolution is None:
        run.note("escalation_expired", intervention_id=intervention_id)
        return needs_human_on_expiry(
            intervention_id, step, escalation.reason, run.traces, run.evidence
        )

    # Trust the page, not the report. This runs before the outcome is even looked at, so the
    # operator's claim is compared against something rather than accepted.
    held = verify_after_return(run.surface, run.capability, step)
    run.note(
        "resumed",
        intervention_id=intervention_id,
        outcome=resolution.outcome.value,
        postcondition_held=held,
        human_actions=len(resolution.human_actions),
    )
    run.session.resume()

    return decide_resume(
        resolution,
        step,
        postcondition_held=held,
        intervention_id=intervention_id,
        traces=run.traces,
        evidence=run.evidence,
    )


@dataclass(frozen=True)
class _Escalation:
    """A stopping condition that a human could resolve, rather than a terminal result."""

    reason: StuckReason
    why: str


def _run_step(run: _Run, step: Step) -> RunResult | None:
    """Execute one step, escalating and resuming as many times as it takes.

    The loop exists because a resume can put the step back at the start: a human who approves
    an irreversible action hands it back for automation to perform, and that is the same code
    path as the first attempt with one bit changed.
    """
    while True:
        outcome = _attempt_step(run, step)
        if not isinstance(outcome, _Escalation):
            return outcome

        resumed = _escalate(run, step, outcome)
        if not isinstance(resumed, ResumeAction):
            return resumed

        if resumed is ResumeAction.SKIP:
            run.traces.append(
                StepTrace(
                    index=step.index,
                    action=step.action,
                    description=f"{step.description} (completed by a human)",
                    locator_strategy_used=None,
                    attempt_count=0,
                    duration_ms=0,
                    recovered_by=None,
                )
            )
            return None

        if outcome.reason is StuckReason.RISKY_ACTION_REQUIRES_APPROVAL:
            run.approved_steps.add(step.index)


def _attempt_step(run: _Run, step: Step) -> RunResult | None | _Escalation:
    """One pass at a step. Returns a terminal result, None on success, or an escalation."""
    index = step.index
    started = time.monotonic()

    # (c) the gate decides before anything is touched, and an irreversible step under
    # require_approval stops here so a human can approve it.
    action = _action_for(step, run.params, run.capability.surface.base_url)
    decision = run.gate.check(action, step.risk)
    if isinstance(decision, Blocked):
        approval_rule = decision.rule.startswith("risky_action_policy:require_approval")
        if approval_rule and index in run.approved_steps:
            # A human approved this exact step. Nothing else is waived.
            run.note("approval_honoured", step_index=index, rule=decision.rule)
        else:
            run.note("policy_block", step_index=index, rule=decision.rule)
            if approval_rule:
                return _Escalation(
                    StuckReason.RISKY_ACTION_REQUIRES_APPROVAL,
                    f"step {index} is {step.risk.value} and the policy requires a human to "
                    f"approve it before it runs: {step.description}",
                )
            return PolicyBlockedResult(
                rule=decision.rule,
                attempted_action=step.action,
                step_index=index,
                evidence=run.evidence,
            )

    # (a, b, d) resolve as recorded, bind, act. Retries are bounded and an irreversible
    # step has a budget of zero no matter what the WaitSpec asked for.
    attempts = 0
    strategy: str | None = None
    budget = _retry_budget(step)
    while True:
        attempts += 1
        try:
            if step.target is not None:
                strategy = run.surface.resolve(step.target).strategy
            run.surface.act(
                action, wait=step.wait, risk=step.risk,
                approved=index in run.approved_steps,
            )
            break
        except LocatorAmbiguous as exc:
            _capture_failure(run, index)
            return _Escalation(
                StuckReason.LOCATOR_AMBIGUOUS,
                f"the recorded locator for step {index} matched more than one element, so "
                f"there is no safe way to pick one: {exc}",
            )
        except LocatorUnresolved as exc:
            _capture_failure(run, index)
            tiers = (
                [step.target.primary.strategy, *(f.strategy for f in step.target.fallbacks)]
                if step.target
                else []
            )
            return FailureResult(
                error_class=FailureClass.LOCATOR_UNRESOLVED,
                step_index=index,
                action=step.action,
                expected=f"one element from the recorded bundle, tiers tried: {tiers}",
                observed=str(exc),
                steps=run.traces,
                evidence=run.evidence,
            )
        except ActionTimeout as exc:
            # A wait that runs out is often waiting for a screen the application replaced with
            # an answer: a rejected form never shows the review page. Ask whether a declared
            # outcome is on screen before retrying or failing, or 0023's promise that an answer
            # is never reported as a crash does not hold for any step that waits on text.
            outcome = _matching_outcome(run, index)
            if outcome is not None:
                run.traces.append(
                    StepTrace(
                        index=index,
                        action=step.action,
                        description=step.description,
                        locator_strategy_used=strategy,
                        attempt_count=attempts,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        recovered_by=None,
                    )
                )
                run.note("wait_timed_out_on_outcome", step_index=index, code=outcome.code)
                return _outcome_result(run, outcome, index)
            if attempts > budget:
                _capture_failure(run, index)
                if step.risk is RiskClass.RISKY_IRREVERSIBLE:
                    # Per DECISIONS 0024 this is never retried automatically, because a
                    # timeout cannot be told apart from a completed action that did not
                    # report. A human can look at the screen and tell.
                    return _Escalation(
                        StuckReason.STEP_TIMEOUT,
                        f"step {index} is irreversible and timed out after "
                        f"{step.wait.timeout_ms}ms. It may or may not have completed, and "
                        f"nothing here can tell which: {exc}",
                    )
                return FailureResult(
                    error_class=FailureClass.TIMEOUT,
                    step_index=index,
                    action=step.action,
                    expected=f"the step to settle within {step.wait.timeout_ms}ms",
                    observed=str(exc),
                    steps=run.traces,
                    evidence=run.evidence,
                )
            run.note("retry", step_index=index, attempt=attempts)
        except PolicyViolation as exc:
            return PolicyBlockedResult(
                rule=exc.rule,
                attempted_action=step.action,
                step_index=index,
                evidence=run.evidence,
            )

    run.traces.append(
        StepTrace(
            index=index,
            action=step.action,
            description=step.description,
            locator_strategy_used=strategy,
            attempt_count=attempts,
            duration_ms=int((time.monotonic() - started) * 1000),
            recovered_by=run.recoveries[-1] if run.recoveries else None,
        )
    )
    run.note("action", step_index=index, tier=strategy, attempts=attempts)

    # The application itself erroring is not a checkpoint miss and must not be reported as
    # one. Only the transport can tell the difference, since a 500 page renders like any
    # other page.
    status = getattr(run.surface, "last_status", None)
    if isinstance(status, int) and status >= 500:
        _capture_failure(run, index)
        return FailureResult(
            error_class=FailureClass.APP_ERROR,
            step_index=index,
            action=step.action,
            expected="the application to respond successfully",
            observed=f"it answered HTTP {status}",
            steps=run.traces,
            evidence=run.evidence,
        )

    # (e) recoveries first, so an interstitial is cleared before anything is judged
    stuck_on = _apply_recoveries(run, index)
    if stuck_on is not None:
        return _Escalation(
            StuckReason.RECOVERY_EXHAUSTED,
            f"the recovery {stuck_on!r} ran its attempts at step {index} and its condition "
            "is still on screen, so whatever interrupted the flow is still there",
        )

    # (f) a declared business outcome BEFORE the postcondition. A not-found screen fails the
    # postcondition too, and asking that question first turns an answer into a crash.
    outcome = _matching_outcome(run, index)
    if outcome is not None:
        return _outcome_result(run, outcome, index)

    # (g) and only now, did this step do what it said
    if step.postcondition is not None and not run.surface.evaluate(step.postcondition.signal):
        _capture_failure(run, index)
        return FailureResult(
            error_class=FailureClass.CHECKPOINT_FAILED,
            step_index=index,
            action=step.action,
            expected=step.postcondition.description,
            observed=f"the postcondition {step.postcondition.signal.kind} did not hold",
            steps=run.traces,
            evidence=run.evidence,
        )
    return None


def _extract(run: _Run, only: list[str] | None = None) -> tuple[dict[str, Any], str | None]:
    """Read the declared outputs. Returns the values and the name of any required miss."""
    values: dict[str, Any] = {}
    for spec in run.capability.outputs:
        if only is not None and spec.name not in only:
            continue
        try:
            raw = extract_value(run.surface, spec.extraction)
        except (LocatorAmbiguous, LocatorUnresolved):
            raw = None
        if raw is None:
            if spec.extraction.required and only is None:
                return values, spec.name
            continue
        values[spec.name] = _coerce_output(spec, raw)
    return values, None


def replay(
    capability: Capability,
    params: dict[str, Any],
    surface: Any,
    policy: PolicyConfig,
    *,
    evidence: EvidenceRef | Callable[[], EvidenceRef],
    sink: EvidenceSink | None = None,
    allow_draft: bool = False,
    session: Any = None,
) -> RunResult:
    """Run a recorded capability with no model in the decision loop.

    With a Session passed in, every stopping condition a human could resolve becomes a real
    handoff on this same browser context. Without one, those conditions return NeedsHuman and
    the run ends, which is the same contract with nobody listening.

    Every exit goes through one place, so a run that fails in pre-flight and a run that fails
    at the last step leave the same shaped directory behind.
    """
    run = _Run(
        capability=capability,
        params={},
        surface=surface,
        gate=PolicyGate(policy),
        policy=policy,
        evidence_source=evidence,
        sink=sink,
        started=time.monotonic(),
        session=session,
    )
    result = _execute(run, capability, params, surface, allow_draft)
    step = next((s for s in capability.steps
                 if s.index == int(getattr(result, "step_index", -1) or -1)), None)
    write_failure_artifacts(surface, run.sink, result, step=step,
                            on_error=lambda: run.note("failure_capture_incomplete"))
    return result


def _execute(
    run: _Run,
    capability: Capability,
    params: dict[str, Any],
    surface: Any,
    allow_draft: bool,
) -> RunResult:
    ref = run.evidence

    # 1. pre-flight, before a browser is touched
    approval = check_approval(capability, allow_draft, ref)
    if approval is not None:
        return approval
    bound = check_parameters(capability, params, ref)
    if isinstance(bound, FailureResult):
        return bound
    run.params = bound
    run.note(
        "preflight",
        capability=capability.capability_id,
        status=capability.status.value,
        allow_draft=allow_draft,
    )

    # Screenshots black out any field bound to a pii or secret parameter, for the rest of the
    # run. Set here rather than earlier so that the checks above genuinely touch nothing: the
    # first screenshot is taken by the fingerprint check on the next line.
    masker = getattr(surface, "set_pii_masks", None)
    if callable(masker):
        masker(_pii_bundles(capability))

    drift = check_fingerprint(capability, surface, ref, run.params)
    if drift is not None:
        return drift

    # 2. the steps
    for step in capability.steps:
        result = _run_step(run, step)
        if result is not None:
            return result

    # 4. the checkpoint the whole flow is judged on
    if not surface.evaluate(capability.checkpoint.signal):
        _capture_failure(run, len(capability.steps) - 1)
        return FailureResult(
            error_class=FailureClass.CHECKPOINT_FAILED,
            step_index=len(capability.steps) - 1,
            action=ActionType.WAIT_FOR,
            expected=capability.checkpoint.description,
            observed="the checkpoint did not hold after the last step",
            steps=run.traces,
            evidence=run.evidence,
        )

    # 5. the outputs
    values, missing = _extract(run)
    if missing is not None:
        _capture_failure(run, len(capability.steps) - 1)
        return FailureResult(
            error_class=FailureClass.EXTRACTION_FAILED,
            step_index=len(capability.steps) - 1,
            action=ActionType.WAIT_FOR,
            expected=f"a value for required output {missing!r}",
            observed="nothing could be read from the element it names",
            steps=run.traces,
            evidence=run.evidence,
        )

    _capture_outcome(run)
    return SuccessResult(
        outputs=values,
        steps=run.traces,
        recoveries_applied=run.recoveries,
        evidence=run.evidence,
        duration_ms=int((time.monotonic() - run.started) * 1000),
    )
