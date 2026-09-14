"""The CLI, run as a real subprocess.

A subprocess rather than calling main() directly, for two reasons. The CLI starts its own
browser, and Playwright's sync API will not start a second one in the same thread. And callers
rely on the exit code, so the thing to check is the code the process really returned.

Every subprocess call checks the return code first and includes stderr in the failure message.
Otherwise a crashed subprocess shows up as a TypeError on None a few lines later, which tells
you nothing.

No network: every run here goes through --dry-run. No run writes into the repository's
evidence/ directory; every one is given a temporary path.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.models.results import EXIT_CODES
from test_secret_guard import scan

SECRET = "MEMBER-SUPERSECRET-98765"
RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "src.cli", *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


def assert_exit(
    result: subprocess.CompletedProcess[str], expected: int, what: str
) -> None:
    """Assert the exit code, and say what was observed when it is wrong."""
    assert result.returncode == expected, (
        f"{what}\n"
        f"  expected exit: {expected}\n"
        f"  observed exit: {result.returncode}\n"
        f"  stdout:\n{result.stdout or '    <empty>'}\n"
        f"  stderr:\n{result.stderr or '    <empty>'}"
    )


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


def policy_for(tmp_path: Path, base: str) -> Path:
    """The shipped policy pins port 8080; the test app gets a random one.

    Without this every navigation in these tests is refused on allowed_hosts, no action is
    ever recorded, and the tests pass while exercising only the blocked path. Found when a
    step description assertion had no steps to assert on.
    """
    config = json.loads(Path("config/policy.json").read_text())
    config["allowed_hosts"] = [base.removeprefix("http://")]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(config))
    return path


@pytest.fixture
def discovered(tmp_path: Path, live_app: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    """One real CLI run, with a secret in the goal, writing into tmp_path."""
    evidence = tmp_path / "evidence"
    result = run_cli(
        "discover",
        "--goal",
        f"look up member {SECRET} and read the balance",
        "--target",
        live_app + "/search",
        "--dry-run",
        str(write_script(tmp_path, live_app)),
        "--config",
        str(policy_for(tmp_path, live_app)),
        "--evidence-dir",
        str(evidence),
        "--redact",
        SECRET,
        "--max-steps",
        "4",
    )
    assert_exit(result, EXIT_CODES["needs_human"], "scripted discovery run should need a human")

    printed = result.stdout.strip().splitlines()
    assert printed, f"the CLI printed no run directory\n  stderr:\n{result.stderr}"
    directory = Path(printed[-1])
    assert directory.is_dir(), (
        f"the printed path is not a directory: {directory}\n  stderr:\n{result.stderr}"
    )
    return result, directory


def test_the_run_directory_is_printed_and_named_correctly(
    discovered: tuple[subprocess.CompletedProcess[str], Path],
) -> None:
    _, directory = discovered
    assert RUN_ID.match(directory.name), f"run id is not YYYYMMDD-HHMMSS-xxxx: {directory.name}"


def test_the_run_is_written_where_it_was_told_and_not_into_the_repo(
    discovered: tuple[subprocess.CompletedProcess[str], Path], tmp_path: Path
) -> None:
    _, directory = discovered
    assert directory.parent == tmp_path / "evidence"


def test_the_expected_files_are_written(
    discovered: tuple[subprocess.CompletedProcess[str], Path],
) -> None:
    _, directory = discovered
    assert (directory / "run.jsonl").is_file()
    assert (directory / "transcript.json").is_file()
    assert (directory / "result.json").is_file()
    assert (directory / "screenshots").is_dir()
    lines = (directory / "run.jsonl").read_text().strip().splitlines()
    assert lines and all(json.loads(line)["kind"] for line in lines)


def test_a_redacted_value_appears_nowhere_under_the_run_directory(
    discovered: tuple[subprocess.CompletedProcess[str], Path],
) -> None:
    _, directory = discovered
    offenders = [
        str(p)
        for p in directory.rglob("*")
        if p.is_file() and SECRET in p.read_bytes().decode("utf-8", errors="ignore")
    ]
    assert not offenders, f"the redacted value survived into: {offenders}"


def test_redaction_replaced_the_value_rather_than_the_value_never_being_written(
    discovered: tuple[subprocess.CompletedProcess[str], Path],
) -> None:
    """Absence proves nothing on its own. The placeholder proves redaction actually ran."""
    _, directory = discovered
    transcript = (directory / "transcript.json").read_text()
    assert "<param:redacted_0>" in transcript
    assert "look up member <param:redacted_0>" in transcript


def test_a_real_run_directory_holds_no_credential_shaped_strings(
    discovered: tuple[subprocess.CompletedProcess[str], Path],
) -> None:
    """The secret guard, pointed at evidence a run actually produced."""
    _, directory = discovered
    assert not scan([directory])


def test_running_the_suite_never_writes_into_the_repository_evidence_directory(
    evidence_dirs_at_session_start: set[str],
) -> None:
    """Every test passes --evidence-dir. A NEW run directory here means one of them did not.

    Compared against a snapshot taken before the session rather than against an empty
    directory, because real discovery runs write here too and those are not test litter.
    """
    now = {p.name for p in Path("evidence").glob("*") if p.is_dir() and RUN_ID.match(p.name)}
    strays = sorted(now - evidence_dirs_at_session_start)
    assert not strays, f"tests wrote run directories into evidence/: {strays}"


# -- argument handling -----------------------------------------------------------
def test_a_missing_policy_file_fails_without_launching_anything(tmp_path: Path) -> None:
    result = run_cli(
        "discover",
        "--goal",
        "x",
        "--target",
        "http://127.0.0.1:1/",
        "--config",
        str(tmp_path / "nope.json"),
        "--evidence-dir",
        str(tmp_path / "evidence"),
    )
    assert_exit(result, EXIT_CODES["failure"], "a missing policy file should exit as a failure")
    assert "cannot read policy file" in result.stderr


def test_goal_and_target_are_required() -> None:
    result = run_cli("discover")
    assert result.returncode != 0, (
        "discover with no arguments should fail\n"
        f"  observed exit: {result.returncode}\n  stderr:\n{result.stderr}"
    )
    assert "--goal" in result.stderr


def test_every_screenshot_on_disk_appears_in_the_result(
    discovered: tuple[subprocess.CompletedProcess[str], Path],
) -> None:
    """Section 3.5 wants a richer signal on failure. A caller has to be able to find it.

    The evidence reference used to be captured before the run started, so it listed no
    screenshots at all while several sat on disk beside it.
    """
    _, directory = discovered
    on_disk = sorted(str(p) for p in (directory / "screenshots").glob("*.png"))
    assert on_disk, "the run should have written at least one screenshot"

    result = json.loads((directory / "result.json").read_text())
    listed = sorted(result["evidence"]["screenshot_paths"])
    assert listed == on_disk, (
        f"result.json lists {len(listed)} screenshots but {len(on_disk)} are on disk"
    )


def test_a_redacted_value_never_appears_in_a_step_description(
    discovered: tuple[subprocess.CompletedProcess[str], Path],
) -> None:
    """StepTrace.description is built before redaction, so it must never carry a value."""
    _, directory = discovered
    result = json.loads((directory / "result.json").read_text())
    descriptions = [s["description"] for s in result.get("steps", [])]
    assert descriptions, (
        "the run should have recorded at least one step; result was:\n"
        + json.dumps(result, indent=2)[:900]
    )
    for description in descriptions:
        assert SECRET not in description, f"a redacted value reached: {description!r}"



def test_record_will_not_silently_replace_a_different_capability(tmp_path: Path) -> None:
    """Recompiling the same run is fine; overwriting a reviewed artifact needs --overwrite."""
    transcript = "evidence/curated/01-discovery-real/transcript.json"
    out = tmp_path / "caps"

    first = run_cli("record", "--transcript", transcript, "--out", str(out))
    assert_exit(first, 0, "the first compile writes the file")
    path = next(out.glob("*.json"))
    original = path.read_text()

    again = run_cli("record", "--transcript", transcript, "--out", str(out))
    assert_exit(again, 0, "an identical recompile is allowed")
    assert "unchanged" in again.stdout

    path.write_text(original.replace('"status": "draft"', '"status": "approved"'))
    refused = run_cli("record", "--transcript", transcript, "--out", str(out))
    assert_exit(refused, 1, "a different file already there is not replaced")
    assert "refusing to replace" in refused.stderr
    assert '"approved"' in path.read_text()

    forced = run_cli("record", "--transcript", transcript, "--out", str(out), "--overwrite")
    assert_exit(forced, 0, "--overwrite replaces it")
    assert path.read_text() == original
