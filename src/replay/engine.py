"""Replay: runs a saved capability, with no model.

Nothing here imports a model, not the SDK and not the discovery package, even indirectly.
tests/test_replay_isolation.py checks that.

The order of checks inside a step is the important part. Business outcomes are checked before
the step's postcondition, because a "no such member" page fails the postcondition too, and
checking that first would report an answer as a crash. See DECISIONS.md 0023.
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

    A protocol instead of importing EvidenceWriter, so the engine does no file I/O itself.
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
    # Steps a person has approved. Approval skips the approval rule for that step only. The
    # allowlist, denied paths and allowed actions still apply.
    approved_steps: set[int] = field(default_factory=set)
    # Set once any irreversible step has run. Starting the flow over after that could repeat it.
    irreversible_done: bool = False
    # How many times each restarting recovery has fired, bounded by its max_attempts.
    restarts: dict[str, int] = field(default_factory=dict)

    @property
    def evidence(self) -> EvidenceRef:
        source = self.evidence_source
        return source() if callable(source) else source

    def note(self, kind: str, **payload: Any) -> None:
        if self.sink is not None:
            self.sink.event(kind, **payload)


def _capture_failure(run: _Run, step_index: int) -> None:
    """A screenshot at the moment a step went wrong.

    The full failure files are written once at the end by `write_failure_artifacts`, because
    the state the run ended in is the one worth keeping.
    """
    if run.sink is None:
        return
    try:
        observation = run.surface.observe()
    except Exception:  # noqa: BLE001
        # A failed screenshot must not hide the failure it was meant to record.
        run.note("evidence_capture_failed", step_index=step_index)
        return
    if observation.screenshot_png:
        run.sink.screenshot(observation.screenshot_png)


def _capture_outcome(run: _Run) -> None:
    """One screenshot of the final screen.

    Failures get more than this. A success or business outcome has no other picture, and "the
    balance was 4182.55" means more with the screen it was read from.
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
    """Every locator for a field that gets a pii or secret value.

    Worked out from the declared inputs, not the values passed in, so it is the same either way.
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
        # The step has the path and the surface has the host, so one capability can run
        # against another deployment of the same app.
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

    From here, a timeout looks the same as an action that went through without showing it.
    Retrying a click that opens an account could open it twice. See DECISIONS.md 0024.
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


class _RestartFlow(Exception):
    """Raised by a recovery that can only be answered by starting the flow again."""

    def __init__(self, rule: RecoveryRule) -> None:
        super().__init__(rule.name)
        self.rule = rule


def _entry_url(capability: Capability, params: dict[str, Any]) -> str:
    return _template(
        capability.surface.base_url.rstrip("/") + "/" + capability.surface.entry_path.lstrip("/"),
        params,
    )


def _apply_recoveries(run: _Run, index: int) -> str | None:
    """Run any recovery whose check matches, up to max_attempts times.

    A recovery is noted on whatever result comes next. It is never a result of its own.

    Returns the name of a rule whose condition is still there after all its attempts. That
    is recovery_exhausted: the problem was recognised and the fix did not clear it. Carrying on
    would mean judging the flow against whatever is covering it, so the caller asks for a
    person instead.
    """
    for rule in run.capability.recoveries:
        if rule.applies_to_steps is not None and index not in rule.applies_to_steps:
            continue
        if rule.action is RecoveryAction.REAUTHENTICATE:
            # You cannot click past an expired session. Whatever the flow had done is gone, so
            # the only fix is to start again from the first page. The caller decides whether
            # that is still safe.
            if run.surface.evaluate(rule.detect):
                run.recoveries.append(rule.name)
                run.note("recovery", rule=rule.name, step_index=index, restart=True)
                raise _RestartFlow(rule)
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
    """Hand the browser to a person, wait, then decide what to do with their answer.

    With no session there is nobody to hand over to, so this just returns a NeedsHumanResult
    with the reason.
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

    # Check the page before looking at what the operator said, so their answer is compared
    # against something instead of just accepted.
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
    """A reason to stop that a person could sort out, as opposed to a final result."""

    reason: StuckReason
    why: str


def _run_step(run: _Run, step: Step) -> RunResult | None:
    """Run one step, handing over to a person and resuming as many times as needed.

    It loops because resuming can send the step back to the start. When a person approves an
    irreversible action, the run performs it through the same code as the first attempt, just
    with the approval set.
    """
    while True:
        outcome = _attempt_step(run, step)
        if not isinstance(outcome, _Escalation):
            return outcome

        resumed = _escalate(run, step, outcome)
        if not isinstance(resumed, ResumeAction):
            return resumed

        if resumed is ResumeAction.SKIP:
            if step.risk is RiskClass.RISKY_IRREVERSIBLE:
                run.irreversible_done = True
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
    """One try at a step. Returns a final result, None on success, or an _Escalation."""
    index = step.index
    started = time.monotonic()

    # Policy first, before touching anything. An irreversible step under require_approval
    # stops here so a person can approve it.
    action = _action_for(step, run.params, run.capability.surface.base_url)
    decision = run.gate.check(action, step.risk)
    if isinstance(decision, Blocked):
        approval_rule = decision.rule.startswith("risky_action_policy:require_approval")
        if approval_rule and index in run.approved_steps:
            # A person approved this step. Nothing else is skipped.
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

    # Find the control, fill in values and act. Retries are limited, and an irreversible step
    # gets none whatever the WaitSpec says.
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
            if step.risk is RiskClass.RISKY_IRREVERSIBLE:
                run.irreversible_done = True
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
            # A wait that times out is often waiting for a page the app replaced with an answer,
            # like a rejected form that never reaches the review page. So check for a declared
            # outcome before retrying or failing. Recoveries go first, as after a normal step,
            # since an expired session or maintenance page may be what the wait was stuck
            # behind. See DECISIONS.md 0045.
            stuck_on = _apply_recoveries(run, index)
            if stuck_on is not None:
                return _Escalation(
                    StuckReason.RECOVERY_EXHAUSTED,
                    f"the recovery {stuck_on!r} ran its attempts at step {index} and its "
                    "condition is still on screen",
                )
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
                    # Never retried (DECISIONS.md 0024): a timeout looks the same as an action
                    # that went through without showing it. A person can look and tell.
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

    # The app returning an error is not a failed check and should not be reported as one.
    # Only the HTTP status tells them apart, since a 500 page renders like any other page.
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

    # recoveries first, so a maintenance page is cleared before anything is judged
    stuck_on = _apply_recoveries(run, index)
    if stuck_on is not None:
        return _Escalation(
            StuckReason.RECOVERY_EXHAUSTED,
            f"the recovery {stuck_on!r} ran its attempts at step {index} and its condition "
            "is still on screen, so whatever interrupted the flow is still there",
        )

    # business outcomes before the postcondition, since a not-found page fails the
    # postcondition too
    outcome = _matching_outcome(run, index)
    if outcome is not None:
        return _outcome_result(run, outcome, index)

    # and only now, did the step do what it should
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
    """Run a saved capability with no model.

    With a Session, anything a person could sort out becomes a real handoff on this same
    browser. Without one, the run just ends with NeedsHuman.

    Every exit goes through one place, so a run that fails before starting and one that fails
    at the last step leave the same folder layout behind.
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


def _restart(
    run: _Run, step: Step, rule: RecoveryRule
) -> RunResult | ResumeAction | None:
    """Start the flow again from the first page, or ask for a person if that is not safe.

    Returns None when the run should restart at step 0. A restart is not safe if an
    irreversible step has already run, since it could happen twice (DECISIONS.md 0024), or if
    this rule has already used up its restarts.
    """
    used = run.restarts.get(rule.name, 0) + 1
    run.restarts[rule.name] = used
    why: str | None = None
    if run.irreversible_done:
        why = (
            f"the recovery {rule.name!r} would start the flow again at step 0, but an "
            "irreversible step has already run and starting over could repeat it"
        )
    elif used > rule.max_attempts:
        why = (
            f"the recovery {rule.name!r} has already restarted the flow {rule.max_attempts} "
            f"time(s) and its condition came back at step {step.index}"
        )
    if why is not None:
        run.note("restart_refused", rule=rule.name, step_index=step.index)
        return _escalate(run, step, _Escalation(StuckReason.RECOVERY_EXHAUSTED, why))

    entry = _entry_url(run.capability, run.params)
    try:
        run.surface.act(NavigateAction(url=entry))
    except (ActionTimeout, PolicyViolation) as exc:
        _capture_failure(run, step.index)
        return FailureResult(
            error_class=FailureClass.SURFACE_UNAVAILABLE,
            step_index=step.index,
            action=ActionType.NAVIGATE,
            expected=f"the entry screen to load again after {rule.name!r}",
            observed=str(exc),
            steps=run.traces,
            evidence=run.evidence,
        )
    run.note("restart_flow", rule=rule.name, from_step=step.index, attempt=used)
    return None


def _execute(
    run: _Run,
    capability: Capability,
    params: dict[str, Any],
    surface: Any,
    allow_draft: bool,
) -> RunResult:
    ref = run.evidence

    # checks before the browser does anything
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

    # From here on, screenshots black out fields filled from pii or secret parameters. The
    # first screenshot is taken by the fingerprint check on the next line.
    masker = getattr(surface, "set_pii_masks", None)
    if callable(masker):
        masker(_pii_bundles(capability))

    drift = check_fingerprint(capability, surface, ref, run.params)
    if drift is not None:
        return drift

    # the steps. A restarting recovery sends the run back to step 0.
    position = 0
    while position < len(capability.steps):
        step = capability.steps[position]
        try:
            result = _run_step(run, step)
        except _RestartFlow as restart:
            decision = _restart(run, step, restart.rule)
            if decision is None:
                position = 0
                continue
            if isinstance(decision, ResumeAction):
                # A person dealt with it during the handoff. Carry on from this step, or past it
                # if they completed it themselves.
                position += 1 if decision is ResumeAction.SKIP else 0
                continue
            return decision
        if result is not None:
            return result
        position += 1

    # the checkpoint for the whole flow
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

    # the outputs
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
