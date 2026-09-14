"""Load the policy file and validate it.

If a broken allowlist only showed up at the first blocked action, it would show up halfway
through a run. Loading through PolicyConfig means a bad regex, an unknown action type or a
misspelled risk policy is caught when the file is read, before a browser starts.

Why the error pages are allowed:

`/maintenance`, `/maintenance/continue` and `/session-expired` are all allowed, even though
they are error pages. The maintenance recovery works by landing on the notice and clicking
Continue. If those paths were denied, the policy would block the recovery: the run lands on
the page, the arrival check refuses it, and something the run knows how to handle turns into a
PolicyViolation. The same goes for the session expired page, which a handoff needs to reach.
The allowlist says where a run is allowed to be, not where things are going well. See
DECISIONS.md 0009.

Patterns are regular expressions, not globs. PolicyGate uses re.search, so a bare "/" would
match every path and make the rest of the list pointless. So entries are anchored: "/member/*"
is written "^/member(/.*)?$", which covers /member/100001 and /member/100001/subaccount but not
/x/member-notes.
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
    """Read and validate a policy file, raising PolicyConfigError if anything is wrong."""
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
