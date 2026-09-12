"""The richer signal section 3.5 asks for, captured at the step that went wrong.

Three artifacts and an explanation, in `failure/`:

    dom.html        what the page actually was
    aria.yaml       what the page looked like to the accessibility tree
    screenshot.png  what it looked like to a person
    context.json    why that was not what was expected

The first three are raw. `context.json` is the one that explains, and it is the one worth
getting right: a screenshot of a legacy back office screen tells you almost nothing about
which of five locator tiers was being tried, or that tier one matched three elements when it
should have matched one. That is the sentence a person actually needs, and no picture of the
page contains it.

The directory is written on any result that is not success, business outcomes included. A
business outcome is a correct answer rather than a fault, so `context.json` records the real
`result_kind` and the folder name is not read as a verdict. See DECISIONS.md 0036.
"""
from __future__ import annotations

from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

STRICT: Final[ConfigDict] = ConfigDict(extra="forbid")


class TierProbe(BaseModel):
    """One locator tier and what it actually matched when the step failed."""

    model_config = STRICT

    tier_index: int
    strategy: str
    matched: int | None = None
    container_matched: int | None = None
    note: str | None = None


class FailureContext(BaseModel):
    """context.json. The explanation, not the raw material."""

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
    """Count what each tier of a bundle matches, right now, with no waiting.

    Deliberately not `resolve`. Resolve waits, and raises on the first tier that is ambiguous,
    so it can never tell you what the other tiers would have done. For a post mortem the
    interesting fact is usually the whole row: primary matched 0, first fallback matched 3.
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
        # A post mortem must never raise on top of the failure it is describing.
        return [
            TierProbe(tier_index=index, strategy=spec.strategy, note="probe failed")
            for index, spec in enumerate([bundle.primary, *bundle.fallbacks])
        ]


def write_failure_artifacts(
    surface: Any,
    sink: Any,
    result: Any,
    *,
    step: Any = None,
    on_error: Any = None,
) -> None:
    """Write failure/ for any result that is not success. Used by replay and by discovery.

    One function so the two subsystems cannot drift into producing different post mortems.
    Everything it needs is duck typed, because discovery has no Capability and no Step and
    should not have to invent one to be described.
    """
    if sink is None or getattr(result, "kind", None) == "success":
        return
    writer = getattr(sink, "write_failure", None)
    if writer is None:
        return

    index = int(getattr(result, "step_index", -1) or -1)
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
        # A post mortem must never raise on top of the failure it is describing.
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
