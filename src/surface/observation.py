"""Normalized perception: what the surface looks like right now.

The parser in this module was written against real captures in tests/fixtures/, not against
an assumed format. Everything it handles was observed in one of them:

    - table [ref=e2] [box=12,12,900,593]:          container, children follow
    - cell "Marcus Webb" [ref=f1e10] [box=...]     element with an accessible name
    - textbox [ref=f2e38] [box=...]                element with NO accessible name
    - textbox [active] [ref=..] [box=...]: "250.00"   flag token, and a value after the colon
    - textbox [ref=f2e38] [box=...]: Vacation      unquoted value
    - iframe [ref=e28] [box=...]:                  child frame content is inlined below it
    - text: Open Sub-Account                       bare text, no role and no ref
    - option "Savings" [selected] [box=0,0,0,0]    no ref at all
    - /url: /search                                a property of the element above

Two things from the captures matter for everything else. Refs have a frame prefix (e17 in
the main frame, f1e36 in the first iframe) and change on every snapshot, so they can never be
saved. And some real controls show up only as plain text, because a span with an onclick has
no ARIA role. Those can never get a role-based locator, which is why the text tier exists.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.models.common import Rect

INDENT = 2

_LINE = re.compile(r"^(?P<indent>[ ]*)- (?P<body>.*)$")
_ELEMENT = re.compile(
    r"^(?P<role>[A-Za-z][A-Za-z0-9_-]*)"
    r"(?:\s+\"(?P<name>(?:[^\"\\\\]|\\\\.)*)\")?"
    r"(?P<tokens>(?:\s*\[[^\]]*\])*)"
    r"(?::(?P<value>.*))?$"
)
_TOKEN = re.compile(r"\[([^\]]*)\]")

TEXT_ROLE = "text"
"""Role given to a bare `text:` node. Not an ARIA role, and never role-resolvable."""

RESOLVABLE_ROLES_EXCLUDED = {TEXT_ROLE, "iframe"}
"""Roles that exist in the snapshot but cannot be handed to get_by_role."""

HEADING_ROLES = {"cell", "columnheader", "heading"}
CONTAINER_ROLES = {"table", "group", "region", "form", "list"}


class ObservedElement(BaseModel):
    """One node of the accessibility tree at one moment.

    `ref` only means something within this snapshot. Use it to look things up during one turn,
    and never put it in a LocatorBundle or a saved capability.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str
    role: str
    name: str | None = None
    value: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    box: Rect | None = None
    depth: int
    parent_ref: str | None = None
    frame_path: list[str] = Field(default_factory=list)

    @property
    def role_resolvable(self) -> bool:
        """Whether get_by_role could ever find this element."""
        return self.role not in RESOLVABLE_ROLES_EXCLUDED


class Observation(BaseModel):
    """One look at the screen.

    `aria_yaml` is what the model sees and `elements` is what the code uses. Both come from
    the same snapshot, so they always agree.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    url: str
    title: str
    aria_yaml: str
    elements: list[ObservedElement] = Field(default_factory=list)
    screenshot_png: bytes | None = Field(default=None, repr=False)
    captured_at: datetime

    def by_ref(self, ref: str) -> ObservedElement | None:
        for element in self.elements:
            if element.ref == ref:
                return element
        return None

    def find(self, role: str | None = None, name: str | None = None) -> list[ObservedElement]:
        """Every element matching the given role and exact accessible name."""
        return [
            e
            for e in self.elements
            if (role is None or e.role == role) and (name is None or e.name == name)
        ]

    def ancestors_of(self, ref: str) -> list[ObservedElement]:
        """Ancestors of an element, nearest first."""
        chain: list[ObservedElement] = []
        current = self.by_ref(ref)
        while current is not None and current.parent_ref is not None:
            parent = self.by_ref(current.parent_ref)
            if parent is None:
                break
            chain.append(parent)
            current = parent
        return chain

    def descendants_of(self, ref: str) -> list[ObservedElement]:
        """Descendants in document order."""
        start = next((i for i, e in enumerate(self.elements) if e.ref == ref), None)
        if start is None:
            return []
        root_depth = self.elements[start].depth
        out: list[ObservedElement] = []
        for element in self.elements[start + 1 :]:
            if element.depth <= root_depth:
                break
            out.append(element)
        return out

    def nearest_heading(self, ref: str) -> tuple[ObservedElement, str] | None:
        """The closest surrounding container with a visible heading a person could name it by.

        Closest first, because the outermost table on these pages is the page frame, and its
        first cell is the brand name, which is no use as a name.
        """
        for ancestor in self.ancestors_of(ref):
            if ancestor.role not in CONTAINER_ROLES:
                continue
            for descendant in self.descendants_of(ancestor.ref):
                if descendant.role in HEADING_ROLES and descendant.name:
                    return ancestor, descendant.name
        return None


def _parse_tokens(raw: str) -> tuple[str | None, Rect | None, dict[str, str]]:
    ref: str | None = None
    box: Rect | None = None
    attributes: dict[str, str] = {}
    for token in _TOKEN.findall(raw):
        if token.startswith("ref="):
            ref = token[4:]
        elif token.startswith("box="):
            parts = token[4:].split(",")
            if len(parts) == 4:
                x, y, w, h = (int(float(p)) for p in parts)
                box = Rect(x=x, y=y, width=w, height=h)
        elif "=" in token:
            key, _, value = token.partition("=")
            attributes[key] = value
        elif token:
            attributes[token] = "true"
    return ref, box, attributes


def _clean_value(raw: str | None) -> str | None:
    """A trailing colon with nothing after it means children follow, not an empty value."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def parse_aria_snapshot(
    aria_yaml: str, frame_names: Sequence[str] | None = None
) -> list[ObservedElement]:
    """Parse an AI-mode aria snapshot into a flat, parent-linked element list.

    `frame_names` supplies the name of each iframe in document order, because the snapshot
    marks frame boundaries but does not carry the frame name. WebSurface takes them from
    page.frames; tests pass them literally.
    """
    names = list(frame_names or [])
    elements: list[ObservedElement] = []
    open_by_depth: dict[int, ObservedElement] = {}
    iframe_frames: dict[str, str] = {}
    iframes_seen = 0
    synthetic = 0

    for line in aria_yaml.splitlines():
        matched = _LINE.match(line)
        if matched is None:
            continue
        depth = len(matched.group("indent")) // INDENT
        body = matched.group("body").rstrip()

        # A property line such as "/url: /search" belongs to the element above it.
        if body.startswith("/"):
            key, _, raw = body.partition(":")
            parent = open_by_depth.get(depth - 1)
            if parent is not None:
                parent.attributes[key.lstrip("/")] = raw.strip()
            continue

        element_match = _ELEMENT.match(body)
        if element_match is None:
            continue

        role = element_match.group("role")
        name = element_match.group("name")
        value = _clean_value(element_match.group("value"))
        ref, box, attributes = _parse_tokens(element_match.group("tokens") or "")

        # A bare `text:` node carries its content where a value would be.
        if role == TEXT_ROLE:
            name, value = value, None

        if ref is None:
            synthetic += 1
            ref = f"syn{synthetic}"

        parent = open_by_depth.get(depth - 1)
        frame_path = list(parent.frame_path) if parent is not None else []
        if parent is not None and parent.role == "iframe":
            frame_path.append(iframe_frames.get(parent.ref, ""))

        element = ObservedElement(
            ref=ref,
            role=role,
            name=name,
            value=value,
            attributes=attributes,
            box=box,
            depth=depth,
            parent_ref=parent.ref if parent is not None else None,
            frame_path=frame_path,
        )
        if role == "iframe":
            iframe_frames[ref] = names[iframes_seen] if iframes_seen < len(names) else ""
            iframes_seen += 1

        elements.append(element)
        open_by_depth[depth] = element
        for deeper in [d for d in open_by_depth if d > depth]:
            del open_by_depth[deeper]

    return elements
