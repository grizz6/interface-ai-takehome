"""Summarize a directory of runs as one table.

The point is that a reviewer should be able to see what is in `evidence/` without opening
anything. One row per run, and the detail column carries the single fact that distinguishes
this run from its neighbours: the outcome code, the failure class, the intervention id.

Reads meta.json where it exists and falls back to result.json, so a directory written before
meta.json existed still appears rather than silently vanishing from its own index.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.models.results import EXIT_CODES


@dataclass(frozen=True)
class Row:
    run_id: str
    kind: str
    result_kind: str
    exit_code: int | None
    duration_ms: int | None
    detail: str

    @property
    def duration(self) -> str:
        return "" if self.duration_ms is None else f"{self.duration_ms / 1000:.1f}s"


def _load(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _detail(result: dict[str, Any], meta: dict[str, Any]) -> str:
    """The one line that says what happened, chosen by result kind."""
    kind = result.get("kind")
    if kind == "success":
        outputs = result.get("outputs") or {}
        named = ", ".join(f"{k}={v}" for k, v in list(outputs.items())[:3])
        return named or "no declared outputs"
    if kind == "business_outcome":
        return f"{result.get('code', '?')}: {result.get('message', '')}".strip().rstrip(":")
    if kind == "needs_human":
        return f"{result.get('reason', '?')}, intervention {result.get('intervention_id', '?')}"
    if kind == "policy_blocked":
        return f"blocked by {result.get('rule', '?')}"
    if kind == "failure":
        return f"{result.get('error_class', '?')} at step {result.get('step_index', '?')}"
    capability = meta.get("capability_id")
    return f"no result written{f' for {capability}' if capability else ''}"


def _duration(meta: dict[str, Any], result: dict[str, Any]) -> int | None:
    if isinstance(meta.get("duration_ms"), int):
        return int(meta["duration_ms"])
    if isinstance(result.get("duration_ms"), int):
        return int(result["duration_ms"])
    started, finished = meta.get("started_at"), meta.get("finished_at")
    if isinstance(started, str) and isinstance(finished, str):
        from datetime import datetime

        try:
            delta = datetime.fromisoformat(finished) - datetime.fromisoformat(started)
        except ValueError:
            return None
        return int(delta.total_seconds() * 1000)
    return None


def collect(root: Path | str) -> list[Row]:
    """One Row per run directory under root, in name order.

    The row is labelled by the DIRECTORY name, not by meta.json's run_id. In `evidence/` the
    two are the same. In a curated set they are not: the directories are renamed to say what
    each run demonstrates, and a table of raw run ids there tells a reader nothing about which
    row to open. Name order rather than newest first for the same reason, since a curated set
    is numbered in the order it should be read.
    """
    rows: list[Row] = []
    base = Path(root)
    if not base.exists():
        return rows
    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or directory.name == "curated":
            continue
        meta = _load(directory / "meta.json")
        result = _load(directory / "result.json")
        kind = str(result.get("kind", "")) or "none"
        rows.append(
            Row(
                run_id=directory.name,
                kind=str(meta.get("kind") or "unknown"),
                result_kind=kind,
                exit_code=meta.get("exit_code") if isinstance(meta.get("exit_code"), int)
                else EXIT_CODES.get(kind),
                duration_ms=_duration(meta, result),
                detail=_detail(result, meta),
            )
        )
    return rows


def render(rows: list[Row]) -> str:
    """A Markdown table. Deliberately not a report: the runs speak for themselves."""
    lines = [
        "| run | kind | result | exit | duration | detail |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        exit_code = "" if row.exit_code is None else str(row.exit_code)
        detail = row.detail.replace("|", "\\|")
        lines.append(
            f"| `{row.run_id}` | {row.kind} | {row.result_kind} | {exit_code} | "
            f"{row.duration} | {detail} |"
        )
    if not rows:
        lines.append("| | | | | | no runs found |")
    return "\n".join(lines) + "\n"


def write_index(root: Path | str, destination: Path | str) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(collect(root)))
    return path
