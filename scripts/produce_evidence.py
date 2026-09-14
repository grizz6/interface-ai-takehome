"""Produce the sample runs the plain CLI cannot produce by itself.

The fault runs need a fault turned on first. The target app keeps faults in the Flask session
cookie, so a fault turned on with curl belongs to curl's session, and a separate replay process
opens a new browser that never sees it. So the fault has to be turned on in the same browser
the run then uses.

The handoff run needs a person's click. The operator page runs as its own process and the lease
moves over real HTTP, but Playwright's sync API ties a page to the thread that created it, so
the click has to come from this script. See DECISIONS.md 0035.

Everything goes through the normal code and the same EvidenceWriter as the CLI, so the folders
look the same. Nothing writes files by hand or edits a result.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# Run by path rather than as a module, so the repo root is not on sys.path by default.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.escalation.session import Session  # noqa: E402
from src.evidence.meta import RunMeta  # noqa: E402
from src.evidence.writer import EvidenceWriter, new_run_id  # noqa: E402
from src.models.capability import Capability, describe_params  # noqa: E402
from src.models.common import LeaseState  # noqa: E402
from src.policy.gate import PolicyGate  # noqa: E402
from src.policy.loading import load_policy_config  # noqa: E402
from src.policy.redaction import Redactor  # noqa: E402
from src.replay.engine import replay  # noqa: E402
from src.surface.web import WebSurface  # noqa: E402

POLICY_PATH = "config/policy.json"
LOOKUP = "capabilities/lookup-member-savings-balance-1.2.0.json"
SUBACCOUNT = "capabilities/open-member-subaccount-1.0.0.json"


def arm(page: Any, base_url: str, fault: str) -> None:
    """Turn on a fault through the fault page, in this browser session.

    Uses the raw page, not the surface, because the policy blocks /dev/. That rule is for the
    automation being tested, not for this setup step.
    """
    page.goto(f"{base_url}/dev/faults")
    page.get_by_role("button", name=f"Arm {fault}", exact=True).click()
    print(f"armed: {fault}")


def build(capability_path: str, params: dict[str, str], redact: list[str]) -> tuple[Any, ...]:
    policy = load_policy_config(POLICY_PATH)
    capability = Capability.model_validate_json(Path(capability_path).read_text())
    run_id = new_run_id()
    writer = EvidenceWriter(
        run_id,
        Redactor({f"redacted_{i}": v for i, v in enumerate(redact)}),
        root=Path("evidence"),
        meta=RunMeta.start(
            run_id, "replay",
            policy_path=POLICY_PATH,
            capability_id=capability.capability_id,
            capability_version=capability.version,
            params_redacted=describe_params(capability, params),
            # Every run this script produces replays a draft, and says so.
            allow_draft=True,
        ),
    )
    surface = WebSurface(policy, PolicyGate(policy), headless=True)
    return policy, capability, writer, surface


def run_with_fault(fault: str, base_url: str, capability_path: str = LOOKUP) -> int:
    params = {"member_id": "100001"}
    policy, capability, writer, surface = build(capability_path, params, ["100001"])
    try:
        arm(surface.page, base_url, fault)
        result = replay(
            capability, params, surface, policy,
            evidence=lambda: writer.ref, sink=writer, allow_draft=True,
        )
    finally:
        surface.close()
    writer.write_result(result)
    print(writer.directory)
    return _report(result)


class ConsoleOperator(Session):
    """Waits for someone to take control on the operator page, then clicks Confirm for them.

    The lease really moves between this process and the operator page. Only the click itself is
    scripted, because it can only come from this thread. Everything on the operator page is a
    real HTTP request.
    """

    def await_return(self, intervention_id: str, **kwargs: Any) -> Any:
        print(f"paused. intervention: {intervention_id}")
        print("waiting for an operator to take control through the console")
        deadline = time.monotonic() + 300
        while self.lease.read().state is not LeaseState.HUMAN_CONTROL:
            if time.monotonic() > deadline:
                print("nobody took control", file=sys.stderr)
                break
            time.sleep(0.3)

        if self.lease.read().state is LeaseState.HUMAN_CONTROL:
            print("operator has control. performing the confirm by hand.")
            page = self.surface.page
            page.get_by_role("button", name="Confirm", exact=True).click()
            print(f"confirm clicked by hand, page is now {page.url}")
            print("waiting for control to be returned")
        return super().await_return(intervention_id, poll_interval=0.3, **kwargs)


def run_handoff(lease_path: str, interventions_dir: str) -> int:
    params = {
        "member_id": "100001", "account_type": "Savings",
        "nickname": "Vacation", "initial_deposit": "250.00",
    }
    policy, capability, writer, surface = build(SUBACCOUNT, params, ["100001"])
    session = ConsoleOperator(
        surface,
        session_id=writer.run_id,
        lease_path=lease_path,
        interventions_dir=interventions_dir,
        evidence_sink=writer,
        redactor=Redactor({"redacted_0": "100001"}),
        deadline_seconds=300,
    )
    try:
        result = replay(
            capability, params, surface, policy,
            evidence=lambda: writer.ref, sink=writer, allow_draft=True, session=session,
        )
    finally:
        surface.close()
    writer.write_result(result)
    print(writer.directory)
    return _report(result)


def _report(result: Any) -> int:
    from src.models.results import EXIT_CODES

    payload = json.loads(result.model_dump_json())
    print("kind:", payload["kind"])
    for field in ("outputs", "code", "recoveries_applied", "error_class", "step_index"):
        if field in payload:
            print(f"  {field}: {payload[field]}")
    return EXIT_CODES[result.kind]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="what", required=True)

    fault = sub.add_parser("fault", help="Arm a fault, then replay the lookup capability.")
    fault.add_argument(
        "name",
        choices=[
            "interstitial", "slow", "server_error", "session_expired",
            "confirm_dialog", "alert_dialog",
        ],
    )
    fault.add_argument("--target", default="http://localhost:8080")
    fault.add_argument(
        "--capability", default=LOOKUP,
        help="Which lookup capability to replay. Runs 04 and 05 used the default.",
    )

    handoff = sub.add_parser("handoff", help="Replay the sub-account capability and pause.")
    handoff.add_argument("--lease-path", default="interventions/lease.json")
    handoff.add_argument("--interventions-dir", default="interventions")

    args = parser.parse_args()
    if args.what == "fault":
        return run_with_fault(args.name, args.target, args.capability)
    return run_handoff(args.lease_path, args.interventions_dir)


if __name__ == "__main__":
    raise SystemExit(main())
