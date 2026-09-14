# computer-use-automation

An LLM works out how to complete a task inside a real UI that has no API, driving it the way a
clerk would. The successful run is compiled into a typed, versioned, parameterized capability
artifact. From then on that artifact replays deterministically with no model in the decision
loop, returns a typed result, and escalates to a human when it cannot safely proceed.

Built for the interface.ai take-home. This file is operational: what to install, what to run,
what to expect. The reasoning and the trade-offs are in [REPORT.md](REPORT.md), and the running
log those are assembled from is [DECISIONS.md](DECISIONS.md).

## Setup

### Prerequisites

- Python 3.11 or newer
- macOS or Linux
- Chromium, installed by Playwright in the step below
- A Gemini API key, **for a real discovery run only**. Replay, escalation, the scripted dry run
  and the entire test suite need no key and make no network calls.

### Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
```

The `[dev]` extra adds pytest and mypy, which the checks further down use. Without it the
package still runs but `make test` has nothing to run. The last command downloads the browser
Playwright drives, roughly 100MB, which is not bundled with the package.

Every `make` target uses `.venv/bin/python`, so activate nothing and set nothing up beyond the
three commands above.

### Configuration

```bash
cp .env.example .env
```

`.env.example` holds variable names and nothing after the equals sign, deliberately: a template
carrying a placeholder is indistinguishable from one carrying a live credential when you are
scanning a diff. Put your Gemini key in `.env` against `GEMINI_API_KEY`.

`.env` is gitignored and is read in exactly one place, `src/cli.py`. No code here references the
key's value. The Gemini SDK reads it from the environment itself, so the key travels from your
shell to the SDK without passing through anything written in this repository.

What the agent may do lives in `config/policy.json`: which hosts and paths it may reach, which
action types are permitted, and which control names count as irreversible. It is validated when
it loads, so a malformed allowlist is refused before a browser starts.

### Start the target application

```bash
make app
```

This runs in the foreground and holds the terminal. Leave it running and open a second terminal
in the repository root for everything that follows.

It serves the stand-in application on <http://localhost:8080>: a fictional credit union member
services console, built deliberately like a legacy system, with nested table layout, an iframe,
ASP.NET style element ids and no test IDs anywhere. `target_app/README.md` lists every route,
every seed member, and how to arm each simulated fault.

## Running the whole pipeline with no live model

`--dry-run` feeds a scripted list of model turns through the same code path a real model uses.
The browser, the policy gate, the locator machinery, the verification step and the evidence
writer all run for real. Only the model is replaced. Start the target app first.

**The happy path.** Exits 0.

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001 and read their current savings balance" \
  --target http://localhost:8080 \
  --config config/policy.json \
  --dry-run evidence-inputs/happy-path.json
```

What it exercises: navigation through the gate, three locator resolutions against the live page,
a ref converted into a durable `LocatorBundle` at the moment of each action, and then the part
that matters most, the `finish` claim being verified rather than believed. The model-declared
checkpoint is evaluated against the page that is actually on screen and the declared output is
genuinely extracted from it before exit 0 is allowed. Exit 0 here means the vertical slice
worked, not that the script reached its end.

**The blocked-and-stalled path.** Exits 20.

```bash
.venv/bin/python -m src.cli discover \
  --goal "open the fault console" \
  --target http://localhost:8080 \
  --config config/policy.json \
  --dry-run evidence-inputs/failure-path.json
```

What it exercises: the policy gate refusing a forbidden route, the refusal being fed back to the
model as "this direction is closed" rather than crashing the run, and the stall detector ending
it once three consecutive observations come back identical.

## Demo path

Six steps in order. Every command is copy-pasteable from the repository root.

### 1. Start the target app

Already running if you followed Setup. Running `make app` a second time fails with `Port 8080 is
in use`. If you stopped it, start it again in its own terminal the same way.

### 2. Discover a goal

**This spends API quota.** A run is up to 25 model turns, each carrying an accessibility
snapshot and sometimes a screenshot. If you would rather not, skip to the second command in step
3, which needs no key, or use the dry run above, which exercises everything except the model.

With `GEMINI_API_KEY` blank, as `.env.example` leaves it, this exits `1` with the SDK's
`ValueError: No API key was provided` as a Python traceback. No request is sent. Exit 1 is outside
the result contract below, because the run never started.

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001 and read their current savings balance" \
  --target http://localhost:8080
```

### 3. Record the transcript into a capability

Add `--record` to discover and compile in one invocation. Run it **instead of** step 2, not
after it, or you spend the quota twice:

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001 and read their current savings balance" \
  --target http://localhost:8080 --record
```

To compile a transcript that already exists, with no key and no call, use the real discovery run
that ships in the repository:

```bash
.venv/bin/python -m src.cli record \
  --transcript evidence/curated/01-discovery-real/transcript.json \
  --out capabilities/
```

That rewrites `capabilities/lookup-member-savings-balance-1.0.0.json` byte for byte identical to
the committed file, which is itself a check that compilation is deterministic: `git status` stays
clean. `--out capabilities/` overwrites any capability with the same id and version, so compiling
a different run, a dry run included, replaces the committed artifact's provenance with that run's.
Point `--out` somewhere else for anything but this transcript.

Compiled capabilities come out `status: draft`. A draft has been recorded once and replayed
never, so replaying one unattended is refused; see step 4.

### 4. Replay it, success case

```bash
.venv/bin/python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "100001"}' --allow-draft --redact 100001
```

Exit 0. `savings_balance` is extracted and coerced to the declared currency type. No model
client is imported anywhere in this path.

### 5. Replay with a member id that does not exist

```bash
.venv/bin/python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "999999"}' --allow-draft --redact 999999
```

Exit **10**, `BusinessOutcomeResult`, code `member_not_found`. This is not a failure. A record
that does not exist is an answer, and the exit code distinguishes it from a broken run without
anything having to parse stdout.

A restricted record behaves the same way, with code `member_restricted`:

```bash
.venv/bin/python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "100003"}' --allow-draft
```

### 6. Escalation walkthrough

Three terminals. The target app is already running from step 1.

**Terminal A, the operator console:**

```bash
.venv/bin/python -m src.cli operator --port 8090 --interventions-dir interventions
```

**Terminal B, a replay that will stop and wait.** The capability's final Confirm step is marked
`risky_irreversible`, and `config/policy.json` sets `risky_action_policy` to `require_approval`,
so the gate stops the run there and hands the session over:

```bash
.venv/bin/python -m src.cli replay \
  --capability capabilities/open-member-subaccount-1.0.0.json \
  --params '{"member_id":"100001","account_type":"Savings","nickname":"Vacation","initial_deposit":"250.00"}' \
  --allow-draft --headed --redact 100001 \
  --lease-path interventions/lease.json --interventions-dir interventions
```

**Then, in a browser:** open <http://127.0.0.1:8090>. The intervention names the capability, the
step, the risk class, why the run stopped, the URL, a screenshot, the accessibility snapshot, and
the parameter names with their sensitivities. No parameter values.

Click **Take control**. The lease moves to `human_control` and the run stops touching the
browser. Now work in the headed Chromium window the run opened: that window is the live session,
not a copy of it. Click Confirm yourself.

Come back to the console and **Return control** with `completed_manually` and a note. The run
re-observes the page, checks the step's postcondition actually holds, skips the step it did not
perform, reads the output off the screen you left behind, and exits 0.

If you return `completed_manually` without having clicked Confirm, the run fails with a message
saying the operator reported the step done and the page does not show it. It trusts the page,
not the report.

Without `--lease-path` the run holds an in-process lease and a stopping condition ends the run
with exit 20 instead of waiting for anyone.

## Agent-facing capability interface

The capability catalog is how a calling agent learns what it can invoke and exactly what each
capability accepts and returns, without reading artifact JSON. Two read-only commands, no server.
Add `--json` to either for machine-readable output. With the target app running:

```bash
.venv/bin/python -m src.cli catalog list
```

```
lookup-member-savings-balance 1.0.0 [draft]
  Look up a member by ID and read their current savings balance.
  in:  member_id: string
  out: savings_balance: currency
lookup-member-savings-balance 1.1.0 [draft]
  Look up a member by ID and read their current savings balance.
  in:  member_id: string
  out: savings_balance: currency
lookup-member-savings-balance 1.2.0 [draft]
  Look up a member by ID and read their current savings balance.
  in:  member_id: string
  out: savings_balance: currency
open-member-subaccount 1.0.0 [draft]
  Opens a deposit sub-account against an existing member relationship.
  in:  member_id: string, account_type: string, nickname: string, initial_deposit: currency
  out: new_account_number: string
open-member-subaccount 1.1.0 [draft]
  Opens a deposit sub-account against an existing member relationship.
  in:  member_id: string, account_type: string, nickname: string, initial_deposit: currency
  out: new_account_number: string
```

```bash
.venv/bin/python -m src.cli catalog describe lookup-member-savings-balance
```

```
lookup-member-savings-balance 1.2.0 [draft]
  lookup-member-savings-balance: Look up a member by ID and read their current savings balance.
  file:    capabilities/lookup-member-savings-balance-1.2.0.json
  surface: legacy_web localhost variant (unspecified)

inputs
  member_id: string (required, sensitivity pii)
      Member ID to look up
outputs
  savings_balance: currency (sensitivity none)
      Current savings account balance
business outcomes, returned with exit 10 and never raised
  member_not_found  No member record matches the supplied member id.
  member_restricted  The member record exists but this operator may not view it.
success means
  Look up a member by ID and read their current savings balance.
requires human approval before
  nothing: no step is irreversible
exit codes
  success 0, business_outcome 10, needs_human 20, policy_blocked 30, failure 40
invoke
  .venv/bin/python -m src.cli replay --capability capabilities/lookup-member-savings-balance-1.2.0.json --params '{"member_id": "<string>"}' --allow-draft
```

`describe` picks the highest version unless `--version` is given. `member_id` is `pii`, and the
schema forbids a sensitive input from carrying an example, so the invoke line holds a typed
placeholder rather than a value. Filling it in and running that line is the invocation:

```bash
.venv/bin/python -m src.cli replay --capability capabilities/lookup-member-savings-balance-1.2.0.json --params '{"member_id": "100001"}' --allow-draft --redact 100001
```

```
evidence/20260913-005333-2be9
exit: 0
```

```
$ .venv/bin/python -c "import json; r = json.load(open('evidence/20260913-005333-2be9/result.json')); print(r['kind'], r['outputs'], [s['locator_strategy_used'] for s in r['steps']])"
success {'savings_balance': 4182.55} [None, 'role_name', 'role_name', 'role_name']
```

Invocation is the existing `replay` command rather than a third catalog command, so there is
exactly one execution path and the catalog only ever reads. The run directory name will differ on
your machine.

## Exit codes

One code per result kind, spaced by ten so a caller can branch on the decade without parsing
JSON and so a related code can be added later without renumbering. These cover every run that
starts. A process that dies before a run exists, a missing API key for instance, exits `1` with a
traceback instead.

| Code | Result kind | Meaning |
|---|---|---|
| 0 | `Success` | The goal was reached and the checkpoint held. Typed outputs are on the result |
| 10 | `BusinessOutcome` | The application gave a declared, legitimate answer: not found, permission denied, validation rejected. Not a fault |
| 20 | `NeedsHuman` | The run stopped and raised an intervention, or a raised intervention was never answered |
| 30 | `PolicyBlocked` | An action was refused by the allowlist, naming the rule and the attempted action |
| 40 | `Failure` | Something broke: a locator that would not resolve, a checkpoint that did not hold, a timeout, the application returning 5xx |

## Repository layout

| Path | What is in it |
|---|---|
| `src/models/` | Every Pydantic schema: `Capability`, `LocatorBundle`, the five-member `RunResult` union, policy config |
| `src/surface/` | The `Surface` protocol, the accessibility-tree `Observation`, `WebSurface`, locator compilation, action types |
| `src/policy/` | `PolicyGate`, allowlist loading and validation, risk classification, the redactor |
| `src/discovery/` | The model loop, tool schemas, the system prompt, the transcript, stopping conditions, the `ModelClient` seam |
| `src/recorder/` | Compiles a transcript into a `Capability` |
| `src/replay/` | The deterministic executor, pre-flight checks, resume semantics. Imports no model client |
| `src/catalog.py` | The capability catalog: `catalog list` and `catalog describe`, read-only over `capabilities/` |
| `src/escalation/` | `ControlLease`, `Session`, the intervention store, the page recorder, the operator console |
| `src/evidence/` | The single evidence writer, run metadata, failure artifacts, the run index |
| `target_app/` | The stand-in legacy application. Variant A only; see the note on multi-tenant below |
| `capabilities/` | Saved capability artifacts |
| `evidence/` | Per-run evidence directories. `evidence/curated/` is the tracked deliverable |
| `interventions/` | Open and resolved handoff requests, plus the control lease file |
| `evidence-inputs/` | Scripted model transcripts for the dry run path |
| `config/` | `policy.json`, the allowlist |
| `schemas/` | JSON Schema exported from the Pydantic models |
| `scripts/` | A model smoke test, and the driver that produced the curated evidence |
| `tests/` | The suite |

## Tests and checks

```bash
make test        # the full suite. No network anywhere in it
make schemas     # regenerate schemas/ from the Pydantic models
.venv/bin/mypy   # strict, over the typed packages
```

The suite drives a real Chromium against the real target application, so Chromium must be
installed. No test needs an API key.

## What is mocked, stubbed or cut

Three things, and each is a deliberate cut rather than an unfinished edge. `REPORT.md` section 7
covers the reasoning; this is the operational summary.

**The operator console is minimal, and live session streaming was cut.** No co-browsing, no
VNC, no screencast. The headed Chromium window automation opened *is* the live session, and the
operator works in it directly with their own mouse and keyboard. The console exists to show the
handoff packet and to move the control lease. What this costs is real: the operator has to be at
the machine running the browser, so a remote operator cannot use this. What it keeps is
everything the handoff is actually about, which is that control genuinely changes hands on one
browser context and that what the human did comes back as evidence.

**No desktop surface is implemented.** The design accommodates one and the code does not attempt
it. `Surface` is a protocol and `Observation` is populated from an accessibility tree, so a
`DesktopSurface` reading UI Automation or the macOS AX API would populate the same model without
the artifact schema changing. That claim is architectural, and it is argued rather than
demonstrated.

**Multi-tenant support is a `variant_id` and an `overrides` map, and nothing more.** No tenant
registry, no per-tenant deployment plumbing, no fleet rollout tooling. **Variant B of the target
app was never built.** `seed.py` defines one variant, `a`, and every user-visible string is read
from it, so a second tenant would be a config entry rather than a forked template. But it is a
config entry nobody has written: starting the app with `VARIANT=b` raises `KeyError: 'b'` on
every request. The override machinery in the schema is therefore validated and unexercised, and
the heterogeneity argument in `REPORT.md` is an argument rather than a demonstration.

Two further cuts worth naming because they are absences rather than stubs: replay has no
assisted LLM fallback when a locator fails, and multi-run stability across many replays has not
been measured. Both are in `REPORT.md` section 7 with reasons.

## Where to read next

- `evidence/curated/` is six real runs with an `INDEX.md`, each mapped to the brief requirement
  it demonstrates. `evidence/README.md` is the map.
- `REPORT.md` is the design write-up under the brief's seven headings.
- `DECISIONS.md` is the running log those sections are assembled from, one entry per decision
  with the rejected alternative and the known weakness of what was chosen.
- `target_app/README.md` describes the stand-in application.
