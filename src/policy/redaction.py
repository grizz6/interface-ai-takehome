"""Replace sensitive values with placeholders before anything is written down.

design rule 6 says nothing marked pii or secret reaches disk: not artifacts, not
logs, not evidence, not filenames. This is the one place that rule is implemented, so that
everything which writes can route through it rather than each caller remembering.

The placeholder form is `<param:name>`, which is the same shape a ParamBinding uses, so a
redacted log line reads as a description of the flow rather than as damaged text.
"""
from __future__ import annotations

from collections.abc import Mapping


class Redactor:
    """Built from name to sensitive value, applied to anything on its way out."""

    def __init__(self, values: Mapping[str, str]) -> None:
        # Longest first, so a value that contains another value cannot be partly rewritten
        # and leave a recognizable fragment behind.
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
