"""Load the policy from disk, validated on the way in.

A malformed allowlist is a configuration error, and a configuration error that surfaces on
the first blocked action surfaces halfway through a run against a live system. Loading
through PolicyConfig means a bad regex, an unknown action kind or a misspelled risk policy is
rejected when the file is read, before a browser has even been launched.

WHY THE ERROR SCREENS ARE ALLOWED, which looks wrong at first glance:

`/maintenance`, `/maintenance/continue` and `/session-expired` are all permitted, even though
every one of them is a failure state. That is deliberate and it is the difference between a
recoverable condition and a hard failure.

The interstitial recovery works by navigating to the maintenance notice and clicking Continue,
which is how a run absorbs a transient interstitial and carries on. If those paths were denied,
the gate would block our own recovery: the run would arrive at the maintenance screen, the
arrival check would refuse the URL, and a condition the system is designed to handle would be
converted into a PolicyViolation. The same applies to the session expired screen, which an
escalation needs to be able to reach in order to hand a human a session to repair.

The rule underneath is that the allowlist describes where the agent may legitimately BE, not
where things are going well. Denying a path you will predictably land on does not prevent
landing there; it only removes your ability to act once you have.

PATTERNS ARE REGULAR EXPRESSIONS, NOT GLOBS. PolicyGate matches with re.search, so a bare "/"
would match every path ever and make the rest of the allowlist decorative. Each entry is
therefore anchored: "/member/*" is written "^/member(/.*)?$" so it covers /member/100001 and
/member/100001/subaccount without also matching /x/member-notes.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from src.models.policy import PolicyConfig

DEFAULT_POLICY_PATH = Path("config/policy.json")


class PolicyConfigError(ValueError):
    """The policy file is missing or does not describe a valid policy."""


def load_policy_config(path: Path | str = DEFAULT_POLICY_PATH) -> PolicyConfig:
    """Read and validate a policy file. Raises PolicyConfigError rather than returning junk."""
    location = Path(path)
    try:
        raw = location.read_text()
    except OSError as exc:
        raise PolicyConfigError(f"cannot read policy file {location}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyConfigError(f"{location} is not valid JSON: {exc}") from exc

    try:
        return PolicyConfig.model_validate(parsed)
    except ValidationError as exc:
        raise PolicyConfigError(
            f"{location} is not a valid policy, rejected at load rather than at first "
            f"use: {exc}"
        ) from exc
