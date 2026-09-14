"""The policy check, which the model cannot talk its way past.

It runs inside the surface, not in the prompt. A rule in a prompt is a request; a rule in
Python is enforced. WebSurface.act calls check() before touching Playwright.

The check only looks at the config and the action. It does no I/O and knows nothing about a
browser, so it can be tested thoroughly without one.

Limit: because it cannot see the page, host and path are checked when navigation happens.
check() looks at a NavigateAction's URL, and WebSurface calls check_url() again on wherever a
click actually ended up. So a click that navigates somewhere denied is caught on arrival, not
before it leaves. Stopping it earlier would need network interception, which I did not build.
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
    """The action can go ahead. `risky` is just a note, not a permission."""

    model_config = STRICT
    decision: Literal["allowed"] = "allowed"
    risky: bool = False
    note: str | None = None


class Blocked(BaseModel):
    """The action must not go ahead. `rule` says which rule refused it."""

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

        # Both forms work. "127.0.0.1:8080" allows only that port, which is usually what you
        # want, and "127.0.0.1" allows any port on that host.
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
