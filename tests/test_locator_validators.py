"""LocatorBundle validators. A bundle is an ordered strategy list, not a selector bag."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from conftest import assert_error
from src.models import (
    CssFallbackLocator,
    LocatorBundle,
    RoleNameLocator,
)


def test_primary_may_not_be_the_brittle_strategy() -> None:
    with pytest.raises(ValidationError) as exc:
        LocatorBundle(
            primary=CssFallbackLocator(css="#ctl00_x", note="nothing better existed")
        )
    assert_error(exc, "primary must not use the css_fallback strategy")


def test_brittle_strategy_is_allowed_as_a_fallback() -> None:
    bundle = LocatorBundle(
        primary=RoleNameLocator(role="button", name="Confirm"),
        fallbacks=[CssFallbackLocator(css="#ctl00_btnConfirm", note="last resort")],
    )
    assert bundle.fallbacks[0].strategy == "css_fallback"


def test_a_strategy_may_not_appear_twice_in_one_bundle() -> None:
    with pytest.raises(ValidationError) as exc:
        LocatorBundle(
            primary=RoleNameLocator(role="button", name="Confirm"),
            fallbacks=[RoleNameLocator(role="button", name="Confirm ")],
        )
    assert_error(exc, "duplicate strategies: role_name")


def test_css_fallback_requires_a_note_explaining_itself() -> None:
    with pytest.raises(ValidationError) as exc:
        CssFallbackLocator(css="#ctl00_x")  # type: ignore[call-arg]
    assert_error(exc, "note")
