"""The minimal operator console.

Deliberately minimal, and the cut is the design rather than a shortcut. There is no live
session streaming, no co-browsing, no VNC. The headed Chromium window that automation is
already driving IS the live session: the operator works in that window with their own mouse
and keyboard, and this console exists only to move the lease and to record what they decided.
See DECISIONS.md 0030.

Everything here talks to two directories and nothing else. It holds no browser handle, opens
no page, and shares no memory with the run. That is what makes the transfer model real rather
than a demo: the two sides agree on a file, and either can be restarted.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from flask import Flask, Response, abort, redirect, render_template_string, request, send_file, url_for

from src.escalation.intervention import (
    InterventionRequest,
    refuse_unsafe_outcome,
    InterventionResolution,
    InterventionStore,
)
from src.escalation.lease import IllegalTransition, LeaseStore
from src.models.common import LeaseState, ResolutionOutcome

STYLE: Final[str] = """
body { font: 13px -apple-system, Segoe UI, sans-serif; margin: 2em; max-width: 60em; }
table { border-collapse: collapse; margin: 1em 0; }
td, th { border: 1px solid #ccc; padding: 4px 8px; text-align: left; vertical-align: top; }
th { background: #eee; }
pre { background: #f6f6f6; padding: 8px; overflow-x: auto; max-height: 22em; }
.risk-risky_irreversible { color: #a00; font-weight: bold; }
.err { background: #fee; border: 1px solid #a00; padding: 8px; margin: 1em 0; }
img { border: 1px solid #ccc; max-width: 100%; }
form { display: inline; }
"""

INDEX: Final[str] = """
<!doctype html><title>Interventions</title><style>{{ style }}</style>
<h1>Open interventions</h1>
{% if not requests %}<p>Nothing waiting.</p>{% endif %}
{% for r in requests %}
  <h2><a href="{{ url_for('detail', intervention_id=r.id) }}">{{ r.id }}</a></h2>
  <table>
    <tr><th>reason</th><td>{{ r.reason.value }}</td></tr>
    <tr><th>capability</th><td>{{ r.capability_id }} {{ r.capability_version }}</td></tr>
    <tr><th>step</th><td>{{ r.step_index }}: {{ r.step_description }}</td></tr>
    <tr><th>risk</th><td class="risk-{{ r.risk.value }}">{{ r.risk.value }}</td></tr>
    <tr><th>why</th><td>{{ r.why }}</td></tr>
    <tr><th>deadline</th><td>{{ r.deadline_at }}</td></tr>
  </table>
  {% if r.screenshot_path %}
    <img src="{{ url_for('screenshot', intervention_id=r.id) }}" width="640">
  {% endif %}
{% endfor %}
"""

DETAIL: Final[str] = """
<!doctype html><title>{{ r.id }}</title><style>{{ style }}</style>
<p><a href="{{ url_for('index') }}">&larr; all interventions</a></p>
<h1>{{ r.id }}</h1>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
<table>
  <tr><th>lease state</th><td>{{ lease.state.value }} (held by {{ lease.holder.value }})</td></tr>
  <tr><th>reason</th><td>{{ r.reason.value }}</td></tr>
  <tr><th>why</th><td>{{ r.why }}</td></tr>
  <tr><th>capability</th><td>{{ r.capability_id }} {{ r.capability_version }}</td></tr>
  <tr><th>goal</th><td>{{ r.goal_text }}</td></tr>
  <tr><th>step</th><td>{{ r.step_index }}: {{ r.step_description }}</td></tr>
  <tr><th>risk</th><td class="risk-{{ r.risk.value }}">{{ r.risk.value }}</td></tr>
  <tr><th>url</th><td>{{ r.url }}</td></tr>
  <tr><th>deadline</th><td>{{ r.deadline_at }}</td></tr>
</table>

<h2>Parameters</h2>
<p>Names and sensitivities only. Values are never written to an intervention file.</p>
<table>
  <tr><th>name</th><th>sensitivity</th><th>required</th><th>supplied</th></tr>
  {% for p in r.params_redacted %}
  <tr><td>{{ p.name }}</td><td>{{ p.sensitivity.value }}</td>
      <td>{{ p.required }}</td><td>{{ p.supplied }}</td></tr>
  {% endfor %}
</table>

{% if r.screenshot_path %}<h2>Screen when it stopped</h2>
<img src="{{ url_for('screenshot', intervention_id=r.id) }}">{% endif %}

<h2>Accessibility snapshot</h2>
<pre>{{ r.aria_snapshot }}</pre>

{% if r.resolution %}
  <h2>Resolved</h2>
  <table>
    <tr><th>outcome</th><td>{{ r.resolution.outcome.value }}</td></tr>
    <tr><th>note</th><td>{{ r.resolution.operator_note }}</td></tr>
    <tr><th>at</th><td>{{ r.resolution.resolved_at }}</td></tr>
  </table>
  <h3>What the human did</h3>
  <table>
    <tr><th>kind</th><th>tag</th><th>text or field</th><th>url</th></tr>
    {% for a in r.resolution.human_actions %}
    <tr><td>{{ a.kind }}</td><td>{{ a.tag }}</td>
        <td>{{ a.text or a.field }}</td><td>{{ a.url }}</td></tr>
    {% endfor %}
  </table>
{% else %}
  <h2>Take control</h2>
  <p>The live session is the browser window automation opened. Work in that window.</p>
  <form method="post" action="{{ url_for('take', intervention_id=r.id) }}">
    <input type="submit" value="Take control">
  </form>
  <form method="post" action="{{ url_for('abort_run', intervention_id=r.id) }}">
    <input type="submit" value="Abort the run">
  </form>

  <h2>Return control</h2>
  <form method="post" action="{{ url_for('return_control', intervention_id=r.id) }}">
    <select name="outcome">
      <option value="approved">approved: automation performs the step</option>
      <option value="completed_manually">completed_manually: I did it, skip the step</option>
      {% if r.risk.value != "risky_irreversible" %}
      <option value="retry_step">retry_step: automation tries the step again</option>
      {% endif %}
      <option value="aborted">aborted: stop the run</option>
    </select>
    <input type="text" name="note" size="50" placeholder="what you did and why">
    <input type="submit" value="Return control">
  </form>
  {% if r.risk.value == "risky_irreversible" %}
  <p><b>retry_step is not offered.</b> This step cannot be undone, and you may already have
     performed it. Re-performing it would do it twice.</p>
  {% endif %}
{% endif %}
"""


def create_app(
    *, interventions_dir: Path | str = "interventions", lease_path: Path | str = "interventions/lease.json"
) -> Flask:
    app = Flask(__name__)
    store = InterventionStore(interventions_dir)
    leases = LeaseStore(lease_path)

    def _load(intervention_id: str) -> InterventionRequest:
        try:
            return store.read(intervention_id)
        except (OSError, ValueError):
            abort(404)

    def _render(request_obj: InterventionRequest, error: str | None = None) -> str:
        lease = leases.read() if leases.exists() else None
        return render_template_string(
            DETAIL, r=request_obj, lease=lease, style=STYLE, error=error
        )

    @app.get("/")
    def index() -> str:
        return render_template_string(INDEX, requests=store.open_requests(), style=STYLE)

    @app.get("/i/<intervention_id>")
    def detail(intervention_id: str) -> str:
        return _render(_load(intervention_id))

    @app.get("/i/<intervention_id>/screenshot")
    def screenshot(intervention_id: str) -> Any:
        found = _load(intervention_id)
        if not found.screenshot_path or not Path(found.screenshot_path).exists():
            abort(404)
        return send_file(Path(found.screenshot_path).resolve(), mimetype="image/png")

    @app.post("/i/<intervention_id>/take")
    def take(intervention_id: str) -> Any:
        found = _load(intervention_id)
        try:
            leases.transition(LeaseState.HUMAN_CONTROL, intervention_id=intervention_id)
        except IllegalTransition as exc:
            return _render(found, str(exc)), 409
        return redirect(url_for("detail", intervention_id=intervention_id))

    @app.post("/i/<intervention_id>/return")
    def return_control(intervention_id: str) -> Any:
        found = _load(intervention_id)
        raw = (request.form.get("outcome") or "").strip()
        try:
            outcome = ResolutionOutcome(raw)
        except ValueError:
            return _render(found, f"unknown outcome {raw!r}"), 400

        refusal = refuse_unsafe_outcome(found.risk, outcome)
        if refusal is not None:
            return _render(found, refusal), 400

        store.resolve(
            intervention_id,
            InterventionResolution(
                outcome=outcome,
                operator_note=(request.form.get("note") or "").strip(),
                resolved_at=datetime.now(UTC),
            ),
        )
        target = LeaseState.CLOSED if outcome is ResolutionOutcome.ABORTED else LeaseState.RESUMING
        try:
            leases.transition(target, intervention_id=intervention_id)
        except IllegalTransition as exc:
            return _render(found, str(exc)), 409
        return redirect(url_for("detail", intervention_id=intervention_id))

    @app.post("/i/<intervention_id>/abort")
    def abort_run(intervention_id: str) -> Any:
        found = _load(intervention_id)
        store.resolve(
            intervention_id,
            InterventionResolution(
                outcome=ResolutionOutcome.ABORTED,
                operator_note=(request.form.get("note") or "aborted from the console").strip(),
                resolved_at=datetime.now(UTC),
            ),
        )
        leases.transition(LeaseState.CLOSED, intervention_id=intervention_id)
        return redirect(url_for("detail", intervention_id=intervention_id))

    @app.errorhandler(404)
    def missing(_: Any) -> tuple[str, int]:
        return "<!doctype html><p>No such intervention.</p>", 404

    return app


def serve(port: int, interventions_dir: Path | str, lease_path: Path | str) -> None:
    create_app(interventions_dir=interventions_dir, lease_path=lease_path).run(
        port=port, debug=False
    )
