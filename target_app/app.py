"""Stand-in for a legacy back-office banking application.

Exists only to give the automation a realistic surface to drive. Not a deliverable
of the project and deliberately capped in size. See target_app/README.md for routes,
seed members, and how to arm each fault.

Two classes of exceptional state live here and they are kept apart on purpose.
Not found, permission denied, and validation failure are REAL: they fall out of the
seed data and the submitted input. The four faults on /dev/faults are SIMULATED
runtime conditions, armed by hand, firing once each.
"""
from __future__ import annotations

import os
import time
from typing import Any

from flask import Flask, Response, redirect, render_template, request, session, url_for
from werkzeug.wrappers.response import Response as WerkzeugResponse

from seed import MEMBERS, VARIANTS

SLOW_SECONDS = 6
FAULTS = ("interstitial", "session_expired", "slow", "server_error")

# Paths that never fire a fault: the console that arms them, the screens a fault
# redirects to, and static assets. Without this you could not reach the console
# to disarm, and the interstitial would loop.
SKIP_FAULT_PREFIXES = ("/static/", "/dev/", "/maintenance", "/session-expired")

app = Flask(__name__)
# Local single-process dev app with no auth and no real data. Not a credential.
app.secret_key = "cedar-ridge-local-dev"

VARIANT_ID = os.environ.get("VARIANT", "a")
PORT = int(os.environ.get("PORT", "8080"))

# Sub-accounts opened during this process. No database by design.
OPENED: dict[str, list[dict[str, str]]] = {}
_issued = [0]


@app.context_processor
def inject_variant() -> dict[str, Any]:
    """Every template reads its user-visible strings from here, never inline."""
    return {"v": VARIANTS[VARIANT_ID]}


def _v() -> dict[str, Any]:
    return VARIANTS[VARIANT_ID]


def _title(key: str) -> str:
    return str(_v()["titles"][key])


def _requested_path() -> str:
    if request.query_string:
        return request.path + "?" + request.query_string.decode()
    return request.path


# --------------------------------------------------------------------------
# Simulated faults. Armed on /dev/faults, stored in the Flask session, fired
# once on the next GET, then disarmed. GET only: an interstitial or a session
# expiry fired on a POST would discard the submission and leave the Continue
# control with nothing to resume, which is noise rather than signal.
# --------------------------------------------------------------------------
@app.before_request
def fire_armed_fault() -> Response | WerkzeugResponse | tuple[str, int] | None:
    if request.method != "GET" or request.path.startswith(SKIP_FAULT_PREFIXES):
        return None

    fault = session.pop("armed_fault", None)
    if fault is None:
        return None

    if fault == "slow":
        time.sleep(SLOW_SECONDS)
        return None
    if fault == "server_error":
        return render_template("error_500.html", title=_title("error")), 500
    if fault == "session_expired":
        return redirect(url_for("session_expired"))
    if fault == "interstitial":
        session["interstitial_next"] = _requested_path()
        return redirect(url_for("maintenance"))
    return None


# --------------------------------------------------------------------------
# Flow 1: member lookup
# --------------------------------------------------------------------------
@app.get("/")
def home() -> str:
    return render_template("home.html", title=_title("home"))


@app.get("/search")
def search() -> str:
    return render_template("search.html", title=_title("search"), error=None)


@app.post("/search")
def search_submit() -> str | WerkzeugResponse:
    member_id = (request.form.get("ctl00_ContentPlaceHolder1_txtMemberId") or "").strip()
    if not member_id:
        return render_template(
            "search.html", title=_title("search"), error=_v()["errors"]["member_id_required"]
        )
    return redirect(url_for("member_detail", member_id=member_id))


def _guard(member_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return (member, screen). Exactly one is populated.

    A missing record and a restricted record are ordinary business outcomes, so both
    render a real screen with HTTP 200 rather than a 404 or a 403. The automation is
    meant to read the screen, not the status line, because that is what it will have
    to do against the real systems this stands in for.
    """
    member = MEMBERS.get(member_id)
    if member is None:
        return None, "member_not_found.html"
    if member["restricted"]:
        return None, "member_denied.html"
    return member, None


@app.get("/member/<member_id>")
def member_detail(member_id: str) -> str:
    member, screen = _guard(member_id)
    if screen is not None:
        key = "not_found" if screen == "member_not_found.html" else "denied"
        return render_template(screen, title=_title(key), member_id=member_id)
    return render_template("member_detail.html", title=_title("detail"), member=member)


@app.get("/member/<member_id>/panel")
def member_panel(member_id: str) -> str:
    """Main content of the member detail page. Loaded inside an iframe."""
    member, screen = _guard(member_id)
    if screen is not None:
        key = "not_found" if screen == "member_not_found.html" else "denied"
        return render_template(screen, title=_title(key), member_id=member_id)
    assert member is not None
    deposits = list(member["deposit_accounts"]) + OPENED.get(member_id, [])
    return render_template(
        "member_panel.html", title=_title("detail"), member=member, deposits=deposits
    )


@app.get("/member/<member_id>/loan-servicing")
def loan_servicing(member_id: str) -> str:
    """Where the Loan Accounts Select lands.

    Deliberately NOT the sub-account form. The two Select buttons carry identical
    accessible names, so tier 3 is the only strategy that can tell them apart, and
    that claim is only testable if resolving the wrong one goes somewhere visibly
    different. This screen is that difference.
    """
    member, screen = _guard(member_id)
    if screen is not None:
        key = "not_found" if screen == "member_not_found.html" else "denied"
        return render_template(screen, title=_title(key), member_id=member_id)
    return render_template(
        "loan_servicing.html", title=_title("loan_servicing"), member=member
    )


# --------------------------------------------------------------------------
# Flow 2: open sub-account. The confirm step is the irreversible one.
# --------------------------------------------------------------------------
def _read_form() -> dict[str, str]:
    get = request.form.get
    return {
        "account_type": (get("ctl00_ContentPlaceHolder1_ddlAccountType") or "").strip(),
        "nickname": (get("ctl00_ContentPlaceHolder1_txtNickname") or "").strip(),
        "initial_deposit": (get("ctl00_ContentPlaceHolder1_txtInitialDeposit") or "").strip(),
        "statement": (get("ctl00_ContentPlaceHolder1_ddlStatement") or "").strip(),
    }


def _validate(form: dict[str, str]) -> dict[str, str]:
    """Real server-side validation. Returns field name -> message."""
    errors: dict[str, str] = {}
    messages = _v()["errors"]

    if form["account_type"] not in _v()["account_types"]:
        errors["account_type"] = messages["account_type_required"]
    if not form["nickname"]:
        errors["nickname"] = messages["nickname_required"]

    raw = form["initial_deposit"]
    if not raw:
        errors["initial_deposit"] = messages["deposit_required"]
    else:
        try:
            amount = float(raw.replace(",", ""))
        except ValueError:
            errors["initial_deposit"] = messages["deposit_numeric"]
        else:
            if amount <= 0:
                errors["initial_deposit"] = messages["deposit_positive"]
    return errors


@app.get("/member/<member_id>/subaccount")
def subaccount_form(member_id: str) -> str:
    member, screen = _guard(member_id)
    if screen is not None:
        key = "not_found" if screen == "member_not_found.html" else "denied"
        return render_template(screen, title=_title(key), member_id=member_id)
    return render_template(
        "subaccount_form.html",
        title=_title("subaccount_form"),
        member=member,
        form={},
        errors={},
    )


@app.post("/member/<member_id>/subaccount")
def subaccount_submit(member_id: str) -> str:
    member, screen = _guard(member_id)
    if screen is not None:
        key = "not_found" if screen == "member_not_found.html" else "denied"
        return render_template(screen, title=_title(key), member_id=member_id)

    form = _read_form()
    errors = _validate(form)
    if errors:
        return render_template(
            "subaccount_form.html",
            title=_title("subaccount_form"),
            member=member,
            form=form,
            errors=errors,
        )
    return render_template(
        "subaccount_review.html",
        title=_title("subaccount_review"),
        member=member,
        form=form,
    )


@app.post("/member/<member_id>/subaccount/confirm")
def subaccount_confirm(member_id: str) -> str:
    member, screen = _guard(member_id)
    if screen is not None:
        key = "not_found" if screen == "member_not_found.html" else "denied"
        return render_template(screen, title=_title(key), member_id=member_id)

    form = _read_form()
    # Re-validate rather than trusting the values that came back from the review screen.
    errors = _validate(form)
    if errors:
        return render_template(
            "subaccount_form.html",
            title=_title("subaccount_form"),
            member=member,
            form=form,
            errors=errors,
        )

    _issued[0] += 1
    number = "90" + member_id[-4:] + str(_issued[0]).zfill(3)
    OPENED.setdefault(member_id, []).append(
        {
            "type": form["account_type"],
            "masked": "xxxxxx" + number[-4:],
            "balance": form["initial_deposit"],
        }
    )
    return render_template(
        "subaccount_confirm.html",
        title=_title("subaccount_confirm"),
        member=member,
        form=form,
        number=number,
    )


# --------------------------------------------------------------------------
# Fault console and the screens faults land on
# --------------------------------------------------------------------------
@app.get("/dev/faults")
def dev_faults() -> str:
    return render_template(
        "dev_faults.html",
        title=_title("faults"),
        faults=FAULTS,
        armed=session.get("armed_fault"),
    )


@app.post("/dev/faults")
def dev_faults_arm() -> WerkzeugResponse:
    name = request.form.get("fault") or ""
    if name in FAULTS:
        session["armed_fault"] = name
    elif name == "clear":
        session.pop("armed_fault", None)
    return redirect(url_for("dev_faults"))


@app.get("/maintenance")
def maintenance() -> str:
    return render_template("maintenance.html", title=_title("maintenance"))


@app.get("/maintenance/continue")
def maintenance_continue() -> WerkzeugResponse:
    return redirect(session.pop("interstitial_next", None) or url_for("home"))


@app.get("/session-expired")
def session_expired() -> str:
    return render_template("session_expired.html", title=_title("session_expired"))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, debug=False)
