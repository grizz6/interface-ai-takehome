"""Phase 8: what a run directory contains, and what it must never contain.

The claim under test is that a reviewer can open any evidence directory, know which code and
which allowlist produced it, and read a post mortem if it did not succeed. That claim is only
worth making if it holds for a discovery run and a replay run equally, so the first test here
compares the two directly rather than checking each in isolation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import valid_capability
from src.discovery.client import ModelTurn, ScriptedClient, StopReason, ToolCall
from src.discovery.loop import DiscoveryLimits, run_discovery
from src.evidence.failure import write_failure_artifacts
from src.evidence.index import collect, render
from src.evidence.meta import RunMeta
from src.evidence.writer import EvidenceWriter
from src.models.capability import Capability, describe_params
from src.models.results import SuccessResult
from src.policy.redaction import Redactor
from src.replay.engine import replay
from test_secret_guard import scan_run_directory

LOOKUP = Path("capabilities/lookup-member-savings-balance-1.2.0.json")
SUBACCOUNT = Path("capabilities/open-member-subaccount-1.0.0.json")

REQUIRED_META = {
    "schema_version", "run_id", "kind", "started_at", "finished_at",
    "capability_id", "capability_version", "params_redacted", "model",
    "git_commit", "policy_path", "policy_sha256", "result_kind", "exit_code",
}


@pytest.fixture
def lookup(live_app: str) -> Capability:
    data = json.loads(LOOKUP.read_text())
    data["surface"]["base_url"] = live_app
    return Capability.model_validate(data)


def _writer(tmp_path: Path, run_id: str, capability: Capability | None = None,
            kind: str = "replay", redact: dict[str, str] | None = None,
            params: dict[str, Any] | None = None) -> EvidenceWriter:
    meta = RunMeta.start(
        run_id,
        kind,  # type: ignore[arg-type]
        policy_path="config/policy.json",
        capability_id=capability.capability_id if capability else None,
        capability_version=capability.version if capability else None,
        params_redacted=describe_params(capability, params or {}) if capability else [],
    )
    return EvidenceWriter(run_id, Redactor(redact or {}), root=tmp_path, meta=meta)


# ---------------------------------------------------------------------------
# One shape, whichever subsystem produced it
# ---------------------------------------------------------------------------
def test_a_discovery_run_and_a_replay_run_produce_the_same_shaped_directory(
    tmp_path: Path, live_app: str, lookup: Capability, surface: Any, policy_config: Any
) -> None:
    replay_writer = _writer(tmp_path, "replay-run", lookup, params={"member_id": "100001"})
    result = replay(
        lookup, {"member_id": "100001"}, surface, policy_config,
        evidence=lambda: replay_writer.ref, sink=replay_writer, allow_draft=True,
    )
    replay_writer.write_result(result)

    # A scripted discovery run: no network, no key, and it drives the same live app on the
    # same surface. A second WebSurface would mean a second Playwright loop in one thread,
    # which the sync API refuses, and inventing one would also break invariant 7.
    discovery_writer = _writer(tmp_path, "discovery-run", kind="discovery")
    outcome = run_discovery(
        goal="open the member lookup screen",
        surface=surface,
        client=ScriptedClient([
            ModelTurn(
                tool_calls=[ToolCall(id="c1", name="give_up",
                                    arguments={"reason": "stopping here for the test"})],
                stop_reason=StopReason.TOOL_USE,
            )
        ]),
        evidence=lambda: discovery_writer.ref,
        model="scripted",
        surface_descriptor=valid_capability().surface,
        target=live_app,
        limits=DiscoveryLimits(max_steps=2, wall_clock_s=30),
        on_observation=lambda obs: (
            discovery_writer.screenshot(obs.screenshot_png) if obs.screenshot_png else None
        ),
    )
    write_failure_artifacts(surface, discovery_writer, outcome.result)
    discovery_writer.write_transcript(outcome.transcript)
    discovery_writer.write_result(outcome.result)

    def shape(directory: Path) -> set[str]:
        return {
            str(p.relative_to(directory))
            for p in directory.rglob("*")
            # Screenshot filenames are a count, not a shape, so the directory is compared and
            # its contents are not.
            if p.is_file() and "screenshots/" not in str(p.relative_to(directory))
        } | {"screenshots"}

    replay_shape = shape(replay_writer.directory)
    discovery_shape = shape(discovery_writer.directory)

    common = {"meta.json", "run.jsonl", "result.json", "screenshots"}
    assert common <= replay_shape, replay_shape
    assert common <= discovery_shape, discovery_shape
    # transcript.json is discovery only, and failure/ tracks the result rather than the
    # subsystem. Everything else has to match exactly.
    assert replay_shape - common == set()
    assert discovery_shape - common - {"transcript.json"} <= {
        "failure/dom.html", "failure/aria.yaml", "failure/screenshot.png",
        "failure/context.json",
    }
    for directory in (replay_writer.directory, discovery_writer.directory):
        assert set(json.loads((directory / "meta.json").read_text())) == REQUIRED_META


def test_meta_records_the_commit_and_a_hash_of_the_policy(tmp_path: Path) -> None:
    """Which code, and which allowlist. Neither is recoverable after the fact."""
    writer = _writer(tmp_path, "meta-run")
    meta = json.loads((writer.directory / "meta.json").read_text())

    assert meta["git_commit"] != "unknown"
    assert len(meta["git_commit"].removesuffix("-dirty")) == 40
    assert meta["policy_path"] == "config/policy.json"
    assert len(meta["policy_sha256"]) == 64

    import hashlib
    expected = hashlib.sha256(Path("config/policy.json").read_bytes()).hexdigest()
    assert meta["policy_sha256"] == expected, "the hash is not of the file it names"


def test_meta_is_stamped_with_the_result_when_the_run_finishes(
    tmp_path: Path, lookup: Capability, surface: Any, policy_config: Any
) -> None:
    writer = _writer(tmp_path, "finish-run", lookup, params={"member_id": "100001"})
    assert json.loads((writer.directory / "meta.json").read_text())["finished_at"] is None

    result = replay(
        lookup, {"member_id": "100001"}, surface, policy_config,
        evidence=lambda: writer.ref, sink=writer, allow_draft=True,
    )
    writer.write_result(result)

    meta = json.loads((writer.directory / "meta.json").read_text())
    assert meta["finished_at"] is not None
    assert meta["result_kind"] == "success"
    assert meta["exit_code"] == 0
    assert [p["name"] for p in meta["params_redacted"]] == ["member_id"]
    assert meta["params_redacted"][0]["sensitivity"] == "pii"
    assert "100001" not in (writer.directory / "meta.json").read_text()


# ---------------------------------------------------------------------------
# Failure artifacts
# ---------------------------------------------------------------------------
@pytest.fixture
def broken(lookup: Capability) -> Capability:
    """The lookup capability with one locator renamed, so step 1 cannot resolve."""
    data = lookup.model_dump(mode="json")
    data["steps"][1]["target"]["primary"]["name"] = "Renamed By A Tenant"
    data["steps"][1]["target"]["fallbacks"] = []
    return Capability.model_validate(data)


def test_a_failing_replay_writes_all_four_failure_artifacts(
    tmp_path: Path, broken: Capability, surface: Any, policy_config: Any
) -> None:
    writer = _writer(tmp_path, "failing-run", broken, params={"member_id": "100001"})
    result = replay(
        broken, {"member_id": "100001"}, surface, policy_config,
        evidence=lambda: writer.ref, sink=writer, allow_draft=True,
    )
    writer.write_result(result)
    assert result.kind == "failure", result

    failure = writer.directory / "failure"
    assert (failure / "dom.html").read_text().strip().startswith("<")
    assert (failure / "aria.yaml").read_text().strip()
    assert (failure / "screenshot.png").read_bytes()[:4] == b"\x89PNG"
    assert json.loads((failure / "context.json").read_text())


def test_context_json_names_the_strategies_tried_and_what_each_matched(
    tmp_path: Path, broken: Capability, surface: Any, policy_config: Any
) -> None:
    """The one artifact that explains rather than records.

    A screenshot of the page shows a screen that looks perfectly normal. What it cannot show
    is that the recorded locator asked for a control by a name nothing on the page answers to.
    """
    writer = _writer(tmp_path, "context-run", broken, params={"member_id": "100001"})
    result = replay(
        broken, {"member_id": "100001"}, surface, policy_config,
        evidence=lambda: writer.ref, sink=writer, allow_draft=True,
    )
    writer.write_result(result)

    context = json.loads((writer.directory / "failure" / "context.json").read_text())
    assert context["result_kind"] == "failure"
    assert context["step_index"] == 1
    assert context["action"] == "click"
    assert context["detail"]["error_class"] == "locator_unresolved"
    assert context["url"]

    tried = context["strategies_tried"]
    assert tried, "no strategies recorded, so nothing explains the failure"
    assert [t["strategy"] for t in tried] == ["role_name"]
    # The number is the point: zero says the name is wrong, two would say it is ambiguous.
    assert tried[0]["matched"] == 0


def test_a_business_outcome_is_labelled_as_one_inside_the_failure_directory(
    tmp_path: Path, lookup: Capability, surface: Any, policy_config: Any
) -> None:
    """The folder is called failure/. A not found lookup is not a failure, and says so."""
    writer = _writer(tmp_path, "outcome-run", lookup, params={"member_id": "999999"})
    result = replay(
        lookup, {"member_id": "999999"}, surface, policy_config,
        evidence=lambda: writer.ref, sink=writer, allow_draft=True,
    )
    writer.write_result(result)

    context = json.loads((writer.directory / "failure" / "context.json").read_text())
    assert context["result_kind"] == "business_outcome"
    assert context["detail"]["code"] == "member_not_found"


# ---------------------------------------------------------------------------
# Redaction, including the two new surfaces
# ---------------------------------------------------------------------------
def test_the_guard_fails_on_a_key_planted_in_a_run_directory(tmp_path: Path) -> None:
    """The guard has to work before its silence means anything.

    dom.html is the easiest place for a real value to survive, because it is a dump of the
    whole page and nobody reads it.
    """
    run = tmp_path / "planted-run" / "failure"
    run.mkdir(parents=True)
    (run / "dom.html").write_text(
        "<html><body><input value='AIza" + "B" * 35 + "'></body></html>"
    )

    findings = scan_run_directory(tmp_path / "planted-run")

    assert [f.rule for f in findings] == ["google_api_key"]
    assert findings[0].path.endswith("dom.html")
    assert "AIza" not in str(findings[0]), "a finding must never reproduce what it found"


def test_a_redacted_value_appears_in_no_file_of_a_completed_run(
    tmp_path: Path, lookup: Capability, surface: Any, policy_config: Any
) -> None:
    """Including dom.html and aria.yaml, which are the files most likely to keep one."""
    writer = _writer(
        tmp_path, "redacted-run", lookup,
        params={"member_id": "100001"}, redact={"member_id": "100001"},
    )
    result = replay(
        lookup, {"member_id": "100001"}, surface, policy_config,
        evidence=lambda: writer.ref, sink=writer, allow_draft=True,
    )
    writer.write_result(result)
    # A success writes no failure/, so force the post mortem too: those two files are the
    # point of this test and skipping them would make it pass for the wrong reason.
    write_failure_artifacts(
        surface, writer, type("R", (), {"kind": "failure", "step_index": 3})()
    )
    assert (writer.directory / "failure" / "dom.html").exists()
    assert (writer.directory / "failure" / "aria.yaml").exists()

    findings = scan_run_directory(writer.directory, {"member_id": "100001"})
    assert not findings, "\n".join(str(f) for f in findings)
    assert "<param:member_id>" in (writer.directory / "failure" / "dom.html").read_text()


def test_a_pii_bound_field_is_masked_in_the_screenshot(
    tmp_path: Path, lookup: Capability, surface: Any, policy_config: Any
) -> None:
    """member_id is declared pii, so the box it was typed into is blacked out.

    Compared against the same run with masking disabled. Asserting the mask exists rather
    than asserting on pixels, because a screenshot that merely differs could differ for any
    reason: the count of pure black pixels is what the mask actually adds.
    """
    from src.replay.engine import _pii_bundles

    bundles = _pii_bundles(lookup)
    assert bundles, "the capability declares no pii bound field, so this proves nothing"

    surface.act(__import__(
        "src.surface.actions", fromlist=["NavigateAction"]
    ).NavigateAction(url=lookup.surface.base_url + "/search"))

    surface.set_pii_masks([])
    plain = surface.observe().screenshot_png
    surface.set_pii_masks(bundles)
    masked = surface.observe().screenshot_png
    surface.set_pii_masks([])

    assert plain != masked, "masking changed nothing at all"
    assert _black_pixels(masked) > _black_pixels(plain), "no solid region was added"


def _black_pixels(png: bytes) -> int:
    """Count fully black pixels, without pulling in an imaging dependency for one assertion.

    Playwright emits 8 bit truecolour, so this handles the non palette colour types and
    asserts rather than guesses if that ever changes.
    """
    import struct
    import zlib

    channels_for = {0: 1, 2: 3, 4: 2, 6: 4}
    pos, width, height, channels, raw = 8, 0, 0, 3, b""
    while pos < len(png):
        length, kind = struct.unpack(">I4s", png[pos:pos + 8])
        data = png[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", data[:10])
            assert depth == 8, f"unexpected bit depth {depth}"
            assert colour in channels_for, f"palette PNGs are not handled, got {colour}"
            channels = channels_for[colour]
        elif kind == b"IDAT":
            raw += data
        pos += length + 12

    pixels = zlib.decompress(raw)
    stride = width * channels
    previous = bytearray(stride)
    count = 0
    for row in range(height):
        start = row * (stride + 1)
        filter_type = pixels[start]
        line = bytearray(pixels[start + 1:start + 1 + stride])
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = previous[i]
            if filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + (left + up) // 2) & 0xFF
            elif filter_type == 4:
                upper_left = previous[i - channels] if i >= channels else 0
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                best = left if (pa <= pb and pa <= pc) else (up if pb <= pc else upper_left)
                line[i] = (line[i] + best) & 0xFF
        count += sum(
            1 for i in range(0, stride, channels)
            if all(line[i + c] == 0 for c in range(min(3, channels)))
        )
        previous = line
    return count
