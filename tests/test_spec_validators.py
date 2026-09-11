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


# -- user supplied regexes are compiled at record time, not at replay time -------
def test_a_broken_signal_pattern_is_rejected_at_record_time() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(kind=SignalKind.TEXT_PRESENT, pattern="Member (")
    assert_error(exc, "pattern is not a valid regular expression")
    assert_error(exc, "rejected at record time rather than at replay time")
    assert_error(exc, "'Member ('")


def test_a_broken_url_pattern_is_rejected_at_record_time() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(kind=SignalKind.URL_MATCHES, url_pattern="/member/[0-9")
    assert_error(exc, "url_pattern is not a valid regular expression")
    assert_error(exc, "rejected at record time")


def test_a_broken_strip_pattern_is_rejected_at_record_time() -> None:
    with pytest.raises(ValidationError) as exc:
        ExtractionSpec(
            locator=role_name_bundle("cell", "Balance"),
            source="text",
            parse="currency",
            strip_pattern="[$,",
        )
    assert_error(exc, "strip_pattern is not a valid regular expression")
    assert_error(exc, "rejected at record time")


def test_valid_regexes_pass_through_untouched() -> None:
    signal = Signal(kind=SignalKind.URL_MATCHES, url_pattern=r"^/member/\d+$")
    spec = ExtractionSpec(
        locator=role_name_bundle("cell", "Balance"),
        source="text",
        parse="currency",
        strip_pattern=r"[$,]",
    )
    assert signal.url_pattern == r"^/member/\d+$"
    assert spec.strip_pattern == r"[$,]"


def test_a_none_pattern_is_still_legal() -> None:
    assert Signal(kind=SignalKind.TEXT_PRESENT, text="Member Detail").pattern is None


# -- aria_matches: assert the shape of a screen, not one string -----------------
def test_an_aria_signal_requires_a_template() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(kind=SignalKind.ARIA_MATCHES)
    assert_error(exc, "requires aria_template")


def test_an_aria_signal_may_not_also_carry_text() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(
            kind=SignalKind.ARIA_MATCHES,
            aria_template='- heading "Sub-Account Opened"',
            text="Sub-Account Opened",
        )
    assert_error(exc, "must not populate text")


def test_a_text_signal_may_not_carry_an_aria_template() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(
            kind=SignalKind.TEXT_PRESENT,
            text="Member Detail",
            aria_template='- heading "Member Detail"',
        )
    assert_error(exc, "must not populate aria_template")


def test_an_element_signal_may_not_carry_an_aria_template() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(
            kind=SignalKind.ELEMENT_PRESENT,
            locator=role_name_bundle("button", "Confirm"),
            aria_template='- button "Confirm"',
        )
    assert_error(exc, "must not populate aria_template")


def test_a_url_signal_may_not_carry_an_aria_template() -> None:
    with pytest.raises(ValidationError) as exc:
        Signal(
            kind=SignalKind.URL_MATCHES,
            url_pattern=r"^/member/\d+$",
            aria_template='- heading "Member Detail"',
        )
    assert_error(exc, "must not populate aria_template")


def test_a_valid_aria_signal_is_accepted() -> None:
    signal = Signal(
        kind=SignalKind.ARIA_MATCHES,
        aria_template='- heading "Sub-Account Opened"\n- text "New Account Number"',
    )
    assert signal.aria_template is not None
    assert signal.text is None


def test_every_signal_kind_has_a_branch_in_the_exclusivity_validator() -> None:
    """Adding a SignalKind without extending the validator fails here, not at replay."""
    builders = {
        SignalKind.TEXT_PRESENT: lambda: Signal(kind=SignalKind.TEXT_PRESENT, text="x"),
        SignalKind.TEXT_ABSENT: lambda: Signal(kind=SignalKind.TEXT_ABSENT, text="x"),
        SignalKind.URL_MATCHES: lambda: Signal(kind=SignalKind.URL_MATCHES, url_pattern="^/x$"),
        SignalKind.ELEMENT_PRESENT: lambda: Signal(
            kind=SignalKind.ELEMENT_PRESENT, locator=role_name_bundle("button", "Go")
        ),
        SignalKind.ELEMENT_ABSENT: lambda: Signal(
            kind=SignalKind.ELEMENT_ABSENT, locator=role_name_bundle("button", "Go")
        ),
        SignalKind.ARIA_MATCHES: lambda: Signal(
            kind=SignalKind.ARIA_MATCHES, aria_template="- button \"Go\""
        ),
    }
    assert set(builders) == set(SignalKind), "a SignalKind has no construction path here"
    for kind, build in builders.items():
        assert build().kind is kind


def test_surface_fingerprint_carries_an_aria_template() -> None:
    from src.models import SurfaceFingerprint
    from datetime import UTC, datetime

    fingerprint = SurfaceFingerprint(
        aria_template='- banner:\n  - text "Cedar Ridge Credit Union"',
        captured_at=datetime(2026, 9, 10, tzinfo=UTC),
    )
    assert fingerprint.aria_template is not None


def test_policy_config_risky_control_names_defaults_empty_and_accepts_names() -> None:
    from src.models import PolicyConfig

    assert PolicyConfig(risky_action_policy="block").risky_control_names == []
    configured = PolicyConfig(
        risky_action_policy="require_approval", risky_control_names=["Confirm", "Authorize"]
    )
    assert configured.risky_control_names == ["Confirm", "Authorize"]

