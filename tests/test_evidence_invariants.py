"""Checks against evidence from real runs.

The rest of the tests use fixtures and scripted models. This file checks the output of a real
model run, which is the only way to check that no snapshot ref ended up in a saved locator
when the model's turns were not written by us.

It reads the committed sample runs, so it runs on every checkout. It used to look in
evidence/*/transcript.json, which matches nothing on a clean clone because the sample
transcripts are one level deeper, so the tests were silently skipped. Local runs at the top of
evidence/ are not read, so results do not depend on what someone ran last. See DECISIONS.md 0042.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.discovery.transcript import DiscoveryTranscript

TRANSCRIPTS = sorted(Path("evidence/curated").glob("*/transcript.json"))


def _refs_used(transcript: dict[str, object]) -> set[str]:
    """Every ref the run touched, from the actions and from the event payloads."""
    refs: set[str] = set()
    for action in transcript.get("actions", []) or []:
        assert isinstance(action, dict)
        if action.get("ref_used"):
            refs.add(str(action["ref_used"]))
    for event in transcript.get("events", []) or []:
        assert isinstance(event, dict)
        payload = event.get("payload") or {}
        assert isinstance(payload, dict)
        if payload.get("ref"):
            refs.add(str(payload["ref"]))
    return refs


def test_the_curated_evidence_contains_a_real_transcript() -> None:
    """An empty parameter set makes pytest skip the tests below silently. This makes it fail."""
    assert TRANSCRIPTS, "evidence/curated/ holds no transcript, so nothing is being checked"


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda p: p.parent.name)
def test_a_real_transcript_still_validates(path: Path) -> None:
    """Round tripping re-runs every ActionRecord validator against what a live run wrote."""
    restored = DiscoveryTranscript.model_validate_json(path.read_text())
    assert restored.run_id


@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda p: p.parent.name)
def test_no_ref_from_a_real_run_reached_a_recorded_bundle(path: Path) -> None:
    """No snapshot ref from a real model run ended up in a saved locator.

    The ActionRecord validator already rejects a bundle carrying the ref it was built from.
    This is broader: it checks EVERY bundle in the transcript against EVERY ref the run used
    anywhere, including the declared capability's extraction locators, which the per-record
    validator never sees.
    """
    raw = json.loads(path.read_text())
    refs = _refs_used(raw)
    if not refs:
        # A run that was blocked or stalled before using any refs has nothing to check. That is
        # a pass, not a skip.
        return

    bundles: list[tuple[str, object]] = []
    for action in raw.get("actions", []):
        if action.get("bundle"):
            bundles.append((f"action {action['seq']}", action["bundle"]))
    declared = raw.get("declared") or {}
    for output in declared.get("outputs", []) or []:
        locator = (output.get("extraction") or {}).get("locator")
        if locator:
            bundles.append((f"output {output['name']}", locator))
    assert bundles, "this run used refs, so it should have recorded at least one bundle"

    leaks = [
        f"{where}: ref {ref!r}"
        for where, bundle in bundles
        for ref in refs
        if re.search(rf"\b{re.escape(ref)}\b", json.dumps(bundle))
    ]
    assert not leaks, "refs leaked into recorded locators:\n" + "\n".join(leaks)
