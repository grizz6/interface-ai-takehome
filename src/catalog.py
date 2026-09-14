"""The capability catalog: what an agent can run, and what each capability takes and returns.

This is the first stretch goal in the brief. An agent should not need to open JSON files to find
out what a capability does, so the catalog reads `capabilities/` and answers two questions: what
is there, and what does this one accept, return and refuse.

It is a module and two CLI commands, not a server. Running a capability still goes through
`replay`, and `describe` prints the exact command, so an agent can go list, describe, run.

Every file is validated through the Capability model when loaded. A file that fails is an error
naming the file, never skipped, because an agent told a capability does not exist when it is
really broken will work around a problem nobody knows about.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.models.capability import Capability
from src.models.common import ApprovalStatus, RiskClass
from src.models.results import EXIT_CODES

CAPABILITIES_ROOT = Path("capabilities")


class UnknownCapability(LookupError):
    """No capability with that id, or no such version of it."""


class InvalidCapability(ValueError):
    """A file in the catalog directory that does not validate as a Capability."""


@dataclass(frozen=True)
class Entry:
    path: Path
    capability: Capability


def _version_key(version: str) -> tuple[int, ...]:
    """Sort versions as numbers, so 1.10.0 comes after 1.2.0."""
    return tuple(int(part) for part in version.split("."))


def load(root: Path | str = CAPABILITIES_ROOT) -> list[Entry]:
    """Every capability under root, validated, ordered by id and then by version."""
    entries: list[Entry] = []
    for path in sorted(Path(root).glob("*.json")):
        try:
            capability = Capability.model_validate_json(path.read_text())
        except ValueError as exc:
            raise InvalidCapability(f"{path} is not a valid capability: {exc}") from exc
        entries.append(Entry(path=path, capability=capability))
    return sorted(
        entries,
        key=lambda e: (e.capability.capability_id, _version_key(e.capability.version)),
    )


def find(entries: list[Entry], capability_id: str, version: str | None = None) -> Entry:
    """One capability by id. The highest version unless a version is named."""
    matching = [e for e in entries if e.capability.capability_id == capability_id]
    if version is not None:
        matching = [e for e in matching if e.capability.version == version]
    if not matching:
        known = sorted({e.capability.capability_id for e in entries})
        wanted = capability_id if version is None else f"{capability_id} {version}"
        raise UnknownCapability(f"no capability {wanted!r}. Known: {known}")
    return max(matching, key=lambda e: _version_key(e.capability.version))


def _typed(name: str, value_type: Any) -> str:
    return f"{name}: {getattr(value_type, 'value', value_type)}"


def summary(entry: Entry) -> dict[str, Any]:
    """The one row `catalog list` prints for a capability."""
    cap = entry.capability
    return {
        "capability_id": cap.capability_id,
        "version": cap.version,
        "status": cap.status.value,
        "description": cap.description,
        "inputs": [_typed(p.name, p.type) for p in cap.inputs],
        "outputs": [_typed(o.name, o.type) for o in cap.outputs],
    }


def _invocation(entry: Entry) -> str:
    """The command that runs this capability, with a placeholder for each input.

    Uses an input's example value if it has one. pii and secret inputs are not allowed examples,
    so they get a placeholder like <string>.
    """
    cap = entry.capability
    params = {p.name: p.example if p.example is not None else f"<{p.type.value}>" for p in cap.inputs}
    draft = " --allow-draft" if cap.status is ApprovalStatus.DRAFT else ""
    return (
        f".venv/bin/python -m src.cli replay --capability {entry.path} "
        f"--params '{json.dumps(params)}'{draft}"
    )


def contract(entry: Entry) -> dict[str, Any]:
    """Everything `catalog describe` prints about one capability."""
    cap = entry.capability
    return {
        "capability_id": cap.capability_id,
        "version": cap.version,
        "name": cap.name,
        "description": cap.description,
        "status": cap.status.value,
        "path": str(entry.path),
        "surface": {
            "kind": cap.surface.kind.value,
            "app_id": cap.surface.app_id,
            "variant_id": cap.surface.variant_id,
        },
        "inputs": [
            {
                "name": p.name,
                "type": p.type.value,
                "required": p.required,
                "sensitivity": p.sensitivity.value,
                "pattern": p.pattern,
                "description": p.description,
            }
            for p in cap.inputs
        ],
        "outputs": [
            {
                "name": o.name,
                "type": o.type.value,
                "sensitivity": o.sensitivity.value,
                "description": o.description,
            }
            for o in cap.outputs
        ],
        "business_outcomes": [
            {"code": o.code, "description": o.description} for o in cap.known_outcomes
        ],
        "success_means": cap.checkpoint.description,
        "requires_human_approval": [
            {"step_index": s.index, "description": s.description}
            for s in cap.steps
            if s.risk is RiskClass.RISKY_IRREVERSIBLE
        ],
        "exit_codes": dict(sorted(EXIT_CODES.items(), key=lambda kv: kv[1])),
        "invoke": _invocation(entry),
    }


def render_list(entries: list[Entry]) -> str:
    lines: list[str] = []
    for entry in entries:
        row = summary(entry)
        lines.append(f"{row['capability_id']} {row['version']} [{row['status']}]")
        lines.append(f"  {row['description']}")
        lines.append(f"  in:  {', '.join(row['inputs']) or '(none)'}")
        lines.append(f"  out: {', '.join(row['outputs']) or '(none)'}")
    return "\n".join(lines)


def render_contract(spec: dict[str, Any]) -> str:
    lines = [
        f"{spec['capability_id']} {spec['version']} [{spec['status']}]",
        f"  {spec['name']}: {spec['description']}",
        f"  file:    {spec['path']}",
        f"  surface: {spec['surface']['kind']} {spec['surface']['app_id']} "
        f"variant {spec['surface']['variant_id'] or '(unspecified)'}",
        "",
        "inputs",
    ]
    for p in spec["inputs"]:
        flags = "required" if p["required"] else "optional"
        pattern = f", pattern {p['pattern']}" if p["pattern"] else ""
        lines.append(f"  {p['name']}: {p['type']} ({flags}, sensitivity {p['sensitivity']}{pattern})")
        lines.append(f"      {p['description']}")
    lines.append("outputs")
    for o in spec["outputs"]:
        lines.append(f"  {o['name']}: {o['type']} (sensitivity {o['sensitivity']})")
        lines.append(f"      {o['description']}")
    lines.append("business outcomes, returned with exit 10 and never raised")
    for o in spec["business_outcomes"] or [{"code": "(none declared)", "description": ""}]:
        lines.append(f"  {o['code']}  {o['description']}".rstrip())
    lines.append(f"success means\n  {spec['success_means']}")
    lines.append("requires human approval before")
    if not spec["requires_human_approval"]:
        lines.append("  nothing: no step is irreversible")
    for s in spec["requires_human_approval"]:
        lines.append(f"  step {s['step_index']}: {s['description']}")
    codes = ", ".join(f"{k} {v}" for k, v in spec["exit_codes"].items())
    lines.append(f"exit codes\n  {codes}")
    lines.append(f"invoke\n  {spec['invoke']}")
    return "\n".join(lines)
