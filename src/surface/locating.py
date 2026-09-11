"""Turn a durable LocatorBundle into a live Playwright locator.

Split out of WebSurface because this is the half that replay will reuse unchanged, and
because it is the half worth reading on its own: it is where the four tiers in the design rules
section 6 stop being a design and become selectors.

Note what tier 2 and tier 3 actually compile to. Both use XPath, and both use it to express a
SEMANTIC RELATION rather than a markup path: "the row that contains a cell reading Nickname",
"the table that this heading belongs to". The role still comes from get_by_role. That is a
different thing from the CSS fallback, which names a specific element by a specific attribute
and breaks when the markup is rearranged. See DECISIONS.md 0006.
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
)

Scope = Page | FrameLocator


@dataclass(frozen=True)
class Built:
    """A compiled locator, plus an optional guard.

    `guard` exists for container scoping. A `.nth(i)` locator always matches at most one
    element by construction, so ambiguity there hides in the container rather than in the
    target. The guard is the container, and it must match exactly one thing.
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

    `scope` is typed loosely on purpose: Page and FrameLocator share these methods but do not
    share a base class in the Playwright stubs, and get_by_role types its role argument as a
    closed Literal while our schema carries an open string.
    """
    loose = cast(Any, scope)

    if isinstance(spec, RoleNameLocator):
        return Built(target=loose.get_by_role(spec.role, name=spec.name, exact=spec.exact))

    if isinstance(spec, LabelRelationLocator):
        label = xpath_literal(spec.label_text)
        if spec.relation in ("cell_to_left", "enclosing_row"):
            # The row that contains a cell reading <label>. The control is whatever in that
            # row carries the wanted role, which is a relation, not a path.
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

    if isinstance(spec, CssFallbackLocator):
        return Built(target=loose.locator(spec.css))

    raise TypeError(f"unknown locator strategy: {spec!r}")
