# computer-use-automation

An LLM works out how to complete a task inside a real UI that has no API. The successful run is
saved as a typed, versioned capability, and from then on that capability replays
deterministically with no model in the decision loop.

Built for the interface.ai take-home. This README documents what runs today. Phase 10 finishes
it; the pending sections below say so explicitly rather than pretending.

## Setup

### Prerequisites

- Python 3.11 or newer
- macOS or Linux
- A Gemini API key, and only for a real discovery run. Everything else in this repo, including
  the whole test suite, runs without one.

### Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/playwright install chromium
```

The editable install pulls the dependencies from `pyproject.toml`. The second command downloads
the browser Playwright drives, which is about 100MB and is not bundled with the package.

### Configuration

```bash
cp .env.example .env
```

Then put your key in `.env`:

```
GEMINI_API_KEY=your-key-here
```

`.env` is gitignored and is read in exactly one place, `src/cli.py`. No code in this repo ever
references the key's value: the Gemini SDK reads it from the environment itself, so the key
travels from your shell to the SDK without passing through anything written here. There is a
test, `tests/test_secret_guard.py`, that walks every byte of written evidence looking for
credential shaped strings and for any value the environment holds under a name ending in
`_API_KEY`.

What the agent is permitted to do lives in `config/policy.json`: which hosts and paths it may
reach, which action types are allowed, and which control names are treated as irreversible. It
is validated when it loads, so a malformed allowlist is refused before a browser starts rather
than at the first blocked action.

### Run the target application

```bash
make app
```

This serves the stand-in application on <http://localhost:8080>: a fictional credit union member
services console, deliberately built like a legacy system, with nested table layout, an iframe,
ASP.NET style element ids and no test IDs anywhere. `target_app/README.md` lists every route,
every seed member and what condition it demonstrates, and how to arm each simulated fault.

## Demo path

### 1. Discovery: let the model work it out

In one terminal:

```bash
make app
```

In another:

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001 and read the current savings balance" \
  --target http://localhost:8080/search
```

or the same thing through the Makefile, which carries that goal as its default:

```bash
make discover
```

The run prints its evidence directory on the last line of stdout and exits with a code that
names the result kind, so a caller can branch without parsing anything:

| Exit | Result | Meaning |
|---|---|---|
| 0 | `Success` | The goal was reached and the checkpoint held |
| 10 | `BusinessOutcome` | A legitimate answer, such as no such member |
| 20 | `NeedsHuman` | Stuck, ambiguous, or out of budget |
| 30 | `PolicyBlocked` | The guardrail refused the action |
| 40 | `Failure` | Something broke |

Evidence lands in `evidence/<run_id>/`:

```
run.jsonl         one event per line, as the run happened
transcript.json   the full transcript, including the dead ends
result.json       the result the caller received
screenshots/      one per observation
```

Useful flags:

| Flag | What it does |
|---|---|
| `--headed` | Show the browser instead of running headless |
| `--max-steps` | Step budget, default 25 |
| `--timeout` | Wall clock budget in seconds, default 300 |
| `--redact VALUE` | A value that must never appear in evidence. Repeatable |
| `--evidence-dir DIR` | Write run directories somewhere other than `evidence/` |
| `--dry-run FILE` | Replay scripted model turns instead of calling a model |

### Running the whole pipeline without a model

`--dry-run` replays a scripted list of model turns through the same code path a real model
uses. The browser, the policy gate, the locator machinery and the evidence writer all run for
real; only the model is replaced. Two scripts ship in `evidence-inputs/`.

**The happy path**, which reaches a verified finish and exits 0:

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001 and read their current savings balance" \
  --target http://localhost:8080 \
  --config config/policy.json \
  --dry-run evidence-inputs/happy-path.json
```

It navigates, clicks Member Lookup, types the member id, searches, and then calls `finish`.
The checkpoint is evaluated against the live page and the declared output is actually
extracted from it before success is accepted, so exit 0 here means the whole vertical slice
worked, not merely that the script ran to the end:

```json
{
  "kind": "success",
  "outputs": { "savings_balance": "4182.55" },
  "steps": [
    { "action": "navigate", "locator_strategy_used": null },
    { "action": "click", "description": "click 'Member Lookup'", "locator_strategy_used": "role_name" },
    { "action": "type",  "description": "type into 'Member ID'", "locator_strategy_used": "role_name" },
    { "action": "click", "description": "click 'Search'",        "locator_strategy_used": "role_name" }
  ]
}
```

**The failure path**, which exits 20:

```bash
.venv/bin/python -m src.cli discover \
  --goal "open the fault console" \
  --target http://localhost:8080 \
  --config config/policy.json \
  --dry-run evidence-inputs/failure-path.json
```

It tries a route the policy forbids, gets refused, is told the direction is closed rather
than crashing, and then stops when three consecutive observations come back identical. That
is the guardrail and the stall detector both doing their job.

This is the fastest way to see the system work, and it needs no API key.

### 2. Replay: no model in the loop

Replay a saved capability with parameters. Nothing in this path imports a model client, and
`tests/test_replay_isolation.py` proves it four different ways, including a control test that
fails if the proof stops being able to detect an import.

```bash
python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "100001"}' --allow-draft
```

That exits 0 and writes `savings_balance` to `evidence/<run>/result.json`. Two more, to see the
result contract rather than just the happy path:

```bash
python -m src.cli replay --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "999999"}' --allow-draft   # exit 10, member_not_found
python -m src.cli replay --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "100003"}' --allow-draft   # exit 10, member_restricted
```

Neither is a failure. A record that does not exist is an answer, and the exit code says so.

`--allow-draft` is needed because nothing in this repo promotes a capability from draft to
approved yet. See DECISIONS.md 0026.

### 3. Operator handoff

Escalation hands the live browser session to a person and takes it back. Run the console in one
terminal:

```bash
python -m src.cli operator --port 8090 --interventions-dir interventions
```

and a replay that can reach it in another. The capability below has a Confirm step marked
`risky_irreversible`, and `config/policy.json` sets `risky_action_policy` to `require_approval`,
so the run stops there and waits:

```bash
python -m src.cli replay \
  --capability capabilities/open-member-subaccount-1.0.0.json \
  --params '{"member_id":"100001","account_type":"Savings","nickname":"Vacation","initial_deposit":"250.00"}' \
  --allow-draft --headed \
  --lease-path interventions/lease.json --interventions-dir interventions
```

Open http://127.0.0.1:8090. The intervention shows which capability, which step, the risk, why
it stopped, a screenshot, the accessibility snapshot, and the parameter names. Take control,
work in the browser window the run opened, then return control with one of `approved`,
`completed_manually` or `aborted`.

**The operator console is deliberately minimal, and live session streaming was cut.** There is
no co-browsing, no VNC, no screencast. The headed Chromium window that automation opened is the
live session: the operator works in that window directly. What was cut is the pixel stream. What
is real is the transfer model itself, and that is the part worth checking:

- the run pauses and holds no lease while a human has it, enforced on every `act()` and every
  `resolve()` rather than by convention
- the handoff packet carries the five things section 3.6 asks for, with parameter names and
  sensitivities but never parameter values
- control returns on the **same browser context and the same page**, never a fresh one
- what the human did comes back as evidence: which fields they changed and what they clicked,
  by identity and never by value, plus accessibility snapshots from before and after
- on resume the page is re-observed and the step is re-verified. An operator reporting
  `completed_manually` on a step the page does not show as done fails the run rather than
  continuing
- `retry_step` is refused in code on an irreversible step, not merely hidden in the UI

Without `--lease-path` the run holds an in-process lease, invariant 10 still holds, and a
stopping condition ends the run with exit 20 instead of waiting for a person.

## Tests and checks

```bash
make test        # the full suite, no network anywhere in it
make schemas     # regenerate schemas/ from the Pydantic models
.venv/bin/mypy   # strict, over the typed packages
```

The suite needs Chromium installed, because a number of tests drive the real browser against
the real target application. None of them needs an API key.

## Where to read next

- `REPORT.md` is the design write-up: architecture, the artifact schema, determinism and error
  handling, heterogeneity, escalation, safety, and what was left out.
- `DECISIONS.md` is the running log those sections are assembled from, one entry per decision
  with the alternative that was rejected and the known weakness of what was chosen.
- `target_app/README.md` describes the stand-in application.
