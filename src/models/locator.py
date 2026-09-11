"""How a control is identified, recorded as an ordered bundle rather than one selector.

The tier order in design rules section 6 is deliberate. Role plus accessible name comes first
because it is the only tier with a real analogue on a desktop surface through UI Automation
or the AX API. CSS comes last and is marked brittle, because a selector built on markup
structure is the tier that cannot cross to a surface with no DOM at all.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from src.models.common import STRICT, Rect


class ContainerRef(BaseModel):
    """An enclosing region, named the way a person reading the screen would name it.

    Deliberately carries no CSS. `dom_id_hint` is recorded so a human debugging a failed
    run can find the element quickly, and is never consulted when resolving.
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


class CssFallbackLocator(BaseModel):
    """Tier 4. Brittle by construction, which is why `note` is required."""

    model_config = STRICT

    strategy: Literal["css_fallback"] = "css_fallback"
    css: str
    brittle: Literal[True] = True
    note: str = Field(description="Why no better strategy was available.")


Locator = Annotated[
    RoleNameLocator | LabelRelationLocator | ContainerOrdinalLocator | CssFallbackLocator,
    Field(discriminator="strategy"),
]


class LocatorBundle(BaseModel):
    """An ordered set of ways to find one control, plus the frame it lives in."""

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
                "a brittle selector may be a fallback, never the first choice"
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
