"""Command line entry points.

.env IS LOADED HERE AND NOWHERE ELSE. This is the single place in the repo that knows a .env
file can exist. `load_dotenv` moves its contents into the process environment and returns
nothing to us, so no value is ever held, logged or written by anything we wrote; the Gemini
SDK then reads GEMINI_API_KEY from the environment itself.

Note for whoever reads the design rules alongside this: invariant 6 says .env is never read. The one
call below is the exception that instruction implies, and the invariant needs a carve out
saying so. It is flagged rather than quietly assumed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from src.discovery.client import GeminiClient, ModelClient, ModelTurn, ScriptedClient
from src.discovery.loop import DiscoveryLimits, run_discovery
from src.evidence.writer import EVIDENCE_ROOT, EvidenceWriter, new_run_id
from src.models.capability import SurfaceDescriptor, SurfaceFingerprint
from src.models.common import SurfaceKind
from src.models.results import EXIT_CODES
from src.policy.gate import PolicyGate
from src.policy.loading import DEFAULT_POLICY_PATH, PolicyConfigError, load_policy_config
from src.policy.redaction import Redactor
from src.surface.web import WebSurface

DEFAULT_MODEL = "gemini-3-flash-preview"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cua", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="Run an LLM driven discovery run.")
    discover.add_argument("--goal", required=True, help="What to accomplish, in plain words.")
    discover.add_argument("--target", required=True, help="Entry point URL.")
    discover.add_argument(
        "--config", default=str(DEFAULT_POLICY_PATH), help="Path to the policy file."
    )
    discover.add_argument("--model", default=DEFAULT_MODEL)
    discover.add_argument("--provider", default="gemini", choices=["gemini"])
    discover.add_argument("--max-steps", type=int, default=DiscoveryLimits().max_steps)
    discover.add_argument("--timeout", type=float, default=DiscoveryLimits().wall_clock_s)
    discover.add_argument(
        "--headed", action="store_true", help="Show the browser rather than running headless."
    )
    discover.add_argument(
        "--dry-run",
        metavar="SCRIPT.JSON",
        help="Replay a scripted list of model turns instead of calling a model. No network.",
    )
    discover.add_argument(
        "--redact",
        action="append",
        default=[],
        metavar="VALUE",
        help="A value that must never appear in evidence. Repeatable.",
    )
    return parser


def _descriptor(target: str) -> SurfaceDescriptor:
    parsed = urlparse(target)
    return SurfaceDescriptor(
        kind=SurfaceKind.LEGACY_WEB,
        app_id=parsed.hostname or "unknown",
        base_url=f"{parsed.scheme}://{parsed.netloc}",
        entry_path=parsed.path or "/",
        fingerprint=SurfaceFingerprint(captured_at=datetime.now(UTC)),
    )


def _client(args: argparse.Namespace) -> ModelClient:
    if args.dry_run:
        turns = [ModelTurn.model_validate(t) for t in json.loads(Path(args.dry_run).read_text())]
        return ScriptedClient(turns)
    return GeminiClient(args.model)


def cmd_discover(args: argparse.Namespace) -> int:
    try:
        policy = load_policy_config(args.config)
    except PolicyConfigError as exc:
        print(f"policy: {exc}", file=sys.stderr)
        return EXIT_CODES["failure"]

    redactor = Redactor({f"redacted_{i}": v for i, v in enumerate(args.redact)})
    writer = EvidenceWriter(new_run_id(), redactor, root=EVIDENCE_ROOT)
    surface = WebSurface(policy, PolicyGate(policy), headless=not args.headed)

    try:
        outcome = run_discovery(
            goal=args.goal,
            surface=surface,
            client=_client(args),
            evidence=writer.ref,
            model="scripted" if args.dry_run else args.model,
            surface_descriptor=_descriptor(args.target),
            limits=DiscoveryLimits(max_steps=args.max_steps, wall_clock_s=args.timeout),
            on_observation=lambda obs: (
                writer.screenshot(obs.screenshot_png) if obs.screenshot_png else None
            ),
        )
    finally:
        surface.close()

    for event in outcome.transcript.events:
        writer.event(event.kind.value, seq=event.seq, at=event.at, **event.payload)
    writer.write_transcript(outcome.transcript)
    writer.write_result(outcome.result)

    print(writer.directory)
    return EXIT_CODES[outcome.result.kind]


def main(argv: list[str] | None = None) -> int:
    # The one and only place .env is touched.
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        return cmd_discover(args)
    return EXIT_CODES["failure"]


if __name__ == "__main__":
    raise SystemExit(main())
