"""Pydantic schemas: Capability, LocatorBundle, RunResult union.

The public surface of the model package. Import from here rather than reaching into the
individual modules, so the internal file layout stays free to change.
"""
from __future__ import annotations

from src.models.capability import (
    SEMVER_PATTERN,
    SLUG_PATTERN,
    SNAKE_PATTERN,
    Assertion,
    BusinessOutcomeSpec,
    Capability,
    ExtractionSpec,
    InsertedStep,
    LiteralBinding,
    OutputSpec,
    ParamBinding,
    ParamSpec,
    Provenance,
    RecoveryRule,
    Signal,
    Step,
    StepPatch,
    SurfaceDescriptor,
    SurfaceFingerprint,
    ValueBinding,
    VariantOverride,
    WaitSpec,
)
from src.models.common import (
    ActionType,
    ApprovalStatus,
    FailureClass,
    Rect,
    RecoveryAction,
    RegexPattern,
    RiskClass,
    Sensitivity,
    SignalKind,
    StuckReason,
    SurfaceKind,
    ValueType,
)
from src.models.locator import (
    ContainerOrdinalLocator,
    ContainerRef,
    CssFallbackLocator,
    LabelRelationLocator,
    Locator,
    LocatorBundle,
    RoleNameLocator,
    TextRelationLocator,
)
from src.models.policy import PolicyConfig
from src.models.results import (
    EXIT_CODES,
    BusinessOutcomeResult,
    EvidenceRef,
    FailureResult,
    NeedsHumanResult,
    PolicyBlockedResult,
    RunResult,
    StepTrace,
    SuccessResult,
)

__all__ = [
    # enums and value types
    "ActionType",
    "ApprovalStatus",
    "FailureClass",
    "Rect",
    "RecoveryAction",
    "RegexPattern",
    "RiskClass",
    "Sensitivity",
    "SignalKind",
    "StuckReason",
    "SurfaceKind",
    "ValueType",
    # locators
    "ContainerOrdinalLocator",
    "ContainerRef",
    "CssFallbackLocator",
    "LabelRelationLocator",
    "Locator",
    "LocatorBundle",
    "RoleNameLocator",
    "TextRelationLocator",
    # capability artifact
    "Assertion",
    "BusinessOutcomeSpec",
    "Capability",
    "ExtractionSpec",
    "InsertedStep",
    "LiteralBinding",
    "OutputSpec",
    "ParamBinding",
    "ParamSpec",
    "Provenance",
    "RecoveryRule",
    "Signal",
    "Step",
    "StepPatch",
    "SurfaceDescriptor",
    "SurfaceFingerprint",
    "ValueBinding",
    "VariantOverride",
    "WaitSpec",
    # results
    "EXIT_CODES",
    "BusinessOutcomeResult",
    "EvidenceRef",
    "FailureResult",
    "NeedsHumanResult",
    "PolicyBlockedResult",
    "RunResult",
    "StepTrace",
    "SuccessResult",
    # policy
    "PolicyConfig",
    # patterns
    "SEMVER_PATTERN",
    "SLUG_PATTERN",
    "SNAKE_PATTERN",
]
