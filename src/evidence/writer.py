"""Write what a run did to disk, with everything on its way out passing the Redactor.

Minimal on purpose. Phase 8 formalizes evidence and this is the shape it builds on, so the
layout and the redaction seam are what matter here rather than completeness.

    evidence/<run_id>/
        run.jsonl          one event per line, appended as the run happens
        transcript.json    the full DiscoveryTranscript, for phase 5 to compile
        result.json        the RunResult the caller received
        screenshots/NNN.png

Every text write goes through `Redactor.redact` on the serialized string rather than on the
object, so a sensitive value cannot slip through in a field nobody thought to redact. That is
the whole reason redaction happens at the boundary instead of at each call site.

SCREENSHOTS ARE THE HOLE, and it is worth naming rather than discovering. A PNG is bytes, the
redactor reads text, and a screenshot of a member detail screen contains every piece of PII on
it. Nothing here can fix that. See DECISIONS.md 0017.
"""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.models.results import EvidenceRef, RunResult
from src.policy.redaction import Redactor

EVIDENCE_ROOT = Path("evidence")


def new_run_id(now: datetime | None = None) -> str:
    """YYYYMMDD-HHMMSS-xxxx. Sorts chronologically, and the suffix survives a same-second collision."""
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


class EvidenceWriter:
    """One run's evidence directory."""

    def __init__(
        self,
        run_id: str,
        redactor: Redactor,
        *,
        root: Path = EVIDENCE_ROOT,
    ) -> None:
        self.run_id = run_id
        self._redactor = redactor
        self.directory = Path(root) / run_id
        self.screenshots = self.directory / "screenshots"
        self.screenshots.mkdir(parents=True, exist_ok=True)
        self._log = self.directory / "run.jsonl"
        self._shots = 0

    @property
    def ref(self) -> EvidenceRef:
        """Where this run's evidence is, in the form every RunResult carries."""
        return EvidenceRef(
            run_id=self.run_id,
            directory=str(self.directory),
            log_path=str(self._log),
            screenshot_paths=sorted(str(p) for p in self.screenshots.glob("*.png")),
        )

    def _write_text(self, path: Path, payload: Any) -> None:
        """Serialize, redact the serialized form, then write. Never the other way round."""
        raw = json.dumps(payload, indent=2, sort_keys=True, default=str)
        path.write_text(self._redactor.redact(raw) + "\n")

    def event(self, kind: str, **payload: Any) -> None:
        line = json.dumps(
            {"at": datetime.now(UTC).isoformat(), "kind": kind, **payload},
            sort_keys=True,
            default=str,
        )
        with self._log.open("a") as handle:
            handle.write(self._redactor.redact(line) + "\n")

    def screenshot(self, png: bytes) -> Path:
        """Written as bytes. The redactor cannot see inside a picture; see the module docstring."""
        path = self.screenshots / f"{self._shots:03d}.png"
        path.write_bytes(png)
        self._shots += 1
        return path

    def snapshot(self, name: str, text: str) -> Path:
        """Write a richer failure signal: a DOM dump or an aria snapshot.

        Redacted like every other text write. Section 3.5 asks for at least one signal beyond
        the log on failure, and a screenshot alone does not tell you which locator was being
        looked for when it went wrong.
        """
        path = self.directory / f"{name}.txt"
        path.write_text(self._redactor.redact(text))
        return path

    def write_transcript(self, transcript: Any) -> Path:
        path = self.directory / "transcript.json"
        self._write_text(path, json.loads(transcript.model_dump_json()))
        return path

    def write_result(self, result: RunResult) -> Path:
        path = self.directory / "result.json"
        self._write_text(path, json.loads(result.model_dump_json()))
        return path
