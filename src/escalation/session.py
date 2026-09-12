"""The live session: one browser context, one lease, and the handoff protocol around it.

Invariant 7 says one browser context per run, and this is the module that has to mean it.
Escalation, human control and resume all happen on the same context that discovery or replay
was already using. Nothing here opens a page, and nothing here recreates one.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from src.escalation import capture
from src.escalation.intervention import (
    CapturedAction,
    InterventionRequest,
    InterventionResolution,
    InterventionStore,
    load_actions,
    new_intervention_id,
)
from src.escalation.lease import ControlLease, ControlLost, InProcessLease, LeaseStore
from src.models.capability import Capability, ParamDescriptor, describe_params
from src.models.common import Holder, LeaseState, RiskClass, StuckReason

DEFAULT_POLL_SECONDS: Final[float] = 0.5
DEFAULT_DEADLINE_SECONDS: Final[int] = 900


class EscalationContext:
    """What the caller knows about why it stopped.

    Carries identity fields rather than a Capability, because discovery escalates too and
    discovery has no capability yet: that is the artifact it is in the middle of earning. The
    engine builds one with `from_capability`; the loop builds one with what it knows.
    """

    def __init__(
        self,
        *,
        capability_id: str,
        capability_version: str,
        goal_text: str,
        step_index: int,
        step_description: str,
        risk: RiskClass,
        why: str,
        run_id: str,
        params_redacted: list[ParamDescriptor] | None = None,
    ) -> None:
        self.capability_id = capability_id
        self.capability_version = capability_version
        self.goal_text = goal_text
        self.step_index = step_index
        self.step_description = step_description
        self.risk = risk
        self.why = why
        self.run_id = run_id
        self.params_redacted = params_redacted or []

    @classmethod
    def from_capability(
        cls,
        capability: Capability,
        params: dict[str, Any],
        *,
        step_index: int,
        step_description: str,
        risk: RiskClass,
        why: str,
        run_id: str,
    ) -> EscalationContext:
        return cls(
            capability_id=capability.capability_id,
            capability_version=capability.version,
            goal_text=capability.provenance.goal_text,
            step_index=step_index,
            step_description=step_description,
            risk=risk,
            why=why,
            run_id=run_id,
            params_redacted=describe_params(capability, params),
        )


class Session:
    """Owns the lease, and is the only thing allowed to move it on the automation side."""

    def __init__(
        self,
        surface: Any,
        *,
        session_id: str,
        lease_path: Path | str | None = None,
        interventions_dir: Path | str = "interventions",
        evidence_sink: Any = None,
        deadline_seconds: int = DEFAULT_DEADLINE_SECONDS,
        redactor: Any = None,
    ) -> None:
        # An intervention file carries two aria snapshots of a real back office screen, so it
        # is on the same footing as evidence and gets the same treatment. Without this,
        # invariant 6 would hold for evidence/ and quietly not hold for interventions/.
        self.redactor = redactor
        self.surface = surface
        self.session_id = session_id
        self.store = InterventionStore(interventions_dir)
        self.evidence_sink = evidence_sink
        self.deadline_seconds = deadline_seconds
        self.lease: LeaseStore | InProcessLease = (
            LeaseStore(lease_path) if lease_path is not None else InProcessLease(session_id)
        )
        if not (isinstance(self.lease, InProcessLease) or self.lease.exists()):
            self.lease.write(ControlLease.start(session_id))
        # The surface asserts against this same store from now on, so invariant 10 is one
        # object rather than two that have to agree.
        if hasattr(surface, "attach_lease"):
            surface.attach_lease(self.lease)
        # Captured at escalate, compared after the handoff. Kept even when the injected
        # recorder is lost, which is the point of having them.
        self._before_url: str | None = None
        self._before_aria: str | None = None

    def _redact(self, text: str) -> str:
        return str(self.redactor.redact(text)) if self.redactor is not None else text

    # -- control -------------------------------------------------------------
    def assert_control(self) -> None:
        lease = self.lease.read()
        if not lease.automation_may_act:
            raise ControlLost(lease.state, lease.holder)

    def close(self) -> ControlLease:
        return self.lease.transition(LeaseState.CLOSED)

    # -- handing over --------------------------------------------------------
    def escalate(self, reason: StuckReason, context: EscalationContext) -> str:
        """Capture the state, write the request, arm the recorder, then pause.

        Order matters. Everything that needs the browser happens while automation still holds
        the lease, and the pause is the last thing, so there is no window in which the lease
        says paused but automation is still driving.
        """
        observation = self.surface.observe()
        screenshot_path: str | None = None
        if self.evidence_sink is not None and observation.screenshot_png:
            screenshot_path = str(self.evidence_sink.screenshot(observation.screenshot_png))

        intervention_id = new_intervention_id(context.run_id, context.step_index, reason)
        deadline = datetime.now(UTC) + timedelta(seconds=self.deadline_seconds)

        request = InterventionRequest(
            id=intervention_id,
            session_id=self.session_id,
            created_at=datetime.now(UTC),
            deadline_at=deadline,
            capability_id=context.capability_id,
            capability_version=context.capability_version,
            goal_text=context.goal_text,
            step_index=context.step_index,
            step_description=context.step_description,
            risk=context.risk,
            reason=reason,
            why=context.why,
            url=self._redact(observation.url),
            aria_snapshot=self._redact(observation.aria_yaml),
            screenshot_path=screenshot_path,
            params_redacted=context.params_redacted,
        )
        self.store.write(request)

        # Armed before the handoff, not on take. The console is a separate process with no
        # handle on this page, so installation has to happen on this side of the boundary.
        self._before_url = observation.url
        self._before_aria = observation.aria_yaml
        try:
            capture.install(self.surface.page)
        except Exception:  # noqa: BLE001
            # A page that refuses the injection still hands over. The before and after
            # snapshots remain, and the resolution simply carries no action list.
            pass

        self.lease.transition(
            LeaseState.PAUSED, intervention_id=intervention_id, deadline_at=deadline
        )
        return intervention_id

    def await_return(
        self,
        intervention_id: str,
        *,
        poll_interval: float = DEFAULT_POLL_SECONDS,
        deadline: datetime | None = None,
    ) -> InterventionResolution | None:
        """Block until the operator gives the lease back, or until the deadline passes.

        Returns None on expiry, having closed the session. A caller that gets None is not
        allowed to carry on: nobody answered, so nobody authorized anything.
        """
        limit = deadline or (datetime.now(UTC) + timedelta(seconds=self.deadline_seconds))
        while True:
            lease = self.lease.read()
            if lease.state is LeaseState.RESUMING:
                return self._collect_resolution(intervention_id)
            if lease.state is LeaseState.CLOSED:
                # An aborted handoff still has to record what the human did. Losing the audit
                # trail because the answer was "stop" is the wrong way round: that is the case
                # where somebody will most want to know what happened in the session.
                self._collect_resolution(intervention_id)
                return None
            if datetime.now(UTC) >= limit:
                self.lease.transition(LeaseState.CLOSED)
                return None
            time.sleep(poll_interval)

    def _collect_resolution(self, intervention_id: str) -> InterventionResolution | None:
        """Read what the operator wrote, and add what the page itself observed.

        The operator's note is a claim. The captured actions and the after snapshot are
        evidence. Both go on the record; only the second is trusted by resume.
        """
        request = self.store.read(intervention_id)
        resolution = request.resolution
        if resolution is None:
            return None

        actions: list[CapturedAction] = list(resolution.human_actions)
        url_after = resolution.url_after
        aria_after = resolution.aria_after
        try:
            actions.extend(load_actions(capture.drain(self.surface.page)))
            after = self.surface.observe()
            url_after = self._redact(after.url)
            aria_after = self._redact(after.aria_yaml)
        except Exception:  # noqa: BLE001
            pass

        enriched = resolution.model_copy(
            update={"human_actions": actions, "url_after": url_after, "aria_after": aria_after}
        )
        self.store.resolve(intervention_id, enriched)
        return enriched

    # -- taking it back ------------------------------------------------------
    def resume(self) -> ControlLease:
        """resuming -> running. Called once the caller has re-verified the surface."""
        return self.lease.transition(LeaseState.RUNNING)

    @property
    def holder(self) -> Holder:
        return self.lease.read().holder
