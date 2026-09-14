"""A web surface driven by Playwright, using one browser context for its whole life.

The two methods that matter most:

describe() turns a snapshot ref into a LocatorBundle while the snapshot is still current. It
is the only place a ref is read, and nothing it returns contains one.

resolve() tries each tier in order and stops as soon as a tier matches more than one element.
It never moves on to the next tier when something is ambiguous and never takes the first
match, because "there were two Select buttons and I picked one" is how you act on the wrong
account.
"""
from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any, cast

from playwright.sync_api import Locator as PWLocator
from playwright.sync_api import Playwright, expect, sync_playwright

from src.models.capability import Signal, WaitSpec
from src.models.common import RiskClass, SignalKind
from src.models.locator import (
    ContainerOrdinalLocator,
    ContainerRef,
    CssFallbackLocator,
    LabelRelationLocator,
    Locator as LocatorSpec,
    LocatorBundle,
    RoleNameLocator,
    TextRelationLocator,
)
from src.models.policy import PolicyConfig
from src.policy.gate import Blocked, PolicyGate
from src.surface.actions import Action, ActionOutcome
from src.surface.locating import Built, Scope, build, frame_scope
from src.surface.observation import Observation, parse_aria_snapshot
from src.escalation.lease import ControlLost, InProcessLease
from src.evidence.failure import TierProbe

_APPROVAL_RULE = "risky_action_policy:require_approval"
# Black instead of Playwright's default pink, so a masked area looks removed, not glitched.
MASK_COLOR = "#000000"
from src.surface.protocol import (
    ActionTimeout,
    LocatorAmbiguous,
    LocatorUnresolved,
    PolicyViolation,
    Resolved,
    SurfaceUnavailable,
)

LABEL_ROLES = {"cell", "columnheader"}
ROW_ROLES = {"row", "group"}


class WebSurface:
    """One browser context and one page for the life of the surface, never a fresh one."""

    def __init__(
        self,
        config: PolicyConfig,
        gate: PolicyGate,
        *,
        headless: bool = True,
        resolve_timeout_ms: int = 5000,
        poll_ms: int = 100,
        nav_settle_ms: int = 500,
    ) -> None:
        # The resolve timeout covers the whole bundle, not each tier. Every tier is tried once
        # without waiting first, so a bundle that matches on its primary costs nothing, and one
        # that matches nothing waits once rather than once per tier.
        self._resolve_timeout_ms = resolve_timeout_ms
        self._poll_ms = poll_ms
        self._nav_settle_ms = nav_settle_ms
        self._config = config
        self._gate = gate
        self._pw: Playwright = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._last: Observation | None = None
        # Every surface has a lease. With no operator attached it is an in-process lease that
        # automation already holds, so the control check below runs on every path, tests too.
        self._lease: Any = InProcessLease()
        # Fields holding a pii or secret value, blacked out of every screenshot this surface
        # takes. Empty until a run declares them.
        self._mask_bundles: list[Any] = []
        # A 500 from the app is not the same as a check that failed, and only the HTTP status
        # tells them apart. Playwright renders an error page like any other page.
        self._last_status: int | None = None
        self._page.on("response", self._remember_status)

    @property
    def page(self) -> Any:
        """The live page. For the operator handoff, not for building locators."""
        return self._page

    def attach_lease(self, lease: Any) -> None:
        """Swap the in-process lease for the shared one a Session owns."""
        self._lease = lease

    def assert_control(self) -> None:
        """Raise unless automation holds the lease. Called before anything that acts or decides."""
        lease = self._lease.read()
        if not lease.automation_may_act:
            raise ControlLost(lease.state, lease.holder)

    def _remember_status(self, response: Any) -> None:
        if response.request.is_navigation_request() and response.frame is self._page.main_frame:
            self._last_status = int(response.status)

    @property
    def last_status(self) -> int | None:
        """HTTP status of the most recent main frame navigation, if there was one."""
        return self._last_status

    def set_pii_masks(self, bundles: list[Any]) -> None:
        """Fields holding a pii or secret value, to be blacked out in screenshots.

        Set once per run from the capability's declared inputs. DECISIONS.md 0037 explains why
        this finds the field on the live page instead of using the saved geometry_hint.
        """
        self._mask_bundles = list(bundles)

    def _mask_locators(self) -> list[Any]:
        """Build a locator for each masked field, including ones not on this screen.

        Playwright ignores a mask that matches nothing, so fields from other steps cost
        nothing. These are built, not resolved, because resolve waits and raises, and neither
        is wanted while taking a screenshot.
        """
        locators: list[Any] = []
        for bundle in self._mask_bundles:
            scope: Scope = frame_scope(self._page, bundle.frame_path)
            for spec in [bundle.primary, *bundle.fallbacks]:
                try:
                    locators.append(build(scope, spec).target)
                except Exception:  # noqa: BLE001
                    # Not worth failing a screenshot over a tier that cannot be built.
                    continue
        return locators

    def probe_tiers(self, bundle: LocatorBundle) -> list[TierProbe]:
        """How many elements each tier matches right now. For failure reports only."""
        scope: Scope = frame_scope(self._page, bundle.frame_path)
        probes: list[TierProbe] = []
        for index, spec in enumerate([bundle.primary, *bundle.fallbacks]):
            try:
                built = build(scope, spec)
                guard = built.guard.count() if built.guard is not None else None
                probes.append(
                    TierProbe(
                        tier_index=index,
                        strategy=spec.strategy,
                        matched=built.target.count(),
                        container_matched=guard,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                probes.append(
                    TierProbe(
                        tier_index=index, strategy=spec.strategy, note=type(exc).__name__
                    )
                )
        return probes

    def dom_snapshot(self) -> str:
        """Raw HTML, for evidence only. Never for locating anything."""
        return str(self._page.content())

    # -- perception ----------------------------------------------------------
    def observe(self) -> Observation:
        try:
            aria = self._page.aria_snapshot(mode="ai", boxes=True)
            frame_names = [frame.name for frame in self._page.frames[1:]]
            observation = Observation(
                url=self._page.url,
                title=self._page.title(),
                aria_yaml=aria,
                elements=parse_aria_snapshot(aria, frame_names),
                screenshot_png=self._page.screenshot(
                    type="png", mask=self._mask_locators(), mask_color=MASK_COLOR
                ),
                captured_at=datetime.now(UTC),
            )
        except OSError as exc:
            raise SurfaceUnavailable(str(exc)) from exc
        self._last = observation
        return observation

    def _require_observation(self) -> Observation:
        if self._last is None:
            raise SurfaceUnavailable("no observation yet; call observe() first")
        return self._last

    # -- describing ----------------------------------------------------------
    def describe(self, ref: str) -> LocatorBundle:
        """Build a locator bundle for the element behind a snapshot ref.

        Tiers are tried best first. Every other tier that also matches exactly one element is
        kept as a fallback, so the bundle has real backups.
        """
        observation = self._require_observation()
        element = observation.by_ref(ref)
        if element is None:
            raise LocatorUnresolved(f"ref {ref!r} is not in the current observation")

        failures: list[str] = []
        candidates: list[LocatorSpec] = []

        tier1 = self._tier_role_name(observation, element, failures)
        if tier1 is not None:
            candidates.append(tier1)
        tier2 = self._tier_label_relation(observation, element, failures)
        if tier2 is not None:
            candidates.append(tier2)
        tier3 = self._tier_container_ordinal(observation, element, failures)
        if tier3 is not None:
            candidates.append(tier3)

        tier4 = self._tier_text_relation(observation, element, failures)
        candidates.extend(tier4)

        verified = self._verify(candidates, element.frame_path)
        if candidates and not verified:
            # Every tier produced a candidate and none matched the live page. That means the
            # page is not what the snapshot showed, not that the locators are bad. Record both
            # URLs so whoever picks this up can see what happened.
            counts = ", ".join(
                f"{c.strategy}={self._live_count(c, element.frame_path)}" for c in candidates
            )
            failures.append(
                f"every tier produced a candidate and none resolved on the live page "
                f"(match counts: {counts}); the page is now at {self._page.url!r} while the "
                f"observation was taken at {observation.url!r}"
            )
        tier5 = self._tier_css(element, verified, failures)
        if tier5 is not None:
            verified.append(tier5)

        # A bundle may carry each strategy at most once, so keep the first that verified.
        seen: set[str] = set()
        deduped: list[LocatorSpec] = []
        for spec in verified:
            if spec.strategy in seen:
                continue
            seen.add(spec.strategy)
            deduped.append(spec)
        verified = deduped

        if not verified:
            raise LocatorUnresolved(
                f"cannot describe {element.role!r} ref {ref!r}: " + "; ".join(failures)
            )

        if isinstance(verified[0], CssFallbackLocator):
            # Only CSS worked, and LocatorBundle refuses a CSS primary. Rather than bend the
            # schema, refuse to describe the element instead of saving a flow that depends on
            # a generated DOM id.
            raise LocatorUnresolved(
                f"{element.role!r} ref {ref!r} can only be located by CSS, and the schema "
                "forbids a brittle primary. Not even its visible text resolves uniquely. "
                "Reasons the other tiers failed: " + "; ".join(failures)
            )

        return LocatorBundle(
            primary=verified[0],
            fallbacks=verified[1:],
            frame_path=list(element.frame_path),
            geometry_hint=element.box,
            recorded_accessible_name=element.name,
            notes=("; ".join(failures) or None),
        )

    def _tier_role_name(
        self, observation: Observation, element: Any, failures: list[str]
    ) -> LocatorSpec | None:
        if not element.role_resolvable:
            failures.append(
                f"tier 1 unavailable: {element.role!r} is not an ARIA role, so get_by_role "
                "can never find this element"
            )
            return None
        if not element.name:
            failures.append("tier 1 unavailable: element has no accessible name")
            return None
        matches = observation.find(role=element.role, name=element.name)
        if len(matches) != 1:
            failures.append(
                f"tier 1 unavailable: role {element.role!r} with name {element.name!r} "
                f"matches {len(matches)} elements in this observation"
            )
            return None
        return RoleNameLocator(role=element.role, name=element.name, exact=True)

    def _tier_label_relation(
        self, observation: Observation, element: Any, failures: list[str]
    ) -> LocatorSpec | None:
        if not element.role_resolvable:
            return None
        for ancestor in observation.ancestors_of(element.ref):
            if ancestor.role not in ROW_ROLES:
                continue
            for sibling in observation.descendants_of(ancestor.ref):
                if sibling.ref == element.ref or sibling.role not in LABEL_ROLES:
                    continue
                if sibling.name:
                    return LabelRelationLocator(
                        label_text=sibling.name,
                        relation="cell_to_left",
                        role=element.role,
                    )
            break
        failures.append("tier 2 unavailable: no text bearing cell labels this element")
        return None

    def _tier_container_ordinal(
        self, observation: Observation, element: Any, failures: list[str]
    ) -> LocatorSpec | None:
        if not element.role_resolvable:
            return None
        heading = observation.nearest_heading(element.ref)
        if heading is None:
            failures.append("tier 3 unavailable: no enclosing container has a heading")
            return None
        container, heading_text = heading
        same_role = [
            d.ref
            for d in observation.descendants_of(container.ref)
            if d.role == element.role and (not element.name or d.name == element.name)
        ]
        if element.ref not in same_role:
            return None
        return ContainerOrdinalLocator(
            container=ContainerRef(
                heading_text=heading_text,
                role=container.role,
                dom_id_hint=container.attributes.get("id"),
            ),
            role=element.role,
            ordinal=same_role.index(element.ref),
            name=element.name,
        )

    def _tier_text_relation(
        self, observation: Observation, element: Any, failures: list[str]
    ) -> list[LocatorSpec]:
        """Visible text, the only tier that can find a control with no ARIA role.

        Offers two candidates, unscoped first. Verification keeps the ones that match exactly
        one element, and describe() keeps the first of each strategy.
        """
        if not element.name:
            failures.append("tier 4 unavailable: element has no visible text")
            return []
        candidates: list[LocatorSpec] = [
            TextRelationLocator(text=element.name, exact=True)
        ]
        heading = observation.nearest_heading(element.ref)
        if heading is not None:
            container, heading_text = heading
            candidates.append(
                TextRelationLocator(
                    text=element.name,
                    exact=True,
                    container=ContainerRef(
                        heading_text=heading_text, role=container.role
                    ),
                )
            )
        return candidates

    def _tier_css(
        self, element: Any, verified: list[LocatorSpec], failures: list[str]
    ) -> LocatorSpec | None:
        """Last resort. Reads a DOM id, the only place raw DOM is used for locating."""
        scope = frame_scope(self._page, element.frame_path)
        probe: PWLocator | None = None
        if verified:
            probe = build(scope, verified[0]).target
        elif element.name:
            probe = cast(Any, scope).get_by_text(element.name, exact=True)
        if probe is None or probe.count() != 1:
            return None
        dom_id = probe.get_attribute("id")
        if not dom_id:
            return None
        return CssFallbackLocator(
            css=f"#{dom_id}",
            note=(
                "brittle, recorded because no better tier applied. "
                + ("; ".join(failures) or "no reason recorded")
            ),
        )

    def _live_count(self, spec: LocatorSpec, frame_path: list[str]) -> int | str:
        """How many elements a tier matches right now. For error messages only."""
        try:
            built = build(frame_scope(self._page, frame_path), spec)
            if built.guard is not None and built.guard.count() != 1:
                return f"container x{built.guard.count()}"
            return int(built.target.count())
        except (ValueError, RuntimeError) as exc:
            return f"error: {type(exc).__name__}"

    def _verify(
        self, specs: list[LocatorSpec], frame_path: list[str]
    ) -> list[LocatorSpec]:
        """Every candidate that matches exactly one element, waiting if none do yet.

        Zero matches is not believed until the timeout, because a control that has not
        rendered yet looks the same as a tier that does not apply. More than one match just
        means not unique. It is not an error here, since that is how tier 1 gets dropped for a
        control whose name is shared.
        """
        if not specs:
            return []
        scope = frame_scope(self._page, frame_path)
        builts = [(spec, build(scope, spec)) for spec in specs]
        deadline = time.monotonic() + self._resolve_timeout_ms / 1000
        while True:
            unique = [
                spec
                for spec, built in builts
                if not (built.guard is not None and built.guard.count() != 1)
                and built.target.count() == 1
            ]
            if unique or time.monotonic() >= deadline:
                return unique
            self._page.wait_for_timeout(self._poll_ms)

    # -- resolution ----------------------------------------------------------
    def resolve(self, bundle: LocatorBundle) -> Resolved:
        """Try each tier in order, and wait before deciding nothing matched.

        Zero matches and two matches are handled differently.

        Zero is not trusted straight away. A page that is still rendering shows zero for a
        control that is about to appear, and treating that as "this tier does not apply" turns
        a slow page into an error, or worse, a quiet slide down to a weaker tier. So every tier
        is retried until the shared timeout runs out.

        Two or more is never waited on and never passed to a fallback. It is ambiguous, it
        raises at once, and the run stops instead of picking one. Waiting could only turn two
        into one by luck, and a fallback that happens to work does not make it safe.
        """
        # Resolving drives the browser and decides what happens next, so it needs the lease
        # just like acting does.
        self.assert_control()

        scope: Scope = frame_scope(self._page, bundle.frame_path)
        tiers: list[LocatorSpec] = [bundle.primary, *bundle.fallbacks]
        builts: list[tuple[int, LocatorSpec, Built]] = [
            (index, spec, build(scope, spec)) for index, spec in enumerate(tiers)
        ]
        deadline = time.monotonic() + self._resolve_timeout_ms / 1000

        while True:
            for index, spec, built in builts:
                if built.guard is not None:
                    guards = built.guard.count()
                    if guards > 1:
                        raise LocatorAmbiguous(
                            f"{spec.strategy}: container matched {guards} regions, so the "
                            "ordinal is meaningless. Stopping rather than guessing."
                        )
                    if guards != 1:
                        continue
                count = built.target.count()
                if count > 1:
                    raise LocatorAmbiguous(
                        f"{spec.strategy} matched {count} elements. The run stops and asks "
                        "for a person rather than taking the first match."
                    )
                if count == 1:
                    return Resolved(
                        strategy=spec.strategy,
                        tier_index=index,
                        handle=built.target,
                        frame_path=tuple(bundle.frame_path),
                    )
            if time.monotonic() >= deadline:
                break
            self._page.wait_for_timeout(self._poll_ms)

        raise LocatorUnresolved(
            "no tier matched after waiting "
            f"{self._resolve_timeout_ms}ms: " + ", ".join(s.strategy for s in tiers)
        )

    # -- action --------------------------------------------------------------
    def act(
        self,
        action: Action,
        *,
        wait: WaitSpec | None = None,
        risk: RiskClass | None = None,
        approved: bool = False,
    ) -> ActionOutcome:
        # Control first, then policy. Acting while a person is driving is not a policy question,
        # it is two people on one browser, and the policy check cannot see that.
        self.assert_control()

        decision = self._gate.check(action, risk)
        if isinstance(decision, Blocked):
            # This is where policy is enforced, so a person's approval has to be passed in here
            # rather than handled by the caller. It skips one rule, for this call only.
            if not (approved and decision.rule.startswith(_APPROVAL_RULE)):
                raise PolicyViolation(decision.rule, decision.reason)

        started = time.monotonic()
        strategy: str | None = None

        if action.kind == "navigate":
            self._page.goto(action.url, wait_until="load")
            self._assert_arrival()
        elif action.kind == "press":
            self._page.keyboard.press(action.key)
        elif action.kind == "wait_for":
            self._await_signal(action.signal, timeout_ms=10000, poll_ms=250)
        else:
            resolved = self.resolve(action.bundle)
            strategy = resolved.strategy
            handle = cast(PWLocator, resolved.handle)
            if action.kind == "click":
                previous_url = self._page.url
                handle.click()
                self._assert_arrival(previous_url)
            elif action.kind == "type":
                handle.fill(action.text)
            elif action.kind == "select":
                handle.select_option(action.value)

        if wait is not None:
            self._apply_wait(wait)

        return ActionOutcome(
            ok=True,
            resolved_strategy=strategy,
            duration_ms=int((time.monotonic() - started) * 1000),
            note=decision.note if not isinstance(decision, Blocked) else None,
        )

    def _assert_arrival(self, previous_url: str | None = None) -> None:
        """Check the URL again after anything that might have navigated.

        Reading page.url right after click() can beat the navigation, since click returns as
        soon as the event is sent. The URL could still be the old page, so a click onto a
        denied route would be checked against the allowed page it came from. So wait for load
        first, and if the URL has not changed, give it a short window before deciding the
        click did not navigate.
        """
        self._page.wait_for_load_state("load")
        if previous_url is not None and self._page.url == previous_url:
            deadline = time.monotonic() + self._nav_settle_ms / 1000
            while time.monotonic() < deadline and self._page.url == previous_url:
                self._page.wait_for_timeout(self._poll_ms)
            if self._page.url != previous_url:
                self._page.wait_for_load_state("load")

        decision = self._gate.check_url(self._page.url)
        if isinstance(decision, Blocked):
            raise PolicyViolation(decision.rule, decision.reason)

    def _apply_wait(self, wait: WaitSpec) -> None:
        if wait.condition == "load":
            self._page.wait_for_load_state("load")
        elif wait.condition == "network_idle":
            self._page.wait_for_load_state("networkidle")
        elif wait.condition == "fixed":
            self._page.wait_for_timeout(wait.timeout_ms)
        elif wait.condition == "signal" and wait.signal is not None:
            self._await_signal(wait.signal, wait.timeout_ms, wait.poll_ms)

    def _await_signal(self, signal: Signal, timeout_ms: int, poll_ms: int) -> None:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            # timeout 0, because this loop is already doing the waiting
            if self._evaluate(signal, 0):
                return
            self._page.wait_for_timeout(poll_ms)
        raise ActionTimeout(
            f"signal {signal.kind} did not hold within {timeout_ms}ms"
        )

    # -- evaluation ----------------------------------------------------------
    def evaluate(self, signal: Signal) -> bool:
        """Check a signal, waiting for an element to appear before calling it absent."""
        return self._evaluate(signal, self._resolve_timeout_ms)

    def _evaluate(self, signal: Signal, timeout_ms: int) -> bool:
        if signal.kind is SignalKind.URL_MATCHES:
            return bool(re.search(signal.url_pattern or "", self._page.url))
        if signal.kind in (SignalKind.TEXT_PRESENT, SignalKind.TEXT_ABSENT):
            found = self._text_matches(signal)
            return found if signal.kind is SignalKind.TEXT_PRESENT else not found
        if signal.kind in (SignalKind.ELEMENT_PRESENT, SignalKind.ELEMENT_ABSENT):
            present = self._element_present(signal, timeout_ms)
            return present if signal.kind is SignalKind.ELEMENT_PRESENT else not present
        return self._aria_matches(signal)

    def _scope_text(self, frame_path: list[str]) -> str:
        scope = frame_scope(self._page, frame_path)
        body = cast(Any, scope).locator("body")
        return str(body.inner_text()) if body.count() else ""

    def _text_matches(self, signal: Signal) -> bool:
        text = self._scope_text(signal.frame_path)
        if signal.pattern is not None:
            flags = 0 if signal.case_sensitive else re.IGNORECASE
            return bool(re.search(signal.pattern, text, flags))
        needle = signal.text or ""
        if signal.case_sensitive:
            return needle in text
        return needle.lower() in text.lower()

    def _element_present(self, signal: Signal, timeout_ms: int) -> bool:
        """Wait for the element to appear before calling it absent.

        Two matches still counts as present. The no-guessing rule is about acting on an
        element, not about noticing that one exists.
        """
        bundle = signal.locator
        if bundle is None:
            return False
        scope = frame_scope(self._page, bundle.frame_path)
        builts = [build(scope, spec) for spec in [bundle.primary, *bundle.fallbacks]]
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            for built in builts:
                if built.guard is not None and built.guard.count() != 1:
                    continue
                if built.target.count() >= 1:
                    return True
            if time.monotonic() >= deadline:
                return False
            self._page.wait_for_timeout(self._poll_ms)

    def _aria_matches(self, signal: Signal) -> bool:
        scope = frame_scope(self._page, signal.frame_path)
        root = cast(Any, scope).locator("body")
        try:
            expect(root).to_match_aria_snapshot(signal.aria_template or "", timeout=2000)
        except AssertionError:
            return False
        return True

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._pw.stop()
