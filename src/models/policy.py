"""The guardrail configuration the policy gate reads.

Configuration only. The gate that enforces it lives in src/policy and is phase 3, because
design rule 3 puts enforcement inside the surface layer rather than in a prompt or
at the call sites.

PRECEDENCE, and this is the rule that matters:

    Denied patterns beat allowed patterns, always.

A path matching both `allowed_path_patterns` and `denied_path_patterns` is DENIED. The
allowlist is not a grant that a later rule can qualify; the denylist is an absolute veto
applied after it. The ordering is fixed in this direction on purpose, because the opposite
reading fails open, and a guardrail that fails open under an ambiguous config is worse than
no guardrail at all. Anything added to this model later must preserve that direction.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models.common import ActionType, Sensitivity


class PolicyConfig(BaseModel):
    """What the agent is permitted to do, and where.

    Not frozen, unlike the artifact models: this is operator configuration that is edited
    between runs, not a fact recorded about a run that already happened.
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
