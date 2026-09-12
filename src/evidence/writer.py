"""The one writer. Discovery, replay and escalation all go through it.

A run directory has the same shape whichever subsystem produced it, because a reviewer should
not have to learn two layouts, and because a tool that reads one can read all of them.

    evidence/<run_id>/
        meta.json          what produced this run: commit, policy hash, params by name
        run.jsonl          one event per line, appended as the run happens
        result.json        the RunResult the caller received
        transcript.json    discovery only
        screenshots/NNN.png
        failure/           written only when the result is not success
            dom.html
            aria.yaml
            screenshot.png
            context.json

Every text write goes through `Redactor.redact` on the SERIALIZED string rather than on the
object. Redacting at the boundary means a sensitive value cannot slip through in a field
nobody thought about, which is the failure mode of redacting per call site.

SCREENSHOTS are the one thing the redactor cannot read, so they are handled upstream: the
surface masks pii bound fields at capture time with Playwright's own masking. That is best
effort and its limits are stated in DECISIONS.md 0037 and REPORT.md section 6.
"""
from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.evidence.failure import FailureContext
from src.evidence.meta import RunMeta
from src.models.results import EXIT_CODES, EvidenceRef, RunResult
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
        meta: RunMeta | None = None,
    ) -> None:
        self.run_id = run_id
        self._redactor = redactor
        self.directory = Path(root) / run_id
        self.screenshots = self.directory / "screenshots"
        self.screenshots.mkdir(parents=True, exist_ok=True)
        self._log = self.directory / "run.jsonl"
        self._shots = 0
        # A run always has metadata, even one that never calls start_meta, so a directory is
        # never missing the file that says which code produced it.
        self.meta = meta or RunMeta.start(run_id, "replay")
        self._write_meta()

    # -- paths ---------------------------------------------------------------
    @property
    def ref(self) -> EvidenceRef:
        """Where this run's evidence is, in the form every RunResult carries."""
        return EvidenceRef(
            run_id=self.run_id,
            directory=str(self.directory),
            log_path=str(self._log),
            screenshot_paths=sorted(str(p) for p in self.screenshots.glob("*.png")),
        )

    @property
    def failure_dir(self) -> Path:
        return self.directory / "failure"

    # -- the redaction seam --------------------------------------------------
    def _write_text(self, path: Path, payload: Any) -> None:
        """Serialize, redact the serialized form, then write. Never the other way round."""
        raw = json.dumps(payload, indent=2, sort_keys=True, default=str)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._redactor.redact(raw) + "\n")

    def _write_raw(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._redactor.redact(text))

    # -- during the run ------------------------------------------------------
    def event(self, kind: str, **payload: Any) -> None:
        line = json.dumps(
            {"at": datetime.now(UTC).isoformat(), "kind": kind, **payload},
            sort_keys=True,
            default=str,
        )
        with self._log.open("a") as handle:
            handle.write(self._redactor.redact(line) + "\n")

    def screenshot(self, png: bytes) -> Path:
        """Bytes, unredactable. Masking happens at capture time in the surface."""
        path = self.screenshots / f"{self._shots:03d}.png"
        path.write_bytes(png)
        self._shots += 1
        return path

    def snapshot(self, name: str, text: str) -> Path:
        """A named text capture during the run. Failure artifacts use `write_failure`."""
        path = self.directory / f"{name}.txt"
        self._write_raw(path, text)
        return path

    # -- metadata ------------------------------------------------------------
    def _write_meta(self) -> None:
        self._write_text(self.directory / "meta.json", json.loads(self.meta.model_dump_json()))

    def update_meta(self, **fields: Any) -> RunMeta:
        self.meta = self.meta.model_copy(update=fields)
        self._write_meta()
        return self.meta

    # -- the end of the run --------------------------------------------------
    def write_transcript(self, transcript: Any) -> Path:
        """transcript.json, and the events it holds flushed into run.jsonl.

        Discovery accumulates its events on the transcript rather than emitting them as it
        goes, so without this a discovery directory has no run.jsonl at all. It used to be
        the CLI that remembered to loop over them, which meant the directory shape depended
        on the caller rather than on the writer. It does not any more.
        """
        path = self.directory / "transcript.json"
        self._write_text(path, json.loads(transcript.model_dump_json()))
        for entry in getattr(transcript, "events", []):
            self.event(
                getattr(entry.kind, "value", str(entry.kind)),
                seq=entry.seq,
                at=entry.at,
                **entry.payload,
            )
        return path

    def write_result(self, result: RunResult) -> Path:
        """Write the result, stamp the metadata, and close the run out."""
        path = self.directory / "result.json"
        self._write_text(path, json.loads(result.model_dump_json()))
        self.update_meta(
            finished_at=datetime.now(UTC),
            result_kind=result.kind,
            exit_code=EXIT_CODES[result.kind],
        )
        return path

    def write_failure(
        self,
        context: FailureContext,
        *,
        dom: str | None = None,
        aria: str | None = None,
        png: bytes | None = None,
    ) -> Path:
        """The three raw artifacts and the one that explains them.

        Each piece is optional because a surface that has already fallen over cannot always
        produce all three, and a partial post mortem beats none. What is never optional is
        context.json, which is the only one that says why.
        """
        target = self.failure_dir
        target.mkdir(parents=True, exist_ok=True)
        if dom is not None:
            self._write_raw(target / "dom.html", dom)
        if aria is not None:
            self._write_raw(target / "aria.yaml", aria)
        if png is not None:
            (target / "screenshot.png").write_bytes(png)
        self._write_text(target / "context.json", json.loads(context.model_dump_json()))
        return target
