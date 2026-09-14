"""Checks that run before the browser does anything, plus the fingerprint check right after.

They all ask whether this capability, with these parameters, is safe to run against this app
right now. Finding out no here is cheap. Finding out four steps in is not.
"""
from __future__ import annotations

from typing import Any

from src.models.capability import Capability
from src.models.common import ActionType, ApprovalStatus, FailureClass, ValueType
from src.models.results import EvidenceRef, FailureResult
from src.surface.actions import NavigateAction
from src.surface.protocol import ActionTimeout, PolicyViolation

COERCERS: dict[ValueType, Any] = {
    ValueType.STRING: str,
    ValueType.INTEGER: int,
    ValueType.DECIMAL: float,
    ValueType.CURRENCY: float,
    ValueType.BOOLEAN: bool,
    ValueType.DATE: str,
}


def _failure(evidence: EvidenceRef, expected: str, observed: str,
             error_class: FailureClass = FailureClass.INTERNAL) -> FailureResult:
    return FailureResult(
        error_class=error_class,
        step_index=-1,
        action=ActionType.WAIT_FOR,
        expected=expected,
        observed=observed,
        steps=[],
        evidence=evidence,
    )


def check_parameters(
    capability: Capability, params: dict[str, Any], evidence: EvidenceRef
) -> dict[str, Any] | FailureResult:
    """Required present, values coercible to the declared type, patterns satisfied."""
    import re

    resolved: dict[str, Any] = {}
    declared = {spec.name: spec for spec in capability.inputs}

    unknown = sorted(set(params) - set(declared))
    if unknown:
        return _failure(
            evidence,
            f"only the declared inputs {sorted(declared)}",
            f"unknown parameters were supplied: {unknown}",
        )

    for name, spec in declared.items():
        if name not in params:
            if spec.required:
                return _failure(
                    evidence,
                    f"a value for required input {name!r} ({spec.type})",
                    "it was not supplied",
                )
            continue
        raw = params[name]
        try:
            value = COERCERS[spec.type](raw)
        except (TypeError, ValueError):
            return _failure(
                evidence,
                f"input {name!r} coercible to {spec.type}",
                f"a value of type {type(raw).__name__} that will not convert",
            )
        if spec.pattern and not re.search(spec.pattern, str(raw)):
            return _failure(
                evidence,
                f"input {name!r} matching {spec.pattern!r}",
                f"a value that does not match (length {len(str(raw))})",
            )
        resolved[name] = str(raw) if spec.type is ValueType.STRING else value
    return resolved


def check_approval(
    capability: Capability, allow_draft: bool, evidence: EvidenceRef
) -> FailureResult | None:
    """Refuse a draft unless --allow-draft was passed. A draft has never been replayed."""
    if capability.status is ApprovalStatus.APPROVED or allow_draft:
        return None
    return _failure(
        evidence,
        "an approved capability, or --allow-draft for development",
        f"{capability.capability_id} is still draft. It has been recorded once and replayed "
        "never, so nothing has yet shown that its locators resolve on a fresh load",
    )


def check_fingerprint(
    capability: Capability, surface: Any, evidence: EvidenceRef,
    params: dict[str, Any] | None = None,
) -> FailureResult | None:
    """Check this is the app the capability was recorded on.

    Only the parts of the fingerprint that were recorded are checked. If nothing was recorded
    there is nothing to compare.

    The first page has to load before comparing. A new browser sits on about:blank with an
    empty title, so checking before navigating failed every capability with a fingerprint and
    passed only the ones without. See DECISIONS.md 0034.
    """
    params = params or {}
    fingerprint = capability.surface.fingerprint
    if not (fingerprint.title or fingerprint.brand_text or fingerprint.landmark_signals):
        return None

    entry = capability.surface.base_url.rstrip("/") + "/" + capability.surface.entry_path.lstrip("/")
    for name, value in params.items():
        entry = entry.replace("{" + name + "}", str(value))
    try:
        surface.act(NavigateAction(url=entry))
    except (ActionTimeout, PolicyViolation) as exc:
        return _failure(
            evidence,
            f"the entry screen at {entry} to load so the fingerprint can be compared",
            str(exc),
            FailureClass.SURFACE_UNAVAILABLE,
        )
    observation = surface.observe()
    drift = _drift(capability, surface, observation, evidence)
    if drift is None:
        return None

    # A declared business outcome is not drift. A restricted or unknown member lands on a page
    # with a different title, and treating that as "the app changed" stopped the run before
    # step 0 could report the outcome. Only outcomes checked at step 0 count, so any other
    # mismatch still stops the run here. See DECISIONS.md 0045.
    for outcome in capability.known_outcomes:
        if outcome.check_after_step in (None, 0) and surface.evaluate(outcome.detect):
            return None
    return drift


def _drift(
    capability: Capability, surface: Any, observation: Any, evidence: EvidenceRef
) -> FailureResult | None:
    fingerprint = capability.surface.fingerprint
    if fingerprint.title and fingerprint.title != observation.title:
        return _failure(
            evidence,
            f"page title {fingerprint.title!r}",
            f"title {observation.title!r}. The surface has drifted from what was recorded",
            FailureClass.SURFACE_UNAVAILABLE,
        )
    if fingerprint.brand_text and fingerprint.brand_text not in observation.aria_yaml:
        return _failure(
            evidence,
            f"brand text {fingerprint.brand_text!r} somewhere on the entry screen",
            "it is not present. The surface has drifted from what was recorded",
            FailureClass.SURFACE_UNAVAILABLE,
        )
    for signal in fingerprint.landmark_signals:
        if not surface.evaluate(signal):
            return _failure(
                evidence,
                f"landmark signal {signal.kind} to hold on the entry screen",
                "it does not. The surface has drifted from what was recorded",
                FailureClass.SURFACE_UNAVAILABLE,
            )
    return None
