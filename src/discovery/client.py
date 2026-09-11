"""The seam between the discovery loop and whichever model is behind it.

Everything provider shaped is confined to this module. The loop upstream sees only
`ModelTurn` and `ToolCall`, which is what makes `ScriptedClient` a genuine substitute rather
than a mock with a different shape: the loop cannot tell which one it is talking to, so a
scripted run exercises the real code path.

The key is never handled here. `genai.Client()` is constructed with no arguments and reads
`GEMINI_API_KEY` from the environment itself, per design rule 6. There is no line in
this repo that references its value.
"""
from __future__ import annotations

import random
import time
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

STRICT = ConfigDict(extra="forbid", frozen=True)


class StopReason(StrEnum):
    """Why the model stopped, normalized away from any provider's vocabulary."""

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    ERROR = "error"
    OTHER = "other"


class ToolCall(BaseModel):
    """One tool invocation the model asked for."""

    model_config = STRICT

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelTurn(BaseModel):
    """One reply from the model. The only thing the loop ever receives."""

    model_config = STRICT

    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: StopReason = StopReason.END_TURN


class ToolSpec(BaseModel):
    """A tool offered to the model, described in JSON Schema."""

    model_config = STRICT

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class UserMessage(BaseModel):
    model_config = STRICT
    role: Literal["user"] = "user"
    text: str


class ModelMessage(BaseModel):
    """A previous reply, replayed back as history in stateless mode."""

    model_config = STRICT
    role: Literal["model"] = "model"
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolResultMessage(BaseModel):
    model_config = STRICT
    role: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    content: str
    is_error: bool = False


Message = Annotated[
    UserMessage | ModelMessage | ToolResultMessage, Field(discriminator="role")
]


@runtime_checkable
class ModelClient(Protocol):
    """What the discovery loop is allowed to know about a model."""

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> ModelTurn: ...


class ScriptedExhausted(RuntimeError):
    """The loop asked for more turns than the script provides.

    Raised rather than returning a default turn, because a scripted test that silently runs
    past its script is a test that stops asserting anything.
    """


class ScriptedClient:
    """A deterministic stand in, for tests and for exercising the loop without a network."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self._turns = list(turns)
        self._index = 0
        self.calls: list[tuple[str, list[Message], list[ToolSpec]]] = []

    @property
    def remaining(self) -> int:
        return len(self._turns) - self._index

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> ModelTurn:
        self.calls.append((system, list(messages), list(tools)))
        if self._index >= len(self._turns):
            raise ScriptedExhausted(
                f"the loop requested turn {self._index + 1} but only "
                f"{len(self._turns)} were scripted"
            )
        turn = self._turns[self._index]
        self._index += 1
        return turn


# --------------------------------------------------------------------------
# Gemini. Everything below this line is the only provider specific code in the repo.
# --------------------------------------------------------------------------
def to_tool_payload(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """Our ToolSpec into the function declaration shape the Interactions API takes."""
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        }
        for tool in tools
    ]


def to_input_payload(messages: list[Message]) -> list[dict[str, Any]]:
    """Our message list into the input items the Interactions API takes.

    The whole transcript goes in every request, because this client runs stateless. See
    DECISIONS.md 0008 for why the server side conversation store is deliberately unused.
    """
    payload: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            payload.append({"type": "text", "text": message.text})
        elif message.role == "model":
            if message.text:
                payload.append({"type": "text", "text": message.text})
            for call in message.tool_calls:
                payload.append(
                    {
                        "type": "function_call",
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                )
        else:
            payload.append(
                {
                    "type": "function_result",
                    "name": message.name,
                    "call_id": message.call_id,
                    "is_error": message.is_error,
                    "result": [{"type": "text", "text": message.content}],
                }
            )
    return payload


_STATUS_TO_STOP = {
    "completed": StopReason.END_TURN,
    "requires_action": StopReason.TOOL_USE,
    "failed": StopReason.ERROR,
    "in_progress": StopReason.OTHER,
}


def to_model_turn(interaction: Any) -> ModelTurn:
    """An Interaction into a ModelTurn, dropping everything the loop must not see."""
    calls: list[ToolCall] = []
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "function_call":
            continue
        calls.append(
            ToolCall(
                id=str(getattr(step, "id", "")),
                name=str(getattr(step, "name", "")),
                arguments=dict(getattr(step, "arguments", None) or {}),
            )
        )
    status = str(getattr(interaction, "status", "") or "")
    stop = _STATUS_TO_STOP.get(status, StopReason.OTHER)
    if calls and stop is StopReason.OTHER:
        stop = StopReason.TOOL_USE
    return ModelTurn(
        text=getattr(interaction, "output_text", None),
        tool_calls=calls,
        stop_reason=stop,
    )


class GeminiClient:
    """The real client. Constructed with a model string and nothing else."""

    def __init__(
        self,
        model: str,
        *,
        max_attempts: int = 5,
        base_backoff_s: float = 2.0,
        max_backoff_s: float = 30.0,
    ) -> None:
        self._model = model
        self._max_attempts = max_attempts
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        """Imported and constructed lazily, so importing this module needs no key."""
        if self._client is None:
            from google import genai

            # No arguments on purpose. The SDK reads GEMINI_API_KEY from the environment
            # and the key never enters this process as a value we hold. Invariant 6.
            self._client = genai.Client()
        return self._client

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> ModelTurn:
        from google.genai import errors

        client = self._ensure_client()
        request: dict[str, Any] = {
            "model": self._model,
            "input": to_input_payload(messages),
            "system_instruction": system,
            # Stateless. The transcript is ours, held locally, and goes up in full each turn.
            "store": False,
        }
        if tools:
            request["tools"] = to_tool_payload(tools)

        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return to_model_turn(client.interactions.create(**request))
            except errors.APIError as exc:
                if getattr(exc, "code", None) != 429:
                    raise
                last = exc
                if attempt == self._max_attempts - 1:
                    break
                time.sleep(self._backoff_for(attempt))
        raise RuntimeError(
            f"Gemini rate limited this request {self._max_attempts} times. The free tier "
            f"allows roughly ten requests a minute and a long run brushes it: {last}"
        )

    def _backoff_for(self, attempt: int) -> float:
        """Exponential with jitter. Jitter matters because a stalled loop retries in lockstep."""
        delay = min(self._base_backoff_s * (2**attempt), self._max_backoff_s)
        return float(delay * (0.5 + random.random() / 2))
