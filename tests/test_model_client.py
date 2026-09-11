"""The model client seam. No network in any test here.

GeminiClient is exercised only through its pure translation functions. The request it would
send is asserted; the sending is not, because a test that needs a key and a quota is a test
that does not run.
"""
from __future__ import annotations

import pytest

from src.discovery.client import (
    GeminiClient,
    ModelClient,
    ModelMessage,
    ModelTurn,
    ScriptedClient,
    ScriptedExhausted,
    StopReason,
    ToolCall,
    ToolResultMessage,
    ToolSpec,
    UserMessage,
    to_input_payload,
    to_model_turn,
    to_tool_payload,
)

TOOLS = [ToolSpec(name="click", description="Click a control", parameters={"type": "object"})]


# -- ScriptedClient --------------------------------------------------------------
def test_scripted_client_returns_turns_in_order() -> None:
    first = ModelTurn(text="one")
    second = ModelTurn(
        tool_calls=[ToolCall(id="c1", name="click", arguments={"ref": "e7"})],
        stop_reason=StopReason.TOOL_USE,
    )
    client = ScriptedClient([first, second])

    assert client.complete("sys", [], TOOLS) == first
    assert client.complete("sys", [], TOOLS) == second


def test_scripted_client_raises_when_the_loop_runs_past_the_script() -> None:
    """Not a default turn. A test that runs past its script asserts nothing."""
    client = ScriptedClient([ModelTurn(text="only one")])
    client.complete("sys", [], TOOLS)

    with pytest.raises(ScriptedExhausted) as exc:
        client.complete("sys", [], TOOLS)
    assert "requested turn 2" in str(exc.value)
    assert "only 1 were scripted" in str(exc.value)


def test_scripted_client_records_what_the_loop_asked_for() -> None:
    client = ScriptedClient([ModelTurn(text="x")])
    client.complete("be careful", [UserMessage(text="go")], TOOLS)

    system, messages, tools = client.calls[0]
    assert system == "be careful"
    assert messages[0].text == "go"
    assert tools[0].name == "click"
    assert client.remaining == 0


def test_both_clients_satisfy_the_same_protocol() -> None:
    """The loop must not be able to tell them apart."""
    assert isinstance(ScriptedClient([]), ModelClient)
    assert isinstance(GeminiClient("gemini-3-flash-preview"), ModelClient)


# -- translation, the only part of GeminiClient testable without a network -------
def test_constructing_the_gemini_client_touches_no_key_and_no_network() -> None:
    client = GeminiClient("gemini-3-flash-preview")
    assert client._client is None, "the SDK client is built lazily, on first call"


def test_tools_translate_to_function_declarations() -> None:
    payload = to_tool_payload(TOOLS)
    assert payload == [
        {
            "type": "function",
            "name": "click",
            "description": "Click a control",
            "parameters": {"type": "object"},
        }
    ]


def test_a_full_transcript_translates_to_input_items() -> None:
    messages = [
        UserMessage(text="look up member 100001"),
        ModelMessage(
            text="I will search",
            tool_calls=[ToolCall(id="c1", name="click", arguments={"ref": "e7"})],
        ),
        ToolResultMessage(call_id="c1", name="click", content="ok"),
    ]
    payload = to_input_payload(messages)

    assert [item["type"] for item in payload] == [
        "text",
        "text",
        "function_call",
        "function_result",
    ]
    assert payload[2]["id"] == "c1"
    assert payload[3]["call_id"] == "c1"
    assert payload[3]["result"] == [{"type": "text", "text": "ok"}]


def test_a_tool_error_result_is_marked_as_one() -> None:
    payload = to_input_payload(
        [ToolResultMessage(call_id="c1", name="click", content="boom", is_error=True)]
    )
    assert payload[0]["is_error"] is True


class _FakeStep:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


class _FakeInteraction:
    def __init__(self, status: str, steps: list[object], output_text: str | None) -> None:
        self.status = status
        self.steps = steps
        self.output_text = output_text


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("completed", StopReason.END_TURN),
        ("requires_action", StopReason.TOOL_USE),
        ("failed", StopReason.ERROR),
        ("something_new", StopReason.OTHER),
    ],
)
def test_interaction_status_maps_onto_our_stop_reason(
    status: str, expected: StopReason
) -> None:
    turn = to_model_turn(_FakeInteraction(status, [], "hello"))
    assert turn.stop_reason is expected
    assert turn.text == "hello"


def test_function_call_steps_become_tool_calls_and_other_steps_are_dropped() -> None:
    interaction = _FakeInteraction(
        "requires_action",
        [
            _FakeStep(type="reasoning", text="thinking"),
            _FakeStep(type="function_call", id="c9", name="type", arguments={"text": "x"}),
        ],
        None,
    )
    turn = to_model_turn(interaction)
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0] == ToolCall(id="c9", name="type", arguments={"text": "x"})


def test_an_unknown_status_carrying_tool_calls_is_still_tool_use() -> None:
    interaction = _FakeInteraction(
        "", [_FakeStep(type="function_call", id="c1", name="click", arguments={})], None
    )
    assert to_model_turn(interaction).stop_reason is StopReason.TOOL_USE


def test_backoff_grows_and_is_jittered() -> None:
    client = GeminiClient("m", base_backoff_s=2.0, max_backoff_s=30.0)
    assert 1.0 <= client._backoff_for(0) <= 2.0
    assert 2.0 <= client._backoff_for(1) <= 4.0
    assert client._backoff_for(10) <= 30.0
