"""The interface between the discovery loop and the model behind it.

Everything specific to a provider stays in this module. The loop only sees `ModelTurn` and
`ToolCall`, so `ScriptedClient` can stand in for the real model and the loop cannot tell the
difference. A scripted run uses the same code path as a real one.

The API key is never touched here. `genai.Client()` is created with no arguments and reads
`GEMINI_API_KEY` from the environment itself. No code in this repo reads its value.
"""
from __future__ import annotations

import base64
import random
import re
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
    """Text, and optionally one screenshot.

    The image is carried as raw bytes and base64 encoded only at the provider boundary, so
    nothing upstream has to know how a given API wants pictures delivered.
    """

    model_config = STRICT
    role: Literal["user"] = "user"
    text: str
    image_png: bytes | None = Field(default=None, repr=False)


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
    """The loop asked for more turns than the script has.

    Raised instead of returning a default turn, because a test that quietly runs past its
    script is not testing anything any more.
    """


class ScriptedClient:
    """A scripted stand-in for tests and for running the loop without a network."""

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
# Gemini. Everything below here is the only provider-specific code in the repo.
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
    """Our messages as input items for the Interactions API.

    The shape is not the obvious one. `text` and `image` are content parts, not top-level
    input items. A single bare text is accepted, which is why a one-shot call works and a
    conversation does not. History has to wrap its parts in a `user_input` envelope. Getting it
    wrong gives a 400 that names the last item, which is not where the problem is. See
    DECISIONS.md 0018.
    """
    payload: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "user":
            content: list[dict[str, Any]] = [{"type": "text", "text": message.text}]
            if message.image_png is not None:
                content.append(
                    {
                        "type": "image",
                        "mime_type": "image/png",
                        "data": base64.b64encode(message.image_png).decode(),
                    }
                )
            payload.append({"type": "user_input", "content": content})
        elif message.role == "model":
            # Sends nothing. The server keeps the model's side of the conversation, reached
            # through previous_interaction_id, and the API rejects model_output and
            # function_call as input. The turn is still in our own transcript.
            continue
        else:
            # The input format has no is_error field, so mark errors in the text instead.
            text = f"ERROR: {message.content}" if message.is_error else message.content
            payload.append(
                {
                    "type": "function_result",
                    "name": message.name,
                    "call_id": message.call_id,
                    "result": [{"type": "text", "text": text}],
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
    """An Interaction as a ModelTurn, keeping only what the loop needs."""
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


TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
"""Provider errors worth waiting out instead of failing the run.

429 is the free tier limit and 5xx is the provider having a bad minute. Neither says anything
about the goal, so neither should end a run that may be twenty steps in.
"""


def _status_code(exc: Exception) -> int | None:
    """The HTTP status on an SDK exception, whichever exception class it is."""
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    return None


class ModelUnavailable(RuntimeError):
    """The model could not be reached or refused the request, so discovery cannot go on.

    Raised instead of letting a provider exception escape, so a missing key or rejected request
    ends as a normal failure with evidence, not a traceback with exit 1. The message does not
    repeat the provider's response, since I cannot be sure it has nothing sensitive in it.
    """


class GeminiClient:
    """The real client. Takes a model name and nothing else."""

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
        # Where the server-side conversation is up to. See complete() and DECISIONS.md 0018.
        self._previous_id: str | None = None
        self._sent = 0

    def _ensure_client(self) -> Any:
        """Created on first use, so importing this module needs no key."""
        if self._client is None:
            from google import genai

            # No arguments. The SDK reads GEMINI_API_KEY from the environment, so our code
            # never holds the key.
            try:
                self._client = genai.Client()
            except ValueError as exc:
                # Raised before any request when no key is set. The SDK's message contains no
                # value, so it is passed on.
                raise ModelUnavailable(f"the model client could not start: {exc}") from exc
        return self._client

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> ModelTurn:
        """Continue the conversation on the server, sending only new messages.

        DECISIONS.md 0008 planned to resend everything each turn, but the Interactions API does
        not accept `model_output` or `function_call` as input, so only the server can hold the
        model's side of a tool-calling conversation. Each call sends the messages added since
        the last one and passes `previous_interaction_id`.

        Our own transcript is unchanged: it is still what the recorder compiles and what goes
        into evidence. See DECISIONS.md 0018.
        """
        client = self._ensure_client()
        fresh = messages[self._sent :] if self._previous_id else messages
        request: dict[str, Any] = {
            "model": self._model,
            "input": to_input_payload(fresh),
            "system_instruction": system,
        }
        if self._previous_id is not None:
            request["previous_interaction_id"] = self._previous_id
        if tools:
            request["tools"] = to_tool_payload(tools)

        last: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                interaction = client.interactions.create(**request)
            except Exception as exc:
                # Matched on status code, not exception class. The SDK raises from two
                # unrelated classes, google.genai.errors.APIError and an internal
                # compat_errors one, and rate limits and 5xx come as the second. An earlier
                # version only caught the first, so the backoff below never ran.
                status = _status_code(exc)
                if status not in TRANSIENT_STATUSES:
                    detail = f"HTTP {status}" if status else type(exc).__name__
                    raise ModelUnavailable(
                        f"the model API refused the request ({detail})"
                    ) from exc
                last = exc
                if attempt == self._max_attempts - 1:
                    break
                time.sleep(self._retry_after(exc, attempt))
                continue
            self._previous_id = getattr(interaction, "id", None)
            self._sent = len(messages)
            return to_model_turn(interaction)
        raise ModelUnavailable(
            f"Gemini returned a transient error {self._max_attempts} times in a row "
            f"(last: HTTP {_status_code(last) if last else 'unknown'}). This is usually the "
            "free tier rate limit or the provider having problems."
        )

    def _retry_after(self, exc: Exception, attempt: int) -> float:
        """Wait as long as the server asks, or fall back to our own backoff.

        A 429 usually says "Please retry in 38.9s". Retrying sooner just gets another 429 and
        uses up another request.
        """
        match = re.search(r"retry in ([0-9.]+)s", str(exc))
        if match:
            return min(float(match.group(1)) + 1.0, self._max_backoff_s * 2)
        return self._backoff_for(attempt)

    def _backoff_for(self, attempt: int) -> float:
        """Exponential backoff with some randomness, so retries do not all line up."""
        delay = min(self._base_backoff_s * (2**attempt), self._max_backoff_s)
        return float(delay * (0.5 + random.random() / 2))
