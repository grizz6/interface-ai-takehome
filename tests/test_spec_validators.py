"""Signal, WaitSpec, ParamSpec and ExtractionSpec validators."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from conftest import assert_error, role_name_bundle, text_signal
from src.models import (
    ExtractionSpec,
    ParamSpec,
    Sensitivity,
    Signal,
    SignalKind,
    ValueType,
    WaitSpec,
)


def test_text_signal_requires_text_or_pattern() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(kind=SignalKind.TEXT_PRESENT)
    assert_error(exc, "requires text or pattern")


def test_url_signal_requires_a_url_pattern() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(kind=SignalKind.URL_MATCHES, text="nope")
    assert_error(exc, "requires url_pattern")


def test_element_signal_requires_a_locator() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(kind=SignalKind.ELEMENT_ABSENT, text="nope")
    assert_error(exc, "requires locator")


def test_a_signal_may_not_populate_fields_belonging_to_another_kind() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(
            kind=SignalKind.TEXT_PRESENT,
            text="Member Detail",
            locator=role_name_bundle("cell", "Member Name"),
        )
    assert_error(exc, "must not populate locator")


def test_wait_on_a_signal_requires_the_signal() -> None:
    with pytest.raises(ValidationError) as exc:
        WaitSpec(condition="signal")
    assert_error(exc, 'wait condition "signal" requires signal')


def test_wait_on_load_needs_no_signal() -> None:
    assert WaitSpec(condition="load").signal is None


def test_a_sensitive_param_may_not_carry_an_example() -> None:
    """design rule 6, enforced by the schema rather than by discipline."""
    with pytest.raises(ValidationError) as exc:
        ParamSpec(
            name="ssn",
            type=ValueType.STRING,
            description="Tax identifier.",
            sensitivity=Sensitivity.SECRET,
            example="123-45-6789",
        )
    assert_error(exc, "must not carry an example")


def test_a_non_sensitive_param_may_carry_an_example() -> None:
    param = ParamSpec(
        name="nickname", type=ValueType.STRING, description="Label.", example="Vacation"
    )
    assert param.example == "Vacation"


def test_param_names_must_be_snake_case() -> None:
    with pytest.raises(ValidationError) as exc:
        ParamSpec(name="memberId", type=ValueType.STRING, description="x")
    assert_error(exc, "String should match pattern")


def test_attribute_extraction_requires_naming_the_attribute() -> None:
    with pytest.raises(ValidationError) as exc:
        ExtractionSpec(
            locator=role_name_bundle("link", "Statement"), source="attribute", parse="raw"
        )
    assert_error(exc, 'extraction source "attribute" requires attribute')


def test_text_extraction_needs_no_attribute() -> None:
    spec = ExtractionSpec(
        locator=role_name_bundle("cell", "Balance"), source="text", parse="currency"
    )
    assert spec.attribute is None


def test_signal_used_by_the_fixture_is_valid() -> None:
    assert text_signal("Member Detail").kind is SignalKind.TEXT_PRESENT
