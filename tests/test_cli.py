"""The CLI, run as a real subprocess.

A subprocess rather than calling main() directly, for two reasons. The CLI launches its own
browser and Playwright's sync API refuses a second instance in one thread. And the exit code
is part of the contract: a caller branches on it without parsing stdout, so the thing worth
asserting is the code the process actually returned.

No network: every run here goes through --dry-run.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.models.results import EXIT_CODES
from test_secret_guard import scan

SECRET = "MEMBER-SUPERSECRET-98765"


def write_script(tmp_path: Path, base: str) -> Path:
    """A scripted run: go to the app, then hand over. No refs needed, so no live peeking."""
    script = [
        {
            "text": "Opening the search screen.",
            "tool_calls": [
                {"id": "c1", "name": "navigate", "arguments": {"url": base + "/search"}}
            ],
            "stop_reason": "tool_use",
        },
        {
            "text": "Handing over.",
            "tool_calls": [
                {"id": "c2", "name": "give_up", "arguments": {"reason": "scripted stop"}}
            ],
            "stop_reason": "tool_use",
        },
    ]
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script))
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture
def discovered(tmp_path: Path, live_app: str) -> object:
    """One real CLI run with a secret in the goal, cleaned up afterwards."""
    script = write_script(tmp_path, live_app)
    result = run_cli(
        "discover",
        "--goal",
        f"look up member {SECRET} and read the balance",
        "--target",
        live_app + "/search",
        "--dry-run",
        str(script),
        "--redact",
        SECRET,
        "--max-steps",
        "4",
    )
    directory = Path(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else None
    try:
        yield result, directory
    finally:
        if directory is not None and directory.exists():
            shutil.rmtree(directory)


def test_the_run_directory_is_printed_and_exists(discovered: object) -> None:
    result, directory = discovered  # type: ignore[misc]
    assert result.returncode == EXIT_CODES["needs_human"], result.stderr
    assert directory is not None and directory.is_dir()
    assert directory.name.count("-") == 2, "run id is YYYYMMDD-HHMMSS-xxxx"


def test_the_expected_files_are_written(discovered: object) -> None:
    _, directory = discovered  # type: ignore[misc]
    assert (directory / "run.jsonl").is_file()
    assert (directory / "transcript.json").is_file()
    assert (directory / "result.json").is_file()
    assert (directory / "screenshots").is_dir()
    lines = (directory / "run.jsonl").read_text().strip().splitlines()
    assert lines and all(json.loads(line)["kind"] for line in lines)


def test_a_redacted_value_appears_nowhere_under_the_run_directory(
    discovered: object,
) -> None:
    """The named test for item 4."""
    _, directory = discovered  # type: ignore[misc]
    offenders = [
        str(p)
        for p in directory.rglob("*")
        if p.is_file() and SECRET in p.read_bytes().decode("utf-8", errors="ignore")
    ]
    assert not offenders, f"the redacted value survived into: {offenders}"


def test_redaction_replaced_the_value_rather_than_the_value_never_being_written(
    discovered: object,
) -> None:
    """Absence proves nothing on its own. The placeholder proves redaction actually ran."""
    _, directory = discovered  # type: ignore[misc]
    transcript = (directory / "transcript.json").read_text()
    assert "<param:redacted_0>" in transcript
    assert "look up member <param:redacted_0>" in transcript


def test_a_real_run_directory_holds_no_credential_shaped_strings(
    discovered: object,
) -> None:
    """The secret guard, pointed at evidence a run actually produced."""
    _, directory = discovered  # type: ignore[misc]
    assert not scan([directory])


# -- argument handling -----------------------------------------------------------
def test_a_missing_policy_file_fails_without_launching_anything() -> None:
    result = run_cli(
        "discover", "--goal", "x", "--target", "http://127.0.0.1:1/", "--config", "nope.json"
    )
    assert result.returncode == EXIT_CODES["failure"]
    assert "cannot read policy file" in result.stderr


def test_goal_and_target_are_required() -> None:
    assert run_cli("discover").returncode != 0
