"""The capability: a saved flow, and everything needed to run it again.

Discovery produces one, a person reviews it, and replay runs it with no model. Replay can be
deterministic because everything it needs is written down here.
"""
from __future__ import annotations

import re
from datetime import datetime
from collections.abc import Mapping
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from src.models.common import (
    STRICT,
    ActionType,
    RegexPattern,
    ApprovalStatus,
    RecoveryAction,
    RiskClass,
    Sensitivity,
    SignalKind,
    SurfaceKind,
    ValueType,
)
from src.models.locator import LocatorBundle

SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"
SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
SNAKE_PATTERN = r"^[a-z][a-z0-9_]*$"
_TEMPLATE_RE = re.compile(r"\{([^{}]*)\}")


class Signal(BaseModel):
    """Something you can check on the screen.

    Checkpoints, business outcomes, recovery triggers and waits all use this one type, so
    "how do we know we got there" is written the same way everywhere.
    """

    model_config = STRICT

    kind: SignalKind
    text: str | None = None
    pattern: RegexPattern = None
    url_pattern: RegexPattern = None
    locator: LocatorBundle | None = None
    aria_template: str | None = Field(
        default=None,
        description="Partial aria snapshot in Playwright YAML form, for aria_matches.",
    )
    frame_path: list[str] = Field(default_factory=list)
    case_sensitive: bool = False

    def _populated(self, *names: str) -> list[str]:
        return [name for name in names if getattr(self, name) is not None]

    @model_validator(mode="after")
    def _aria_template_is_parseable(self) -> Signal:
        """Checks YAML syntax only. DECISIONS.md 0005 explains what it cannot check."""
        if self.aria_template is None:
            return self
        try:
            yaml.safe_load(self.aria_template)
        except yaml.YAMLError as exc:
            raise ValueError(
                "aria_template is not parseable as YAML, so the artifact was rejected at "
                f"record time rather than at match time: {exc}. This checks SYNTAX ONLY. "
                "A template that parses cleanly can still describe a shape that no screen "
                "will ever have, and that is only discovered when replay tries to match it"
            ) from exc
        return self

    @model_validator(mode="after")
    def _fields_match_kind(self) -> Signal:
        text_kinds = {SignalKind.TEXT_PRESENT, SignalKind.TEXT_ABSENT}

        if self.kind in text_kinds:
            if self.text is None and self.pattern is None:
                raise ValueError(f"signal {self.kind} requires text or pattern")
            extra = self._populated("url_pattern", "locator", "aria_template")
        elif self.kind is SignalKind.URL_MATCHES:
            if self.url_pattern is None:
                raise ValueError(f"signal {self.kind} requires url_pattern")
            extra = self._populated("text", "pattern", "locator", "aria_template")
        elif self.kind is SignalKind.ARIA_MATCHES:
            if self.aria_template is None:
                raise ValueError(f"signal {self.kind} requires aria_template")
            extra = self._populated("text", "pattern", "url_pattern", "locator")
        else:
            if self.locator is None:
                raise ValueError(f"signal {self.kind} requires locator")
            extra = self._populated("text", "pattern", "url_pattern", "aria_template")

        if extra:
            raise ValueError(
                f"signal {self.kind} must not populate " + ", ".join(sorted(extra))
            )
        return self


class Assertion(BaseModel):
    """A signal plus a sentence saying what it checks, for whoever reads the failure."""

    model_config = STRICT

    signal: Signal
    description: str
    timeout_ms: int = 5000


class WaitSpec(BaseModel):
    """What replay waits for before treating a step as done."""

    model_config = STRICT

    condition: Literal["load", "network_idle", "signal", "fixed"]
    signal: Signal | None = None
    timeout_ms: int = 10000
    poll_ms: int = 250
    retry_on_timeout: int = 0

    @model_validator(mode="after")
    def _signal_condition_has_signal(self) -> WaitSpec:
        if self.condition == "signal" and self.signal is None:
            raise ValueError('wait condition "signal" requires signal')
        return self


class ParamBinding(BaseModel):
    """Use the value of a parameter the caller passes in."""

    model_config = STRICT

    source: Literal["param"] = "param"
    param: str


class LiteralBinding(BaseModel):
    """A fixed value saved in the capability. Never used for anything sensitive."""

    model_config = STRICT

    source: Literal["literal"] = "literal"
    value: str


ValueBinding = Annotated[ParamBinding | LiteralBinding, Field(discriminator="source")]


class Step(BaseModel):
    """One recorded action, with everything replay needs to perform and verify it."""

    model_config = STRICT

    index: int
    description: str = Field(description="Written for a human reviewer, not for a parser.")
    action: ActionType
    target: LocatorBundle | None = None
    url: str | None = Field(default=None, description="navigate only; may contain {param} templates")
    value: ValueBinding | None = None
    risk: RiskClass
    wait: WaitSpec
    postcondition: Assertion | None = None

    @model_validator(mode="after")
    def _shape_matches_action(self) -> Step:
        if self.action is ActionType.NAVIGATE:
            if self.url is None:
                raise ValueError("navigate step requires url")
            if self.target is not None:
                raise ValueError("navigate step must not carry a target")
        if self.action is ActionType.CLICK and self.target is None:
            raise ValueError("click step requires target")
        if self.action in (ActionType.TYPE, ActionType.SELECT):
            if self.target is None:
                raise ValueError(f"{self.action} step requires target")
            if self.value is None:
                raise ValueError(f"{self.action} step requires value")
        return self

    @model_validator(mode="after")
    def _irreversible_step_proves_itself(self) -> Step:
        if self.risk is RiskClass.RISKY_IRREVERSIBLE and self.postcondition is None:
            raise ValueError(
                "risky_irreversible step requires a postcondition: "
                "an irreversible action must prove what it did"
            )
        return self


class ParamSpec(BaseModel):
    """One typed input the caller passes each time the capability runs."""

    model_config = STRICT

    name: str = Field(pattern=SNAKE_PATTERN)
    type: ValueType
    required: bool = True
    description: str
    example: str | None = None
    sensitivity: Sensitivity = Sensitivity.NONE
    pattern: str | None = None

    @model_validator(mode="after")
    def _sensitive_params_carry_no_example(self) -> ParamSpec:
        if self.sensitivity is not Sensitivity.NONE and self.example is not None:
            raise ValueError(
                f"param {self.name!r} is marked {self.sensitivity} and must not carry an "
                "example: the schema itself refuses to hold a sensitive value"
            )
        return self


class ExtractionSpec(BaseModel):
    """How to read one value off the screen.

    Declared on an output rather than run as a step. DECISIONS.md 0003 says why.
    """

    model_config = STRICT

    locator: LocatorBundle
    source: Literal["text", "value", "attribute"]
    attribute: str | None = None
    parse: Literal["raw", "currency", "integer", "decimal", "date"]
    strip_pattern: RegexPattern = None
    required: bool = True

    @model_validator(mode="after")
    def _attribute_source_names_attribute(self) -> ExtractionSpec:
        if self.source == "attribute" and self.attribute is None:
            raise ValueError('extraction source "attribute" requires attribute')
        return self


class OutputSpec(BaseModel):
    """One typed value the caller gets back."""

    model_config = STRICT

    name: str
    type: ValueType
    description: str
    extraction: ExtractionSpec
    sensitivity: Sensitivity = Sensitivity.NONE


class BusinessOutcomeSpec(BaseModel):
    """An expected answer from the app, declared up front so replay can return it.

    These are results, never exceptions. "No such member" is an answer, not a crash, and
    mixing the two up is the mistake the brief warns about.
    """

    model_config = STRICT

    code: str = Field(pattern=SNAKE_PATTERN)
    description: str
    detect: Signal
    check_after_step: int | None = Field(
        default=None, description="None means check after every step"
    )
    terminal: bool = True
    partial_outputs: list[str] = Field(default_factory=list)


class ParamDescriptor(BaseModel):
    """A parameter's name and sensitivity, never its value.

    Used wherever a file on disk needs to list a run's inputs. It is built from the declared
    inputs, not from the values passed in, so there is no way for a value to end up in it.
    See DECISIONS.md 0033.
    """

    model_config = STRICT

    name: str
    sensitivity: Sensitivity
    required: bool
    supplied: bool


class RecoveryRule(BaseModel):
    """A problem the run can fix itself, and how.

    A recovery is never a result of its own. It is listed in `recoveries_applied` on a
    success, so a run that needed three retries still looks different from one that did not.
    """

    model_config = STRICT

    name: str
    detect: Signal
    action: RecoveryAction
    action_target: LocatorBundle | None = None
    max_attempts: int = 2
    applies_to_steps: list[int] | None = None

    @model_validator(mode="after")
    def _dismiss_needs_something_to_dismiss(self) -> RecoveryRule:
        if self.action is RecoveryAction.DISMISS and self.action_target is None:
            raise ValueError('recovery action "dismiss" requires action_target')
        return self


class SurfaceFingerprint(BaseModel):
    """What the app looked like when the flow was recorded.

    Used to notice when the app has changed or is a different tenant. If it no longer
    matches, the capability might still work, but it should not just be trusted.
    """

    model_config = STRICT

    title: str | None = None
    brand_text: str | None = None
    landmark_signals: list[Signal] = Field(default_factory=list)
    aria_template: str | None = Field(
        default=None,
        description="Aria snapshot of the landmark chrome as recorded, for structural drift.",
    )
    captured_at: datetime


class SurfaceDescriptor(BaseModel):
    """Which application, which tenant variant, and where the flow starts."""

    model_config = STRICT

    kind: SurfaceKind
    app_id: str
    variant_id: str | None = None
    base_url: str
    entry_path: str
    fingerprint: SurfaceFingerprint


class StepPatch(BaseModel):
    """A partial Step, applied over a base step for one tenant variant.

    Every field is optional. A patch that sets nothing is legal and means no change.
    """

    model_config = STRICT

    index: int | None = None
    description: str | None = None
    action: ActionType | None = None
    target: LocatorBundle | None = None
    url: str | None = None
    value: ValueBinding | None = None
    risk: RiskClass | None = None
    wait: WaitSpec | None = None
    postcondition: Assertion | None = None


class InsertedStep(BaseModel):
    """A step one tenant needs that the base flow does not, like an extra confirmation."""

    model_config = STRICT

    after_index: int
    step: Step


class VariantOverride(BaseModel):
    """How one tenant differs from the base recording.

    A second tenant on the same vendor product should be a small patch, not a new recording.
    """

    model_config = STRICT

    variant_id: str
    description: str
    step_overrides: dict[int, StepPatch] = Field(default_factory=dict)
    inserted_steps: list[InsertedStep] = Field(default_factory=list)
    output_overrides: dict[str, ExtractionSpec] = Field(default_factory=dict)
    outcome_overrides: dict[str, Signal] = Field(default_factory=dict)


class Provenance(BaseModel):
    """Where this capability came from, kept separate from what it does.

    The model transcript is not copied in. `raw_step_count` is the only sign of how many
    turns the recorder boiled down.
    """

    model_config = STRICT

    discovered_by_model: str
    discovery_run_id: str
    recorded_at: datetime
    goal_text: str
    raw_step_count: int
    redaction_policy_version: str
    human_edited: bool = False


class Capability(BaseModel):
    """A saved flow with parameters, which a person can review and an agent can run by name.

    Frozen. To change one, publish a new version.
    """

    model_config = STRICT

    schema_version: Literal["1.0"] = "1.0"
    capability_id: str = Field(pattern=SLUG_PATTERN)
    version: str = Field(pattern=SEMVER_PATTERN)
    name: str
    description: str
    status: ApprovalStatus = ApprovalStatus.DRAFT
    surface: SurfaceDescriptor
    inputs: list[ParamSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    steps: list[Step]
    checkpoint: Assertion
    known_outcomes: list[BusinessOutcomeSpec] = Field(default_factory=list)
    recoveries: list[RecoveryRule] = Field(default_factory=list)
    overrides: dict[str, VariantOverride] = Field(default_factory=dict)
    provenance: Provenance

    # -- helpers used by the cross field validators -------------------------------
    def _input_names(self) -> set[str]:
        return {p.name for p in self.inputs}

    def _output_names(self) -> set[str]:
        return {o.name for o in self.outputs}

    def _outcome_codes(self) -> set[str]:
        return {o.code for o in self.known_outcomes}

    def _step_indices(self) -> set[int]:
        return {s.index for s in self.steps}

    def _templates_in(self, text: str) -> set[str]:
        return set(_TEMPLATE_RE.findall(text))

    def _referenced_params(self) -> set[str]:
        used: set[str] = set()
        for step in self.steps:
            if step.value is not None and step.value.source == "param":
                used.add(step.value.param)
            if step.url is not None:
                used |= self._templates_in(step.url)
        used |= self._templates_in(self.surface.entry_path)
        return used

    # -- cross field validators ---------------------------------------------------
    @model_validator(mode="after")
    def _steps_are_contiguous(self) -> Capability:
        if not self.steps:
            raise ValueError("capability requires at least one step")
        actual = [s.index for s in self.steps]
        expected = list(range(len(self.steps)))
        if actual != expected:
            raise ValueError(
                f"step indices must be contiguous starting at 0, got {actual}"
            )
        return self

    @model_validator(mode="after")
    def _param_bindings_name_declared_inputs(self) -> Capability:
        declared = self._input_names()
        for step in self.steps:
            if step.value is not None and step.value.source == "param":
                if step.value.param not in declared:
                    raise ValueError(
                        f"step {step.index} binds undeclared input {step.value.param!r}"
                    )
        return self

    @model_validator(mode="after")
    def _url_templates_name_declared_inputs(self) -> Capability:
        declared = self._input_names()
        sources = [("surface.entry_path", self.surface.entry_path)]
        sources += [(f"step {s.index} url", s.url) for s in self.steps if s.url is not None]
        for where, text in sources:
            for name in sorted(self._templates_in(text)):
                if name not in declared:
                    raise ValueError(
                        f"{where} references undeclared input {name!r}"
                    )
        return self

    @model_validator(mode="after")
    def _names_are_unique(self) -> Capability:
        for label, values in (
            ("input names", [p.name for p in self.inputs]),
            ("output names", [o.name for o in self.outputs]),
            ("outcome codes", [o.code for o in self.known_outcomes]),
        ):
            duplicates = sorted({x for x in values if values.count(x) > 1})
            if duplicates:
                raise ValueError(f"duplicate {label}: " + ", ".join(duplicates))
        return self

    @model_validator(mode="after")
    def _partial_outputs_name_declared_outputs(self) -> Capability:
        declared = self._output_names()
        for outcome in self.known_outcomes:
            for name in outcome.partial_outputs:
                if name not in declared:
                    raise ValueError(
                        f"outcome {outcome.code!r} lists undeclared partial output {name!r}"
                    )
        return self

    @model_validator(mode="after")
    def _overrides_target_existing_steps(self) -> Capability:
        indices = self._step_indices()
        for variant_id, override in self.overrides.items():
            for index in sorted(override.step_overrides):
                if index not in indices:
                    raise ValueError(
                        f"override {variant_id!r} patches nonexistent step index {index}"
                    )
            for inserted in override.inserted_steps:
                if inserted.after_index not in indices:
                    raise ValueError(
                        f"override {variant_id!r} inserts after nonexistent step index "
                        f"{inserted.after_index}"
                    )
        return self

    @model_validator(mode="after")
    def _required_inputs_are_used(self) -> Capability:
        used = self._referenced_params()
        for param in self.inputs:
            if param.required and param.name not in used:
                raise ValueError(
                    f"required input {param.name!r} is never referenced by a step or by "
                    "entry_path: an unused required parameter is a schema error"
                )
        return self

    @model_validator(mode="after")
    def _override_keys_name_declared_things(self) -> Capability:
        outputs = self._output_names()
        codes = self._outcome_codes()
        for variant_id, override in self.overrides.items():
            for name in sorted(override.output_overrides):
                if name not in outputs:
                    raise ValueError(
                        f"override {variant_id!r} overrides undeclared output {name!r}"
                    )
            for code in sorted(override.outcome_overrides):
                if code not in codes:
                    raise ValueError(
                        f"override {variant_id!r} overrides undeclared outcome {code!r}"
                    )
        return self

    @model_validator(mode="after")
    def _outcome_checks_name_existing_steps(self) -> Capability:
        indices = self._step_indices()
        for outcome in self.known_outcomes:
            if outcome.check_after_step is None:
                continue
            if outcome.check_after_step not in indices:
                raise ValueError(
                    f"outcome {outcome.code!r} checks after nonexistent step index "
                    f"{outcome.check_after_step}"
                )
        return self

    @model_validator(mode="after")
    def _recovery_scopes_name_existing_steps(self) -> Capability:
        indices = self._step_indices()
        for rule in self.recoveries:
            if rule.applies_to_steps is None:
                continue
            for index in rule.applies_to_steps:
                if index not in indices:
                    raise ValueError(
                        f"recovery {rule.name!r} applies to nonexistent step index {index}"
                    )
        return self


def describe_params(capability: Capability, supplied: Mapping[str, object]) -> list[ParamDescriptor]:
    """Names and sensitivities from the declared inputs. Values are only used to set `supplied`."""
    return [
        ParamDescriptor(
            name=spec.name,
            sensitivity=spec.sensitivity,
            required=spec.required,
            supplied=spec.name in supplied,
        )
        for spec in capability.inputs
    ]
