"""Export JSON Schema for the two contracts a reviewer and a caller need.

Run with `make schemas`. Writes schemas/capability.schema.json and
schemas/run_result.schema.json.

Capability is exported because it is the artifact a human reviews and an agent invokes.
RunResult is exported because it is what the caller has to branch on, and a caller that
cannot see the five kinds up front will collapse them.
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
