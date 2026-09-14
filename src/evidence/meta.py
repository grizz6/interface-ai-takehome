"""What produced a run, saved next to what the run did.

Someone opening an evidence folder weeks later will want to know which code ran and which
allowlist it used. Both change over time, both affect how to read the rest, and neither can be
worked out afterwards, so both are saved when the run happens.

The policy is saved as a path and a hash. The path alone is not enough because
config/policy.json gets edited, and the hash alone does not say which file it was.
"""
from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models.capability import ParamDescriptor

STRICT: Final[ConfigDict] = ConfigDict(extra="forbid")

# Bumped when the folder layout changes, so a reader can tell whether a folder is older than
# a field they are looking for.
EVIDENCE_SCHEMA_VERSION: Final[str] = "1.0"

RunKind = Literal["discovery", "replay"]


def git_commit() -> str:
    """The commit that produced this run, with -dirty added if there were uncommitted changes.

    Returns "unknown" if git is not available, rather than failing the run over one field.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return f"{head}-dirty" if dirty else head


def policy_hash(path: Path | str | None) -> str | None:
    """sha256 of the policy file as it stood during the run."""
    if path is None:
        return None
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


class RunMeta(BaseModel):
    """meta.json. The same fields whatever produced the run."""

    model_config = STRICT

    schema_version: str = EVIDENCE_SCHEMA_VERSION
    run_id: str
    kind: RunKind
    started_at: datetime
    finished_at: datetime | None = None

    capability_id: str | None = None
    capability_version: str | None = None
    params_redacted: list[ParamDescriptor] = Field(default_factory=list)
    model: str | None = None
    # Whether --allow-draft was passed. Replaying a draft needs the flag, and without this the
    # evidence could not say which kind of run it was. None for discovery.
    allow_draft: bool | None = None

    git_commit: str = Field(default_factory=git_commit)
    policy_path: str | None = None
    policy_sha256: str | None = None

    result_kind: str | None = None
    exit_code: int | None = None

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)

    @classmethod
    def start(
        cls,
        run_id: str,
        kind: RunKind,
        *,
        policy_path: Path | str | None = None,
        **fields: object,
    ) -> RunMeta:
        return cls(
            run_id=run_id,
            kind=kind,
            started_at=datetime.now(UTC),
            policy_path=str(policy_path) if policy_path else None,
            policy_sha256=policy_hash(policy_path),
            **fields,  # type: ignore[arg-type]
        )
