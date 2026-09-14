"""Replay never imports a model client, checked by code rather than by a comment.

A comment saying replay has no model dependency stops being true the day someone adds a handy
import from src.discovery, and nobody notices.

Two separate checks. A static walk of the import graph, which catches an import that exists but
never runs. And a runtime check in a fresh subprocess, which catches what the static walk
misses, like an import inside a function.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

FORBIDDEN_PREFIXES = ("google.genai", "google.generativeai", "anthropic", "src.discovery")
SRC = Path("src")


def _module_path(name: str) -> Path | None:
    candidate = Path(name.replace(".", "/") + ".py")
    if candidate.is_file():
        return candidate
    package = Path(name.replace(".", "/")) / "__init__.py"
    return package if package.is_file() else None


def _imports_of(path: Path) -> set[str]:
    """Every module this file imports, including imports inside functions."""
    found: set[str] = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            # `from google import genai` records the module as "google", which would slip
            # straight past a prefix check for "google.genai". The names have to be joined
            # on. The control test at the bottom of this file caught exactly that.
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def transitive_imports(entry: str) -> dict[str, set[str]]:
    """Walk the local import graph from an entry module. Returns module -> its imports."""
    graph: dict[str, set[str]] = {}
    queue = [entry]
    while queue:
        name = queue.pop()
        if name in graph:
            continue
        path = _module_path(name)
        if path is None:
            graph[name] = set()
            continue
        graph[name] = _imports_of(path)
        queue.extend(m for m in graph[name] if m.startswith("src.") and m not in graph)
    return graph


def test_the_replay_import_graph_reaches_no_model() -> None:
    graph = transitive_imports("src.replay.engine")
    offenders = [
        f"{module} imports {imported}"
        for module, imports in graph.items()
        for imported in imports
        if imported.startswith(FORBIDDEN_PREFIXES)
    ]
    assert not offenders, (
        "replay must not reach a model client:\n" + "\n".join(offenders)
    )
    assert len(graph) > 5, "the walk found suspiciously little; it may not be following edges"


def test_every_module_under_replay_is_covered_by_the_walk() -> None:
    """A new file in src/replay must be reachable, or the graph test proves nothing about it."""
    on_disk = {
        ".".join(p.with_suffix("").parts)
        for p in (SRC / "replay").rglob("*.py")
        if p.name != "__init__.py"
    }
    reached = set(transitive_imports("src.replay.engine"))
    missed = sorted(on_disk - reached)
    assert not missed, (
        f"these files live under src/replay but nothing imports them, so the isolation "
        f"proof does not cover them: {missed}"
    )


def test_importing_replay_in_a_clean_process_loads_no_model_module() -> None:
    """Catches a deferred import that only fires at call time."""
    code = (
        "import sys, src.replay.engine, src.replay.preflight\n"
        "bad = sorted(m for m in sys.modules "
        "if m.startswith(('google.genai','google.generativeai','anthropic','src.discovery')))\n"
        "print(';'.join(bad))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=Path.cwd()
    )
    assert done.returncode == 0, done.stderr
    loaded = [m for m in done.stdout.strip().split(";") if m]
    assert not loaded, f"importing replay pulled in: {loaded}"


def test_the_discovery_package_does_still_import_a_model() -> None:
    """A control. If this passes vacuously the test above proves nothing."""
    graph = transitive_imports("src.discovery.client")
    reaches = any(
        imported.startswith("google.genai")
        for imports in graph.values()
        for imported in imports
    )
    assert reaches, "discovery should reach the SDK; if it does not, the walk is broken"
