"""The seam between perceiving a surface and the recorded flow.

Nothing in these signatures is web specific. That is the whole point: a desktop surface
driven through UI Automation or the AX API would implement the same six methods, because
every one of them speaks in roles, accessible names and LocatorBundles rather than in pages,
selectors or DOM nodes. design rules section 3.7 asks for that seam to be real, and this is it.
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

    Raised immediately and never swallowed. Per invariant 4 the run stops and escalates
    rather than picking the first match, because picking one silently is how automation
    ends up acting on the wrong account.
    """


class SurfaceUnavailable(SurfaceError):
    """The surface could not be reached or has gone away."""


class ActionTimeout(SurfaceError):
    """A wait condition did not become true in time."""


class PolicyViolation(SurfaceError):
    """The policy gate refused the action. A refusal, not a malfunction.

    `rule` and `reason` are kept apart on purpose. The rule id is safe to hand back to the
    model so it knows the direction is closed. The reason names the pattern that matched,
    which is a description of the allowlist, and a model told where the boundaries are will
    reason about the boundaries.
    """

    def __init__(self, rule: str, reason: str) -> None:
        super().__init__(f"{rule}: {reason}")
        self.rule = rule
        self.reason = reason


@dataclass(frozen=True)
class Resolved:
    """A live handle to one control, plus which tier actually found it.

    Deliberately a dataclass rather than a pydantic model: it wraps a handle onto a live
    session, it is meaningless once that session ends, and nothing should ever be tempted
    to serialize it.
    """

    strategy: str
    tier_index: int
    handle: object
    frame_path: tuple[str, ...] = ()

    @property
    def is_primary(self) -> bool:
        return self.tier_index == 0


@runtime_checkable
class Surface(Protocol):
    """What every surface must provide, web or otherwise."""

    def observe(self) -> Observation:
        """Perceive the current state."""
        ...

    def describe(self, ref: str) -> LocatorBundle:
        """Turn a per-snapshot ref into a durable locator bundle.

        The one method that must run while the observation is still fresh, because the ref
        dies with the snapshot that produced it.
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
    ) -> ActionOutcome:
        """Perform an action, after the policy gate has allowed it.

        `wait` is the recorded WaitSpec for this step, applied after the action lands.
        `risk` is the recorded risk classification, which exists during replay and does not
        during discovery; the gate falls back to control names when it is None.
        """
        ...

    def evaluate(self, signal: Signal) -> bool:
        """Whether a signal currently holds on this surface."""
        ...

    def close(self) -> None:
        """Release the session."""
        ...
