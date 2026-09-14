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

    SHAPE, and it is not the obvious one. `text` and `image` are CONTENT PARTS, not top level
    input items. A single bare text is accepted as a convenience, which is exactly why a one
    shot call works and a conversation does not, but history has to wrap its parts in the
    `user_input` and `model_output` envelopes. Getting this wrong returns a 400 naming the
    trailing item, which points at the wrong place entirely. See DECISIONS.md 0018.
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
            # Deliberately contributes nothing. The assistant side of the conversation lives
            # on the server, reached by previous_interaction_id, and the API rejects both
            # model_output and function_call as input items. Sending the model its own turn
            # back is neither possible nor necessary. It stays in our transcript regardless,
            # which is the copy that matters.
            continue
        else:
            # No is_error field: the input shape does not carry one, so a failure is marked
            # in the text the model actually reads.
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


TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
"""Provider side conditions worth waiting out rather than failing the run over.

429 is the free tier ceiling. The 5xx family is the provider having a bad minute. Neither
says anything about whether the goal is achievable, so neither should end a discovery run
that may be twenty steps in.
"""


def _status_code(exc: Exception) -> int | None:
    """The HTTP status behind an SDK exception, whichever hierarchy it came from."""
    for attribute in ("status_code", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    return None


class ModelUnavailable(RuntimeError):
    """The model could not be reached or refused the request, so discovery cannot continue.

    Raised instead of letting a provider exception escape, so a missing key or a rejected request
    ends the run as a typed failure with evidence rather than as a traceback with exit 1. The
    message names what went wrong without repeating the provider's response, which is not ours
    to vouch for as free of anything sensitive.
    """


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
        # Server side continuation state. See the complete() docstring and DECISIONS 0018.
        self._previous_id: str | None = None
        self._sent = 0

    def _ensure_client(self) -> Any:
        """Imported and constructed lazily, so importing this module needs no key."""
        if self._client is None:
            from google import genai

            # No arguments on purpose. The SDK reads GEMINI_API_KEY from the environment
            # and the key never enters this process as a value we hold. Invariant 6.
            try:
                self._client = genai.Client()
            except ValueError as exc:
                # Raised locally, before any request, when no key is set. The SDK's own
                # wording says so and contains no value, so it is passed on.
                raise ModelUnavailable(f"the model client could not start: {exc}") from exc
        return self._client

    def complete(
        self, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> ModelTurn:
        """Continue the interaction on the server, sending only what is new.

        This is not what DECISIONS 0008 chose and the reason is evidence rather than
        preference. The Interactions API does not accept `model_output` or `function_call`
        as input items, so a tool calling conversation cannot be replayed statelessly: only
        the server can hold the assistant side of it. Each call therefore sends the messages
        added since the last one and carries `previous_interaction_id` forward.

        The local transcript is unaffected. We still own it, it is still what the recorder
        compiles, and it is still what lands in evidence. What moved to the server is the
        model's own view of the conversation, not our record of it.
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
                # Matched on status code rather than on an exception class. The SDK raises
                # from two unrelated hierarchies: google.genai.errors.APIError and an
                # internal compat_errors tree, and both rate limits and 5xx arrive as the
                # latter. An earlier version caught only the former, so the backoff below
                # had never once run. Anything not transient is re-raised untouched.
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
            f"(last: HTTP {_status_code(last) if last else 'unknown'}). The free tier ceiling "
            "and provider 5xx both land here, and a long run brushes both."
        )

    def _retry_after(self, exc: Exception, attempt: int) -> float:
        """Honour the delay the server asks for, falling back to our own backoff.

        A 429 usually carries "Please retry in 38.9s". Guessing shorter than that just
        earns another 429 and burns another request against the same quota.
        """
        match = re.search(r"retry in ([0-9.]+)s", str(exc))
        if match:
            return min(float(match.group(1)) + 1.0, self._max_backoff_s * 2)
        return self._backoff_for(attempt)

    def _backoff_for(self, attempt: int) -> float:
        """Exponential with jitter. Jitter matters because a stalled loop retries in lockstep."""
        delay = min(self._base_backoff_s * (2**attempt), self._max_backoff_s)
        return float(delay * (0.5 + random.random() / 2))
