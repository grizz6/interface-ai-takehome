"""Control transfer, with no human in the room.

Every handoff here goes through the operator page's own Flask test client, so the routes a
person would click are the ones being tested. Nothing fakes the operator page by writing its
files directly, because its lease handling is the most likely thing to be wrong.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import pytest

from src.escalation.intervention import (
    InterventionResolution,
    InterventionStore,
    refuse_unsafe_outcome,
)
from src.escalation.lease import (
    ControlLease,
    ControlLost,
    IllegalTransition,
    LeaseStore,
)
from src.escalation.operator import create_app
from src.escalation.session import Session
from src.evidence.writer import EvidenceWriter
from src.models.capability import Capability
from src.models.common import (
    Holder,
    LeaseState,
    ResolutionOutcome,
    RiskClass,
    StuckReason,
)
from src.models.results import FailureResult, NeedsHumanResult, SuccessResult
from src.policy.redaction import Redactor
from src.replay.engine import replay

SUBACCOUNT = Path("capabilities/open-member-subaccount-1.0.0.json")
PARAMS = {
    "member_id": "100001",
    "account_type": "Savings",
    "nickname": "Vacation",
    "initial_deposit": "250.00",
}


@pytest.fixture(autouse=True)
def restore_the_shared_lease(surface: Any) -> Iterator[None]:
    """Give the session scoped surface its lease back after every test.

    Attaching a lease is how a Session takes ownership of a surface, and the surface here
    outlives the test that attached one. Without this, a run that ends closed or paused
    leaves every later test unable to touch the browser, because the lease check is doing
    its job on the wrong test.
    """
    original = surface._lease
    yield
    surface.attach_lease(original)


# ---------------------------------------------------------------------------
# The lease on its own
# ---------------------------------------------------------------------------
def test_the_lease_rejects_every_illegal_transition() -> None:
    """The table is the protocol, so this walks the whole product of states."""
    legal = {
        (LeaseState.RUNNING, LeaseState.PAUSED),
        (LeaseState.PAUSED, LeaseState.HUMAN_CONTROL),
        (LeaseState.HUMAN_CONTROL, LeaseState.RESUMING),
        (LeaseState.RESUMING, LeaseState.RUNNING),
    } | {(state, LeaseState.CLOSED) for state in LeaseState}

    for start in LeaseState:
        for target in LeaseState:
            lease = ControlLease(
                session_id="s", holder=Holder.NONE, state=start, updated_at=datetime.now(UTC)
            )
            if (start, target) in legal:
                assert lease.to(target).state is target
            else:
                with pytest.raises(IllegalTransition):
                    lease.to(target)


def test_the_holder_is_derived_from_the_state_and_cannot_disagree() -> None:
    """A lease that says paused and automation at once is the bug this design removes."""
    lease = ControlLease.start("s")
    assert lease.holder is Holder.AUTOMATION and lease.automation_may_act

    paused = lease.to(LeaseState.PAUSED)
    assert paused.holder is Holder.NONE and not paused.automation_may_act

    human = paused.to(LeaseState.HUMAN_CONTROL)
    assert human.holder is Holder.HUMAN and not human.automation_may_act

    resuming = human.to(LeaseState.RESUMING)
    assert resuming.holder is Holder.AUTOMATION and resuming.automation_may_act


def test_the_lease_file_round_trips(tmp_path: Path) -> None:
    store = LeaseStore(tmp_path / "lease.json")
    store.write(ControlLease.start("session-1"))
    assert store.read().state is LeaseState.RUNNING
    store.transition(LeaseState.PAUSED, intervention_id="i-1")
    assert store.read().intervention_id == "i-1"
    # Nothing but the target file is left behind, so a reader globbing the directory does not
    # trip over a half written temp file.
    assert [p.name for p in tmp_path.iterdir()] == ["lease.json"]


# ---------------------------------------------------------------------------
# No browser action without the lease
# ---------------------------------------------------------------------------
def test_act_without_the_lease_raises_and_touches_no_playwright(
    surface: Any, live_app: str
) -> None:
    """The assertion has to come before the call, not after it fails."""
    from src.surface.actions import NavigateAction

    class Tripwire:
        """Stands in for the page. Any use at all is the failure."""

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"Playwright was touched without the lease: {name}")

    from src.escalation.lease import InProcessLease

    store = InProcessLease("no-control")
    store.write(store.read().to(LeaseState.PAUSED))

    real_page = surface._page
    surface.attach_lease(store)
    surface._page = Tripwire()
    try:
        with pytest.raises(ControlLost) as exc:
            surface.act(NavigateAction(url=live_app))
        assert exc.value.state is LeaseState.PAUSED
        assert exc.value.holder is Holder.NONE
    finally:
        surface._page = real_page


def test_resolve_also_asserts_the_lease(surface: Any) -> None:
    """Resolving drives the browser and decides the next move, so it is covered too."""
    from src.escalation.lease import InProcessLease
    from src.models.locator import LocatorBundle, RoleNameLocator

    store = InProcessLease("no-control")
    store.write(store.read().to(LeaseState.PAUSED))
    surface.attach_lease(store)
    with pytest.raises(ControlLost):
        surface.resolve(LocatorBundle(primary=RoleNameLocator(role="button", name="Confirm")))


# ---------------------------------------------------------------------------
# The safety rule
# ---------------------------------------------------------------------------
def test_retry_step_is_refused_on_an_irreversible_step_in_code() -> None:
    refusal = refuse_unsafe_outcome(
        RiskClass.RISKY_IRREVERSIBLE, ResolutionOutcome.RETRY_STEP
    )
    assert refusal is not None
    assert "already have been performed" in refusal

    for outcome in (
        ResolutionOutcome.APPROVED,
        ResolutionOutcome.COMPLETED_MANUALLY,
        ResolutionOutcome.ABORTED,
    ):
        assert refuse_unsafe_outcome(RiskClass.RISKY_IRREVERSIBLE, outcome) is None
    assert (
        refuse_unsafe_outcome(RiskClass.SAFE_REVERSIBLE, ResolutionOutcome.RETRY_STEP) is None
    )


# ---------------------------------------------------------------------------
# A simulated operator
# ---------------------------------------------------------------------------
class ScriptedOperator(Session):
    """A Session that plays both parts, because only one thread can drive this browser.

    Playwright's sync API binds a page to the thread that created it, so a human simulated in
    a second thread cannot touch the page at all: it raises before it does anything. The human
    therefore acts from inside the wait, at the moment control is theirs. What that gives up
    is timing realism, and what it keeps is everything the handoff is actually about: the
    lease really moves through the console's own routes, the page actions really happen
    outside the replay engine, and the context is never recreated. See DECISIONS.md 0035.
    """

    def __init__(self, *args: Any, outcome: ResolutionOutcome, note: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.outcome = outcome
        self.note = note
        self.client = create_app(
            interventions_dir=self.store.root, lease_path=self.lease.path
        ).test_client()
        self.observed_state: LeaseState | None = None
        self.human_did: Any = None

    def human_actions(self, page: Any) -> None:
        """Overridden by a test to do whatever the person would have done."""

    def await_return(self, intervention_id: str, **kwargs: Any) -> Any:
        # The run is paused and the request is on disk. Everything below is the operator.
        self.observed_state = self.lease.read().state
        assert self.observed_state is LeaseState.PAUSED

        taken = self.client.post(f"/i/{intervention_id}/take")
        assert taken.status_code in (200, 302), taken.data

        self.human_did = self.human_actions(self.surface.page)

        returned = self.client.post(
            f"/i/{intervention_id}/return",
            data={"outcome": self.outcome.value, "note": self.note},
        )
        assert returned.status_code in (200, 302, 400), returned.data
        self.last_return_status = returned.status_code
        self.last_return_body = returned.data.decode()
        return super().await_return(intervention_id, poll_interval=0.01, **kwargs)


@pytest.fixture
def approval_policy(policy_config: Any) -> Any:
    """The shared policy, which now requires approval exactly as config/policy.json does.

    Kept as a named fixture so these tests say out loud which setting they depend on. The
    surface fixture is built from the same PolicyConfig, and it has to be: the engine and the
    surface each run the gate, so two different policies would let one of them silently
    disagree with the other.
    """
    return policy_config


@pytest.fixture
def capability() -> Capability:
    return Capability.model_validate_json(SUBACCOUNT.read_text())


@pytest.fixture
def wired(tmp_path: Path, live_app: str, capability: Capability) -> dict[str, Any]:
    """Everything a handoff needs: a repointed artifact, an evidence writer, two directories."""
    data = json.loads(SUBACCOUNT.read_text())
    data["surface"]["base_url"] = live_app
    return {
        "capability": Capability.model_validate(data),
        "writer": EvidenceWriter("handoff", Redactor({}), root=tmp_path / "evidence"),
        "interventions": tmp_path / "interventions",
        "lease": tmp_path / "interventions" / "lease.json",
    }


def _run(wired: dict[str, Any], surface: Any, policy_config: Any, session: Any) -> Any:
    return replay(
        wired["capability"],
        PARAMS,
        surface,
        policy_config,
        evidence=lambda: wired["writer"].ref,
        sink=wired["writer"],
        allow_draft=True,
        session=session,
    )


# ---------------------------------------------------------------------------
# Pausing, and what the human is handed
# ---------------------------------------------------------------------------
def test_an_irreversible_step_pauses_and_writes_the_five_required_fields(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """Section 3.6: which capability, which step, the state, why, and what it was given."""
    session = Session(
        surface,
        session_id="pause-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
        deadline_seconds=0,
    )
    result = _run(wired, surface, approval_policy, session)

    assert isinstance(result, NeedsHumanResult), result
    store = InterventionStore(wired["interventions"])
    requests = store.all_requests()
    assert len(requests) == 1
    found = requests[0]

    assert found.capability_id == "open-member-subaccount"
    assert found.capability_version == "1.0.0"
    assert found.step_index == 5
    assert found.risk is RiskClass.RISKY_IRREVERSIBLE
    assert found.reason is StuckReason.RISKY_ACTION_REQUIRES_APPROVAL
    assert "Confirm" in found.step_description
    assert "approve" in found.why
    assert found.url.endswith("/subaccount")
    assert "Review Sub-Account Request" in found.aria_snapshot
    assert found.screenshot_path and Path(found.screenshot_path).exists()
    assert [p.name for p in found.params_redacted] == list(PARAMS)


def test_the_intervention_file_carries_parameter_names_and_no_values(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """A handoff request must not hold parameter values, just like evidence."""
    session = Session(
        surface,
        session_id="redaction-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
        deadline_seconds=0,
    )
    _run(wired, surface, approval_policy, session)

    path = next(Path(wired["interventions"]).glob("*-s5-*.json"))
    raw = path.read_text()

    for name in PARAMS:
        assert name in raw, f"the operator needs to see the parameter name {name!r}"
    for name, value in PARAMS.items():
        # The aria snapshot of a review screen legitimately shows what was entered, since
        # that is the screen a human is being asked to look at. The redaction claim is about
        # the params block, so that is what is checked.
        block = json.loads(raw)["params_redacted"]
        assert value not in json.dumps(block), f"value of {name} leaked into params_redacted"


def test_deadline_expiry_returns_needs_human_naming_the_intervention(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    session = Session(
        surface,
        session_id="expiry-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
        deadline_seconds=0,
    )
    result = _run(wired, surface, approval_policy, session)

    assert isinstance(result, NeedsHumanResult), result
    assert result.reason is StuckReason.RISKY_ACTION_REQUIRES_APPROVAL
    assert result.step_index == 5
    # The id is the point: a caller can go and find the request that nobody answered.
    written = {r.id for r in InterventionStore(wired["interventions"]).all_requests()}
    assert result.intervention_id in written
    # And the session is closed rather than left waiting forever.
    assert LeaseStore(wired["lease"]).read().state is LeaseState.CLOSED


# ---------------------------------------------------------------------------
# Resume: what happens when control comes back
# ---------------------------------------------------------------------------
def _fill_and_confirm(page: Any) -> None:
    """What an operator does by hand: correct the request, resubmit, confirm.

    Driven through the raw page rather than through the surface, because that is the point.
    The human is not the automation, and none of this goes through a LocatorBundle, a policy
    gate or a step index.
    """
    # The review screen was reached by POST, so a plain GET of the same path is the operator
    # going back to the form. There is no back link on this screen by design.
    page.goto(page.url)
    page.select_option("#ctl00_ContentPlaceHolder1_ddlAccountType", "Money Market")
    page.fill("#ctl00_ContentPlaceHolder1_txtNickname", "Corrected By Operator")
    page.fill("#ctl00_ContentPlaceHolder1_txtInitialDeposit", "500.00")
    page.get_by_role("button", name="Submit Request", exact=True).click()
    page.get_by_role("button", name="Confirm", exact=True).click()


def test_completed_manually_skips_the_step_and_verifies_the_page(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """The end to end handoff, on one browser context that is never recreated."""
    context_before = surface._context
    page_before = surface._page

    class Operator(ScriptedOperator):
        def human_actions(self, page: Any) -> None:
            _fill_and_confirm(page)

    session = Operator(
        surface,
        outcome=ResolutionOutcome.COMPLETED_MANUALLY,
        note="corrected the nickname and opened it by hand",
        session_id="manual-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
    )
    result = _run(wired, surface, approval_policy, session)

    assert isinstance(result, SuccessResult), result
    assert session.observed_state is LeaseState.PAUSED
    # Step 5 is present but was not performed by automation.
    skipped = [s for s in result.steps if s.index == 5]
    assert len(skipped) == 1
    assert "completed by a human" in skipped[0].description
    assert skipped[0].attempt_count == 0
    # The output was read off the screen the human left behind, not off a remembered one.
    assert result.outputs["new_account_number"].startswith("90")

    # Same browser context and same page throughout. Nothing was reopened.
    assert surface._context is context_before
    assert surface._page is page_before
    assert LeaseStore(wired["lease"]).read().state is LeaseState.RUNNING


def test_completed_manually_fails_clearly_when_the_page_disagrees(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """The operator says they did it. The page says otherwise. The page wins."""

    class Operator(ScriptedOperator):
        def human_actions(self, page: Any) -> None:
            return None  # says it is done, does nothing

    session = Operator(
        surface,
        outcome=ResolutionOutcome.COMPLETED_MANUALLY,
        note="all done",
        session_id="lying-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
    )
    result = _run(wired, surface, approval_policy, session)

    assert isinstance(result, FailureResult), result
    assert result.step_index == 5
    assert "completed manually" in result.observed
    assert "the page does not show it" in result.observed
    assert "all done" in result.observed


def test_approved_lets_automation_perform_the_step_itself(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """Approval waives the approval rule for one step. Automation still does the work."""

    class Operator(ScriptedOperator):
        def human_actions(self, page: Any) -> None:
            return None  # approving is the whole action

    session = Operator(
        surface,
        outcome=ResolutionOutcome.APPROVED,
        note="checked the amount, go ahead",
        session_id="approved-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
    )
    result = _run(wired, surface, approval_policy, session)

    assert isinstance(result, SuccessResult), result
    performed = [s for s in result.steps if s.index == 5]
    assert len(performed) == 1
    assert performed[0].attempt_count == 1
    assert performed[0].locator_strategy_used == "role_name"
    assert result.outputs["new_account_number"].startswith("90")


def test_retry_step_on_an_irreversible_step_is_refused_by_the_console_and_the_engine(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """Not hidden in the UI. Refused, with the reason, wherever it is requested."""

    class Operator(ScriptedOperator):
        def human_actions(self, page: Any) -> None:
            return None

    session = Operator(
        surface,
        outcome=ResolutionOutcome.RETRY_STEP,
        note="just try it again",
        session_id="retry-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
        deadline_seconds=0,
    )
    result = _run(wired, surface, approval_policy, session)

    # The console refused it, so the lease never moved and the wait expired.
    assert session.last_return_status == 400
    assert "retry_step is refused" in session.last_return_body
    assert isinstance(result, NeedsHumanResult), result
    assert LeaseStore(wired["lease"]).read().state is LeaseState.CLOSED


def test_the_engine_refuses_retry_step_even_when_the_console_is_bypassed(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """A resolution file can be written by hand. The rule is enforced where it is read."""

    class Operator(ScriptedOperator):
        def await_return(self, intervention_id: str, **kwargs: Any) -> Any:
            self.client.post(f"/i/{intervention_id}/take")
            # Straight to the file, past every check the console makes.
            self.store.resolve(
                intervention_id,
                InterventionResolution(
                    outcome=ResolutionOutcome.RETRY_STEP,
                    operator_note="written by hand",
                    resolved_at=datetime.now(UTC),
                ),
            )
            self.lease.transition(LeaseState.RESUMING)
            return Session.await_return(self, intervention_id, poll_interval=0.01)

    session = Operator(
        surface,
        outcome=ResolutionOutcome.RETRY_STEP,
        session_id="bypass-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
    )
    result = _run(wired, surface, approval_policy, session)

    assert isinstance(result, FailureResult), result
    assert result.step_index == 5
    assert "retry_step is refused" in result.observed


def test_captured_human_actions_name_the_field_and_never_its_value(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """What a person types is never recorded, just as with the model."""

    class Operator(ScriptedOperator):
        def human_actions(self, page: Any) -> None:
            _fill_and_confirm(page)

    session = Operator(
        surface,
        outcome=ResolutionOutcome.COMPLETED_MANUALLY,
        note="fixed it",
        session_id="capture-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
        redactor=Redactor({"redacted_0": "100001"}),
    )
    result = _run(wired, surface, approval_policy, session)
    assert isinstance(result, SuccessResult), result

    stored = InterventionStore(wired["interventions"]).all_requests()[0]
    assert stored.resolution is not None
    actions = stored.resolution.human_actions
    assert actions, "the recorder captured nothing at all"

    changes = [a for a in actions if a.kind == "field_change"]
    assert changes, f"no field change recorded in {[a.kind for a in actions]}"
    assert any("Nickname" in (a.field or "") for a in changes)
    assert any(a.kind == "click" for a in actions)

    # Nothing the operator typed appears in the captured actions. Field identity only.
    recorded = json.dumps([a.model_dump(mode="json") for a in actions])
    for typed in ("Corrected By Operator", "500.00", "Money Market"):
        assert typed not in recorded, f"{typed!r} was captured as a value"

    # Including the url each action carried. A path like /member/100001/subaccount is a member
    # id in a URL, and the secret scan once found exactly that in an intervention file.
    for action in actions:
        assert "100001" not in (action.url or ""), "a raw value survived in a captured url"

    # The after snapshot shows the screen as it is, since it is what remains if the page
    # recorder gets lost. It goes through the same redactor as evidence, so declared sensitive
    # values are replaced there too.
    assert stored.resolution.aria_after is not None
    assert "Sub-Account Opened" in stored.resolution.aria_after


def test_declared_sensitive_values_are_redacted_out_of_an_intervention(
    wired: dict[str, Any], surface: Any, approval_policy: Any
) -> None:
    """An intervention file holds two aria snapshots of a real screen, so it needs the same
    treatment as evidence. Without a redactor it is a second, quieter path to disk."""
    session = Session(
        surface,
        session_id="redactor-test",
        lease_path=wired["lease"],
        interventions_dir=wired["interventions"],
        evidence_sink=wired["writer"],
        deadline_seconds=0,
        redactor=Redactor({"nickname": "Vacation"}),
    )
    _run(wired, surface, approval_policy, session)

    raw = next(Path(wired["interventions"]).glob("*-s5-*.json")).read_text()
    assert "Vacation" not in raw
    assert "<param:nickname>" in raw
