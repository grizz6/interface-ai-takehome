"""Who is allowed to touch the browser, kept in a file both processes can read.

The run and the operator page are separate processes, and the only thing they share is who is
driving. That lives in a small JSON file, written atomically and polled by both. DECISIONS.md
0029 explains why a file and not a queue or socket.
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

# The allowed state changes. Nothing else decides what is legal, so this is the whole
# protocol.
_ALLOWED: Final[frozenset[tuple[LeaseState, LeaseState]]] = frozenset(
    [
        (LeaseState.RUNNING, LeaseState.PAUSED),
        (LeaseState.PAUSED, LeaseState.HUMAN_CONTROL),
        (LeaseState.HUMAN_CONTROL, LeaseState.RESUMING),
        (LeaseState.RESUMING, LeaseState.RUNNING),
    ]
    # Any state can be closed. Listed here rather than special-cased in the check.
    + [(state, LeaseState.CLOSED) for state in LeaseState]
)

# Who holds the browser in each state. Worked out from the state rather than stored, so a
# lease can never say "paused but automation is driving".
_HOLDER_FOR: Final[dict[LeaseState, Holder]] = {
    LeaseState.RUNNING: Holder.AUTOMATION,
    LeaseState.PAUSED: Holder.NONE,
    LeaseState.HUMAN_CONTROL: Holder.HUMAN,
    LeaseState.RESUMING: Holder.AUTOMATION,
    LeaseState.CLOSED: Holder.NONE,
}


class IllegalTransition(Exception):
    """Tried to move the lease to a state that is not allowed from here."""

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
    """Who is in control at one moment. Frozen; each change makes a new one."""

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
        """The only way to change a lease. Raises if the change is not allowed."""
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
    """The lease file. Reads are unlocked and writes are atomic.

    No lock is needed, because each state has only one side allowed to write it: the run owns
    running and resuming, and the operator owns paused and human_control. The two never
    compete for the same change.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> ControlLease:
        return ControlLease.model_validate_json(self.path.read_text())

    def write(self, lease: ControlLease) -> ControlLease:
        """Write to a temp file in the same folder, fsync, then rename it over the lease.

        It has to be the same folder: os.replace is only atomic within one filesystem, so a temp
        file in /tmp would become a copy and a reader could see a half-written lease.
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
    """An in-memory lease for a run with no operator attached.

    Every surface needs a lease. A surface created without a lease file gets this one, which
    starts held by automation and follows the same rules. That way the control check always
    runs, including in every test, instead of being optional.
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
