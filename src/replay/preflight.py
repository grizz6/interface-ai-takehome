"""Checks that run before a browser is touched, and one that runs the moment it is.

Everything here answers the same question: is this capability, with these parameters, safe to
run unattended against this surface right now. A no is cheap here and expensive four steps in.
"""
from __future__ import annotations

from typing import Any

from src.models.capability import Capability
from src.models.common import ActionType, ApprovalStatus, FailureClass, ValueType
from src.models.results import EvidenceRef, FailureResult

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
    """A draft has replayed zero times. Nothing that has replayed zero times runs alone."""
    if capability.status is ApprovalStatus.APPROVED or allow_draft:
        return None
    return _failure(
        evidence,
        "an approved capability, or --allow-draft for development",
        f"{capability.capability_id} is still draft. It has been recorded once and replayed "
        "never, so nothing has yet shown that its locators resolve on a fresh load",
    )


def check_fingerprint(
    capability: Capability, surface: Any, evidence: EvidenceRef
) -> FailureResult | None:
    """Is this the application the capability was recorded against.

    Only the parts of the fingerprint that were actually recorded are checked. A recorder that
    captured nothing gives nothing to compare, and inventing a comparison would be worse than
    admitting the check is thin.
    """
    fingerprint = capability.surface.fingerprint
    observation = surface.observe()

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
