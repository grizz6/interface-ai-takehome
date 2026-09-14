"""The live session: one browser context, one lease, and the handoff around them.

Handing over, the person's turn and resuming all happen in the same browser context discovery
or replay was already using. Nothing here opens a new page or recreates one.
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

    Takes plain fields instead of a Capability, because discovery can ask for help too and has
    no capability yet. Replay builds one with `from_capability`, and discovery fills in what it
    knows.
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
    """Owns the lease, and is the only thing on the automation side that changes it."""

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
        # An intervention file holds two snapshots of a real screen, so it needs the same
        # redaction as evidence. Otherwise interventions/ would leak what evidence/ hides.
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
        # The surface checks this same lease from now on, so there is only one to keep right.
        if hasattr(surface, "attach_lease"):
            surface.attach_lease(self.lease)
        # Taken when handing over and compared afterwards. Still there if the page recorder
        # gets lost.
        self._before_url: str | None = None
        self._before_aria: str | None = None

    def _redact(self, text: str) -> str:
        return str(self.redactor.redact(text)) if self.redactor is not None else text

    def _redact_actions(self, actions: list[CapturedAction]) -> list[CapturedAction]:
        """Redact the whole serialised action and parse it back, so no field is missed.

        An action has a URL and the text of what was clicked, and both can hold a value:
        /member/100001/subaccount has a member id in it. Redacting named fields means
        remembering every new field, and forgetting the url field is exactly the leak the
        secret scan found. See DECISIONS.md 0041.
        """
        if self.redactor is None:
            return actions
        cleaned: list[CapturedAction] = []
        for action in actions:
            try:
                cleaned.append(
                    CapturedAction.model_validate_json(
                        self._redact(action.model_dump_json())
                    )
                )
            except ValueError:
                # Do not lose the audit entry. Keep the kind and tag, drop the rest.
                cleaned.append(CapturedAction(kind=action.kind, tag=action.tag))
        return cleaned

    # -- control -------------------------------------------------------------
    def assert_control(self) -> None:
        lease = self.lease.read()
        if not lease.automation_may_act:
            raise ControlLost(lease.state, lease.holder)

    def close(self) -> ControlLease:
        return self.lease.transition(LeaseState.CLOSED)

    # -- handing over --------------------------------------------------------
    def escalate(self, reason: StuckReason, context: EscalationContext) -> str:
        """Capture the screen, write the request, install the recorder, then pause.

        The order matters. Everything that uses the browser happens while automation still
        holds the lease, and pausing comes last, so the lease never says paused while
        automation is still driving.
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
            why=self._redact(context.why),
            url=self._redact(observation.url),
            aria_snapshot=self._redact(observation.aria_yaml),
            screenshot_path=screenshot_path,
            params_redacted=context.params_redacted,
        )
        self.store.write(request)

        # Installed now, not when the operator clicks Take control, because the operator page
        # is a separate process with no access to this browser.
        self._before_url = observation.url
        self._before_aria = observation.aria_yaml
        try:
            capture.install(self.surface.page)
        except Exception:  # noqa: BLE001
            # If the script cannot be added, still hand over. The before and after snapshots
            # are still captured, just without a list of actions.
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
        """Wait until the operator gives control back or the deadline passes.

        Returns None on expiry, after closing the session. A caller that gets None must not
        carry on: nobody answered, so nothing was approved.
        """
        limit = deadline or (datetime.now(UTC) + timedelta(seconds=self.deadline_seconds))
        while True:
            lease = self.lease.read()
            if lease.state is LeaseState.RESUMING:
                return self._collect_resolution(intervention_id)
            if lease.state is LeaseState.CLOSED:
                # Still record what the person did when they abort. That is when someone is
                # most likely to want to know what happened.
                self._collect_resolution(intervention_id)
                return None
            if datetime.now(UTC) >= limit:
                self.lease.transition(LeaseState.CLOSED)
                return None
            self._wait(poll_interval)

    def _wait(self, seconds: float) -> None:
        """Wait while the person has the browser.

        Waits through the page rather than time.sleep, so Playwright keeps handling browser
        events. A pop-up raised while the person works would otherwise freeze the page until
        control came back, because the dialog listener only runs when Playwright gets a turn.
        """
        page = getattr(self.surface, "page", None)
        if page is None:
            time.sleep(seconds)
            return
        try:
            page.wait_for_timeout(seconds * 1000)
        except Exception:  # noqa: BLE001
            # A closed or crashed page must not end the wait; the lease decides that.
            time.sleep(seconds)

    def _collect_resolution(self, intervention_id: str) -> InterventionResolution | None:
        """Read the operator's answer and add what was recorded from the page.

        The note is what the operator says happened. The recorded actions and the after
        snapshot show what did. Both are saved, but resuming only trusts the page.
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
            update={
                "human_actions": self._redact_actions(actions),
                "url_after": url_after,
                "aria_after": aria_after,
            }
        )
        self.store.resolve(intervention_id, enriched)
        return enriched

    # -- taking it back ------------------------------------------------------
    def resume(self) -> ControlLease:
        """resuming -> running. Called after the caller has checked the page again."""
        return self.lease.transition(LeaseState.RUNNING)

    @property
    def holder(self) -> Holder:
        return self.lease.read().holder
