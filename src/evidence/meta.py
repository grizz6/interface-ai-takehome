"""What produced a run, recorded alongside what the run did.

A reviewer opening an evidence directory six weeks from now has two questions the result and
the log cannot answer: which code ran, and which allowlist was it running under. Both change,
both change the meaning of everything else in the directory, and neither is recoverable
afterwards. So both are written at the moment of the run.

The policy is recorded as a path AND a hash of its contents. The path alone is worthless,
because config/policy.json is edited; the hash alone is unreadable, because nobody knows which
file it belonged to. Together they say "this allowlist, exactly this version of it".
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

# Bumped when the shape of an evidence directory changes, so a reader can tell whether a
# directory predates a field it is looking for.
EVIDENCE_SCHEMA_VERSION: Final[str] = "1.0"

RunKind = Literal["discovery", "replay"]


def git_commit() -> str:
    """The commit that produced this run, marked dirty if the tree was not clean.

    "unknown" rather than an exception if git is unavailable: evidence that is missing one
    field is still evidence, and a run that dies because it could not find git is not.
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
    """meta.json. Identical in shape whichever subsystem produced the run."""

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
