"""How a control is found again: an ordered list of ways, not a single selector.

Role and accessible name come first because that is the one tier that also exists on desktop,
through UI Automation or the macOS accessibility API. CSS comes last and is marked brittle,
because a selector tied to HTML markup has nothing to point at on a screen with no DOM.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from src.models.common import STRICT, Rect


class ContainerRef(BaseModel):
    """A surrounding region, named the way a person looking at the screen would name it.

    No CSS here. `dom_id_hint` is only there to help someone debugging a failed run find the
    element. It is never used to find anything.
    """

    model_config = STRICT

    heading_text: str
    role: str = "table"
    dom_id_hint: str | None = None


class RoleNameLocator(BaseModel):
    """Tier 1. Role plus accessible name."""

    model_config = STRICT

    strategy: Literal["role_name"] = "role_name"
    role: str
    name: str
    exact: bool = True


class LabelRelationLocator(BaseModel):
    """Tier 2. For controls that have no accessible name of their own."""

    model_config = STRICT

    strategy: Literal["label_relation"] = "label_relation"
    label_text: str
    relation: Literal["cell_to_left", "preceding_sibling", "enclosing_row"]
    role: str


class ContainerOrdinalLocator(BaseModel):
    """Tier 3. For controls whose accessible names collide across regions."""

    model_config = STRICT

    strategy: Literal["container_ordinal"] = "container_ordinal"
    container: ContainerRef
    role: str
    ordinal: int
    name: str | None = None


class TextRelationLocator(BaseModel):
    """Tier 4. For controls with no ARIA role at all.

    get_by_role cannot see a span with an inline onclick, because the accessibility tree shows
    it as plain text. Its visible text is the only thing left to go on. A person reads that
    text off the screen too, which is why this ranks above CSS.
    """

    model_config = STRICT

    strategy: Literal["text_relation"] = "text_relation"
    text: str
    exact: bool = True
    container: ContainerRef | None = None


class CssFallbackLocator(BaseModel):
    """Tier 5. Brittle, so `note` has to say why nothing better worked."""

    model_config = STRICT

    strategy: Literal["css_fallback"] = "css_fallback"
    css: str
    brittle: Literal[True] = True
    note: str = Field(description="Why no better strategy was available.")


Locator = Annotated[
    RoleNameLocator
    | LabelRelationLocator
    | ContainerOrdinalLocator
    | TextRelationLocator
    | CssFallbackLocator,
    Field(discriminator="strategy"),
]


class LocatorBundle(BaseModel):
    """The ways to find one control, best first, plus the frame it is in."""

    model_config = STRICT

    primary: Locator
    fallbacks: list[Locator] = Field(default_factory=list)
    frame_path: list[str] = Field(
        default_factory=list, description="iframe names to descend, outermost first"
    )
    geometry_hint: Rect | None = None
    recorded_accessible_name: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _primary_is_not_brittle(self) -> LocatorBundle:
        if self.primary.strategy == "css_fallback":
            raise ValueError(
                "locator bundle primary must not use the css_fallback strategy: "
                "a brittle selector may be a fallback, never the first choice. It is the "
                "only strategy forbidden as a primary; text_relation is permitted, because "
                "visible text is something a human reads rather than a generated attribute"
            )
        return self

    @model_validator(mode="after")
    def _strategies_are_unique(self) -> LocatorBundle:
        seen = [self.primary.strategy] + [f.strategy for f in self.fallbacks]
        duplicates = sorted({s for s in seen if seen.count(s) > 1})
        if duplicates:
            raise ValueError(
                "locator bundle contains duplicate strategies: "
                + ", ".join(duplicates)
                + ". Each tier may appear at most once in a bundle"
            )
        return self
