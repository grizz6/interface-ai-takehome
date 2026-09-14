"""Builders for a valid Capability, so each test can invalidate exactly one thing.

The fixture is modelled on the real flow in target_app: deep link to a member, pick the
Deposit Accounts Select, fill the sub-account form, confirm. It uses three locator tiers and
has one risky_irreversible step, so the round trip test covers the parts of the schema that are
easy to get wrong.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.models import (
    ActionType,
    Assertion,
    BusinessOutcomeSpec,
    Capability,
    ContainerOrdinalLocator,
    ContainerRef,
    ExtractionSpec,
    InsertedStep,
    LabelRelationLocator,
    LocatorBundle,
    OutputSpec,
    ParamBinding,
    ParamSpec,
    Provenance,
    RecoveryAction,
    RecoveryRule,
    RiskClass,
    RoleNameLocator,
    Sensitivity,
    Signal,
    SignalKind,
    Step,
    StepPatch,
    SurfaceDescriptor,
    SurfaceFingerprint,
    SurfaceKind,
    ValueType,
    VariantOverride,
    WaitSpec,
)

FRAME = ["maincontent"]


def text_signal(text: str) -> Signal:
    return Signal(kind=SignalKind.TEXT_PRESENT, text=text)


def role_name_bundle(role: str, name: str, frame: list[str] | None = None) -> LocatorBundle:
    return LocatorBundle(
        primary=RoleNameLocator(role=role, name=name),
        frame_path=list(frame or []),
        recorded_accessible_name=name,
    )


def tier2_bundle() -> LocatorBundle:
    """The Nickname field: no accessible name of its own, found by the cell beside it."""
    return LocatorBundle(
        primary=LabelRelationLocator(
            label_text="Nickname", relation="cell_to_left", role="textbox"
        ),
        notes="Field carries no accessible name. Adjacent cell text is the only handle.",
    )


def tier3_bundle() -> LocatorBundle:
    """The Deposit Accounts Select: name collides with the Loan Accounts one."""
    return LocatorBundle(
        primary=ContainerOrdinalLocator(
            container=ContainerRef(heading_text="Deposit Accounts", role="table"),
            role="button",
            ordinal=0,
            name="Select",
        ),
        frame_path=list(FRAME),
        recorded_accessible_name="Select",
        notes="Two controls named Select. Container scope is the only way to separate them.",
    )


def wait_for(text: str) -> WaitSpec:
    return WaitSpec(condition="signal", signal=text_signal(text))


def valid_steps() -> list[Step]:
    return [
        Step(
            index=0,
            action=ActionType.NAVIGATE,
            description="Open the member record directly.",
            url="/member/{member_id}",
            risk=RiskClass.SAFE_REVERSIBLE,
            wait=wait_for("Member Detail"),
        ),
        Step(
            index=1,
            action=ActionType.CLICK,
            description="Select the deposit side, not the loan side.",
            target=tier3_bundle(),
            risk=RiskClass.SAFE_REVERSIBLE,
            wait=wait_for("Open Sub-Account"),
        ),
        Step(
            index=2,
            action=ActionType.TYPE,
            description="Name the new sub-account.",
            target=tier2_bundle(),
            value=ParamBinding(param="nickname"),
            risk=RiskClass.SAFE_REVERSIBLE,
            wait=WaitSpec(condition="fixed", timeout_ms=500),
        ),
        Step(
            index=3,
            action=ActionType.TYPE,
            description="Enter the opening deposit.",
            target=role_name_bundle("textbox", "Initial Deposit"),
            value=ParamBinding(param="initial_deposit"),
            risk=RiskClass.SAFE_REVERSIBLE,
            wait=WaitSpec(condition="fixed", timeout_ms=500),
        ),
        Step(
            index=4,
            action=ActionType.CLICK,
            description="Submit the request and reach the review screen.",
            target=role_name_bundle("button", "Submit Request"),
            risk=RiskClass.SAFE_REVERSIBLE,
            wait=wait_for("Review Sub-Account Request"),
        ),
        Step(
            index=5,
            action=ActionType.CLICK,
            description="Confirm. This opens the account and cannot be undone.",
            target=role_name_bundle("button", "Confirm"),
            risk=RiskClass.RISKY_IRREVERSIBLE,
            wait=wait_for("Sub-Account Opened"),
            postcondition=Assertion(
                signal=text_signal("Sub-Account Opened"),
                description="The confirmation screen proves the account was actually opened.",
            ),
        ),
    ]


def valid_inputs() -> list[ParamSpec]:
    return [
        ParamSpec(
            name="member_id",
            type=ValueType.STRING,
            description="Member record to service.",
            sensitivity=Sensitivity.PII,
        ),
        ParamSpec(
            name="nickname",
            type=ValueType.STRING,
            description="Label for the new sub-account.",
            example="Vacation",
        ),
        ParamSpec(
            name="initial_deposit",
            type=ValueType.CURRENCY,
            description="Opening deposit, must be greater than zero.",
            example="250.00",
        ),
    ]


def valid_outputs() -> list[OutputSpec]:
    return [
        OutputSpec(
            name="new_account_number",
            type=ValueType.STRING,
            description="The account number issued by the confirmation screen.",
            extraction=ExtractionSpec(
                locator=role_name_bundle("cell", "New Account Number"),
                source="text",
                parse="raw",
            ),
        )
    ]


def valid_outcomes() -> list[BusinessOutcomeSpec]:
    return [
        BusinessOutcomeSpec(
            code="member_not_found",
            description="No member record matches the supplied id.",
            detect=text_signal("No member record matches"),
        ),
        BusinessOutcomeSpec(
            code="permission_denied",
            description="The member record is restricted.",
            detect=text_signal("do not have permission"),
        ),
    ]


def valid_capability(**overrides: Any) -> Capability:
    data: dict[str, Any] = {
        "capability_id": "open-member-subaccount",
        "version": "1.0.0",
        "name": "Open a member sub-account",
        "description": "Opens a sub-account against a member deposit relationship.",
        "surface": SurfaceDescriptor(
            kind=SurfaceKind.LEGACY_WEB,
            app_id="cedar-ridge-console",
            variant_id="a",
            base_url="http://localhost:8080",
            entry_path="/member/{member_id}",
            fingerprint=SurfaceFingerprint(
                title="Cedar Ridge Credit Union - Member Detail",
                brand_text="Cedar Ridge Credit Union",
                landmark_signals=[
                    text_signal("Member Services Console"),
                    Signal(
                        kind=SignalKind.ARIA_MATCHES,
                        aria_template='- heading "Member Detail"',
                    ),
                ],
                aria_template='- banner:\n  - text "Cedar Ridge Credit Union"',
                captured_at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
            ),
        ),
        "inputs": valid_inputs(),
        "outputs": valid_outputs(),
        "steps": valid_steps(),
        "checkpoint": Assertion(
            signal=text_signal("Sub-Account Opened"),
            description="The flow only succeeded if the confirmation screen is showing.",
        ),
        "known_outcomes": valid_outcomes(),
        "recoveries": [
            RecoveryRule(
                name="dismiss_maintenance_notice",
                detect=text_signal("scheduled maintenance"),
                action=RecoveryAction.DISMISS,
                action_target=role_name_bundle("link", "Continue"),
            )
        ],
        "overrides": {
            "b": VariantOverride(
                variant_id="b",
                description="Tenant b renames the confirm control and adds a second step.",
                step_overrides={
                    5: StepPatch(target=role_name_bundle("button", "Authorize"))
                },
                inserted_steps=[
                    InsertedStep(
                        after_index=5,
                        step=Step(
                            index=6,
                            action=ActionType.CLICK,
                            description="Tenant b requires a second acknowledgement.",
                            target=role_name_bundle("button", "Acknowledge"),
                            risk=RiskClass.SAFE_REVERSIBLE,
                            wait=wait_for("Sub-Account Opened"),
                        ),
                    )
                ],
                output_overrides={
                    "new_account_number": ExtractionSpec(
                        locator=role_name_bundle("cell", "Account Number Issued"),
                        source="text",
                        parse="raw",
                    )
                },
                outcome_overrides={"member_not_found": text_signal("Member not on file")},
            )
        },
        "provenance": Provenance(
            discovered_by_model="gemini-3-flash-preview",
            discovery_run_id="run-20260910-0001",
            recorded_at=datetime(2026, 9, 10, 12, 5, tzinfo=UTC),
            goal_text="Open a savings sub-account for member 100001 and reach confirmation.",
            raw_step_count=19,
            redaction_policy_version="1.0",
        ),
    }
    data.update(overrides)
    return Capability(**data)


def assert_error(exc: Any, fragment: str) -> None:
    """Assert the SPECIFIC validation error, not merely that something raised."""
    message = str(exc.value)
    assert fragment in message, f"expected {fragment!r} in validation error:\n{message}"


# ---------------------------------------------------------------------------
# Integration fixtures: the real target app on a free port, a real Chromium.
# Session scoped because a surface keeps one browser context for its life, and
# launching a browser for every test would make the suite slow.
# ---------------------------------------------------------------------------
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.models.common import ActionType
from src.models.policy import PolicyConfig
from src.policy.gate import PolicyGate


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


@pytest.fixture(scope="session")
def live_app() -> Iterator[str]:
    port = _free_port()
    process = subprocess.Popen(
        [sys.executable, "target_app/app.py"],
        env={**os.environ, "PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 25
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            break
        except OSError:
            time.sleep(0.2)
    else:
        process.kill()
        pytest.fail("target app did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture(scope="session")
def policy_config() -> PolicyConfig:
    """Realistic: everything under the app is allowed except the developer routes."""
    return PolicyConfig(
        allowed_hosts=["127.0.0.1", "localhost"],
        allowed_path_patterns=[r"^/"],
        denied_path_patterns=[r"^/dev/"],
        allowed_actions=[
            ActionType.NAVIGATE,
            ActionType.CLICK,
            ActionType.TYPE,
            ActionType.SELECT,
            ActionType.WAIT_FOR,
        ],
        # Matches config/policy.json, which is what actually ships. A fixture that only
        # flagged made the surface's gate disagree with the engine's, and an approval test
        # passed because of the disagreement rather than because approval worked.
        risky_action_policy="require_approval",
        risky_control_names=["Confirm"],
    )


@pytest.fixture(scope="session")
def surface(policy_config: PolicyConfig) -> Iterator[object]:
    from src.surface.web import WebSurface

    web = WebSurface(policy_config, PolicyGate(policy_config), headless=True)
    try:
        yield web
    finally:
        web.close()


@pytest.fixture(scope="session")
def evidence_dirs_at_session_start() -> set[str]:
    """Run directories present in evidence/ before any test ran.

    Snapshotted rather than asserting the directory is empty, because real discovery runs
    write here too and a developer's run is not a test's litter.
    """
    root = Path("evidence")
    return {p.name for p in root.glob("*") if p.is_dir()} if root.exists() else set()

