"""The policy config that PolicyGate reads.

Config only. The check itself is in src/policy, and it runs inside the surface rather than in
the prompt or at each call site.

Denied patterns always beat allowed patterns. A path that matches both
`allowed_path_patterns` and `denied_path_patterns` is denied. The other way round would fail
open when the config is ambiguous, which is worse than having no allowlist. Keep it this way
if anything is added here.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models.common import ActionType, Sensitivity


class PolicyConfig(BaseModel):
    """What a run is allowed to do, and where.

    Not frozen, unlike the capability models, because people edit it between runs. It is
    settings, not a record of something that happened.
    """

    model_config = ConfigDict(extra="forbid")

    allowed_hosts: list[str] = Field(default_factory=list)
    allowed_path_patterns: list[str] = Field(default_factory=list)
    denied_path_patterns: list[str] = Field(default_factory=list)
    allowed_actions: list[ActionType] = Field(default_factory=list)
    risky_action_policy: Literal["block", "require_approval", "flag"]
    risky_control_names: list[str] = Field(
        default_factory=list,
        description=(
            "Accessible names of controls whose activation counts as risky and irreversible "
            "during discovery, when no recorded Step risk classification exists yet."
        ),
    )
    redact_sensitivities: list[Sensitivity] = Field(
        default_factory=lambda: [Sensitivity.PII, Sensitivity.SECRET]
    )
