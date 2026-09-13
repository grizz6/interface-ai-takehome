"""The catalog: what exists, which version is meant, and what happens when nothing matches.

Only the parts with logic in them. Rendering is glue and is not tested, per design rules section 9.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src import catalog


def test_every_committed_capability_is_listed_with_its_typed_io() -> None:
    entries = catalog.load("capabilities")
    rows = {(r["capability_id"], r["version"]): r for r in map(catalog.summary, entries)}
    assert len(entries) == len(list(Path("capabilities").glob("*.json")))
    assert rows[("open-member-subaccount", "1.0.0")]["outputs"] == ["new_account_number: string"]
    assert rows[("lookup-member-savings-balance", "1.2.0")]["inputs"] == ["member_id: string"]


def test_describe_picks_the_highest_version_semantically_not_lexically(tmp_path: Path) -> None:
    """1.10.0 sorts below 1.2.0 as a string. The catalog must not hand an agent the older one."""
    shutil.copy("capabilities/lookup-member-savings-balance-1.2.0.json", tmp_path)
    data = json.loads(Path("capabilities/lookup-member-savings-balance-1.2.0.json").read_text())
    data["version"] = "1.10.0"
    (tmp_path / "lookup-member-savings-balance-1.10.0.json").write_text(json.dumps(data))

    entries = catalog.load(tmp_path)
    assert catalog.find(entries, "lookup-member-savings-balance").capability.version == "1.10.0"
    assert catalog.find(entries, "lookup-member-savings-balance", "1.2.0").capability.version == "1.2.0"


def test_an_unknown_id_names_what_does_exist() -> None:
    with pytest.raises(catalog.UnknownCapability) as exc:
        catalog.find(catalog.load("capabilities"), "open-a-loan")
    assert "lookup-member-savings-balance" in str(exc.value)


def test_the_contract_flags_irreversible_steps_and_never_prints_a_sensitive_example() -> None:
    spec = catalog.contract(catalog.find(catalog.load("capabilities"), "open-member-subaccount"))
    assert [s["step_index"] for s in spec["requires_human_approval"]] == [5]
    # member_id is pii and the schema forbids it an example, so the invocation carries a type.
    assert '"member_id": "<string>"' in spec["invoke"]
    assert "--allow-draft" in spec["invoke"]


def test_a_broken_artifact_is_an_error_not_a_missing_capability(tmp_path: Path) -> None:
    (tmp_path / "broken-1.0.0.json").write_text('{"capability_id": "broken"}')
    with pytest.raises(catalog.InvalidCapability):
        catalog.load(tmp_path)
