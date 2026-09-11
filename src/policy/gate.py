"""The guardrail the model cannot talk its way past.

design rule 3 puts this inside the surface layer rather than in a prompt, because a
constraint written in a system prompt is a request and a constraint written in Python is a
rule. WebSurface.act calls check() before it touches Playwright at all.

The gate is a pure function of config plus action. It performs no I/O and knows nothing about
a browser, which is what makes it exhaustively testable without one.

KNOWN LIMIT, stated here because it matters. Being pure, the gate cannot see the current page.
Host and path are therefore enforced at navigation boundaries: check() inspects the URL of a
NavigateAction, and WebSurface calls check_url() again on the URL that a click actually landed
on. A click that triggers navigation is caught on arrival, not before departure. Closing that
gap properly needs interception at the network layer, which is out of scope here.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from src.models.common import ActionType, RiskClass
from src.models.policy import PolicyConfig
from src.surface.actions import Action

STRICT = ConfigDict(extra="forbid", frozen=True)

_ACTION_KINDS: dict[str, ActionType] = {
    "navigate": ActionType.NAVIGATE,
    "click": ActionType.CLICK,
    "type": ActionType.TYPE,
    "select": ActionType.SELECT,
    "press": ActionType.PRESS,
    "wait_for": ActionType.WAIT_FOR,
}


class Allowed(BaseModel):
    """The action may proceed. `risky` is advisory telemetry, not permission."""

    model_config = STRICT
    decision: Literal["allowed"] = "allowed"
    risky: bool = False
    note: str | None = None


class Blocked(BaseModel):
    """The action must not proceed. `rule` names which constraint refused it."""

    model_config = STRICT
    decision: Literal["blocked"] = "blocked"
    rule: str
    reason: str


Decision = Annotated[Allowed | Blocked, Field(discriminator="decision")]


class PolicyGate:
    """Decides whether one action is permitted, given one config."""

    def __init__(self, config: PolicyConfig) -> None:
        self.config = config

    # -- URL rules -----------------------------------------------------------
    def check_url(self, url: str) -> Decision:
        """Host and path rules. Denied patterns always beat allowed ones."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        authority = parsed.netloc or ""
        path = parsed.path or "/"

        # Both forms are accepted so an allowlist can be written either way. Listing
        # "127.0.0.1:8080" pins the port, which is the stricter and usually wanted form;
        # listing "127.0.0.1" allows any port on that host.
        if host and host not in self.config.allowed_hosts:
            if authority not in self.config.allowed_hosts:
                return Blocked(
                    rule="allowed_hosts",
                    reason=(
                        f"neither host {host!r} nor {authority!r} is in the allowlist"
                    ),
                )

        for pattern in self.config.denied_path_patterns:
            if re.search(pattern, path):
                return Blocked(
                    rule="denied_path_patterns",
                    reason=(
                        f"path {path!r} matches denied pattern {pattern!r}; "
                        "denied always wins over allowed"
                    ),
                )

        if self.config.allowed_path_patterns and not any(
            re.search(pattern, path) for pattern in self.config.allowed_path_patterns
        ):
            return Blocked(
                rule="allowed_path_patterns",
                reason=f"path {path!r} matches no allowed pattern",
            )
        return Allowed()

    # -- the entry point WebSurface.act calls --------------------------------
    def check(self, action: Action, risk: RiskClass | None = None) -> Decision:
        kind = _ACTION_KINDS[action.kind]
        if kind not in self.config.allowed_actions:
            return Blocked(
                rule="allowed_actions",
                reason=f"action kind {action.kind!r} is not permitted by this policy",
            )

        if action.kind == "navigate":
            url_decision = self.check_url(action.url)
            if isinstance(url_decision, Blocked):
                return url_decision

        return self._check_risk(action, risk)

    def _check_risk(self, action: Action, risk: RiskClass | None) -> Decision:
        """Risk comes from the recorded Step when replaying, and from the control name
        when discovering, because during discovery no Step exists yet."""
        risky = risk is RiskClass.RISKY_IRREVERSIBLE
        control = ""
        if action.kind == "click":
            control = action.bundle.recorded_accessible_name or ""
            if control and control in self.config.risky_control_names:
                risky = True

        if not risky:
            return Allowed()

        described = f"activating {control!r}" if control else f"a {action.kind} action"
        policy = self.config.risky_action_policy
        if policy == "block":
            return Blocked(
                rule="risky_action_policy:block",
                reason=f"{described} is irreversible and this policy blocks such actions",
            )
        if policy == "require_approval":
            return Blocked(
                rule="risky_action_policy:require_approval",
                reason=(
                    f"{described} is irreversible and needs a human decision before it runs"
                ),
            )
        return Allowed(risky=True, note=f"{described} is irreversible and was flagged")
