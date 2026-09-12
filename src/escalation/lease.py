"""Who is allowed to touch the browser, as a file two processes can both read.

The lease is the whole of the concurrency design. Automation and the operator console run in
separate processes and share exactly one mutable fact: which of them is driving. That fact is
a small JSON file, written atomically, polled by both sides. See DECISIONS.md 0029 for why a
file and not a queue or a socket.
"""
from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from src.models.common import Holder, LeaseState

STRICT: Final[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

# The transition table is the rule. There is no second place that decides what is legal, and
# no branch anywhere else that special cases a state, so reading this tuple tells you the
# entire protocol.
_ALLOWED: Final[frozenset[tuple[LeaseState, LeaseState]]] = frozenset(
    [
        (LeaseState.RUNNING, LeaseState.PAUSED),
        (LeaseState.PAUSED, LeaseState.HUMAN_CONTROL),
        (LeaseState.HUMAN_CONTROL, LeaseState.RESUMING),
        (LeaseState.RESUMING, LeaseState.RUNNING),
    ]
    # Anything may be abandoned. Expanded here rather than special cased at the check, so the
    # table stays the single source of truth.
    + [(state, LeaseState.CLOSED) for state in LeaseState]
)

# Who holds the browser in each state. Derived rather than stored, because a lease carrying
# both a state and an independently settable holder can express "paused but automation is
# driving", which is the exact bug this file exists to prevent.
_HOLDER_FOR: Final[dict[LeaseState, Holder]] = {
    LeaseState.RUNNING: Holder.AUTOMATION,
    LeaseState.PAUSED: Holder.NONE,
    LeaseState.HUMAN_CONTROL: Holder.HUMAN,
    LeaseState.RESUMING: Holder.AUTOMATION,
    LeaseState.CLOSED: Holder.NONE,
}


class IllegalTransition(Exception):
    """An attempt to move the lease somewhere the protocol does not allow."""

    def __init__(self, current: LeaseState, requested: LeaseState) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"lease cannot move from {current.value} to {requested.value}. "
            f"Legal from {current.value}: "
            f"{sorted(t.value for f, t in _ALLOWED if f is current)}"
        )


class ControlLost(Exception):
    """Automation tried to drive the surface while it did not hold the lease."""

    def __init__(self, state: LeaseState, holder: Holder) -> None:
        self.state = state
        self.holder = holder
        super().__init__(
            f"automation does not hold the control lease: state={state.value}, "
            f"holder={holder.value}"
        )


class ControlLease(BaseModel):
    """A snapshot of who is in control. Immutable; transitions produce a new one."""

    model_config = STRICT

    session_id: str
    holder: Holder
    state: LeaseState
    intervention_id: str | None = None
    updated_at: datetime
    deadline_at: datetime | None = None

    @classmethod
    def start(cls, session_id: str) -> ControlLease:
        return cls(
            session_id=session_id,
            holder=Holder.AUTOMATION,
            state=LeaseState.RUNNING,
            updated_at=datetime.now(UTC),
        )

    def to(
        self,
        state: LeaseState,
        *,
        intervention_id: str | None = None,
        deadline_at: datetime | None = None,
    ) -> ControlLease:
        """The only way to change a lease. Raises rather than clamping to something legal."""
        if (self.state, state) not in _ALLOWED:
            raise IllegalTransition(self.state, state)
        return ControlLease(
            session_id=self.session_id,
            holder=_HOLDER_FOR[state],
            state=state,
            intervention_id=(
                intervention_id if intervention_id is not None else self.intervention_id
            ),
            updated_at=datetime.now(UTC),
            deadline_at=deadline_at if deadline_at is not None else self.deadline_at,
        )

    @property
    def automation_may_act(self) -> bool:
        return self.holder is Holder.AUTOMATION and self.state in (
            LeaseState.RUNNING,
            LeaseState.RESUMING,
        )


class LeaseStore:
    """The file. Reads are cheap and unlocked; writes are atomic.

    No locking, deliberately. The protocol has exactly one legal writer per state, so two
    processes never contend for the same transition: automation owns running and resuming,
    the operator owns paused and human_control. A lock would protect against a case the
    transition table already makes illegal.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> ControlLease:
        return ControlLease.model_validate_json(self.path.read_text())

    def write(self, lease: ControlLease) -> ControlLease:
        """Write to a temp file in the same directory, fsync, then rename over the target.

        Same directory is not incidental: os.replace is only atomic within one filesystem, so
        a temp file in /tmp would silently degrade to a copy and a reader could observe a
        half written lease.
        """
        handle = tempfile.NamedTemporaryFile(
            mode="w", dir=self.path.parent, prefix=".lease-", suffix=".tmp", delete=False
        )
        try:
            with handle as out:
                out.write(lease.model_dump_json(indent=2))
                out.flush()
                os.fsync(out.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
        return lease

    def transition(
        self,
        state: LeaseState,
        *,
        intervention_id: str | None = None,
        deadline_at: datetime | None = None,
    ) -> ControlLease:
        current = self.read()
        return self.write(
            current.to(state, intervention_id=intervention_id, deadline_at=deadline_at)
        )


class InProcessLease:
    """A LeaseStore-shaped object backed by memory, for a run with no operator attached.

    Invariant 10 says there is no unleased surface. Rather than making the assertion optional,
    a surface built without a store gets this: a lease that starts held by automation and
    honours the same transition table. The assertion then runs everywhere, on every code path,
    including every test, which is the only way the invariant is worth anything.
    """

    def __init__(self, session_id: str = "in-process") -> None:
        self._lease = ControlLease.start(session_id)
        self.path = None

    def exists(self) -> bool:
        return True

    def read(self) -> ControlLease:
        return self._lease

    def write(self, lease: ControlLease) -> ControlLease:
        self._lease = lease
        return lease

    def transition(
        self,
        state: LeaseState,
        *,
        intervention_id: str | None = None,
        deadline_at: datetime | None = None,
    ) -> ControlLease:
        self._lease = self._lease.to(
            state, intervention_id=intervention_id, deadline_at=deadline_at
        )
        return self._lease
