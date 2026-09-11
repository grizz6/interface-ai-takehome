"""Check what the model claimed when it called finish, against the page it is standing on.

A model that says it is done is making a claim, not reporting a fact. Two of those claims are
load bearing and both are cheap to check right now and expensive to discover later.

A checkpoint that has never once held is a guess. It will be asserted on every future replay
of this capability, and if it does not hold on the very screen it was written for, it will
never hold anywhere.

An output that cannot be extracted from the page it was declared on is broken before replay
has run once. Declaring it and finding out in production is the failure this whole project
exists to avoid.

So both are executed here, against the live surface, before any success is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from src.models.capability import (
    Assertion,
    ExtractionSpec,
    OutputSpec,
    ParamSpec,
    Signal,
)
from src.surface.extraction import extract_value
from src.surface.protocol import LocatorAmbiguous, LocatorUnresolved


@dataclass(frozen=True)
class Verification:
    """The outcome of checking a finish payload. `failure` is what the model is told."""

    ok: bool
    failure: str | None = None
    checkpoint: Assertion | None = None
    inputs: list[ParamSpec] = field(default_factory=list)
    output_specs: list[OutputSpec] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _fail(message: str) -> Verification:
    return Verification(ok=False, failure=message)


def verify_finish(
    payload: dict[str, Any],
    *,
    surface: Any,
    observation: Any,
    goal: str,
    typed_values: list[str],
) -> Verification:
    """Run every check in order, stopping at the first that fails."""
    # (a) the checkpoint must be a well formed Signal, which also compiles any regex
    try:
        signal = Signal(**dict(payload.get("checkpoint") or {}))
    except ValidationError as exc:
        return _fail(f"The checkpoint is not a valid signal: {exc}")
    checkpoint = Assertion(
        signal=signal,
        description=str(payload.get("description") or "the goal was reached"),
    )

    # (b) and it must actually hold, right now, on the page you are standing on
    try:
        holds = bool(surface.evaluate(signal))
    except (LocatorAmbiguous, LocatorUnresolved) as exc:
        return _fail(f"The checkpoint could not be evaluated: {exc}")
    if not holds:
        return _fail(
            "The checkpoint you declared does not hold on the current page. It will be "
            "asserted on every future run of this capability, so it has to be true here "
            "first. Look at the screen again and choose something that is actually on it."
        )

    # (c) every declared output must extract, here, now
    try:
        inputs = [ParamSpec(**item) for item in payload.get("inputs") or []]
    except ValidationError as exc:
        return _fail(f"An input parameter is not valid: {exc}")

    specs: list[OutputSpec] = []
    values: dict[str, str] = {}
    for raw in payload.get("outputs") or []:
        built = _build_output(raw, surface=surface, observation=observation)
        if isinstance(built, str):
            return _fail(built)
        specs.append(built)

    for spec in specs:
        try:
            value = extract_value(surface, spec.extraction)
        except (LocatorAmbiguous, LocatorUnresolved) as exc:
            return _fail(
                f"Output {spec.name!r} could not be read from the page: {exc}. Point at the "
                "element that actually holds the value."
            )
        if value is None:
            if spec.extraction.required:
                return _fail(
                    f"Output {spec.name!r} is declared required but nothing could be read "
                    f"from the element you pointed at, parsed as {spec.extraction.parse}. "
                    "Either it is the wrong element or the wrong parse type."
                )
            continue
        values[spec.name] = value

    # (d) inputs that should have been declared. A warning, never a failure.
    warnings = _unparameterized_values(goal, typed_values, inputs)

    return Verification(
        ok=True,
        checkpoint=checkpoint,
        inputs=inputs,
        output_specs=specs,
        outputs=values,
        warnings=warnings,
    )


def _build_output(raw: dict[str, Any], *, surface: Any, observation: Any) -> OutputSpec | str:
    """Turn a declared output into an OutputSpec, or return the message explaining why not."""
    extraction = dict(raw.get("extraction") or {})
    ref = str(extraction.pop("ref", "") or "")
    name = raw.get("name", "<unnamed>")

    if not ref:
        return f"Output {name!r} does not say which element holds it. Give a ref."
    if observation.by_ref(ref) is None:
        return (
            f"Output {name!r} points at ref {ref!r}, which is not in the current snapshot. "
            "Refs are only valid for the snapshot you were last shown."
        )
    try:
        bundle = surface.describe(ref)
    except (LocatorAmbiguous, LocatorUnresolved) as exc:
        return f"Output {name!r} points at an element that cannot be located durably: {exc}"

    try:
        spec = ExtractionSpec(locator=bundle, **extraction)
        return OutputSpec(**{**raw, "extraction": spec})
    except ValidationError as exc:
        return f"Output {name!r} is not valid: {exc}"


def _unparameterized_values(
    goal: str, typed_values: list[str], inputs: list[ParamSpec]
) -> list[str]:
    """Values taken from the goal and typed into the page that were never declared.

    A warning rather than a failure on purpose. The mapping from a typed literal back to a
    parameter is a guess: the model types a value, it does not tell us which parameter it
    came from. Guessing wrong and failing a good run would be worse than letting a reviewer
    see the note.
    """
    from_goal = sorted({value for value in typed_values if value and value in goal})
    if not from_goal:
        return []
    if len(inputs) >= len(from_goal):
        return []
    declared = ", ".join(p.name for p in inputs) or "nothing"
    return [
        "These values came from the goal text and were typed into the page: "
        + ", ".join(repr(v) for v in from_goal)
        + f". Only {declared} was declared as an input, so this capability may be hardwired "
        "to the values it was recorded with."
    ]
