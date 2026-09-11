"""Invariant 6 under test: no credential may reach evidence or a capability artifact.

This is the exposure that stays open no matter how careful the redactor is, because a leak
needs only one path that forgot to call it. So rather than trusting the writers, this walks
every byte that actually landed on disk and looks for things shaped like credentials.

NOTHING HERE EVER PRINTS A MATCHED VALUE. A test that fails by echoing the secret it found
has published it to CI logs, terminal scrollback and anywhere those are shipped. Findings
carry the file and the field, never the match.

This test belongs in REPORT.md's Safety section.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

SCANNED_ROOTS = [Path("evidence"), Path("capabilities")]

CREDENTIAL_SHAPES: dict[str, re.Pattern[str]] = {
    # Google API keys, which is what GEMINI_API_KEY holds
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{20,}"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "authorization_header": re.compile(r'"[Aa]uthorization"\s*:\s*"[^"]{12,}"'),
}


@dataclass(frozen=True)
class Finding:
    """A leak, described without reproducing it."""

    path: str
    rule: str
    field: str

    def __str__(self) -> str:
        return f"{self.path} :: {self.field} :: matched {self.rule}"


def environment_secrets() -> dict[str, str]:
    """Every value the environment holds under a name ending in _API_KEY.

    The names are used for reporting. The values are only ever compared, never stored in a
    finding and never printed.
    """
    return {
        name: value
        for name, value in os.environ.items()
        if name.endswith("_API_KEY") and len(value) >= 8
    }


def _fields_containing(payload: Any, needle: str, path: str = "") -> Iterator[str]:
    """Find which JSON field holds a match, so a finding can name it without quoting it."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from _fields_containing(value, needle, f"{path}.{key}" if path else str(key))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from _fields_containing(value, needle, f"{path}[{index}]")
    elif isinstance(payload, str) and needle in payload:
        yield path or "<root>"


def _locate(path: Path, text: str, needle: str) -> str:
    """Name the field holding a match, falling back to the file itself."""
    if path.suffix not in {".json", ".jsonl"}:
        return "<binary or plain text>"
    try:
        for line in text.splitlines() or [text]:
            if needle not in line:
                continue
            fields = list(_fields_containing(json.loads(line), needle))
            if fields:
                return fields[0]
    except json.JSONDecodeError:
        return "<unparseable json>"
    return "<unlocated>"


def scan(roots: list[Path]) -> list[Finding]:
    """Walk every file under the given roots. Decodes bytes loosely so PNGs are scanned too."""
    findings: list[Finding] = []
    secrets = environment_secrets()

    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_bytes().decode("utf-8", errors="ignore")

            for rule, pattern in CREDENTIAL_SHAPES.items():
                match = pattern.search(text)
                if match:
                    findings.append(
                        Finding(str(path), rule, _locate(path, text, match.group(0)))
                    )
            for name, value in secrets.items():
                if value in text:
                    findings.append(
                        Finding(str(path), f"environment:{name}", _locate(path, text, value))
                    )
    return findings


# -- the guard must work before its silence means anything -----------------------
def test_the_guard_catches_a_planted_credential(tmp_path: Path) -> None:
    """Without this, a clean scan of an empty directory would look like a passing test."""
    planted = tmp_path / "evidence" / "run-1"
    planted.mkdir(parents=True)
    (planted / "result.json").write_text(
        json.dumps({"outputs": {"note": "key is AIza" + "B" * 35}})
    )

    findings = scan([tmp_path / "evidence"])
    assert len(findings) == 1
    assert findings[0].rule == "google_api_key"
    assert findings[0].field == "outputs.note"


def test_the_guard_catches_a_value_held_in_the_environment(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("TESTPROVIDER_API_KEY", "s3cret-value-not-real-abcdef")
    planted = tmp_path / "evidence"
    planted.mkdir(parents=True)
    (planted / "run.jsonl").write_text(
        json.dumps({"kind": "model_text", "text": "s3cret-value-not-real-abcdef"})
    )

    findings = scan([planted])
    assert [f.rule for f in findings] == ["environment:TESTPROVIDER_API_KEY"]
    assert findings[0].field == "text"


def test_a_finding_never_reproduces_the_value_it_found() -> None:
    finding = Finding("evidence/x/result.json", "google_api_key", "outputs.note")
    assert "AIza" not in str(finding)
    assert set(vars(finding)) == {"path", "rule", "field"}


# -- the assertion that matters --------------------------------------------------
def test_no_credential_has_reached_evidence_or_capabilities() -> None:
    """Invariant 6, checked against what is actually on disk rather than trusted."""
    findings = scan(SCANNED_ROOTS)
    assert not findings, "credential shaped strings found:\n" + "\n".join(
        str(f) for f in findings
    )
