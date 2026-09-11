"""Typed action requests and their outcome.

An action names a control by LocatorBundle, never by ref. That is not a convenience: it is
what lets the same action object be produced by the discovery loop and consumed by replay,
and it is design rule 9 expressed in the type system.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.models.capability import Signal
from src.models.locator import LocatorBundle

STRICT = ConfigDict(extra="forbid", frozen=True)


class NavigateAction(BaseModel):
    model_config = STRICT
    kind: Literal["navigate"] = "navigate"
    url: str


class ClickAction(BaseModel):
    model_config = STRICT
    kind: Literal["click"] = "click"
    bundle: LocatorBundle


class TypeAction(BaseModel):
    model_config = STRICT
    kind: Literal["type"] = "type"
    bundle: LocatorBundle
    text: str


class SelectAction(BaseModel):
    model_config = STRICT
    kind: Literal["select"] = "select"
    bundle: LocatorBundle
    value: str


class PressAction(BaseModel):
    model_config = STRICT
    kind: Literal["press"] = "press"
    key: str


class WaitForAction(BaseModel):
    model_config = STRICT
    kind: Literal["wait_for"] = "wait_for"
    signal: Signal


Action = Annotated[
    NavigateAction | ClickAction | TypeAction | SelectAction | PressAction | WaitForAction,
    Field(discriminator="kind"),
]


class ActionOutcome(BaseModel):
    """What happened when an action ran.

    `resolved_strategy` is the telemetry design rules section 6 asks for: a flow that quietly
    slides from role_name to css_fallback over successive runs has drifted, and that is
    invisible unless the winning tier is recorded every time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    resolved_strategy: str | None = None
    attempts: int = 1
    duration_ms: int = 0
    note: str | None = None
