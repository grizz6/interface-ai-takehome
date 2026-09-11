"""A web surface driven through Playwright, one browser context for its whole life.

Two things here carry the weight of the phase.

describe() converts a per-snapshot ref into a durable LocatorBundle while the observation is
still fresh. It is the only place a ref is ever read, and nothing it returns contains one.
That is design rule 9 implemented rather than merely promised.

resolve() tries each tier in order and stops the moment a tier matches more than one element.
It never falls through to the next tier on ambiguity and never takes the first match, because
"there are two Select buttons and I picked one" is how automation acts on the wrong account.
That is invariant 4.
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
)
from src.models.policy import PolicyConfig
from src.policy.gate import Blocked, PolicyGate
from src.surface.actions import Action, ActionOutcome
from src.surface.locating import Scope, build, frame_scope
from src.surface.observation import Observation, parse_aria_snapshot
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
    """One browser context, one page, for the life of the surface. Invariant 7."""

    def __init__(
        self, config: PolicyConfig, gate: PolicyGate, *, headless: bool = True
    ) -> None:
        self._config = config
        self._gate = gate
        self._pw: Playwright = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()
        self._last: Observation | None = None

    @property
    def page(self) -> Any:
        """The live page. For the operator handoff in phase 7, not for building locators."""
        return self._page

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
                screenshot_png=self._page.screenshot(type="png"),
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

    # -- the core of the phase ----------------------------------------------
    def describe(self, ref: str) -> LocatorBundle:
        """Build a durable bundle for the element behind a per-snapshot ref.

        Tier order follows design rules section 6. Every tier that also resolves uniquely is
        kept as a fallback, so a bundle carries genuine redundancy rather than one strategy
        with an empty list beside it.
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

        verified = [c for c in candidates if self._unique(c, element.frame_path)]
        tier4 = self._tier_css(element, verified, failures)
        if tier4 is not None:
            verified.append(tier4)

        if not verified:
            raise LocatorUnresolved(
                f"cannot describe {element.role!r} ref {ref!r}: " + "; ".join(failures)
            )

        if isinstance(verified[0], CssFallbackLocator):
            # Tiers 1 to 3 all failed and CSS is the only thing left, but LocatorBundle
            # refuses a brittle primary. Both rules are deliberate and they collide here.
            # Invariant 1 says code adapts to the schema, so this refuses to describe the
            # element rather than quietly recording a flow that hangs off a DOM id.
            raise LocatorUnresolved(
                f"{element.role!r} ref {ref!r} can only be located by CSS, and the schema "
                "forbids a brittle primary. This control cannot be recorded durably as "
                "things stand. Reasons the other tiers failed: " + "; ".join(failures)
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

    def _tier_css(
        self, element: Any, verified: list[LocatorSpec], failures: list[str]
    ) -> LocatorSpec | None:
        """Last resort. Reads a DOM id, which is the one place raw DOM is permitted."""
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

    def _unique(self, spec: LocatorSpec, frame_path: list[str]) -> bool:
        scope = frame_scope(self._page, frame_path)
        built = build(scope, spec)
        if built.guard is not None and built.guard.count() != 1:
            return False
        return built.target.count() == 1

    # -- resolution ----------------------------------------------------------
    def resolve(self, bundle: LocatorBundle) -> Resolved:
        scope: Scope = frame_scope(self._page, bundle.frame_path)
        tiers: list[LocatorSpec] = [bundle.primary, *bundle.fallbacks]
        for index, spec in enumerate(tiers):
            built = build(scope, spec)
            if built.guard is not None:
                guards = built.guard.count()
                if guards > 1:
                    raise LocatorAmbiguous(
                        f"{spec.strategy}: container matched {guards} regions, so the "
                        "ordinal is meaningless. Stopping rather than guessing."
                    )
                if guards == 0:
                    continue
            count = built.target.count()
            if count > 1:
                raise LocatorAmbiguous(
                    f"{spec.strategy} matched {count} elements. Invariant 4: the run stops "
                    "and escalates rather than taking the first match."
                )
            if count == 1:
                return Resolved(
                    strategy=spec.strategy,
                    tier_index=index,
                    handle=built.target,
                    frame_path=tuple(bundle.frame_path),
                )
        raise LocatorUnresolved(
            "no tier matched: " + ", ".join(s.strategy for s in tiers)
        )

    # -- action --------------------------------------------------------------
    def act(
        self,
        action: Action,
        *,
        wait: WaitSpec | None = None,
        risk: RiskClass | None = None,
    ) -> ActionOutcome:
        decision = self._gate.check(action, risk)
        if isinstance(decision, Blocked):
            raise PolicyViolation(f"{decision.rule}: {decision.reason}")

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
                handle.click()
                self._assert_arrival()
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

    def _assert_arrival(self) -> None:
        """Re-check the URL after anything that may have navigated. See gate docstring."""
        decision = self._gate.check_url(self._page.url)
        if isinstance(decision, Blocked):
            raise PolicyViolation(f"{decision.rule}: {decision.reason}")

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
            if self.evaluate(signal):
                return
            self._page.wait_for_timeout(poll_ms)
        raise ActionTimeout(
            f"signal {signal.kind} did not hold within {timeout_ms}ms"
        )

    # -- evaluation ----------------------------------------------------------
    def evaluate(self, signal: Signal) -> bool:
        if signal.kind is SignalKind.URL_MATCHES:
            return bool(re.search(signal.url_pattern or "", self._page.url))
        if signal.kind in (SignalKind.TEXT_PRESENT, SignalKind.TEXT_ABSENT):
            found = self._text_matches(signal)
            return found if signal.kind is SignalKind.TEXT_PRESENT else not found
        if signal.kind in (SignalKind.ELEMENT_PRESENT, SignalKind.ELEMENT_ABSENT):
            present = self._element_present(signal)
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

    def _element_present(self, signal: Signal) -> bool:
        bundle = signal.locator
        if bundle is None:
            return False
        scope = frame_scope(self._page, bundle.frame_path)
        for spec in [bundle.primary, *bundle.fallbacks]:
            built = build(scope, spec)
            if built.guard is not None and built.guard.count() != 1:
                continue
            if built.target.count() >= 1:
                return True
        return False

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
