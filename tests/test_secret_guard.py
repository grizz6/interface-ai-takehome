"""No credential may end up in evidence, capabilities or any tracked file.

However careful the redactor is, one code path that forgets to call it is enough to leak. So
instead of trusting the writers, this reads every byte that was written and looks for anything
shaped like a credential.

Nothing here ever prints a matched value. A test that fails by printing the secret it found has
just put it in CI logs and terminal history. Findings name the file and field only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
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
    # Provider agnostic, and the rule that matters most. Every shape above assumes a
    # vendor format, so a provider whose keys look different slips past all of them. This
    # one matches the assignment instead: a secret sounding name given a long opaque
    # value. Added after a real key reached a public commit while every pattern above
    # stayed silent.
    # Two shapes, both requiring an actual value rather than an expression. A bare
    # `secrets = load_secrets()` is code, not a leak, and an earlier draft of this rule
    # flagged it.
    "secret_assignment": re.compile(
        # a quoted literal: "api_key": "abc..." or SECRET = "abc..."
        r"\b[a-z0-9_]*(?:api[_-]?key|secret|token|password|credential)[a-z0-9_]*"
        r"[\"']?\s*[=:]\s*[\"'][A-Za-z0-9_\-]{16,}[\"']"
        r"|"
        # an env file line: GEMINI_API_KEY=abc...
        r"^[a-z][a-z0-9_]*(?:api_key|secret|token|password)[a-z0-9_]*"
        r"=[A-Za-z0-9_\-.~+/=]{16,}\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
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
    # Whole file first: a pretty printed JSON document has no line that parses on its own, and
    # reporting <unparseable json> for the most common shape makes a finding much less useful.
    try:
        fields = list(_fields_containing(json.loads(text), needle))
        if fields:
            return fields[0]
    except json.JSONDecodeError:
        pass
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


# -- the main check -------------------------------------------------------------
def test_no_credential_has_reached_evidence_or_capabilities() -> None:
    """Check what is actually on disk instead of trusting the writers."""
    findings = scan(SCANNED_ROOTS)
    assert not findings, "credential shaped strings found:\n" + "\n".join(
        str(f) for f in findings
    )


def scan_run_directory(directory: Path, redacted: dict[str, str] | None = None) -> list[Finding]:
    """Every byte of one completed run, including dom.html and aria.yaml.

    Those two are the most likely place for a real value to survive. A DOM dump is the whole
    page, hidden inputs and typed values included, and an aria snapshot has every accessible
    name on screen. Both go through the same redactor, and both are too big for anyone to read.

    `redacted` is what the caller passed with --redact. Finding one of those in a finished run
    means something wrote to disk without going through the redactor.
    """
    findings = scan([directory])
    for name, value in (redacted or {}).items():
        if not value:
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            text = path.read_bytes().decode("utf-8", errors="ignore")
            if value in text:
                findings.append(
                    Finding(str(path), f"unredacted:{name}", _locate(path, text, value))
                )
    return findings


def tracked_files() -> list[Path]:
    """Every file git tracks, which is exactly the set that can become public.

    Uses git rather than a walk, so .venv, caches and the gitignored evidence directory are
    excluded without maintaining a skip list.
    """
    listing = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [Path(line) for line in listing.splitlines() if Path(line).is_file()]


ACKNOWLEDGED: dict[str, str] = {
    "target_app/app.py": (
        "Flask session key for the local stand in application. No auth, no real data, and "
        "the app is never deployed. Changing it breaks nothing and protects nothing."
    ),
    "tests/test_cli.py": (
        "The fabricated value the redaction test passes through --redact and then asserts "
        "cannot be found. It has to be a literal for the test to mean anything."
    ),
}
"""Files allowed to contain a secret shaped literal, each with the reason.

An explicit, short, reviewable list beats an inline suppression comment: a marker in the code
can be added by anyone in passing, while an entry here is visible to whoever audits this file
and has to carry a justification.
"""


def test_every_acknowledged_file_still_exists_and_still_matches() -> None:
    """Keeps the exception list from rotting into a set of permanent blanket permissions."""
    stale: list[str] = []
    for name in ACKNOWLEDGED:
        path = Path(name)
        if not path.is_file():
            stale.append(f"{name}: no longer exists")
        elif not CREDENTIAL_SHAPES["secret_assignment"].search(
            path.read_bytes().decode("utf-8", errors="ignore")
        ):
            stale.append(f"{name}: no longer matches, so the exception is unnecessary")
    assert not stale, "the acknowledged list is out of date:\n" + "\n".join(stale)


def test_no_tracked_file_carries_a_secret_assignment() -> None:
    """The check that was missing, and the reason it was missing.

    This guard only ever looked at evidence/ and capabilities/, on the assumption that a leak
    would happen on the way out of a run. A real key reached a public commit through
    .env.example instead: a tracked file, never scanned, holding a value that matched none of
    the vendor specific patterns above.

    Scanning what git tracks closes the first gap. Matching on the shape of the assignment
    rather than on any vendor's key format closes the second.
    """
    findings: list[Finding] = []
    for path in tracked_files():
        if str(path) in ACKNOWLEDGED:
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        match = CREDENTIAL_SHAPES["secret_assignment"].search(text)
        if match:
            line = text[: match.start()].count("\n") + 1
            findings.append(Finding(str(path), "secret_assignment", f"line {line}"))
    assert not findings, "tracked files carry secret shaped assignments:\n" + "\n".join(
        str(f) for f in findings
    )


def test_the_assignment_rule_catches_what_the_vendor_patterns_missed(tmp_path: Path) -> None:
    """Regression test for the actual near miss, using a value of the same shape."""
    root = tmp_path / "evidence"
    root.mkdir(parents=True)
    (root / ".env.example").write_text("GEMINI_API_KEY=" + "k7Qx" * 10 + "\n")

    findings = scan([root])
    assert [f.rule for f in findings] == ["secret_assignment"]


def test_the_assignment_rule_stays_quiet_on_templates_and_prose() -> None:
    rule = CREDENTIAL_SHAPES["secret_assignment"]
    assert not rule.search("GEMINI_API_KEY=")
    assert not rule.search("GEMINI_API_KEY=your-key-here")
    assert not rule.search("# put your GEMINI_API_KEY in .env")


def test_no_tracked_env_file_carries_a_value() -> None:
    """A committed .env template must declare names and nothing else.

    This is the rule that would have caught the real incident, and it needed no knowledge of
    what a given provider's key looks like. Every shape based rule asks "does this look like a
    credential", which fails the moment a provider picks an alphabet you did not anticipate.
    This asks "is a tracked template carrying a value", which has exactly one right answer.

    Only the variable name is reported. The value is never read into a finding, printed, or
    measured.
    """
    findings: list[Finding] = []
    for path in tracked_files():
        if not path.name.startswith(".env"):
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            if value.strip().strip("\"'"):
                findings.append(Finding(str(path), "env_template_carries_a_value", name))
    assert not findings, (
        "a committed .env template carries a value, which is how a real key reached a public "
        "commit:\n" + "\n".join(str(f) for f in findings)
    )

