"""The parser, against the real captures it was written from.

These run without a browser. They exist because the parser is the one piece of this phase
whose correctness is a claim about a third party format, so it is pinned to real output.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.surface.observation import Observation, parse_aria_snapshot

FIXTURES = Path("tests/fixtures")


def load(name: str, frames: list[str] | None = None) -> Observation:
    text = (FIXTURES / name).read_text()
    return Observation(
        url="/fixture",
        title="fixture",
        aria_yaml=text,
        elements=parse_aria_snapshot(text, frames),
        captured_at=datetime(2026, 9, 11, tzinfo=UTC),
    )


def test_iframe_content_is_inlined_and_carries_the_frame_path() -> None:
    observation = load("member_detail.aria.yaml", ["maincontent"])
    selects = observation.find(role="button", name="Select")
    assert len(selects) == 2
    assert all(e.frame_path == ["maincontent"] for e in selects)
    assert observation.find(role="cell", name="Marcus Webb")[0].frame_path == ["maincontent"]
    assert observation.find(role="cell", name="Cedar Ridge Credit Union")[0].frame_path == []


def test_refs_carry_a_frame_prefix_and_are_not_stable_identifiers() -> None:
    observation = load("member_detail.aria.yaml", ["maincontent"])
    refs = {e.ref for e in observation.elements}
    assert any(r.startswith("f1e") for r in refs), "in-frame refs are prefixed"
    assert any(r.startswith("e") and not r.startswith("f") for r in refs)


def test_an_element_with_no_accessible_name_parses_as_name_none() -> None:
    observation = load("subaccount_form.aria.yaml")
    nameless = [e for e in observation.elements if e.role == "textbox" and e.name is None]
    assert len(nameless) == 1, "the Nickname field is the one input with no name"


@pytest.mark.parametrize(
    ("role", "name", "expected"),
    [("textbox", None, "Vacation"), ("textbox", "Initial Deposit", "250.00")],
)
def test_values_parse_whether_quoted_or_not(role: str, name: str | None, expected: str) -> None:
    """Playwright quotes a value that looks numeric and leaves plain text bare."""
    observation = load("subaccount_form_filled.aria.yaml")
    element = next(e for e in observation.elements if e.role == role and e.name == name)
    assert element.value == expected


def test_flag_tokens_become_attributes() -> None:
    observation = load("subaccount_form_filled.aria.yaml")
    active = [e for e in observation.elements if e.attributes.get("active") == "true"]
    assert len(active) == 1


def test_boxes_parse_into_rects() -> None:
    observation = load("member_detail.aria.yaml", ["maincontent"])
    select = observation.find(role="button", name="Select")[0]
    assert select.box is not None
    assert select.box.width > 0 and select.box.height > 0


def test_a_bare_text_node_is_parsed_but_is_not_role_resolvable() -> None:
    """The onclick span. It has no ARIA role, so get_by_role can never find it."""
    observation = load("member_detail.aria.yaml", ["maincontent"])
    span = next(e for e in observation.elements if e.name == "Open Sub-Account")
    assert span.role == "text"
    assert not span.role_resolvable
    assert span.ref.startswith("syn"), "no ref in the snapshot, so one is synthesized"


def test_nearest_heading_picks_the_inner_container_not_the_page_chrome() -> None:
    observation = load("member_detail.aria.yaml", ["maincontent"])
    headings = {
        observation.nearest_heading(e.ref)[1]
        for e in observation.find(role="button", name="Select")
    }
    assert headings == {"Deposit Accounts", "Loan Accounts"}


def test_ancestors_are_nearest_first() -> None:
    observation = load("subaccount_form.aria.yaml")
    nickname = next(e for e in observation.elements if e.role == "textbox" and e.name is None)
    ancestors = observation.ancestors_of(nickname.ref)
    assert [a.role for a in ancestors][:3] == ["cell", "row", "rowgroup"]


def test_url_properties_attach_to_the_element_above_them() -> None:
    observation = load("subaccount_form.aria.yaml")
    link = observation.find(role="link", name="Back to Member Detail")[0]
    assert link.attributes.get("url") == "/member/100001"
