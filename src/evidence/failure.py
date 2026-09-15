"""The failure files section 3.5 of the brief asks for, saved when a run does not succeed.

Three raw files and one that explains them, in `failure/`:

    dom.html        what the page really was
    aria.yaml       what the accessibility tree showed
    screenshot.png  what a person would have seen
    context.json    why that was not what the run expected

`context.json` matters most. A screenshot of an old back-office screen will not tell you which
locator tier was being tried, or that tier one matched three elements instead of one, and that
is usually what you need to know.

The folder is written for any result that is not a success, including business outcomes. A
business outcome is a correct answer, so `context.json` records the real `result_kind` and the
folder name should not be read as the verdict. See DECISIONS.md 0036.
"""
from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

STRICT: Final[ConfigDict] = ConfigDict(extra="forbid")


class TierProbe(BaseModel):
    """One locator tier and how many elements it matched when the step failed."""

    model_config = STRICT

    tier_index: int
    strategy: str
    matched: int | None = None
    container_matched: int | None = None
    note: str | None = None


class FailureContext(BaseModel):
    """context.json: what went wrong, in words."""

    model_config = STRICT

    result_kind: str
    step_index: int
    action: str | None = None
    expected: str | None = None
    observed: str | None = None
    url: str | None = None
    strategies_tried: list[TierProbe] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)


def probe_bundle(surface: Any, bundle: Any) -> list[TierProbe]:
    """Count how many elements each tier of a bundle matches right now, without waiting.

    Not `resolve`, which waits and raises on the first ambiguous tier, so it never shows what
    the other tiers would have matched. For a failure report you want all of them: primary
    matched 0, first fallback matched 3.
    """
    probes: list[TierProbe] = []
    if bundle is None:
        return probes
    prober = getattr(surface, "probe_tiers", None)
    if prober is None:
        return [
            TierProbe(tier_index=index, strategy=spec.strategy, note="surface cannot probe")
            for index, spec in enumerate([bundle.primary, *bundle.fallbacks])
        ]
    try:
        return list(prober(bundle))
    except Exception:  # noqa: BLE001
        # Writing up a failure must never raise on top of the failure itself.
        return [
            TierProbe(tier_index=index, strategy=spec.strategy, note="probe failed")
            for index, spec in enumerate([bundle.primary, *bundle.fallbacks])
        ]


def result_step(result: Any) -> int:
    """The step a result is about, or -1 if it names none.

    A failure has `step_index` and a business outcome has `detected_at_step`. Step 0 is a real
    step, so this must not use `or`, which used to turn 0 into -1.
    """
    for field in ("step_index", "detected_at_step"):
        value = getattr(result, field, None)
        if isinstance(value, int):
            return value
    return -1


def write_failure_artifacts(
    surface: Any,
    sink: Any,
    result: Any,
    *,
    step: Any = None,
    on_error: Any = None,
) -> None:
    """Write failure/ for any result that is not a success. Used by replay and discovery.

    One function so the two cannot drift apart. Arguments are duck-typed because discovery has
    no Capability or Step and should not have to make one up.
    """
    if sink is None or getattr(result, "kind", None) == "success":
        return
    writer = getattr(sink, "write_failure", None)
    if writer is None:
        return

    index = result_step(result)
    dom: str | None = None
    aria: str | None = None
    png: bytes | None = None
    url: str | None = None
    probes: list[TierProbe] = []
    try:
        observation = surface.observe()
        aria, png, url = observation.aria_yaml, observation.screenshot_png, observation.url
        snapshotter = getattr(surface, "dom_snapshot", None)
        if callable(snapshotter):
            dom = str(snapshotter())
        if step is not None and getattr(step, "target", None) is not None:
            probes = probe_bundle(surface, step.target)
    except Exception:  # noqa: BLE001
        # Writing up a failure must never raise on top of the failure itself.
        if on_error is not None:
            on_error()

    detail: dict[str, Any] = {}
    for field in ("error_class", "rule", "reason", "code", "intervention_id", "message"):
        value = getattr(result, field, None)
        if value is not None:
            detail[field] = getattr(value, "value", value)

    postcondition = getattr(step, "postcondition", None) if step else None
    writer(
        FailureContext(
            result_kind=str(getattr(result, "kind", "unknown")),
            step_index=index,
            action=getattr(getattr(step, "action", None), "value", None),
            expected=getattr(result, "expected", None)
            or (postcondition.description if postcondition else None),
            observed=getattr(result, "observed", None),
            url=url,
            strategies_tried=probes,
            detail=detail,
        ),
        dom=dom,
        aria=aria,
        png=png,
    )
