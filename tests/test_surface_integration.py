"""describe() and resolve() against the real target app in a real browser.

Elements are identified by DOM id rather than accessible name, because the two Select buttons
have the same name. Reading the id off the resolved element is the only way to show tier 3
picked the right one.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest

from src.surface.actions import ClickAction, NavigateAction
from src.surface.protocol import LocatorAmbiguous, LocatorUnresolved
from src.surface.web import WebSurface

MEMBER_ID_INPUT = "ctl00_ContentPlaceHolder1_txtMemberId"
NICKNAME_INPUT = "ctl00_ContentPlaceHolder1_txtNickname"
DEPOSIT_SELECT = "ctl00_ContentPlaceHolder1_btnSelectDeposit"
LOAN_SELECT = "ctl00_ContentPlaceHolder1_btnSelectLoan"


def _goto(surface: WebSurface, base: str, path: str) -> Any:
    surface.act(NavigateAction(url=base + path))
    return surface.observe()


def _dom_id(surface: WebSurface, bundle: Any) -> str | None:
    resolved = surface.resolve(bundle)
    return cast(Any, resolved.handle).get_attribute("id")


# -- tier 1: a control with a real accessible name ------------------------------
def test_member_id_input_describes_as_role_name(surface: Any, live_app: str) -> None:
    observation = _goto(surface, live_app, "/search")
    element = next(e for e in observation.elements if e.role == "textbox")
    bundle = surface.describe(element.ref)

    assert bundle.primary.strategy == "role_name"
    assert bundle.primary.name == "Member ID"
    assert _dom_id(surface, bundle) == MEMBER_ID_INPUT


# -- tier 2: a control with no accessible name at all ---------------------------
def test_nickname_input_describes_as_label_relation(surface: Any, live_app: str) -> None:
    observation = _goto(surface, live_app, "/member/100001/subaccount")
    element = next(
        e for e in observation.elements if e.role == "textbox" and e.name is None
    )
    bundle = surface.describe(element.ref)

    assert bundle.primary.strategy == "label_relation"
    assert bundle.primary.label_text == "Nickname"
    assert bundle.recorded_accessible_name is None
    assert _dom_id(surface, bundle) == NICKNAME_INPUT


# -- tier 3: two controls whose accessible names collide ------------------------
@pytest.mark.parametrize(
    ("heading", "expected_id"),
    [("Deposit Accounts", DEPOSIT_SELECT), ("Loan Accounts", LOAN_SELECT)],
)
def test_select_buttons_describe_as_container_ordinal(
    surface: Any, live_app: str, heading: str, expected_id: str
) -> None:
    observation = _goto(surface, live_app, "/member/100001")
    selects = observation.find(role="button", name="Select")
    assert len(selects) == 2, "the collision this tier exists for is missing"

    wanted = next(
        e for e in selects if observation.nearest_heading(e.ref)[1] == heading
    )
    bundle = surface.describe(wanted.ref)

    assert bundle.primary.strategy == "container_ordinal"
    assert bundle.primary.container.heading_text == heading
    assert bundle.frame_path == ["maincontent"], "the panel is inside an iframe"
    # the assertion that matters: the RIGHT one of two identically named buttons
    assert _dom_id(surface, bundle) == expected_id


# -- ambiguity stops the run, it does not pick a winner -------------------------
def test_an_ambiguous_bundle_raises_and_does_not_pick_the_first_match(
    surface: Any, live_app: str
) -> None:
    from src.models.locator import LocatorBundle, RoleNameLocator

    _goto(surface, live_app, "/member/100001")
    ambiguous = LocatorBundle(
        primary=RoleNameLocator(role="button", name="Select"),
        frame_path=["maincontent"],
    )
    with pytest.raises(LocatorAmbiguous) as exc:
        surface.resolve(ambiguous)
    assert "matched 2 elements" in str(exc.value)
    assert "first match" in str(exc.value)


def test_ambiguity_is_raised_rather_than_falling_through_to_a_fallback(
    surface: Any, live_app: str
) -> None:
    """A fallback that works must not rescue a primary that matched two elements."""
    from src.models.locator import ContainerOrdinalLocator, ContainerRef, LocatorBundle, RoleNameLocator

    _goto(surface, live_app, "/member/100001")
    bundle = LocatorBundle(
        primary=RoleNameLocator(role="button", name="Select"),
        fallbacks=[
            ContainerOrdinalLocator(
                container=ContainerRef(heading_text="Deposit Accounts", role="table"),
                role="button",
                ordinal=0,
                name="Select",
            )
        ],
        frame_path=["maincontent"],
    )
    with pytest.raises(LocatorAmbiguous):
        surface.resolve(bundle)


# -- snapshot refs never reach a bundle -----------------------------------------
def test_no_ref_from_any_observation_ever_reaches_a_bundle(
    surface: Any, live_app: str
) -> None:
    """Refs are per-snapshot handles. One in an artifact is a bug, so assert it directly."""
    leaked: list[str] = []
    for path in ("/search", "/member/100001", "/member/100001/subaccount"):
        observation = _goto(surface, live_app, path)
        refs = [e.ref for e in observation.elements]
        assert refs, "observation produced no elements"

        for element in observation.elements:
            if element.role not in {"textbox", "button", "combobox", "link"}:
                continue
            try:
                bundle = surface.describe(element.ref)
            except Exception:
                continue
            serialized = bundle.model_dump_json()
            for ref in refs:
                if re.search(rf"\b{re.escape(ref)}\b", serialized):
                    leaked.append(f"{ref} in bundle for {element.ref}: {serialized}")

    assert not leaked, "refs leaked into bundles:\n" + "\n".join(leaked[:5])


# -- the finding from the captures: a span with an onclick has no role ----------
def test_the_onclick_span_describes_by_visible_text_and_round_trips(
    surface: Any, live_app: str
) -> None:
    observation = _goto(surface, live_app, "/member/100001")
    span = next(
        e for e in observation.elements if e.name == "Open Sub-Account" and e.role == "text"
    )
    assert not span.role_resolvable

    # Tiers 1 to 3 are all role based and none can see it, but its visible text can.
    # Before the text_relation tier existed this raised LocatorUnresolved. See 0007.
    bundle = surface.describe(span.ref)
    assert bundle.primary.strategy == "text_relation"
    assert bundle.primary.text == "Open Sub-Account"
    assert "not an ARIA role" in (bundle.notes or "")

    # and it round trips: the bundle resolves back to the element it was built from
    assert _dom_id(surface, bundle) == "ctl00_ContentPlaceHolder1_lnkOpenSub"


# -- item 1: transient slowness must be waited on, not mistaken for absence ------
def _arm_fault(surface: Any, base: str, fault: str) -> None:
    """Arm through the raw page. The operator may reach /dev/, the agent may not."""
    surface.page.goto(base + "/dev/faults", wait_until="load")
    surface.page.get_by_role("button", name=f"Arm {fault}").click()
    surface.page.wait_for_load_state("load")


def test_resolution_survives_the_slow_response_fault(surface: Any, live_app: str) -> None:
    """The /dev/faults slow response, end to end. Resolution must succeed, not fail."""
    import time as _time

    from src.models.locator import LocatorBundle, RoleNameLocator

    _arm_fault(surface, live_app, "slow")
    started = _time.monotonic()
    surface.act(NavigateAction(url=live_app + "/search"))
    elapsed = _time.monotonic() - started
    assert elapsed > 4.0, f"the slow fault did not fire, only {elapsed:.1f}s elapsed"

    resolved = surface.resolve(
        LocatorBundle(primary=RoleNameLocator(role="textbox", name="Member ID"))
    )
    assert cast(Any, resolved.handle).get_attribute("id") == MEMBER_ID_INPUT


def test_resolution_waits_for_an_element_that_has_not_rendered_yet(
    surface: Any, live_app: str
) -> None:
    """The mechanism itself, made deterministic.

    The slow fault delays the whole response, so by the time goto returns the page is
    complete and the wait is never exercised. This removes a control and puts it back after
    a delay, which is what a slow client side render actually looks like to a locator.
    """
    from src.models.locator import LocatorBundle, RoleNameLocator

    bundle = LocatorBundle(primary=RoleNameLocator(role="textbox", name="Member ID"))

    def hide_then_restore(delay_ms: int) -> None:
        surface.page.evaluate(
            """(ms) => {
                const el = document.querySelector('#ctl00_ContentPlaceHolder1_txtMemberId');
                const parent = el.parentNode;
                el.remove();
                setTimeout(() => parent.appendChild(el), ms);
            }""",
            delay_ms,
        )

    # with no budget the tier reports zero and the bundle is called unresolved
    surface.act(NavigateAction(url=live_app + "/search"))
    hide_then_restore(1200)
    original = surface._resolve_timeout_ms
    surface._resolve_timeout_ms = 0
    try:
        with pytest.raises(LocatorUnresolved):
            surface.resolve(bundle)
    finally:
        surface._resolve_timeout_ms = original

    # with the budget restored the same situation resolves
    surface.act(NavigateAction(url=live_app + "/search"))
    hide_then_restore(1200)
    resolved = surface.resolve(bundle)
    assert cast(Any, resolved.handle).get_attribute("id") == MEMBER_ID_INPUT


def test_ambiguity_is_never_waited_on(surface: Any, live_app: str) -> None:
    """Zero is waited on, two is not. Ambiguity must raise fast, not after the budget."""
    import time as _time

    from src.models.locator import LocatorBundle, RoleNameLocator

    _goto(surface, live_app, "/member/100001")
    ambiguous = LocatorBundle(
        primary=RoleNameLocator(role="button", name="Select"), frame_path=["maincontent"]
    )
    started = _time.monotonic()
    with pytest.raises(LocatorAmbiguous):
        surface.resolve(ambiguous)
    assert _time.monotonic() - started < 1.0, "ambiguity waited instead of raising"


# -- item 2: a click that navigates somewhere denied must be caught --------------
def test_a_click_that_navigates_to_a_denied_path_is_blocked(
    surface: Any, live_app: str
) -> None:
    """The arrival check must beat the navigation rather than race it.

    Clicking Member Lookup navigates to /search. With /search denied, the click must be
    caught on arrival. Before the load state was settled, page.url was still the page we
    came from and this passed straight through.

    The gate is swapped rather than a second WebSurface built, because Playwright's sync API
    refuses a second instance in one thread.
    """
    from src.models.common import ActionType
    from src.models.locator import LocatorBundle, RoleNameLocator
    from src.models.policy import PolicyConfig
    from src.policy.gate import PolicyGate
    from src.surface.protocol import PolicyViolation

    strict = PolicyConfig(
        allowed_hosts=["127.0.0.1"],
        allowed_path_patterns=[r"^/"],
        denied_path_patterns=[r"^/search"],
        allowed_actions=[ActionType.NAVIGATE, ActionType.CLICK],
        risky_action_policy="flag",
    )
    original = surface._gate
    surface.act(NavigateAction(url=live_app + "/"))
    surface._gate = PolicyGate(strict)
    try:
        link = LocatorBundle(primary=RoleNameLocator(role="link", name="Member Lookup"))
        with pytest.raises(PolicyViolation) as exc:
            surface.act(ClickAction(bundle=link))
        assert "denied_path_patterns" in str(exc.value)
        assert "/search" in str(exc.value)
        assert "/search" in surface.page.url, "the click did navigate; it was caught on arrival"
    finally:
        surface._gate = original


# -- item 3: the tier that resolved the conflict --------------------------------
def test_text_relation_is_permitted_as_a_primary_but_css_is_not() -> None:
    from pydantic import ValidationError

    from src.models.locator import CssFallbackLocator, LocatorBundle, TextRelationLocator

    assert LocatorBundle(primary=TextRelationLocator(text="Open Sub-Account"))
    with pytest.raises(ValidationError):
        LocatorBundle(primary=CssFallbackLocator(css="#x", note="only option"))



def test_no_template_reaches_a_vocabulary_key_by_shadowed_dotted_access() -> None:
    """Jinja resolves `v.labels.clear` to dict.clear, not to the label.

    Found the hard way: the fault console's Clear control rendered as a bound method repr
    and could not be found by its accessible name, so nothing could disarm a fault. Dotted
    access is fine for every key that is not also a dict attribute, and subscript access is
    always fine, so this checks the combination that actually breaks rather than banning
    either one.
    """
    import re
    import sys

    sys.path.insert(0, "target_app")
    from seed import VARIANTS

    shadowed = set(dir(dict))
    groups = {group for variant in VARIANTS.values() for group in variant}
    pattern = re.compile(r"\bv\.(" + "|".join(sorted(groups)) + r")\.([A-Za-z_][A-Za-z0-9_]*)")

    offenders = [
        f"{path.name}: v.{group}.{key}"
        for path in sorted(Path("target_app/templates").glob("*.html"))
        for group, key in pattern.findall(path.read_text())
        if key in shadowed
    ]
    assert not offenders, f"use subscript access for these: {offenders}"
