"""Turn a saved LocatorBundle into a live Playwright locator.

Kept apart from WebSurface so it can be read on its own. This is where the locator tiers turn
into actual selectors.

Tiers 2 and 3 use XPath, but to describe a relationship rather than a path through the markup:
"the row with a cell reading Nickname", "the table under this heading". The role still comes
from get_by_role. The CSS tier is different: it names one element by one attribute and breaks
when the markup moves. See DECISIONS.md 0006.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from playwright.sync_api import FrameLocator, Locator, Page

from src.models.locator import (
    ContainerOrdinalLocator,
    CssFallbackLocator,
    LabelRelationLocator,
    Locator as LocatorSpec,
    RoleNameLocator,
    TextRelationLocator,
)

Scope = Page | FrameLocator


@dataclass(frozen=True)
class Built:
    """A compiled locator, plus an optional guard.

    `guard` is for container scoping. A `.nth(i)` locator can never match more than one
    element, so if something is ambiguous it is the container. The guard is the container, and
    it has to match exactly one thing.
    """

    target: Locator
    guard: Locator | None = None


def xpath_literal(text: str) -> str:
    """Quote a string for XPath 1.0, which has no escape character."""
    if '"' not in text:
        return f'"{text}"'
    if "'" not in text:
        return f"'{text}'"
    parts = text.split('"')
    joined = ", '\"', ".join(f'"{part}"' for part in parts)
    return f"concat({joined})"


def frame_scope(page: Page, frame_path: Sequence[str]) -> Scope:
    """Descend into nested frames, outermost first."""
    scope: Scope = page
    for name in frame_path:
        selector = f'iframe[name="{name}"]' if name else "iframe"
        scope = scope.frame_locator(selector)
    return scope


def build(scope: Scope, spec: LocatorSpec) -> Built:
    """Compile one tier into a live locator.

    `scope` is cast to Any because Page and FrameLocator have these methods but no shared base
    class in the Playwright stubs, and get_by_role wants a Literal role while ours is a string.
    """
    loose = cast(Any, scope)

    if isinstance(spec, RoleNameLocator):
        return Built(target=loose.get_by_role(spec.role, name=spec.name, exact=spec.exact))

    if isinstance(spec, LabelRelationLocator):
        label = xpath_literal(spec.label_text)
        if spec.relation in ("cell_to_left", "enclosing_row"):
            # The row with a cell reading <label>, then whatever in that row has the role.
            row = loose.locator(f"xpath=//tr[./*[normalize-space(.)={label}]]")
            return Built(target=cast(Any, row).get_by_role(spec.role))
        sibling = loose.locator(
            f"xpath=//*[normalize-space(.)={label}]/following-sibling::*[1]"
        )
        return Built(target=cast(Any, sibling).get_by_role(spec.role))

    if isinstance(spec, ContainerOrdinalLocator):
        heading = xpath_literal(spec.container.heading_text)
        container = loose.locator(
            f"xpath=//*[normalize-space(text())={heading}]"
            f"/ancestor::{spec.container.role}[1]"
        )
        target = cast(Any, container).get_by_role(spec.role)
        if spec.name:
            target = cast(Any, container).get_by_role(spec.role, name=spec.name, exact=True)
        return Built(target=target.nth(spec.ordinal), guard=container)

    if isinstance(spec, TextRelationLocator):
        if spec.container is None:
            return Built(target=loose.get_by_text(spec.text, exact=spec.exact))
        heading = xpath_literal(spec.container.heading_text)
        container = loose.locator(
            f"xpath=//*[normalize-space(text())={heading}]"
            f"/ancestor::{spec.container.role}[1]"
        )
        return Built(
            target=cast(Any, container).get_by_text(spec.text, exact=spec.exact),
            guard=container,
        )

    if isinstance(spec, CssFallbackLocator):
        return Built(target=loose.locator(spec.css))

    raise TypeError(f"unknown locator strategy: {spec!r}")
