"""Swap sensitive values for placeholders before anything is written down.

Nothing marked pii or secret should reach disk: not capabilities, logs, evidence or file
names. This is the one place that happens, so everything that writes goes through it instead
of each caller having to remember.

Placeholders look like `<param:name>`, the same as a parameter reference, so a redacted log
line still reads sensibly.
"""
from __future__ import annotations

from collections.abc import Mapping


class Redactor:
    """Built from a map of name to sensitive value, and applied to anything being written."""

    def __init__(self, values: Mapping[str, str]) -> None:
        # Longest first, so a value containing a shorter one is not half-replaced, leaving a
        # recognisable piece behind.
        self._pairs: list[tuple[str, str]] = sorted(
            ((value, f"<param:{name}>") for name, value in values.items() if value),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )

    @property
    def is_empty(self) -> bool:
        return not self._pairs

    def redact(self, text: str) -> str:
        for value, placeholder in self._pairs:
            text = text.replace(value, placeholder)
        return text

    def redact_mapping(self, data: Mapping[str, str]) -> dict[str, str]:
        """Redact both keys and values, because a value can end up used as a key."""
        return {self.redact(k): self.redact(v) for k, v in data.items()}
