"""The interface between reading a screen and the saved flow.

Nothing here is web-specific. A desktop surface using UI Automation or the macOS
accessibility API could implement the same six methods, because they all deal in roles,
accessible names and LocatorBundles, never pages, selectors or DOM nodes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.models.capability import Signal, WaitSpec
from src.models.common import RiskClass
from src.models.locator import LocatorBundle
from src.surface.actions import Action, ActionOutcome
from src.surface.observation import Observation


class SurfaceError(Exception):
    """Base class for every failure a surface can raise."""


class LocatorUnresolved(SurfaceError):
    """No tier in the bundle matched anything."""


class LocatorAmbiguous(SurfaceError):
    """A tier matched more than one element.

    Raised straight away and never swallowed. The run stops and asks for a person instead of
    taking the first match, because quietly picking one is how you act on the wrong account.
    """


class SurfaceUnavailable(SurfaceError):
    """The surface could not be reached or has gone away."""


class ActionTimeout(SurfaceError):
    """A wait condition did not become true in time."""


class PolicyViolation(SurfaceError):
    """The policy refused the action.

    `rule` and `reason` are separate so the loop can tell the model the rule without the
    reason. The reason names the pattern that matched, and a model that knows exactly where
    the boundary is will start probing it. See DECISIONS.md 0012.
    """

    def __init__(self, rule: str, reason: str) -> None:
        super().__init__(f"{rule}: {reason}")
        self.rule = rule
        self.reason = reason


@dataclass(frozen=True)
class Resolved:
    """A live handle to one control, plus which tier found it.

    A dataclass, not a pydantic model, because it holds a handle into a live browser session.
    It means nothing once the session ends and should never be serialised.
    """

    strategy: str
    tier_index: int
    handle: object
    frame_path: tuple[str, ...] = ()


@runtime_checkable
class Surface(Protocol):
    """What every surface must provide, web or otherwise."""

    def observe(self) -> Observation:
        """Perceive the current state."""
        ...

    def describe(self, ref: str) -> LocatorBundle:
        """Turn a snapshot ref into a locator bundle that will still work later.

        Has to run while the snapshot is current, because the ref stops meaning anything once
        the next snapshot is taken.
        """
        ...

    def resolve(self, bundle: LocatorBundle) -> Resolved:
        """Find the control a bundle describes, trying each tier in order."""
        ...

    def act(
        self,
        action: Action,
        *,
        wait: WaitSpec | None = None,
        risk: RiskClass | None = None,
        approved: bool = False,
    ) -> ActionOutcome:
        """Perform an action once the policy allows it.

        `approved` is one person's approval for this one call. It skips the approval
        requirement and nothing else: allowed hosts, denied paths and allowed actions still
        apply.

        `wait` is the step's saved WaitSpec, applied after the action. `risk` is the step's
        saved risk class. Discovery has none yet, so the policy falls back to control names.
        """
        ...

    def evaluate(self, signal: Signal) -> bool:
        """Whether a signal currently holds on this surface."""
        ...

    def close(self) -> None:
        """Release the session."""
        ...
