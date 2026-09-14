"""Replay against the real target app, with no model anywhere in the process.

Every test here runs a committed capability in a real Chromium against the running Flask
app. A replay test with a fake surface would only show the engine agrees with itself. Faults
are turned on through the fault page, the same way a person would, and then the run has to
deal with them.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from src.evidence.writer import EvidenceWriter
from src.models.capability import Capability
from src.models.common import ApprovalStatus, FailureClass
from src.models.policy import PolicyConfig
from src.models.results import EXIT_CODES, BusinessOutcomeResult, FailureResult, SuccessResult
from src.policy.redaction import Redactor
from src.replay.engine import replay

CAPABILITY = Path("capabilities/lookup-member-savings-balance-1.2.0.json")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def capability(live_app: str) -> Capability:
    """The committed artifact, repointed at whichever port the test app took.

    Only base_url changes. Every locator, signal, outcome and recovery is exactly what is
    published under capabilities/, because rewriting those for a test would be testing a
    different artifact than the one a reviewer reads.
    """
    data = json.loads(CAPABILITY.read_text())
    data["surface"]["base_url"] = live_app
    return Capability.model_validate(data)


@pytest.fixture
def evidence(tmp_path: Path) -> EvidenceWriter:
    return EvidenceWriter("test-run", Redactor({}), root=tmp_path)


@pytest.fixture(autouse=True)
def clear_armed_faults(surface: Any, live_app: str) -> Iterator[None]:
    """Disarm after every test, so one test cannot poison the next.

    The browser context is session scoped, so the Flask session cookie outlives a test. A
    fault armed and never fired would go off inside whatever ran afterwards.
    """
    yield
    _arm(surface, live_app, "clear")


def _arm(surface: Any, base_url: str, fault: str) -> None:
    """Arm a fault the way a human does: the developer console, in the same session.

    Driven through the raw page rather than through the surface because the policy denies
    /dev/. That denial constrains the automation, not the harness, and routing setup through
    the surface would mean weakening the policy the run under test is judged by.
    """
    page = surface.page
    page.goto(f"{base_url}/dev/faults")
    label = "Clear Armed Fault" if fault == "clear" else f"Arm {fault}"
    page.get_by_role("button", name=label, exact=True).click()


def _run(
    capability: Capability,
    surface: Any,
    policy: PolicyConfig,
    evidence: EvidenceWriter,
    params: dict[str, Any] | None = None,
) -> Any:
    return replay(
        capability,
        params if params is not None else {"member_id": "100001"},
        surface,
        policy,
        evidence=lambda: evidence.ref,
        sink=evidence,
        allow_draft=True,
    )


# ---------------------------------------------------------------------------
# The two paths verified by hand before any of this was written
# ---------------------------------------------------------------------------
def test_happy_path_extracts_and_coerces_the_balance(
    capability: Capability, surface: Any, policy_config: PolicyConfig, evidence: EvidenceWriter
) -> None:
    result = _run(capability, surface, policy_config, evidence)

    assert isinstance(result, SuccessResult), result
    # Coerced to the declared currency type, not left as the screen's "4,182.55".
    assert result.outputs["savings_balance"] == 4182.55
    assert isinstance(result.outputs["savings_balance"], float)
    assert result.recoveries_applied == []
    # The screen the balance was read from, resolved after the shot was written rather than
    # when the run started. An EvidenceRef captured up front is always empty here.
    assert result.evidence.screenshot_paths
    assert Path(result.evidence.screenshot_paths[0]).exists()


def test_unknown_member_is_a_business_outcome_not_a_failure(
    capability: Capability, surface: Any, policy_config: PolicyConfig, evidence: EvidenceWriter
) -> None:
    """Probably the most important test in this file.

    A member id with no record fails the step postcondition and the checkpoint. If outcomes
    were checked after either of those, this would be reported as a crash and a person would
    be paged for an answer the run already had.
    """
    result = _run(capability, surface, policy_config, evidence, {"member_id": "999999"})

    assert isinstance(result, BusinessOutcomeResult), result
    assert result.code == "member_not_found"
    assert result.detected_at_step == 3
    assert EXIT_CODES[result.kind] == 10


def test_restricted_member_is_a_business_outcome_not_a_crash(
    capability: Capability, surface: Any, policy_config: PolicyConfig, evidence: EvidenceWriter
) -> None:
    result = _run(capability, surface, policy_config, evidence, {"member_id": "100003"})

    assert isinstance(result, BusinessOutcomeResult), result
    assert result.code == "member_restricted"
    assert result.partial_outputs == {}


# ---------------------------------------------------------------------------
# The three armed faults
# ---------------------------------------------------------------------------
def test_interstitial_is_dismissed_and_the_run_still_succeeds(
    capability: Capability,
    surface: Any,
    policy_config: PolicyConfig,
    evidence: EvidenceWriter,
    live_app: str,
) -> None:
    _arm(surface, live_app, "interstitial")

    result = _run(capability, surface, policy_config, evidence)

    assert isinstance(result, SuccessResult), result
    assert "dismiss_maintenance_interstitial" in result.recoveries_applied
    assert result.outputs["savings_balance"] == 4182.55
    # A run that needed a recovery stays visibly different from one that did not: the step
    # that was interrupted names what rescued it.
    assert any(t.recovered_by == "dismiss_maintenance_interstitial" for t in result.steps)


def test_an_unexpected_confirm_dialog_stops_the_run_instead_of_answering_it(
    capability: Capability,
    surface: Any,
    policy_config: PolicyConfig,
    evidence: EvidenceWriter,
    live_app: str,
) -> None:
    """A confirm() asks a question. Replay closes it with Cancel and asks a person, never guesses."""
    from src.models.common import StuckReason
    from src.models.results import NeedsHumanResult

    _arm(surface, live_app, "confirm_dialog")

    result = _run(capability, surface, policy_config, evidence)

    assert isinstance(result, NeedsHumanResult), result
    assert result.reason is StuckReason.UNKNOWN_STATE
    assert result.step_index == 0
    events = [json.loads(line) for line in Path(evidence.ref.log_path).read_text().splitlines()]
    dialogs = [e for e in events if e["kind"] == "dialog"]
    assert [d["dialog"] for d in dialogs] == ["confirm"]
    assert "Stay signed in?" in dialogs[0]["message"]
    assert surface.take_dialogs() == [], "the engine should have taken what the surface saw"


def test_an_unexpected_alert_is_closed_and_the_run_carries_on(
    capability: Capability,
    surface: Any,
    policy_config: PolicyConfig,
    evidence: EvidenceWriter,
    live_app: str,
) -> None:
    """An alert only has OK, so closing it changes nothing. It is noted as a recovery."""
    _arm(surface, live_app, "alert_dialog")

    result = _run(capability, surface, policy_config, evidence)

    assert isinstance(result, SuccessResult), result
    assert result.recoveries_applied == ["dismissed_alert"]
    assert result.outputs["savings_balance"] == 4182.55


def test_slow_response_is_survived_by_waiting(
    capability: Capability,
    surface: Any,
    policy_config: PolicyConfig,
    evidence: EvidenceWriter,
    live_app: str,
) -> None:
    """Six seconds of nothing. The declared wait outlasts it, so it is not an error."""
    _arm(surface, live_app, "slow")

    result = _run(capability, surface, policy_config, evidence)

    assert isinstance(result, SuccessResult), result
    assert result.outputs["savings_balance"] == 4182.55


def test_server_error_is_app_error_with_a_dom_snapshot_on_disk(
    capability: Capability,
    surface: Any,
    policy_config: PolicyConfig,
    evidence: EvidenceWriter,
    live_app: str,
) -> None:
    _arm(surface, live_app, "server_error")

    result = _run(capability, surface, policy_config, evidence)

    assert isinstance(result, FailureResult), result
    assert result.error_class is FailureClass.APP_ERROR
    assert "500" in result.observed
    snapshots = list(evidence.directory.rglob("*dom*"))
    assert snapshots, f"no DOM snapshot written under {evidence.directory}"
    assert snapshots[0].read_text().strip()


# ---------------------------------------------------------------------------
# Broken artifacts and refused runs
# ---------------------------------------------------------------------------
def test_corrupted_locator_reports_the_step_and_the_tiers_tried(
    capability: Capability, surface: Any, policy_config: PolicyConfig, evidence: EvidenceWriter
) -> None:
    """Rename the control the artifact looks for. Nothing on the page answers to it."""
    data = capability.model_dump(mode="json")
    data["steps"][1]["target"]["primary"]["name"] = "Member Lookup (renamed by tenant)"
    data["steps"][1]["target"]["fallbacks"] = []
    broken = Capability.model_validate(data)

    result = _run(broken, surface, policy_config, evidence)

    assert isinstance(result, FailureResult), result
    assert result.error_class is FailureClass.LOCATOR_UNRESOLVED
    assert result.step_index == 1
    assert "role_name" in result.expected


def test_missing_required_param_fails_before_the_browser_is_touched(
    capability: Capability, policy_config: PolicyConfig, evidence: EvidenceWriter
) -> None:
    """This surface raises on any use at all, so reaching it is itself the failure."""

    class Untouchable:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"pre-flight touched the surface: {name}")

    result = replay(
        capability,
        {},
        Untouchable(),
        policy_config,
        evidence=lambda: evidence.ref,
        sink=evidence,
        allow_draft=True,
    )

    assert isinstance(result, FailureResult), result
    assert "member_id" in result.expected
    assert result.observed == "it was not supplied"
    assert result.step_index == -1


def test_draft_capability_refuses_to_replay_unattended(
    capability: Capability, surface: Any, policy_config: PolicyConfig, evidence: EvidenceWriter
) -> None:
    assert capability.status is ApprovalStatus.DRAFT

    result = replay(
        capability,
        {"member_id": "100001"},
        surface,
        policy_config,
        evidence=lambda: evidence.ref,
        sink=evidence,
    )

    # Refused up front, and the refusal says why rather than dying somewhere in the middle.
    assert isinstance(result, FailureResult), result
    assert "draft" in result.observed
    assert result.steps == []
    assert EXIT_CODES[result.kind] == 40


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_two_consecutive_replays_are_identical(
    capability: Capability, surface: Any, policy_config: PolicyConfig, tmp_path: Path
) -> None:
    """Same artifact, same params, twice. Same steps and the same tier at every step.

    The tier is the part that can drift silently: a primary that stops matching and a
    fallback that picks up the slack still produces a Success, and this is what notices.
    """
    runs = []
    for index in range(2):
        writer = EvidenceWriter(f"determinism-{index}", Redactor({}), root=tmp_path)
        result = _run(capability, surface, policy_config, writer)
        assert isinstance(result, SuccessResult), result
        runs.append([(t.index, t.action, t.locator_strategy_used) for t in result.steps])

    assert runs[0] == runs[1]
    assert len(runs[0]) == len(capability.steps)
