#!/usr/bin/env python3
"""Check the model connection works, without ever showing the key.

It checks `"GEMINI_API_KEY" in os.environ`, which only asks whether the name is set and never
reads the value. Nothing here prints the key, part of it, or its length. Only the Gemini SDK
reads the value.

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
    # load_dotenv puts .env into the environment and only returns a bool, so no value is held.
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
        # The name is set but the SDK found no usable key, so the value must be empty. This
        # works that out without looking at the file.
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
        # Broad on purpose, and reported with an exit code rather than swallowed. The point of
        # a smoke test is to say what went wrong, and provider errors come in many classes.
        # The message is a provider error and does not contain the key.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(turn.text or "<the model returned no text>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
