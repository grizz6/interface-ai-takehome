"""Read one declared value off the live page.

Lives in the surface rather than in discovery or replay because both need it and both need it
to behave identically. A value that extracts during discovery and not during replay is the
worst kind of bug: the capability was verified, approved, and is wrong.

Parsing is deliberately strict. If an output is declared `currency` and the text on the page
is not a number, this returns None rather than a string that will surprise the caller later.
An output that cannot be parsed as what it claims to be has not been extracted.
"""
from __future__ import annotations

import re
from typing import Any, cast

from src.models.capability import ExtractionSpec

_NOT_NUMERIC = re.compile(r"[^0-9.\-]")


def _parse(text: str, parse: str) -> str | None:
    if parse in ("raw", "date"):
        return text or None
    cleaned = _NOT_NUMERIC.sub("", text)
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if parse == "integer":
        if value != int(value):
            return None
        return str(int(value))
    return f"{value:.2f}" if parse in ("currency", "decimal") else cleaned


def extract_value(surface: Any, spec: ExtractionSpec) -> str | None:
    """Resolve the locator and read the value, or None if there is nothing to read."""
    resolved = surface.resolve(spec.locator)
    handle = cast(Any, resolved.handle)

    if spec.source == "text":
        raw = handle.inner_text()
    elif spec.source == "value":
        raw = handle.input_value()
    else:
        raw = handle.get_attribute(spec.attribute)

    if raw is None:
        return None
    text = str(raw).strip()
    if spec.strip_pattern:
        text = re.sub(spec.strip_pattern, "", text).strip()
    return _parse(text, spec.parse)
