"""Compile a DiscoveryTranscript into a Capability.

A transcript is everything that happened, dead ends included. A capability is the path that
worked, with parameters so it can run again with different values. Most of the work is
deciding what to drop and what to turn into a parameter.

Locator bundles are copied as they are. They were checked against the live page at the moment
of the action, which is the only time they were known to be right. Rebuilding them now would
mean building them against a page that has moved on. See DECISIONS.md 0019.

Validation is not loosened. The Capability is built through the Pydantic model so every
validator runs, and a ValidationError is passed straight up. If a transcript cannot make a
valid capability, that failure should be seen.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from src.discovery.transcript import ActionRecord, DiscoveryStop, DiscoveryTranscript
from src.models.capability import (
    Assertion,
    Capability,
    ParamBinding,
    ParamSpec,
    Provenance,
    Signal,
    Step,
    ValueBinding,
    WaitSpec,
)
from src.models.common import ActionType, RiskClass, SignalKind
from src.models.policy import PolicyConfig

DEFAULT_WAIT_MS = 10_000
SLOW_STEP_MULTIPLE = 3
"""A step this many times slower than the median gets a longer timeout. Other steps do not."""

TEMPLATE_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class CompileFailure(StrEnum):
    """Why a transcript could not become a capability."""

    NOT_A_VERIFIED_FINISH = "not_a_verified_finish"
    NOTHING_SURVIVED_SELECTION = "nothing_survived_selection"
    INPUT_MATCHES_NO_LITERAL = "input_matches_no_literal"
    BINDING_IS_AMBIGUOUS = "binding_is_ambiguous"
    NO_POSTCONDITION_DERIVABLE = "no_postcondition_derivable"


@dataclass(frozen=True)
class CompileError:
    """A refusal to compile, with the reason a person needs to fix it."""

    reason: CompileFailure
    detail: str

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}"


@dataclass
class CompileReport:
    """What the compiler kept, dropped and bound. Never contains a recorded value.

    Values that were not turned into parameters are described by the field they were typed
    into, not by what they were, because a value the model forgot to declare is quite likely
    a member id.
    """

    raw_actions: int = 0
    kept: int = 0
    dropped: list[str] = field(default_factory=list)
    bound: dict[str, int] = field(default_factory=dict)
    unbound: list[str] = field(default_factory=list)
    outcomes: int = 0
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"steps kept       : {self.kept} of {self.raw_actions} recorded actions",
            f"parameters bound : {self.bound or 'none'}",
            f"literals unbound : {self.unbound or 'none'}",
            f"known outcomes   : {self.outcomes}",
        ]
        out += [f"dropped          : {d}" for d in self.dropped]
        out += [f"note             : {n}" for n in self.notes]
        return out


@dataclass(frozen=True)
class CompileOutcome:
    """The result of compiling.

    The plan was `compile(transcript, policy) -> Capability | CompileError`, but the CLI also
    prints a compile report and a plain union has nowhere to put it. So this wraps both.
    Only one of `capability` and `error` is ever set.
    """

    report: CompileReport
    capability: Capability | None = None
    error: CompileError | None = None

    @property
    def ok(self) -> bool:
        return self.capability is not None


def _control_name(action: ActionRecord) -> str | None:
    return action.bundle.recorded_accessible_name if action.bundle else None


def _select_steps(
    transcript: DiscoveryTranscript, report: CompileReport
) -> list[ActionRecord]:
    """Keep the actions that made progress, in order.

    look() and blocked actions never create an ActionRecord today, but they are filtered here
    anyway, so this does not quietly break if the loop changes.
    """
    ordered = transcript.actions_in_order()
    kept: list[ActionRecord] = []
    for index, action in enumerate(ordered):
        if action.action_kind not in {a.value for a in ActionType}:
            report.dropped.append(f"seq {action.seq}: {action.action_kind} is not an action")
            continue
        if not action.outcome_ok:
            report.dropped.append(f"seq {action.seq}: {action.action_kind} did not succeed")
            continue
        if _changed_nothing_then_retried(ordered, index):
            report.dropped.append(
                f"seq {action.seq}: {action.action_kind} changed nothing and was retried"
            )
            continue
        kept.append(action)
    return kept


def _changed_nothing_then_retried(ordered: list[ActionRecord], index: int) -> bool:
    """An action that changed nothing and was tried again straight away was a false start."""
    action = ordered[index]
    if action.obs_hash_before is None or action.obs_hash_after is None:
        return False
    if action.obs_hash_before != action.obs_hash_after:
        return False
    if index + 1 >= len(ordered):
        return False
    following = ordered[index + 1]
    return (
        following.action_kind == action.action_kind
        and _control_name(following) == _control_name(action)
    )


def _bind_parameters(
    inputs: list[ParamSpec], actions: list[ActionRecord], goal: str, report: CompileReport
) -> dict[str, str] | CompileError:
    """Work out which typed value each declared input stands for.

    The model types values but never says which parameter each one came from, so this has to
    work it out.

    An example on the ParamSpec wins. After that, the goal text is the best clue: a value that
    appears in the goal came from the caller, which makes it a parameter and not a fixed part of
    the flow. A value that is not in the goal is more likely something the model made up, like
    a nickname, so it stays a fixed value and is reported.

    If that still leaves a choice between two inputs, this refuses instead of guessing. A wrong
    guess hardwires the capability to the recorded run, and that shows up much later as a
    replay against the wrong record.
    """
    typed = [a.literal_value for a in actions if a.literal_value]
    distinct = list(dict.fromkeys(typed))
    bound: dict[str, str] = {}
    remaining = list(inputs)

    for spec in list(remaining):
        if spec.example and spec.example in distinct:
            bound[spec.name] = spec.example
            distinct.remove(spec.example)
            remaining.remove(spec)

    if remaining:
        from_goal = [value for value in distinct if value in goal]
        if not distinct:
            return CompileError(
                CompileFailure.INPUT_MATCHES_NO_LITERAL,
                f"input {remaining[0].name!r} was declared but no recorded step used a "
                "value. An input the flow never consumes is a false contract, and the "
                "schema would reject the artifact for it anyway",
            )
        if len(remaining) == 1 and len(from_goal) == 1:
            bound[remaining[0].name] = from_goal[0]
            distinct.remove(from_goal[0])
            remaining.clear()
        elif len(remaining) == 1 and not from_goal and len(distinct) == 1:
            bound[remaining[0].name] = distinct[0]
            distinct.clear()
            remaining.clear()

    if remaining:
        return CompileError(
            CompileFailure.BINDING_IS_AMBIGUOUS,
            f"cannot tell which recorded value each of these inputs stands for: "
            f"{sorted(s.name for s in remaining)}. {len(distinct)} literals are unmatched. "
            "Give the non sensitive ones an example so the mapping is stated rather than "
            "inferred",
        )

    for value in distinct:
        where = next((_control_name(a) for a in actions if a.literal_value == value), None)
        report.unbound.append(
            f"a value typed into {where!r}" if where else "a typed value"
        )
    report.bound = {
        name: sum(1 for a in actions if a.literal_value == value)
        for name, value in bound.items()
    }
    return bound


def _placeholder(text: str, bound: dict[str, str]) -> str:
    """Replace a bound value with its <param:name> placeholder wherever it appears in text.

    Two free-text fields end up in the capability and both can contain a recorded value: step
    descriptions and the goal. A member id in either would be pii written to disk.
    """
    for name, value in bound.items():
        if value:
            text = text.replace(value, f"<param:{name}>")
    return text


def _observation_urls(transcript: DiscoveryTranscript) -> list[str]:
    return [
        str(e.payload["url"])
        for e in transcript.events
        if e.kind.value == "observation" and e.payload.get("url")
    ]


def _templated(text: str, bound: dict[str, str]) -> str:
    """Replace any bound literal in a URL with its {parameter} template."""
    for name, value in bound.items():
        if value and value in text:
            text = text.replace(value, "{" + name + "}")
    return text


def _relative_to_surface(url: str, base_url: str) -> str:
    """Strip the recorded host and keep the path.

    A navigate step holding "http://localhost:8080/" would only work on the machine it was
    recorded on. The same app on another host, port or tenant subdomain is exactly what
    capabilities need to handle. The host belongs in the surface descriptor, which can be
    changed, and the step keeps only the path. See DECISIONS.md 0028.
    """
    base = base_url.rstrip("/")
    if base and url.startswith(base):
        return url[len(base):] or "/"
    return url


def _wait_for(action: ActionRecord, median_ms: int) -> WaitSpec:
    """A load wait, with a longer timeout only for a step that was slow when recorded."""
    timeout = DEFAULT_WAIT_MS
    if median_ms and action.duration_ms > max(median_ms * SLOW_STEP_MULTIPLE, 250):
        timeout = max(DEFAULT_WAIT_MS, action.duration_ms * SLOW_STEP_MULTIPLE)
    return WaitSpec(condition="load", timeout_ms=timeout)


def _risk_of(action: ActionRecord, policy: PolicyConfig) -> RiskClass:
    name = _control_name(action)
    if action.action_kind == ActionType.CLICK.value and name in policy.risky_control_names:
        return RiskClass.RISKY_IRREVERSIBLE
    return RiskClass.SAFE_REVERSIBLE


def _postcondition_for(
    action: ActionRecord, transcript: DiscoveryTranscript
) -> Assertion | CompileError:
    """Build a check that an irreversible action did what it should.

    The transcript keeps URLs but not page text, so the only check possible afterwards is
    where the action landed. If it landed nowhere new there is nothing to check, and compiling
    fails. That is correct: making up a postcondition would defeat the point of requiring one.
    """
    urls = _observation_urls(transcript)
    landed = urls[-1] if urls else None
    before = urls[-2] if len(urls) > 1 else None
    if not landed or landed == before:
        return CompileError(
            CompileFailure.NO_POSTCONDITION_DERIVABLE,
            f"step {action.seq} is irreversible and needs a postcondition, but the page did "
            "not change after it, so nothing in the transcript proves what it did",
        )
    path = landed.split("://", 1)[-1]
    path = path[path.index("/") :] if "/" in path else "/"
    return Assertion(
        signal=Signal(kind=SignalKind.URL_MATCHES, url_pattern=f"^{re.escape(path)}$"),
        description=f"the irreversible action landed on {path}",
    )


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "capability"


def compile_capability(
    transcript: DiscoveryTranscript, policy: PolicyConfig
) -> CompileOutcome:
    """Turn a verified discovery run into a reusable capability."""
    report = CompileReport(raw_actions=len(transcript.actions))

    # only a verified finish can be compiled
    if transcript.stop_reason is not DiscoveryStop.GOAL_REACHED:
        return CompileOutcome(
            report,
            error=CompileError(
                CompileFailure.NOT_A_VERIFIED_FINISH,
                f"the run stopped with {transcript.stop_reason}, not a verified finish. A run "
                "that stalled, gave up or was blocked has no capability in it",
            ),
        )
    declared = transcript.declared
    if declared is None:
        return CompileOutcome(
            report,
            error=CompileError(
                CompileFailure.NOT_A_VERIFIED_FINISH,
                "the run reached its goal but declared nothing to compile",
            ),
        )

    # pick the steps to keep
    kept = _select_steps(transcript, report)
    report.kept = len(kept)
    if not kept:
        return CompileOutcome(
            report,
            error=CompileError(
                CompileFailure.NOTHING_SURVIVED_SELECTION,
                "every recorded action was dropped, so there is no flow left to record",
            ),
        )

    # match typed values to declared inputs
    bound = _bind_parameters(list(declared.inputs), kept, transcript.goal, report)
    if isinstance(bound, CompileError):
        return CompileOutcome(report, error=bound)
    by_value = {value: name for name, value in bound.items()}

    durations = sorted(a.duration_ms for a in kept)
    median = durations[len(durations) // 2] if durations else 0

    steps: list[Step] = []
    for index, action in enumerate(kept):
        kind = ActionType(action.action_kind)
        risk = _risk_of(action, policy)

        value: ValueBinding | None = None
        if action.literal_value is not None:
            name = by_value.get(action.literal_value)
            if name is None:
                from src.models.capability import LiteralBinding

                value = LiteralBinding(value=action.literal_value)
            else:
                value = ParamBinding(param=name)

        url = None
        if kind is ActionType.NAVIGATE:
            urls = _observation_urls(transcript)
            raw = urls[0] if urls else transcript.surface.base_url
            url = _templated(_relative_to_surface(raw, transcript.surface.base_url), bound)

        postcondition: Assertion | None = None
        if risk is RiskClass.RISKY_IRREVERSIBLE:
            derived = _postcondition_for(action, transcript)
            if isinstance(derived, CompileError):
                return CompileOutcome(report, error=derived)
            postcondition = derived

        steps.append(
            Step(
                index=index,
                action=kind,
                description=_placeholder(action.note or kind.value, bound),
                # copied as recorded, never rebuilt
                target=action.bundle,
                url=url,
                value=value,
                risk=risk,
                wait=_wait_for(action, median),
                postcondition=postcondition,
            )
        )

    # no outcomes: this run never saw one (DECISIONS.md 0021)
    report.notes.append(
        "known_outcomes is empty: this run never met a not-found or a permission denial, so "
        "declaring one would be fabrication. Add them from a later recording or by hand"
    )

    surface = transcript.surface.model_copy(
        update={"entry_path": _templated(transcript.surface.entry_path, bound)}
    )

    # built through the model, so every validator runs
    capability = Capability(
        capability_id=_slug(declared.capability_name),
        version="1.0.0",
        name=declared.capability_name,
        description=declared.description,
        surface=surface,
        inputs=list(declared.inputs),
        outputs=list(declared.outputs),
        steps=steps,
        checkpoint=declared.checkpoint,
        known_outcomes=[],
        recoveries=[],
        provenance=Provenance(
            discovered_by_model=transcript.model,
            discovery_run_id=transcript.run_id,
            recorded_at=transcript.events[-1].at if transcript.events else _now(),
            goal_text=_placeholder(transcript.goal, bound),
            raw_step_count=len(transcript.actions),
            redaction_policy_version="1.0",
        ),
    )
    return CompileOutcome(report, capability=capability)


def _now() -> Any:
    from datetime import UTC, datetime

    return datetime.now(UTC)
