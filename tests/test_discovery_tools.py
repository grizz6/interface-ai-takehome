"""Tool schemas, and the prompt that describes them.

The JSON Schemas are checked by walking them rather than with a validator, since adding
`jsonschema` for one test is not worth it. The checks cover what actually breaks a provider
call: a leftover $ref, a required property that does not exist, an array without items.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.discovery.prompt import SYSTEM_PROMPT
from src.discovery.tools import ToolName, discovery_tools, inline_defs
from src.models.capability import ExtractionSpec, ParamSpec, Signal
from src.models.locator import LocatorBundle, RoleNameLocator

TOOLS = {tool.name: tool for tool in discovery_tools()}


def walk(node: Any, path: str, problems: list[str]) -> None:
    """Recursively check every schema node for the errors that break a real call."""
    if isinstance(node, dict):
        if "$ref" in node:
            problems.append(f"{path}: dangling $ref {node['$ref']}")
        if "$defs" in node:
            problems.append(f"{path}: $defs survived inlining")
        node_type = node.get("type")
        if node_type == "object":
            props = node.get("properties")
            if not isinstance(props, dict):
                problems.append(f"{path}: object without a properties map")
            else:
                for name in node.get("required", []):
                    if name not in props:
                        problems.append(f"{path}: required {name!r} is not a property")
        if node_type == "array" and "items" not in node:
            problems.append(f"{path}: array without items")
        if "enum" in node and not node["enum"]:
            problems.append(f"{path}: empty enum")
        for key, value in node.items():
            walk(value, f"{path}/{key}", problems)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            walk(item, f"{path}[{index}]", problems)


def test_every_tool_in_the_enum_is_offered_exactly_once() -> None:
    names = [tool.name for tool in discovery_tools()]
    assert sorted(names) == sorted(t.value for t in ToolName)
    assert len(names) == len(set(names))


@pytest.mark.parametrize("name", [t.value for t in ToolName])
def test_every_tool_schema_is_structurally_valid(name: str) -> None:
    problems: list[str] = []
    walk(TOOLS[name].parameters, name, problems)
    assert not problems, "\n".join(problems)
    assert TOOLS[name].parameters["type"] == "object"
    assert TOOLS[name].description.strip()


def test_no_tool_lets_the_model_author_a_locator() -> None:
    """The model points at refs and describe() builds the locators, so no tool takes one."""
    found: list[str] = []

    def scan(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    found.extend(
                        f"{path}.{n}" for n in value if "locator" in n.lower()
                    )
                scan(value, f"{path}/{key}")
        elif isinstance(node, list):
            for item in node:
                scan(item, path)

    for tool in discovery_tools():
        scan(tool.parameters, tool.name)
    assert not found, f"tool schemas expose locator fields: {found}"


def test_there_is_no_wait_tool() -> None:
    """On purpose. See DECISIONS.md 0010."""
    assert not [n for n in TOOLS if "wait" in n or "sleep" in n]


def test_inline_defs_resolves_references() -> None:
    schema = inline_defs(
        {
            "type": "object",
            "properties": {"a": {"$ref": "#/$defs/Thing"}},
            "$defs": {"Thing": {"type": "string"}},
        }
    )
    assert schema["properties"]["a"] == {"type": "string"}
    assert "$defs" not in schema


# -- finish, checked against the models its schema comes from --------------------
WELL_FORMED = {
    "capability_name": "open-member-subaccount",
    "description": "Opens a sub-account against a member deposit relationship.",
    "checkpoint": {"kind": "text_present", "text": "Sub-Account Opened"},
    "inputs": [
        {
            "name": "member_id",
            "type": "string",
            "required": True,
            "description": "Member record to service.",
            "sensitivity": "pii",
        }
    ],
    "outputs": [
        {
            "name": "new_account_number",
            "type": "string",
            "description": "The account number issued on confirmation.",
            "extraction": {"ref": "f1e42", "source": "text", "parse": "raw"},
        }
    ],
}


def test_a_well_formed_finish_payload_satisfies_the_schema() -> None:
    schema = TOOLS["finish"].parameters
    for key in schema["required"]:
        assert key in WELL_FORMED, f"payload is missing required key {key}"
    assert set(WELL_FORMED) <= set(schema["properties"])


def test_a_well_formed_finish_payload_round_trips_into_the_artifact_models() -> None:
    """The point of deriving the schema: what finish accepts, the artifact models accept."""
    assert Signal(**WELL_FORMED["checkpoint"]).text == "Sub-Account Opened"

    param = ParamSpec(**WELL_FORMED["inputs"][0])
    assert param.name == "member_id"
    assert param.sensitivity == "pii"

    # the ref becomes a locator later; everything else maps straight across
    raw = dict(WELL_FORMED["outputs"][0]["extraction"])
    raw.pop("ref")
    spec = ExtractionSpec(
        locator=LocatorBundle(primary=RoleNameLocator(role="cell", name="x")), **raw
    )
    assert spec.source == "text"


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ({"checkpoint": {"kind": "text_present"}}, "requires text or pattern"),
        ({"checkpoint": {"kind": "url_matches", "url_pattern": "[bad"}}, "record time"),
        (
            {"inputs": [{"name": "MemberId", "type": "string", "description": "x"}]},
            "String should match pattern",
        ),
        (
            {
                "inputs": [
                    {
                        "name": "member_id",
                        "type": "string",
                        "description": "x",
                        "sensitivity": "pii",
                        "example": "100001",
                    }
                ]
            },
            "must not carry an example",
        ),
    ],
)
def test_a_malformed_finish_payload_is_rejected(
    mutation: dict[str, Any], fragment: str
) -> None:
    payload = {**WELL_FORMED, **mutation}
    with pytest.raises(ValidationError) as exc:
        if "checkpoint" in mutation:
            Signal(**payload["checkpoint"])
        else:
            ParamSpec(**payload["inputs"][0])
    assert fragment in str(exc.value)


def test_the_checkpoint_schema_offers_only_kinds_the_model_can_author() -> None:
    """element_present needs a locator, and the model is not allowed to write one."""
    kinds = TOOLS["finish"].parameters["properties"]["checkpoint"]["properties"]["kind"]["enum"]
    assert "element_present" not in kinds
    assert "text_present" in kinds


# -- the prompt ------------------------------------------------------------------
@pytest.mark.parametrize("tool", [t.value for t in ToolName])
def test_the_prompt_describes_every_tool_that_exists(tool: str) -> None:
    """Catches drift without making the prompt dynamic and unreadable."""
    assert tool in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "requirement",
    [
        "accessibility snapshot",
        "refs are valid only for the snapshot",
        "never remember a ref",
        "role and its accessible name",
        "a refusal is final",
        "parameter, not part of the flow",
        "there is no wait tool",
    ],
)
def test_the_prompt_states_what_it_must(requirement: str) -> None:
    assert requirement.lower() in SYSTEM_PROMPT.lower(), requirement
