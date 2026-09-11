"""describe() and resolve() against the real target app in a real browser.

Identity is asserted by DOM id rather than by accessible name, because the whole point of
the Select pair is that their names are identical. Reading the id back off the resolved
element is the only assertion that actually proves tier 3 picked the right one of the two.
"""
from __future__ import annotations

import re
from typing import Any, cast

import pytest

from src.surface.actions import NavigateAction
from src.surface.protocol import LocatorAmbiguous
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
    """A working fallback must NOT rescue an ambiguous primary. Invariant 4."""
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


# -- invariant 9 under test -----------------------------------------------------
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
def test_the_onclick_span_cannot_produce_a_role_based_locator(
    surface: Any, live_app: str
) -> None:
    observation = _goto(surface, live_app, "/member/100001")
    span = next(
        e for e in observation.elements if e.name == "Open Sub-Account" and e.role == "text"
    )
    assert not span.role_resolvable

    # Tiers 1 to 3 cannot see it and the schema forbids a brittle primary, so the two
    # rules collide and describe() refuses rather than recording a DOM id as the primary
    # way to find a control. See DECISIONS.md 0006.
    from src.surface.protocol import LocatorUnresolved

    with pytest.raises(LocatorUnresolved) as exc:
        surface.describe(span.ref)
    assert "can only be located by CSS" in str(exc.value)
    assert "not an ARIA role" in str(exc.value)
