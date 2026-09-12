"""Invariants checked against evidence a real run actually produced.

Everything else in this suite tests the system against fixtures and scripted models. This
file tests the output of live runs, which is the only place invariant 9 can be checked against
a real model rather than against one whose turns we wrote ourselves.

Skips when no evidence is present, so a clean checkout still passes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.discovery.transcript import DiscoveryTranscript

TRANSCRIPTS = sorted(Path("evidence").glob("*/transcript.json"))


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


@pytest.mark.skipif(not TRANSCRIPTS, reason="no evidence on disk")
@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda p: p.parent.name)
def test_a_real_transcript_still_validates(path: Path) -> None:
    """Round tripping re-runs every ActionRecord validator against what a live run wrote."""
    restored = DiscoveryTranscript.model_validate_json(path.read_text())
    assert restored.run_id


@pytest.mark.skipif(not TRANSCRIPTS, reason="no evidence on disk")
@pytest.mark.parametrize("path", TRANSCRIPTS, ids=lambda p: p.parent.name)
def test_no_ref_from_a_real_run_reached_a_recorded_bundle(path: Path) -> None:
    """Invariant 9, against a real model for the first time.

    The ActionRecord validator already rejects a bundle carrying the ref it was built from.
    This is broader: it checks EVERY bundle in the transcript against EVERY ref the run used
    anywhere, including the declared capability's extraction locators, which the per-record
    validator never sees.
    """
    raw = json.loads(path.read_text())
    refs = _refs_used(raw)
    assert refs, "a real run should have used at least one ref"

    bundles: list[tuple[str, object]] = []
    for action in raw.get("actions", []):
        if action.get("bundle"):
            bundles.append((f"action {action['seq']}", action["bundle"]))
    declared = raw.get("declared") or {}
    for output in declared.get("outputs", []) or []:
        locator = (output.get("extraction") or {}).get("locator")
        if locator:
            bundles.append((f"output {output['name']}", locator))
    assert bundles, "a successful run should have recorded at least one bundle"

    leaks = [
        f"{where}: ref {ref!r}"
        for where, bundle in bundles
        for ref in refs
        if re.search(rf"\b{re.escape(ref)}\b", json.dumps(bundle))
    ]
    assert not leaks, "refs leaked into recorded locators:\n" + "\n".join(leaks)
