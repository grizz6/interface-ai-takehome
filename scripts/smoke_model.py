#!/usr/bin/env python3
"""Prove the model wiring works, by behaviour, without ever surfacing the key.

Presence is checked with `"GEMINI_API_KEY" in os.environ`, which asks whether the name is set
and never binds the value to anything. Nothing here prints the key, a prefix of it, its length,
or any fingerprint from which it could be narrowed. Per design rule 6, the only thing
that ever reads the value is the Gemini SDK itself.

Exit codes:
    0  the model answered
    1  GEMINI_API_KEY is not set in the environment
    2  the call was attempted and failed; the error class and message are reported

Run it with:
    .venv/bin/python scripts/smoke_model.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Run by path rather than as a module, so the repo root is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

DEFAULT_MODEL = "gemini-3.6-flash"
PROMPT = "Reply with exactly: wiring ok"


def main() -> int:
    # Loading .env is this repo's one sanctioned touch of that file, and load_dotenv returns
    # the values to nobody: it puts them in the environment and hands back a bool.
    load_dotenv()

    if "GEMINI_API_KEY" not in os.environ:
        print(
            "GEMINI_API_KEY: absent. Set it in .env or export it, then run this again.",
            file=sys.stderr,
        )
        return 1
    print("GEMINI_API_KEY: present")

    from src.discovery.client import GeminiClient, UserMessage

    model = os.environ.get("SMOKE_MODEL", DEFAULT_MODEL)
    print(f"model: {model}")
    client = GeminiClient(model)

    try:
        turn = client.complete("Answer in as few words as possible.", [UserMessage(text=PROMPT)], [])
    except ValueError as exc:
        # The SDK says it got no usable key while our own check says the name is set. Those
        # two facts together mean the value is empty or whitespace, and they establish it
        # from behaviour rather than by looking at the file, which invariant 6 forbids.
        if "API key" in str(exc):
            print(
                "The name GEMINI_API_KEY is set but the SDK found no usable key, so the "
                "value is empty. Put the key after the equals sign in .env.",
                file=sys.stderr,
            )
            return 1
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        # Deliberately broad, and converted to a reported exit code rather than swallowed.
        # A smoke test exists to name whatever went wrong, and the useful failures here are
        # provider errors whose classes we should not have to enumerate in advance. The
        # message is printed verbatim: it is a provider error, and never contains the key.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(turn.text or "<the model returned no text>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
