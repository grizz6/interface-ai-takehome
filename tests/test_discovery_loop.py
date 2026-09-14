"""The discovery loop, driven entirely by ScriptedClient. No network anywhere in this file.

The surface is faked for most of these because the behaviour under test is the loop's, not the
browser's: what it does when a checkpoint fails, when actions keep being refused, when the
screen stops changing. The last test uses the real WebSurface against the live target app, to
show the loop works with a real browser when only the model is scripted.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


from conftest import valid_capability
from src.discovery.client import ModelTurn, ScriptedClient, StopReason, ToolCall
from src.discovery.loop import DiscoveryLimits, run_discovery
from src.discovery.transcript import DiscoveryStop
from src.models.common import StuckReason
from src.models.locator import LocatorBundle, RoleNameLocator
from src.models.results import (
    EvidenceRef,
    FailureResult,
    NeedsHumanResult,
    PolicyBlockedResult,
    SuccessResult,
)
from src.surface.observation import Observation, parse_aria_snapshot
from src.surface.protocol import PolicyViolation, Resolved

EVIDENCE = EvidenceRef(run_id="run-test", directory="evidence/run-test", log_path="x.jsonl")
SURFACE = valid_capability().surface

SNAPSHOT = """- table [ref=e2] [box=0,0,900,600]:
  - cell "Sub-Account Opened" [ref=e3] [box=0,0,100,20]
  - cell "New Account Number" [ref=e4] [box=0,20,100,20]
  - cell "900001001" [ref=e5] [box=100,20,100,20]
  - button "Confirm" [ref=e6] [box=0,40,80,20]
"""


def call(name: str, **args: Any) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=args)


def turn(*calls: ToolCall) -> ModelTurn:
    return ModelTurn(tool_calls=list(calls), stop_reason=StopReason.TOOL_USE)


FINISH_ARGS: dict[str, Any] = {
    "capability_name": "open-member-subaccount",
    "description": "Opens a sub-account.",
    "checkpoint": {"kind": "text_present", "text": "Sub-Account Opened"},
    "inputs": [],
    "outputs": [
        {
            "name": "new_account_number",
            "type": "string",
            "description": "The issued number.",
            "extraction": {"ref": "e5", "source": "text", "parse": "raw"},
        }
    ],
}


class FakeHandle:
    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self) -> str:
        return self._text


class FakeSurface:
    """A surface with no browser. Every behaviour the loop branches on is switchable."""

    def __init__(
        self,
        *,
        snapshots: list[str] | None = None,
        checkpoint_holds: bool | list[bool] = True,
        extract_text: str | None = "900001001",
        refuse: bool = False,
    ) -> None:
        self._snapshots = snapshots or [SNAPSHOT]
        self._observes = 0
        self._checkpoint = checkpoint_holds
        self._checks = 0
        self._extract_text = extract_text
        self._refuse = refuse
        self.acted: list[Any] = []

    def observe(self) -> Observation:
        text = self._snapshots[min(self._observes, len(self._snapshots) - 1)]
        self._observes += 1
        return Observation(
            url="http://localhost:8080/member/100001",
            title="Member Detail",
            aria_yaml=text,
            elements=parse_aria_snapshot(text),
            screenshot_png=b"fake-png",
            captured_at=datetime(2026, 9, 11, tzinfo=UTC),
        )

    def describe(self, ref: str) -> LocatorBundle:
        return LocatorBundle(
            primary=RoleNameLocator(role="cell", name="New Account Number"),
            recorded_accessible_name="New Account Number",
        )

    def resolve(self, bundle: LocatorBundle) -> Resolved:
        if self._extract_text is None:
            from src.surface.protocol import LocatorUnresolved

            raise LocatorUnresolved("nothing matched")
        return Resolved(strategy="role_name", tier_index=0, handle=FakeHandle(self._extract_text))

    def act(self, action: Any, **kw: Any) -> Any:
        from src.surface.actions import ActionOutcome

        if self._refuse:
            raise PolicyViolation("denied_path_patterns", "path /dev/x matches ^/dev(/.*)?$")
        self.acted.append(action)
        return ActionOutcome(ok=True, resolved_strategy="role_name", duration_ms=5)

    def evaluate(self, signal: Any) -> bool:
        if isinstance(self._checkpoint, bool):
            return self._checkpoint
        value = self._checkpoint[min(self._checks, len(self._checkpoint) - 1)]
        self._checks += 1
        return value

    def close(self) -> None:
        return None


def drive(client: ScriptedClient, surface: Any, **kw: Any) -> Any:
    return run_discovery(
        goal="open a sub-account for member 100001",
        surface=surface,
        client=client,
        evidence=EVIDENCE,
        model="scripted",
        surface_descriptor=SURFACE,
        **kw,
    )


# -- 1. the happy path -----------------------------------------------------------
def test_a_run_that_finishes_returns_success_with_the_verified_values() -> None:
    outcome = drive(ScriptedClient([turn(call("finish", **FINISH_ARGS))]), FakeSurface())

    assert isinstance(outcome.result, SuccessResult)
    assert outcome.result.outputs == {"new_account_number": "900001001"}
    assert outcome.transcript.stop_reason is DiscoveryStop.GOAL_REACHED
    assert outcome.transcript.declared is not None
    assert outcome.transcript.declared.capability_name == "open-member-subaccount"


def test_the_transcript_keeps_the_bundle_for_every_action() -> None:
    """Without the bundles the recorder has nothing to compile."""
    client = ScriptedClient(
        [turn(call("click", ref="e6")), turn(call("finish", **FINISH_ARGS))]
    )
    outcome = drive(client, FakeSurface(snapshots=[SNAPSHOT, SNAPSHOT.replace("900001001", "900001002")]))
    click = outcome.transcript.actions_in_order()[0]
    assert click.action_kind == "click"
    assert click.bundle is not None
    assert click.tier_resolved == "role_name"


# -- 2. a checkpoint that does not hold gets fed back ----------------------------
def test_a_checkpoint_that_does_not_hold_is_rejected_then_accepted_on_retry() -> None:
    client = ScriptedClient(
        [turn(call("finish", **FINISH_ARGS)), turn(call("finish", **FINISH_ARGS))]
    )
    surface = FakeSurface(checkpoint_holds=[False, True], snapshots=[SNAPSHOT, SNAPSHOT + "  - cell \"x\"\n"])
    outcome = drive(client, surface)

    assert isinstance(outcome.result, SuccessResult)
    # the first attempt came back to the model as an error, in its own words
    _, messages, _ = client.calls[1]
    fed_back = [m for m in messages if getattr(m, "role", "") == "tool_result"]
    assert any("does not hold on the current page" in m.content for m in fed_back)


def test_a_checkpoint_that_never_holds_ends_in_failure_after_three_attempts() -> None:
    snaps = [SNAPSHOT, SNAPSHOT + "  - cell \"a\"\n", SNAPSHOT + "  - cell \"b\"\n"]
    client = ScriptedClient([turn(call("finish", **FINISH_ARGS))] * 3)
    outcome = drive(client, FakeSurface(checkpoint_holds=False, snapshots=snaps))

    assert isinstance(outcome.result, FailureResult)
    assert outcome.result.error_class == "checkpoint_failed"
    assert "does not hold" in outcome.result.observed


# -- 3. an output that cannot be extracted ---------------------------------------
def test_a_declared_output_that_cannot_be_extracted_is_rejected() -> None:
    snaps = [SNAPSHOT, SNAPSHOT + "  - cell \"a\"\n", SNAPSHOT + "  - cell \"b\"\n"]
    client = ScriptedClient([turn(call("finish", **FINISH_ARGS))] * 3)
    outcome = drive(client, FakeSurface(extract_text=None, snapshots=snaps))

    assert isinstance(outcome.result, FailureResult)
    assert "new_account_number" in outcome.result.observed


def test_an_output_pointing_at_a_stale_ref_is_rejected() -> None:
    args = {**FINISH_ARGS, "outputs": [
        {**FINISH_ARGS["outputs"][0], "extraction": {"ref": "e999", "source": "text", "parse": "raw"}}
    ]}
    snaps = [SNAPSHOT, SNAPSHOT + "  - cell \"a\"\n", SNAPSHOT + "  - cell \"b\"\n"]
    outcome = drive(ScriptedClient([turn(call("finish", **args))] * 3), FakeSurface(snapshots=snaps))

    assert isinstance(outcome.result, FailureResult)
    assert "not in the current snapshot" in outcome.result.observed


# -- 4. consecutive policy blocks ------------------------------------------------
def test_three_consecutive_refusals_return_policy_blocked() -> None:
    snaps = [SNAPSHOT, SNAPSHOT + "  - cell \"a\"\n", SNAPSHOT + "  - cell \"b\"\n"]
    client = ScriptedClient([turn(call("navigate", url="http://localhost:8080/dev/faults"))] * 3)
    outcome = drive(client, FakeSurface(refuse=True, snapshots=snaps))

    assert isinstance(outcome.result, PolicyBlockedResult)
    assert outcome.result.rule == "denied_path_patterns"
    assert outcome.transcript.stop_reason is DiscoveryStop.POLICY_BLOCKED


def test_a_refusal_names_the_rule_but_never_describes_the_allowlist() -> None:
    snaps = [SNAPSHOT, SNAPSHOT + "  - cell \"a\"\n", SNAPSHOT + "  - cell \"b\"\n"]
    # Three refusals, because the third is what stops the run. The assertion below
    # reads the refusal that was fed back after the first.
    client = ScriptedClient([turn(call("navigate", url="http://localhost:8080/dev/faults"))] * 3)
    drive(client, FakeSurface(refuse=True, snapshots=snaps))

    _, messages, _ = client.calls[1]
    refusal = next(m for m in messages if getattr(m, "role", "") == "tool_result")
    assert "denied_path_patterns" in refusal.content
    assert "^/dev" not in refusal.content, "the pattern itself must not reach the model"
    assert "/dev/x" not in refusal.content


# -- 5. the screen stopped changing ----------------------------------------------
def test_three_identical_observations_return_needs_human() -> None:
    client = ScriptedClient([turn(call("look")), turn(call("look"))])
    outcome = drive(client, FakeSurface())

    assert isinstance(outcome.result, NeedsHumanResult)
    assert outcome.result.reason is StuckReason.UNKNOWN_STATE
    assert outcome.transcript.stop_reason is DiscoveryStop.NEEDS_HUMAN


def test_two_identical_observations_are_not_yet_a_stall() -> None:
    """A validation error re-renders a structurally identical page. That is not a stall."""
    client = ScriptedClient([turn(call("look")), turn(call("finish", **FINISH_ARGS))])
    outcome = drive(client, FakeSurface())
    assert isinstance(outcome.result, SuccessResult)


# -- 6. budgets ------------------------------------------------------------------
def test_running_out_of_steps_returns_needs_human_with_that_reason() -> None:
    snaps = [SNAPSHOT, SNAPSHOT + "  - cell \"a\"\n"]
    client = ScriptedClient([turn(call("look")), turn(call("look"))])
    outcome = drive(client, FakeSurface(snapshots=snaps), limits=DiscoveryLimits(max_steps=2))

    assert isinstance(outcome.result, NeedsHumanResult)
    assert outcome.result.reason is StuckReason.MAX_STEPS_EXCEEDED
    assert outcome.transcript.stop_reason is DiscoveryStop.MAX_STEPS


def test_give_up_returns_needs_human() -> None:
    outcome = drive(
        ScriptedClient([turn(call("give_up", reason="the record is restricted"))]),
        FakeSurface(),
    )
    assert isinstance(outcome.result, NeedsHumanResult)
    assert outcome.transcript.stop_reason is DiscoveryStop.GAVE_UP


# -- history and screenshots -----------------------------------------------------
def test_only_the_two_most_recent_observations_are_carried_in_full() -> None:
    snaps = [SNAPSHOT + f"  - cell \"{i}\"\n" for i in range(4)]
    client = ScriptedClient([turn(call("look"))] * 4)
    drive(client, FakeSurface(snapshots=snaps), limits=DiscoveryLimits(max_steps=4))

    _, messages, _ = client.calls[-1]
    full = [m for m in messages if getattr(m, "role", "") == "user" and "ref=e2" in m.text]
    collapsed = [m for m in messages if getattr(m, "role", "") == "user" and "earlier screen" in m.text]
    assert len(full) == 2, "exactly two snapshots should be carried in full"
    assert collapsed, "older snapshots should be collapsed to url and title"


def test_a_screenshot_goes_up_on_the_first_turn_and_after_look() -> None:
    snaps = [SNAPSHOT + f"  - cell \"{i}\"\n" for i in range(3)]
    client = ScriptedClient([turn(call("look")), turn(call("navigate", url="/x")), turn(call("look"))])
    drive(client, FakeSurface(snapshots=snaps), limits=DiscoveryLimits(max_steps=3))

    def shots(index: int) -> int:
        _, messages, _ = client.calls[index]
        return sum(1 for m in messages if getattr(m, "image_png", None))

    assert shots(0) == 1, "first turn carries a screenshot"
    assert shots(1) == 2, "look() asked for another"


# -- everything real except the model -------------------------------------------
def test_the_loop_drives_the_real_surface_with_a_scripted_model(
    surface: Any, live_app: str
) -> None:
    """Real browser, real target app, real policy gate, real verification. Scripted model.

    The loop cannot tell whether the turns came from Gemini or from a list, so this runs the
    real code path without a key, quota or network.

    Refs are read from a live snapshot first and only then scripted, because refs change with
    every snapshot and cannot be written down in advance.
    """
    from src.surface.actions import NavigateAction

    surface.act(NavigateAction(url=live_app + "/member/100001"))
    peek = surface.observe()
    number_cell = next(
        e for e in peek.elements if e.role == "cell" and e.name == "Marcus Webb"
    )

    finish_args = {
        "capability_name": "read-member-name",
        "description": "Looks up a member and reads their name.",
        "checkpoint": {"kind": "text_present", "text": "Member Detail"},
        "inputs": [],
        "outputs": [
            {
                "name": "member_name",
                "type": "string",
                "description": "The member's name as shown on the detail screen.",
                "extraction": {"ref": number_cell.ref, "source": "text", "parse": "raw"},
            }
        ],
    }

    client = ScriptedClient([turn(call("finish", **finish_args))])
    outcome = run_discovery(
        goal="look up member 100001 and read their name",
        surface=surface,
        client=client,
        evidence=EVIDENCE,
        model="scripted",
        surface_descriptor=SURFACE,
    )

    assert isinstance(outcome.result, SuccessResult), outcome.result
    assert outcome.result.outputs == {"member_name": "Marcus Webb"}

    declared = outcome.transcript.declared
    assert declared is not None
    # the ref became a durable locator on the way through, and did not survive into it
    locator = declared.outputs[0].extraction.locator
    assert number_cell.ref not in locator.model_dump_json()
    assert locator.frame_path == ["maincontent"], "the name lives inside the iframe"


def test_a_refused_navigation_against_the_real_gate_is_fed_back_not_raised(
    surface: Any, live_app: str
) -> None:
    """The policy gate fires inside act(), and the loop turns that into a tool_result."""
    client = ScriptedClient(
        [
            turn(call("navigate", url=live_app + "/dev/faults")),
            turn(call("give_up", reason="that route is closed")),
        ]
    )
    outcome = run_discovery(
        goal="open the fault console",
        surface=surface,
        client=client,
        evidence=EVIDENCE,
        model="scripted",
        surface_descriptor=SURFACE,
    )

    assert isinstance(outcome.result, NeedsHumanResult)
    _, messages, _ = client.calls[1]
    refusal = next(m for m in messages if getattr(m, "role", "") == "tool_result")
    assert "refused by policy" in refusal.content
    assert "denied_path_patterns" in refusal.content
    assert "^/dev" not in refusal.content


def test_a_typed_value_never_reaches_a_step_description() -> None:
    """B7 at the unit level: the description names the control, never the value.

    Redaction would catch a value the caller thought to pass to --redact. It cannot catch a
    member id nobody declared, so the value must not be in the string at all.
    """
    typed_value = "MEMBER-99999-PRIVATE"
    finish = {
        **FINISH_ARGS,
        "outputs": [],
    }
    client = ScriptedClient(
        [
            turn(call("type_text", ref="e6", text=typed_value)),
            turn(call("finish", **finish)),
        ]
    )
    outcome = drive(
        client,
        FakeSurface(snapshots=[SNAPSHOT, SNAPSHOT + '  - cell "x"\n']),
    )

    assert isinstance(outcome.result, SuccessResult)
    descriptions = [s.description for s in outcome.result.steps]
    assert descriptions, "the run should have recorded a step"
    for description in descriptions:
        assert typed_value not in description, description
    # the value is still recorded where the schema knows it is sensitive
    typed = [a for a in outcome.transcript.actions if a.action_kind == "type"]
    assert typed and typed[0].literal_value == typed_value



def test_a_model_that_cannot_be_reached_ends_as_a_failure_not_a_crash() -> None:
    """No key, a refused request, or a provider that keeps erroring: a typed failure, exit 40."""
    from src.discovery.client import ModelUnavailable
    from src.models.common import FailureClass

    class Unreachable:
        def complete(self, *_: Any) -> ModelTurn:
            raise ModelUnavailable("the model client could not start: no key")

    outcome = drive(Unreachable(), FakeSurface())  # type: ignore[arg-type]

    assert isinstance(outcome.result, FailureResult), outcome.result
    assert outcome.result.error_class is FailureClass.INTERNAL
    assert "could not start" in outcome.result.observed
    assert outcome.transcript.stop_reason is DiscoveryStop.ERROR


# -- handing over to a person ----------------------------------------------------
class AmbiguousSurface(FakeSurface):
    """describe() cannot pin the control down the first time, then can."""

    def __init__(self) -> None:
        # A different screen after each action, so the stall check stays out of it.
        super().__init__(snapshots=[SNAPSHOT + f'  - cell "screen {i}"\n' for i in range(5)])
        self.describes = 0

    def describe(self, ref: str) -> LocatorBundle:
        self.describes += 1
        if self.describes == 1:
            from src.surface.protocol import LocatorAmbiguous

            raise LocatorAmbiguous("role_name matched 2 elements")
        return super().describe(ref)


class FakeSession:
    """Stands in for a person who takes the browser and gives it back."""

    def __init__(self, outcome: str = "approved") -> None:
        from src.models.common import ResolutionOutcome

        self.outcome = ResolutionOutcome(outcome)
        self.escalated: list[tuple[StuckReason, Any]] = []
        self.resumed = 0

    def escalate(self, reason: StuckReason, context: Any) -> str:
        self.escalated.append((reason, context))
        return f"intervention-{len(self.escalated)}"

    def await_return(self, intervention_id: str) -> Any:
        return type("Resolution", (), {"outcome": self.outcome})()

    def resume(self) -> None:
        self.resumed += 1


def test_a_control_that_cannot_be_pinned_down_is_handed_to_a_person_not_guessed() -> None:
    session = FakeSession()
    client = ScriptedClient(
        [
            turn(call("click", ref="e6")),
            turn(call("click", ref="e6")),
            turn(call("finish", **FINISH_ARGS)),
        ]
    )
    outcome = drive(client, AmbiguousSurface(), session=session)

    assert isinstance(outcome.result, SuccessResult), outcome.result
    assert [reason for reason, _ in session.escalated] == [StuckReason.LOCATOR_AMBIGUOUS]
    assert "matched 2 elements" in session.escalated[0][1].why
    assert session.resumed == 1
    _, messages, _ = client.calls[1]
    assert any(
        "handed it back" in getattr(m, "content", "")
        for m in messages if getattr(m, "role", "") == "tool_result"
    )


def test_without_a_session_the_same_control_stops_the_run() -> None:
    client = ScriptedClient([turn(call("click", ref="e6"))])
    outcome = drive(client, AmbiguousSurface())

    assert isinstance(outcome.result, NeedsHumanResult)
    assert outcome.result.reason is StuckReason.LOCATOR_AMBIGUOUS

