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
from src.evidence.failure import write_failure_artifacts
from src.evidence.meta import RunMeta
from src.evidence.writer import EVIDENCE_ROOT, EvidenceWriter, new_run_id

INTERVENTIONS_ROOT = Path("interventions")
from src.models.capability import SurfaceDescriptor, SurfaceFingerprint
from src.models.common import SurfaceKind
from src.discovery.transcript import DiscoveryTranscript
from src.models.results import EXIT_CODES
from src.policy.gate import PolicyGate
from src.policy.loading import DEFAULT_POLICY_PATH, PolicyConfigError, load_policy_config
from src.policy.redaction import Redactor
from src.models.capability import Capability, describe_params
from src.recorder.compile import CompileOutcome, compile_capability
from src import catalog
from src.catalog import CAPABILITIES_ROOT
from src.escalation.operator import serve
from src.escalation.session import DEFAULT_DEADLINE_SECONDS, Session
from src.replay.engine import replay
from src.surface.web import WebSurface

# gemini-3-flash-preview caps the free tier at 20 requests, which a 25 step run
# exhausts before it finishes. This one has the headroom to complete a run.
DEFAULT_MODEL = "gemini-3.6-flash"


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
        "--evidence-dir",
        default=str(EVIDENCE_ROOT),
        metavar="DIR",
        help="Where run directories are written. Tests pass a temporary path.",
    )
    discover.add_argument(
        "--record",
        action="store_true",
        help="On success, compile the transcript into a capability and save it.",
    )
    discover.add_argument(
        "--redact",
        action="append",
        default=[],
        metavar="VALUE",
        help="A value that must never appear in evidence. Repeatable.",
    )
    record = sub.add_parser(
        "record", help="Compile a discovery transcript into a capability artifact."
    )
    record.add_argument("--transcript", required=True, help="Path to a transcript.json.")
    record.add_argument("--out", default="capabilities", help="Directory to write into.")
    record.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    record.add_argument(
        "--overwrite", action="store_true",
        help="Replace an existing capability file with the same id and version.",
    )

    play = sub.add_parser("replay", help="Replay a saved capability, with no model involved.")
    play.add_argument("--capability", required=True, help="Path to a capability JSON file.")
    play.add_argument("--params", default="{}", help='JSON object, e.g. \'{"member_id":"100001"}\'')
    play.add_argument("--config", default=str(DEFAULT_POLICY_PATH))
    play.add_argument("--evidence-dir", default=str(EVIDENCE_ROOT), metavar="DIR")
    play.add_argument("--headed", action="store_true")
    play.add_argument(
        "--allow-draft",
        action="store_true",
        help="Replay a capability that is still draft. For development only.",
    )
    play.add_argument("--redact", action="append", default=[], metavar="VALUE")
    play.add_argument(
        "--interventions-dir",
        default=str(INTERVENTIONS_ROOT),
        metavar="DIR",
        help="Where handoff requests are written for the operator console.",
    )
    play.add_argument(
        "--lease-path",
        default=None,
        metavar="FILE",
        help=(
            "Share a control lease file with an operator console. Without this the run holds "
            "an in-process lease and every stopping condition ends the run instead of "
            "waiting for a person."
        ),
    )
    play.add_argument(
        "--intervention-timeout",
        type=int,
        default=DEFAULT_DEADLINE_SECONDS,
        metavar="SECONDS",
        help="How long a handoff waits before the run gives up on an answer.",
    )

    console = sub.add_parser("operator", help="Serve the minimal operator console.")
    console.add_argument("--port", type=int, default=8090)
    console.add_argument("--interventions-dir", default=str(INTERVENTIONS_ROOT), metavar="DIR")
    console.add_argument("--lease-path", default=None, metavar="FILE")

    catalog = sub.add_parser("catalog", help="List capabilities, or print one's typed contract.")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    listing = catalog_sub.add_parser("list", help="Every capability: id, version, status, types.")
    describe = catalog_sub.add_parser("describe", help="The full typed contract for one id.")
    describe.add_argument("capability_id")
    describe.add_argument("--version", default=None, help="Defaults to the highest version.")
    for command in (listing, describe):
        command.add_argument("--dir", default=str(CAPABILITIES_ROOT), metavar="DIR")
        command.add_argument("--json", action="store_true", help="Machine readable output.")
    return parser


def _save_capability(outcome: CompileOutcome, out_dir: Path, *, overwrite: bool = False) -> int:
    """Print the compile report, write the artifact, return the exit code.

    A capability file is a reviewed artifact, and its name is its id and version, so a different
    run compiled to the same name would silently replace the reviewed one. Writing identical
    bytes is allowed, since that is a reproduction; writing different ones needs --overwrite.
    """
    for line in outcome.report.lines():
        print(f"  {line}")
    if outcome.error is not None or outcome.capability is None:
        print(f"compile failed: {outcome.error}", file=sys.stderr)
        return EXIT_CODES["failure"]

    capability = outcome.capability
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{capability.capability_id}-{capability.version}.json"
    content = capability.model_dump_json(indent=2) + "\n"
    if path.exists() and not overwrite:
        if path.read_text() == content:
            print(f"{path} (unchanged: identical to the file already there)")
            return EXIT_CODES["success"]
        print(
            f"refusing to replace {path}: it already exists and holds a different capability. "
            "Bump the version, write somewhere else with --out, or pass --overwrite.",
            file=sys.stderr,
        )
        return 1
    path.write_text(content)
    print(path)
    return EXIT_CODES["success"]


def cmd_record(args: argparse.Namespace) -> int:
    try:
        policy = load_policy_config(args.policy)
    except PolicyConfigError as exc:
        print(f"policy: {exc}", file=sys.stderr)
        return EXIT_CODES["failure"]

    transcript = DiscoveryTranscript.model_validate_json(
        Path(args.transcript).read_text()
    )
    return _save_capability(
        compile_capability(transcript, policy), Path(args.out), overwrite=args.overwrite
    )


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
    run_id = new_run_id()
    writer = EvidenceWriter(
        run_id,
        redactor,
        root=Path(args.evidence_dir),
        meta=RunMeta.start(run_id, "discovery", policy_path=args.config, model=args.model),
    )
    surface = WebSurface(policy, PolicyGate(policy), headless=not args.headed)

    try:
        outcome = run_discovery(
            goal=args.goal,
            surface=surface,
            client=_client(args),
            evidence=lambda: writer.ref,
            model="scripted" if args.dry_run else args.model,
            surface_descriptor=_descriptor(args.target),
            target=args.target,
            limits=DiscoveryLimits(max_steps=args.max_steps, wall_clock_s=args.timeout),
            on_observation=lambda obs: (
                writer.screenshot(obs.screenshot_png) if obs.screenshot_png else None
            ),
        )
        # Written before the surface closes, because a post mortem of a page that is already
        # gone is three empty files. Same function replay uses, so the directories match.
        write_failure_artifacts(surface, writer, outcome.result)
    finally:
        surface.close()

    writer.write_transcript(outcome.transcript)
    writer.write_result(outcome.result)

    print(writer.directory)

    if args.record and outcome.result.kind == "success":
        # Compiling is a separate concern from running, so a compile failure is reported and
        # does not rewrite the run's own result: the discovery run did succeed.
        print("compiling the transcript into a capability:")
        _save_capability(compile_capability(outcome.transcript, policy), Path("capabilities"))

    return EXIT_CODES[outcome.result.kind]


def cmd_replay(args: argparse.Namespace) -> int:
    try:
        policy = load_policy_config(args.config)
    except PolicyConfigError as exc:
        print(f"policy: {exc}", file=sys.stderr)
        return EXIT_CODES["failure"]

    capability = Capability.model_validate_json(Path(args.capability).read_text())
    params = json.loads(args.params)

    redactor = Redactor({f"redacted_{i}": v for i, v in enumerate(args.redact)})
    run_id = new_run_id()
    writer = EvidenceWriter(
        run_id,
        redactor,
        root=Path(args.evidence_dir),
        meta=RunMeta.start(
            run_id,
            "replay",
            policy_path=args.config,
            capability_id=capability.capability_id,
            capability_version=capability.version,
            params_redacted=describe_params(capability, params),
            allow_draft=args.allow_draft,
        ),
    )
    surface = WebSurface(policy, PolicyGate(policy), headless=not args.headed)
    # A lease path is what turns a stopping condition into a handoff. Without one the surface
    # keeps its own in-process lease, so invariant 10 still holds and nothing waits.
    session = Session(
        surface,
        session_id=writer.run_id,
        lease_path=args.lease_path,
        interventions_dir=args.interventions_dir,
        evidence_sink=writer,
        deadline_seconds=args.intervention_timeout,
        redactor=redactor,
    )
    if args.lease_path:
        print(f"escalations will appear in {args.interventions_dir}, lease at {args.lease_path}")
    try:
        result = replay(
            capability,
            params,
            surface,
            policy,
            evidence=lambda: writer.ref,
            sink=writer,
            allow_draft=args.allow_draft,
            session=session if args.lease_path else None,
        )
    finally:
        surface.close()

    writer.write_result(result)
    print(writer.directory)
    return EXIT_CODES[result.kind]


def cmd_operator(args: argparse.Namespace) -> int:
    """Serve the console. Blocks until interrupted; there is no result to return."""
    lease_path = args.lease_path or str(Path(args.interventions_dir) / "lease.json")
    print(f"operator console on http://127.0.0.1:{args.port}")
    print(f"  interventions: {args.interventions_dir}")
    print(f"  lease:         {lease_path}")
    serve(args.port, args.interventions_dir, lease_path)
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    """Read-only. Exits 1 on an unknown id or an invalid artifact: no run exists to classify."""
    try:
        entries = catalog.load(args.dir)
        if args.catalog_command == "list":
            if args.json:
                print(json.dumps([catalog.summary(e) for e in entries], indent=2))
            else:
                print(catalog.render_list(entries))
            return 0
        spec = catalog.contract(catalog.find(entries, args.capability_id, args.version))
    except (catalog.UnknownCapability, catalog.InvalidCapability) as exc:
        print(f"catalog: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(spec, indent=2) if args.json else catalog.render_contract(spec))
    return 0


def main(argv: list[str] | None = None) -> int:
    # The one and only place .env is touched.
    load_dotenv()
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        return cmd_discover(args)
    if args.command == "record":
        return cmd_record(args)
    if args.command == "replay":
        return cmd_replay(args)
    if args.command == "operator":
        return cmd_operator(args)
    if args.command == "catalog":
        return cmd_catalog(args)
    return EXIT_CODES["failure"]


if __name__ == "__main__":
    raise SystemExit(main())
