"""The observe, decide, act loop.

The loop owns three things the model is not trusted with, and they are the reason it is code
rather than prompt.

It converts refs into durable locators at the moment of the action, while the observation that
issued them is still current. By the next turn those refs are meaningless, so this is the only
moment the conversion can happen at all.

It decides when to stop. A model asked to judge its own progress will keep going, so the
budgets live here: steps, wall clock, consecutive refusals, and a screen that has stopped
changing.

It refuses to believe a success it has not checked. `finish` is a claim, and `verify.py`
executes that claim against the live page before any SuccessResult is returned.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.discovery.client import (
    Message,
    ModelClient,
    ModelMessage,
    ToolCall,
    ToolResultMessage,
    ToolSpec,
    UserMessage,
)
from src.discovery.prompt import SYSTEM_PROMPT
from src.discovery.tools import ToolName, discovery_tools
from src.discovery.transcript import (
    ActionRecord,
    DeclaredCapability,
    DiscoveryStop,
    DiscoveryTranscript,
    EventKind,
    TranscriptEvent,
    hash_observation,
)
from src.discovery.verify import Verification, verify_finish
from src.models.capability import SurfaceDescriptor
from src.models.common import ActionType, FailureClass, StuckReason
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
from src.surface.actions import (
    ClickAction,
    NavigateAction,
    PressAction,
    SelectAction,
    TypeAction,
)
from src.surface.protocol import (
    ActionTimeout,
    LocatorAmbiguous,
    LocatorUnresolved,
    PolicyViolation,
)

TARGETED_TOOLS = {ToolName.CLICK, ToolName.TYPE_TEXT, ToolName.SELECT_OPTION}
FULL_OBSERVATIONS_RETAINED = 2


@dataclass(frozen=True)
class DiscoveryLimits:
    """Every budget in one place, so a run cannot quietly grow one."""

    max_steps: int = 25
    wall_clock_s: float = 300.0
    max_consecutive_blocks: int = 3
    # Three, not one. A validation error legitimately re-renders a structurally identical
    # page, and calling that a stall would abandon a run that is working correctly.
    max_identical_observations: int = 3
    max_finish_attempts: int = 3


@dataclass
class DiscoveryOutcome:
    """The RunResult per design rules section 7, plus the transcript phase 5 compiles."""

    result: RunResult
    transcript: DiscoveryTranscript


@dataclass
class _Turn:
    """One exchange, kept structurally so history can be rebuilt rather than mutated."""

    observation_text: str
    observation_summary: str
    screenshot: bytes | None = None
    model_text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    results: list[tuple[str, str, bool]] = field(default_factory=list)


class _Stop(Exception):
    """Internal control flow: a stopping condition was met."""

    def __init__(self, result: RunResult, reason: DiscoveryStop) -> None:
        super().__init__(reason)
        self.result = result
        self.reason = reason


def _describe_action(kind: ActionType, bundle: Any) -> str:
    """A human readable description that never contains the value that was typed.

    The value is exactly the thing most likely to be a member id or an account number, and
    this string travels into StepTrace and out into result.json. Redaction would catch a
    value the caller thought to pass to --redact; it cannot catch one nobody declared. So the
    description names the control instead, and the value lives only in ActionRecord where the
    schema knows it is sensitive.
    """
    control = getattr(bundle, "recorded_accessible_name", None) if bundle else None
    target = f" into {control!r}" if control else ""
    if kind is ActionType.TYPE:
        return f"type{target or ' a value'}"
    if kind is ActionType.SELECT:
        return f"select an option{target}"
    if kind is ActionType.CLICK:
        return f"click{f' {control!r}' if control else ''}"
    return kind.value


class DiscoveryRun:
    """One discovery run against one surface."""

    def __init__(
        self,
        *,
        goal: str,
        surface: Any,
        client: ModelClient,
        evidence: EvidenceRef | Callable[[], EvidenceRef],
        model: str,
        surface_descriptor: SurfaceDescriptor,
        target: str | None = None,
        limits: DiscoveryLimits | None = None,
        tools: list[ToolSpec] | None = None,
        on_observation: Any | None = None,
    ) -> None:
        # A callback rather than an EvidenceWriter, so the loop stays unaware of what
        # evidence is and the dependency points one way only.
        self._on_observation = on_observation
        self.target = target
        self.goal = goal
        self.surface = surface
        self.client = client
        # Accepts a callable so the reference is resolved when a result is built rather
        # than when the run starts. Screenshots are written as the run goes, so a reference
        # captured up front lists none of them, and a caller reading result.json cannot find
        # the richer signal section 3.5 asks for.
        self._evidence_source = evidence
        self.limits = limits or DiscoveryLimits()
        self.tools = tools if tools is not None else discovery_tools()
        self._run_id = (evidence() if callable(evidence) else evidence).run_id
        self.transcript = DiscoveryTranscript(
            run_id=self._run_id,
            goal=goal,
            model=model,
            surface=surface_descriptor,
            stop_reason=DiscoveryStop.ERROR,
        )
        self._turns: list[_Turn] = []
        self._seq = 0
        self._consecutive_blocks = 0
        self._identical_observations = 1
        self._last_hash: str | None = None
        self._finish_attempts = 0
        self._want_screenshot = True
        self._typed_values: list[str] = []
        self._started = 0.0
        self._observation: Any = None
        self._last_block: tuple[str, ActionType] | None = None

    @property
    def evidence(self) -> EvidenceRef:
        """Resolved fresh each time, so screenshots written since the run began are listed."""
        source = self._evidence_source
        return source() if callable(source) else source

    # -- bookkeeping ---------------------------------------------------------
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _event(self, kind: EventKind, **payload: Any) -> None:
        self.transcript.events.append(
            TranscriptEvent(
                seq=self._next_seq(), at=datetime.now(UTC), kind=kind, payload=payload
            )
        )

    def _traces(self) -> list[StepTrace]:
        return [
            StepTrace(
                index=record.seq,
                action=ActionType(record.action_kind),
                description=record.note or record.action_kind,
                locator_strategy_used=record.tier_resolved,
                duration_ms=record.duration_ms,
            )
            for record in self.transcript.actions_in_order()
            if record.action_kind in {a.value for a in ActionType}
        ]

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    # -- message construction ------------------------------------------------
    def _messages(self, current: str, screenshot: bytes | None) -> list[Message]:
        """Rebuild the whole history each turn.

        Only the two most recent observations are carried in full. An aria snapshot of this
        application runs to five thousand characters, and twenty of them would crowd out the
        thing that actually matters, which is what the model did and what came back.
        """
        messages: list[Message] = [UserMessage(text=f"GOAL: {self.goal}")]
        newest_kept = len(self._turns) - (FULL_OBSERVATIONS_RETAINED - 1)
        for index, turn in enumerate(self._turns):
            body = turn.observation_text if index >= newest_kept else turn.observation_summary
            messages.append(UserMessage(text=body, image_png=turn.screenshot))
            if turn.model_text or turn.tool_calls:
                messages.append(
                    ModelMessage(text=turn.model_text, tool_calls=list(turn.tool_calls))
                )
            for call_id, content, is_error in turn.results:
                name = next(
                    (c.name for c in turn.tool_calls if c.id == call_id), "unknown"
                )
                messages.append(
                    ToolResultMessage(
                        call_id=call_id, name=name, content=content, is_error=is_error
                    )
                )
        messages.append(UserMessage(text=current, image_png=screenshot))
        return messages

    @staticmethod
    def _summarize(observation: Any) -> str:
        return f"[earlier screen: {observation.title} at {observation.url}]"

    @staticmethod
    def _render(observation: Any) -> str:
        return (
            f"Current page: {observation.title}\nURL: {observation.url}\n\n"
            f"{observation.aria_yaml}"
        )

    # -- the loop ------------------------------------------------------------
    def run(self) -> DiscoveryOutcome:
        self._started = time.monotonic()
        try:
            self._drive()
        except _Stop as stop:
            self.transcript.stop_reason = stop.reason
            self._event(EventKind.STOP, reason=stop.reason.value)
            return DiscoveryOutcome(result=stop.result, transcript=self.transcript)

        result: RunResult = NeedsHumanResult(
            intervention_id=f"{self._run_id}-max-steps",
            reason=StuckReason.MAX_STEPS_EXCEEDED,
            step_index=len(self.transcript.actions),
            steps=self._traces(),
            evidence=self.evidence,
        )
        self.transcript.stop_reason = DiscoveryStop.MAX_STEPS
        self._event(EventKind.STOP, reason=DiscoveryStop.MAX_STEPS.value)
        return DiscoveryOutcome(result=result, transcript=self.transcript)

    def _drive(self) -> None:
        # Start where the caller said to start. Without this the model opens on about:blank
        # with no idea where the application lives, and its only option is to guess a URL,
        # which the policy gate then correctly refuses. The brief takes a goal AND an entry
        # point; this is the entry point being used.
        if self.target:
            self._act(
                NavigateAction(url=self.target),
                kind=ActionType.NAVIGATE,
                ref=None,
                bundle=None,
                literal=None,
            )

        for _step in range(self.limits.max_steps):
            self._check_clock()
            observation = self._observe()
            turn = _Turn(
                observation_text=self._render(observation),
                observation_summary=self._summarize(observation),
                screenshot=observation.screenshot_png if self._want_screenshot else None,
            )
            self._want_screenshot = False

            messages = self._messages(turn.observation_text, turn.screenshot)
            reply = self.client.complete(SYSTEM_PROMPT, messages, self.tools)
            turn.model_text = reply.text
            turn.tool_calls = list(reply.tool_calls)
            self._turns.append(turn)
            if reply.text:
                self._event(EventKind.MODEL_TEXT, text=reply.text)

            if not reply.tool_calls:
                turn.results.append(
                    ("none", "No tool was called. Use a tool to make progress.", True)
                )
                continue

            for call in reply.tool_calls:
                content, is_error = self._handle(call, observation)
                turn.results.append((call.id, content, is_error))
                self._event(
                    EventKind.TOOL_RESULT, tool=call.name, ok=not is_error, detail=content
                )

    def _backfill_hash_after(self, digest: str) -> None:
        """Close out the previous action with the screen state that followed it.

        An action cannot know what the page looks like afterwards until the next observation,
        so the hash is written back here. Without it obs_hash_after is always None, and the
        recorder's rule for dropping an action that changed nothing is dead code that never
        fires. ActionRecord is frozen, so the record is replaced rather than mutated.
        """
        if not self.transcript.actions:
            return
        last = self.transcript.actions[-1]
        if last.obs_hash_after is not None:
            return
        self.transcript.actions[-1] = last.model_copy(update={"obs_hash_after": digest})

    def _check_clock(self) -> None:
        if time.monotonic() - self._started <= self.limits.wall_clock_s:
            return
        raise _Stop(
            NeedsHumanResult(
                intervention_id=f"{self._run_id}-timeout",
                reason=StuckReason.STEP_TIMEOUT,
                step_index=len(self.transcript.actions),
                steps=self._traces(),
                evidence=self.evidence,
            ),
            DiscoveryStop.TIMEOUT,
        )

    def _observe(self) -> Any:
        observation = self.surface.observe()
        self._observation = observation
        if self._on_observation is not None:
            self._on_observation(observation)
        digest = hash_observation(observation.aria_yaml)
        self._backfill_hash_after(digest)
        self._event(EventKind.OBSERVATION, url=observation.url, hash=digest)

        if digest == self._last_hash:
            self._identical_observations += 1
        else:
            self._identical_observations = 1
        self._last_hash = digest

        if self._identical_observations >= self.limits.max_identical_observations:
            raise _Stop(
                NeedsHumanResult(
                    intervention_id=f"{self._run_id}-stalled",
                    reason=StuckReason.UNKNOWN_STATE,
                    step_index=len(self.transcript.actions),
                    steps=self._traces(),
                    evidence=self.evidence,
                ),
                DiscoveryStop.NEEDS_HUMAN,
            )
        if self._identical_observations >= self.limits.max_identical_observations - 1:
            # One away from calling it a stall. Give the model a picture before it is too late.
            self._want_screenshot = True
        return observation

    # -- tool dispatch -------------------------------------------------------
    _TOOL_ACTIONS = {
        ToolName.NAVIGATE: ActionType.NAVIGATE,
        ToolName.CLICK: ActionType.CLICK,
        ToolName.TYPE_TEXT: ActionType.TYPE,
        ToolName.SELECT_OPTION: ActionType.SELECT,
        ToolName.PRESS_KEY: ActionType.PRESS,
    }

    def _handle(self, call: ToolCall, observation: Any) -> tuple[str, bool]:
        """Run one tool call. Returns the tool_result text and whether it is an error."""
        name = call.name
        args = call.arguments

        if name == ToolName.LOOK:
            self._want_screenshot = True
            return "Snapshot refreshed. The next message carries the current screen.", False

        if name == ToolName.GIVE_UP:
            reason = str(args.get("reason") or "no reason given")
            self._event(EventKind.STOP, tool="give_up", reason=reason)
            raise _Stop(
                NeedsHumanResult(
                    intervention_id=f"{self._run_id}-gave-up",
                    reason=StuckReason.UNKNOWN_STATE,
                    step_index=len(self.transcript.actions),
                    steps=self._traces(),
                    evidence=self.evidence,
                ),
                DiscoveryStop.GAVE_UP,
            )

        if name == ToolName.FINISH:
            return self._finish(args, observation)

        if name not in self._TOOL_ACTIONS:
            return f"There is no tool called {name!r}.", True

        return self._perform(call, observation)

    def _perform(self, call: ToolCall, observation: Any) -> tuple[str, bool]:
        name = ToolName(call.name)
        args = call.arguments
        kind = self._TOOL_ACTIONS[name]
        bundle = None
        ref = None
        literal = None

        if name in TARGETED_TOOLS:
            ref = str(args.get("ref") or "")
            if not ref or observation.by_ref(ref) is None:
                # A stale ref is a model mistake, not a locator failure. Tell it and move on.
                return (
                    f"There is no {ref!r} in the current snapshot. Refs are reassigned every "
                    "time the screen is captured. Call look and use a ref from the result.",
                    True,
                )
            bundle = self._describe(ref)

        if name is ToolName.NAVIGATE:
            action: Any = NavigateAction(url=str(args.get("url") or ""))
        elif name is ToolName.PRESS_KEY:
            action = PressAction(key=str(args.get("key") or ""))
        else:
            # Every remaining tool acts on a control, so the bundle above is not optional.
            # If this ever fires, describe() returned None rather than raising, which is a
            # bug in the surface rather than something the model did.
            if bundle is None:
                return f"{name} needs a control to act on and none was resolved.", True
            if name is ToolName.CLICK:
                action = ClickAction(bundle=bundle)
            elif name is ToolName.TYPE_TEXT:
                literal = str(args.get("text") or "")
                self._typed_values.append(literal)
                action = TypeAction(bundle=bundle, text=literal)
            else:
                literal = str(args.get("value") or "")
                action = SelectAction(bundle=bundle, value=literal)

        return self._act(action, kind=kind, ref=ref, bundle=bundle, literal=literal)

    def _describe(self, ref: str) -> Any:
        """Convert the ref now, while the snapshot that issued it is still current."""
        try:
            return self.surface.describe(ref)
        except LocatorAmbiguous as exc:
            raise self._escalate(StuckReason.LOCATOR_AMBIGUOUS, str(exc)) from exc
        except LocatorUnresolved as exc:
            raise self._escalate(StuckReason.LOCATOR_UNRESOLVED, str(exc)) from exc

    def _act(
        self,
        action: Any,
        *,
        kind: ActionType,
        ref: str | None,
        bundle: Any,
        literal: str | None,
    ) -> tuple[str, bool]:
        started = time.monotonic()
        try:
            outcome = self.surface.act(action)
        except PolicyViolation as exc:
            attempted = getattr(action, "url", None)
            return self._refused(exc, kind, attempted)
        except LocatorAmbiguous as exc:
            raise self._escalate(StuckReason.LOCATOR_AMBIGUOUS, str(exc)) from exc
        except LocatorUnresolved as exc:
            raise self._escalate(StuckReason.LOCATOR_UNRESOLVED, str(exc)) from exc
        except ActionTimeout as exc:
            raise self._escalate(StuckReason.STEP_TIMEOUT, str(exc)) from exc

        self._consecutive_blocks = 0
        self.transcript.actions.append(
            ActionRecord(
                seq=len(self.transcript.actions),
                action_kind=kind.value,
                ref_used=ref,
                bundle=bundle,
                literal_value=literal,
                tier_resolved=outcome.resolved_strategy,
                outcome_ok=outcome.ok,
                note=_describe_action(kind, bundle),
                obs_hash_before=self._last_hash,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
        self._event(
            EventKind.ACTION,
            action_kind=kind.value,
            tier=outcome.resolved_strategy,
            ref=ref,
        )
        strategy = outcome.resolved_strategy or "n/a"
        return f"Done. The control was found by {strategy}. Look to see the result.", False

    def _refused(
        self, exc: PolicyViolation, kind: ActionType, attempted: str | None = None
    ) -> tuple[str, bool]:
        """Name the rule, never the allowlist.

        The model is told the direction is closed and which rule closed it. It is not told
        what the rule permits, because a model given the shape of the boundary will spend
        its remaining steps probing the boundary.
        """
        self._consecutive_blocks += 1
        self._last_block = (exc.rule, kind)
        self._event(
            EventKind.POLICY_BLOCK,
            rule=exc.rule,
            action=kind.value,
            attempted=attempted,
        )

        if self._consecutive_blocks >= self.limits.max_consecutive_blocks:
            raise _Stop(
                PolicyBlockedResult(
                    rule=exc.rule,
                    attempted_action=kind,
                    step_index=len(self.transcript.actions),
                    evidence=self.evidence,
                ),
                DiscoveryStop.POLICY_BLOCKED,
            )
        return (
            f"That action was refused by policy (rule: {exc.rule}). This is final, not a "
            "transient error, and repeating it will fail identically. That direction is "
            "closed: take a different route, or call give_up.",
            True,
        )

    def _escalate(self, reason: StuckReason, detail: str) -> _Stop:
        """Invariant 4: ambiguity stops the run. It never guesses and never retries."""
        self._event(EventKind.VERIFICATION, escalated=reason.value, detail=detail)
        return _Stop(
            NeedsHumanResult(
                intervention_id=f"{self._run_id}-{reason.value}",
                reason=reason,
                step_index=len(self.transcript.actions),
                steps=self._traces(),
                evidence=self.evidence,
            ),
            DiscoveryStop.NEEDS_HUMAN,
        )

    # -- finish --------------------------------------------------------------
    def _finish(self, args: dict[str, Any], observation: Any) -> tuple[str, bool]:
        self._finish_attempts += 1
        verification: Verification = verify_finish(
            args,
            surface=self.surface,
            observation=observation,
            goal=self.goal,
            typed_values=self._typed_values,
        )
        self._event(
            EventKind.VERIFICATION,
            attempt=self._finish_attempts,
            ok=verification.ok,
            detail=verification.failure,
        )

        if verification.ok:
            assert verification.checkpoint is not None
            self.transcript.declared = DeclaredCapability(
                capability_name=str(args.get("capability_name") or "unnamed"),
                description=str(args.get("description") or ""),
                checkpoint=verification.checkpoint,
                inputs=verification.inputs,
                outputs=verification.output_specs,
            )
            for warning in verification.warnings:
                self._event(EventKind.VERIFICATION, warning=warning)
            raise _Stop(
                SuccessResult(
                    outputs=dict(verification.outputs),
                    steps=self._traces(),
                    evidence=self.evidence,
                    duration_ms=self._elapsed_ms(),
                ),
                DiscoveryStop.GOAL_REACHED,
            )

        failure = verification.failure or "the declaration could not be verified"
        if self._finish_attempts >= self.limits.max_finish_attempts:
            raise _Stop(
                FailureResult(
                    error_class=FailureClass.CHECKPOINT_FAILED,
                    step_index=len(self.transcript.actions),
                    action=ActionType.WAIT_FOR,
                    expected="a checkpoint that holds and outputs that extract",
                    observed=failure,
                    steps=self._traces(),
                    evidence=self.evidence,
                ),
                DiscoveryStop.ERROR,
            )
        remaining = self.limits.max_finish_attempts - self._finish_attempts
        return f"{failure} You may call finish again ({remaining} left).", True


def run_discovery(
    *,
    goal: str,
    surface: Any,
    client: ModelClient,
    evidence: EvidenceRef | Callable[[], EvidenceRef],
    model: str,
    surface_descriptor: SurfaceDescriptor,
    target: str | None = None,
    limits: DiscoveryLimits | None = None,
    tools: list[ToolSpec] | None = None,
    on_observation: Any | None = None,
) -> DiscoveryOutcome:
    """Run one discovery attempt. The RunResult is on `.result`, the transcript on `.transcript`."""
    return DiscoveryRun(
        goal=goal,
        surface=surface,
        client=client,
        evidence=evidence,
        model=model,
        surface_descriptor=surface_descriptor,
        target=target,
        limits=limits,
        tools=tools,
        on_observation=on_observation,
    ).run()
