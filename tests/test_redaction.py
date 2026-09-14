"""Nothing marked pii or secret reaches disk."""
from __future__ import annotations

from src.policy.redaction import Redactor


def test_a_sensitive_value_does_not_survive_redaction() -> None:
    redactor = Redactor({"member_id": "100001"})
    out = redactor.redact("GET /member/100001 returned Marcus Webb")
    assert "100001" not in out
    assert "<param:member_id>" in out


def test_every_occurrence_is_replaced_not_just_the_first() -> None:
    redactor = Redactor({"member_id": "100001"})
    out = redactor.redact("100001 and 100001 again")
    assert "100001" not in out
    assert out.count("<param:member_id>") == 2


def test_a_longer_value_is_replaced_before_a_value_contained_inside_it() -> None:
    """Otherwise the short value rewrites part of the long one and leaves a fragment."""
    redactor = Redactor({"short": "1234", "full": "1234-5678"})
    out = redactor.redact("card 1234-5678 ending 1234")
    assert "1234-5678" not in out
    assert out == "card <param:full> ending <param:short>"


def test_empty_values_are_ignored_rather_than_matching_everywhere() -> None:
    redactor = Redactor({"blank": ""})
    assert redactor.is_empty
    assert redactor.redact("untouched") == "untouched"


def test_mapping_redaction_covers_keys_as_well_as_values() -> None:
    redactor = Redactor({"member_id": "100001"})
    out = redactor.redact_mapping({"100001": "Marcus Webb", "member": "100001"})
    assert "100001" not in "".join(out.keys()) + "".join(out.values())
