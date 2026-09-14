"""The tools the model gets, with schemas built from the capability models.

The model never writes a locator. It points at a ref, and the surface turns that into a
LocatorBundle with describe(). So no tool here takes a LocatorBundle: `finish` takes a `ref`
where OutputSpec has a locator, and the checkpoint only offers check types that need no
locator. If the model wrote locators, choosing a tier would happen in the prompt instead of in
Python. See DECISIONS.md 0011.

The schemas are generated, not copied. `finish` gets its shapes from ParamSpec, OutputSpec,
ExtractionSpec and Signal through `model_json_schema()`, with some fields removed. Add a field
to ParamSpec and `finish` accepts it on the next run, so the tools and the capability format
cannot drift apart.

There is no wait tool. See DECISIONS.md 0010.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any

from src.discovery.client import ToolSpec
from src.models.capability import ExtractionSpec, OutputSpec, ParamSpec, Signal


class ToolName(StrEnum):
    """Every tool the model can call. The loop switches on this rather than raw strings."""

    LOOK = "look"
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE_TEXT = "type_text"
    SELECT_OPTION = "select_option"
    PRESS_KEY = "press_key"
    FINISH = "finish"
    GIVE_UP = "give_up"


AUTHORABLE_SIGNAL_KINDS = ["text_present", "text_absent", "url_matches", "aria_matches"]
"""Check types the model can use without writing a locator.

element_present and element_absent need a locator, and the model should not write one.
Offering a type that always fails validation would just waste a turn.
"""


# --------------------------------------------------------------------------
# deriving schemas from the pydantic models
# --------------------------------------------------------------------------
def inline_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace every $ref with its $defs entry, giving a schema with no references.

    We cannot count on the provider resolving references in tool schemas, so nothing leaves
    here with a $ref in it.
    """
    defs: dict[str, Any] = schema.get("$defs", {})

    def resolve(node: Any, seen: frozenset[str]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.split("/")[-1]
                if name in seen:
                    # Stops a model that refers to itself expanding forever. None do today.
                    return {"type": "object"}
                target = dict(defs.get(name, {}))
                merged = {k: v for k, v in node.items() if k != "$ref"}
                return {**resolve(target, seen | {name}), **merged}
            return {k: resolve(v, seen) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item, seen) for item in node]
        return node

    resolved = resolve({k: v for k, v in schema.items() if k != "$defs"}, frozenset())
    assert isinstance(resolved, dict)
    return resolved


def derive(model: type, *, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    """A JSON Schema with no references for a pydantic model, minus the named properties."""
    schema = inline_defs(model.model_json_schema())  # type: ignore[attr-defined]
    properties = {k: v for k, v in schema.get("properties", {}).items() if k not in drop}
    required = [r for r in schema.get("required", []) if r not in drop]
    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


def _checkpoint_schema() -> dict[str, Any]:
    """Signal, limited to the check types the model can use."""
    schema = derive(Signal, drop=("locator", "frame_path"))
    schema["properties"]["kind"] = {
        "type": "string",
        "enum": AUTHORABLE_SIGNAL_KINDS,
        "description": (
            "text_present is the usual choice. Use aria_matches only to assert the shape "
            "of a screen rather than one string."
        ),
    }
    schema["description"] = "The condition that proves the goal was actually reached."
    return schema


def _output_schema() -> dict[str, Any]:
    """OutputSpec, with the locator swapped for a ref that the surface turns into one."""
    extraction = derive(ExtractionSpec, drop=("locator",))
    extraction["properties"]["ref"] = {
        "type": "string",
        "description": (
            "The ref of the element holding this value, from the CURRENT snapshot. It is "
            "converted into a durable locator immediately and is not stored."
        ),
    }
    extraction["required"] = sorted({*extraction.get("required", []), "ref", "source", "parse"})

    schema = derive(OutputSpec, drop=("extraction",))
    schema["properties"]["extraction"] = extraction
    schema["required"] = sorted({*schema.get("required", []), "extraction"})
    return schema


# --------------------------------------------------------------------------
# the tools themselves
# --------------------------------------------------------------------------
def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


_REF = {
    "type": "string",
    "description": (
        "A ref from the snapshot you were just shown, such as e17 or f1e36. Refs are valid "
        "only for that snapshot. Never reuse one from an earlier turn."
    ),
}


def discovery_tools() -> list[ToolSpec]:
    """Every tool offered to the model, in the order they are described to it."""
    return [
        ToolSpec(
            name=ToolName.LOOK,
            description=(
                "Take a fresh snapshot of the screen and return it. Use this after anything "
                "that may have changed the page, and whenever your refs are from a previous "
                "turn."
            ),
            parameters=_obj({}, []),
        ),
        ToolSpec(
            name=ToolName.NAVIGATE,
            description="Go directly to a URL. Only paths the policy permits will be allowed.",
            parameters=_obj(
                {"url": {"type": "string", "description": "Absolute or site relative URL."}},
                ["url"],
            ),
        ),
        ToolSpec(
            name=ToolName.CLICK,
            description="Click a control identified by a ref from the current snapshot.",
            parameters=_obj({"ref": _REF}, ["ref"]),
        ),
        ToolSpec(
            name=ToolName.TYPE_TEXT,
            description="Replace the contents of a text field with the given text.",
            parameters=_obj(
                {
                    "ref": _REF,
                    "text": {
                        "type": "string",
                        "description": (
                            "The value to enter. If this came from the goal, declare it as "
                            "an input parameter when you finish."
                        ),
                    },
                },
                ["ref", "text"],
            ),
        ),
        ToolSpec(
            name=ToolName.SELECT_OPTION,
            description="Choose an option in a dropdown by its visible label.",
            parameters=_obj(
                {"ref": _REF, "value": {"type": "string", "description": "The option label."}},
                ["ref", "value"],
            ),
        ),
        ToolSpec(
            name=ToolName.PRESS_KEY,
            description="Press a single key, for example Enter or Tab.",
            parameters=_obj(
                {"key": {"type": "string", "description": "Key name, such as Enter."}},
                ["key"],
            ),
        ),
        ToolSpec(
            name=ToolName.FINISH,
            description=(
                "Call this once the goal is reached. It declares the reusable capability: "
                "what it is called, what it proves, what it needs, and what it returns. "
                "Anything the goal supplied as a concrete value belongs in inputs, not baked "
                "into the steps."
            ),
            parameters=_obj(
                {
                    "capability_name": {
                        "type": "string",
                        "description": "Short lowercase slug, words separated by hyphens.",
                    },
                    "description": {
                        "type": "string",
                        "description": "One sentence a reviewer could check the flow against.",
                    },
                    "checkpoint": _checkpoint_schema(),
                    "inputs": {
                        "type": "array",
                        "description": "Typed parameters a caller supplies per invocation.",
                        "items": derive(ParamSpec),
                    },
                    "outputs": {
                        "type": "array",
                        "description": "Typed values read off the screen and returned.",
                        "items": _output_schema(),
                    },
                },
                ["capability_name", "description", "checkpoint", "inputs", "outputs"],
            ),
        ),
        ToolSpec(
            name=ToolName.GIVE_UP,
            description=(
                "Stop and hand over to a human. Use this when the goal cannot be reached, "
                "when an action has been refused and no other route exists, or when you "
                "cannot tell what the screen is showing."
            ),
            parameters=_obj(
                {
                    "reason": {
                        "type": "string",
                        "description": "What you tried and what stopped you.",
                    }
                },
                ["reason"],
            ),
        ),
    ]
