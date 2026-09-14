"""Export JSON Schema for capabilities and run results.

Run with `make schemas`. Writes schemas/capability.schema.json and
schemas/run_result.schema.json.

Capability is what a person reviews and an agent runs. RunResult is what a caller branches
on, and a caller that cannot see all five result types up front tends to lump them together.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from src.models.capability import Capability
from src.models.results import RunResult

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def build() -> dict[str, dict[str, Any]]:
    """Return filename to JSON Schema, without touching the filesystem."""
    return {
        "capability.schema.json": Capability.model_json_schema(),
        "run_result.schema.json": TypeAdapter(RunResult).json_schema(),
    }


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for filename, schema in build().items():
        path = SCHEMA_DIR / filename
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        print(f"wrote {path.relative_to(SCHEMA_DIR.parent)}")


if __name__ == "__main__":
    main()
