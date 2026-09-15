# computer-use-automation

A model works out how to do a task in an app that has no API, by clicking through the screens
the way a clerk would. When it succeeds, the run is saved as a capability: a versioned JSON file
with typed inputs and outputs. After that the capability replays on its own, with no model
involved, returns a typed result, and hands the browser to a person when it gets stuck.

This is my submission for the interface.ai take-home. This file covers setup and how to run
things. [REPORT.md](REPORT.md) explains the design choices, and [DECISIONS.md](DECISIONS.md) is
the notes I kept while building it.

## Setup

### Prerequisites

- Python 3.11 or newer. The tests and the demo commands pass on 3.11, 3.12 and 3.14
- macOS or Linux
- Chromium, installed by Playwright in the step below
- A Gemini API key, only for a real discovery run. Replay, the handoff, the dry run and all of
  the tests work without one and make no network calls.

### Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/playwright install chromium
```

`[dev]` adds pytest and mypy. The last command downloads the Chromium build Playwright uses,
about 100MB.

Every `make` target calls `.venv/bin/python` directly, so there is nothing to activate.

### Configuration

```bash
cp .env.example .env
```

Then put your Gemini key after `GEMINI_API_KEY=` in `.env`. `.env.example` has variable names
only, with nothing after the equals sign, because in a diff a placeholder looks the same as a
real key.

`.env` is gitignored and loaded in one place, `src/cli.py`. Nothing in the code reads the key's
value; the Gemini SDK picks it up from the environment.

`config/policy.json` controls what a run is allowed to do: which hosts and paths it can visit,
which kinds of action it can take, and which button names count as irreversible. The file is
checked when it loads, so a broken allowlist stops the run before a browser opens.

### Start the target app

```bash
make app
```

This keeps the terminal busy. Leave it running and open a second terminal in the repo root for
everything below.

It serves a fake credit union back-office app on <http://localhost:8080>, built to look like an
old system: nested tables, an iframe, ASP.NET style element ids and no test ids.
`target_app/README.md` lists the routes, the seed members and how to turn on each fault.

## Trying it without a model

`--dry-run` replaces the model with a scripted list of replies. Everything else runs for real:
the browser, the policy check, locator lookup, the finish check and the evidence files. Start
the target app first.

**Happy path.** Exits 0.

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001 and read their current savings balance" \
  --target http://localhost:8080 \
  --config config/policy.json \
  --dry-run evidence-inputs/happy-path.json
```

The script navigates, clicks through three controls and then says it is finished. Each click
is saved as a locator that will still work later. The finish claim is not taken on trust: the
success check runs against the page on screen and the balance is read off it before the run
exits 0.

**Blocked path.** Exits 20.

```bash
.venv/bin/python -m src.cli discover \
  --goal "open the fault console" \
  --target http://localhost:8080 \
  --config config/policy.json \
  --dry-run evidence-inputs/failure-path.json
```

The script tries a route the policy forbids. The refusal goes back to the model as a message
instead of crashing the run, and the run stops after the page comes back the same three times in
a row.

## Demo

Six steps. Run every command from the repo root.

### 1. Start the target app

If you followed Setup it is already running. Running `make app` again fails with `Port 8080 is
in use`.

### 2. Discover a goal

**This uses API quota.** A run is up to 25 model turns, each with an accessibility snapshot and
sometimes a screenshot. To skip it, use the `record` command in step 3, which needs no key, or
the dry run above.

If `GEMINI_API_KEY` is empty, no request is sent. The run fails with exit `40` and `observed` in
`result.json` says "the model client could not start: No API key was provided". The Gemini SDK
may also print an `AttributeError` about `_async_httpx_client` on the way out. That comes from
the SDK tidying up a client it never finished creating, and the result is the same.

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001 and read their current savings balance" \
  --target http://localhost:8080
```

### 3. Turn the run into a capability

Adding `--record` discovers and saves in one go. Run it instead of step 2, not after, or you
pay for two runs:

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001 and read their current savings balance" \
  --target http://localhost:8080 --record
```

To compile a run that already exists, with no key, use the real discovery run in the repo:

```bash
.venv/bin/python -m src.cli record \
  --transcript evidence/curated/01-discovery-real/transcript.json \
  --out capabilities/
```

It prints `capabilities/lookup-member-savings-balance-1.0.0.json (unchanged: identical to the file
already there)`. Compiling the same run again gives the same file byte for byte. If a different
run would overwrite a saved capability with the same id and version, `record` refuses and exits
1. Use another `--out` folder, or `--overwrite` if you mean it.

New capabilities are saved as `status: draft`, which means recorded but not yet reviewed.
Replaying a draft needs `--allow-draft`, as in step 4.

### 4. Replay it

```bash
.venv/bin/python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "100001"}' --allow-draft --redact 100001
```

Exit 0, with `savings_balance` read from the page as a number. Nothing on this path imports a
model client.

### 5. Replay with a member that does not exist

```bash
.venv/bin/python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "999999"}' --allow-draft --redact 999999
```

Exit **10**, a `BusinessOutcomeResult` with code `member_not_found`. The run did its job and
the answer is "no such member", so it gets its own exit code rather than being counted as a
failure.

A restricted member works the same way, with code `member_restricted`:

```bash
.venv/bin/python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "100003"}' --allow-draft
```

### 6. Hand the browser to a person

Three terminals. The target app is already running in one.

**Terminal A, the operator page:**

```bash
.venv/bin/python -m src.cli operator --port 8090 --interventions-dir interventions
```

**Terminal B, a replay that stops and waits.** The last step, Confirm, is marked
`risky_irreversible`, and `config/policy.json` sets `risky_action_policy` to `require_approval`,
so the run stops there and asks for a person:

```bash
.venv/bin/python -m src.cli replay \
  --capability capabilities/open-member-subaccount-1.0.0.json \
  --params '{"member_id":"100001","account_type":"Savings","nickname":"Vacation","initial_deposit":"250.00"}' \
  --allow-draft --headed --redact 100001 \
  --lease-path interventions/lease.json --interventions-dir interventions
```

**In a browser,** open <http://127.0.0.1:8090>. The request shows the capability, the step, its
risk class, why the run stopped, the URL, a screenshot, the accessibility snapshot and the
parameter names. Parameter values are not shown.

Click **Take control**. The run stops touching the browser. Switch to the Chromium window the
run opened, which is the same session and not a copy, and click Confirm yourself.

Go back to the operator page and click **Return control** with `completed_manually` and a note.
The run looks at the page, checks that the step really happened, skips it, reads the new account
number and exits 0.

If you return `completed_manually` without clicking Confirm, the run fails and says the
operator reported the step as done but the page does not show it.

Without `--lease-path` there is nobody to hand over to, so the run exits 20 instead of waiting.

`discover` takes the same `--lease-path` and `--interventions-dir` options. With them, a
discovery run that gets stuck (the model gives up, the screen stops changing, a control cannot
be pinned down, or a step times out) hands the browser over the same way, and the model looks
again once control comes back.

## Capability catalog

The catalog tells a calling agent which capabilities exist and what each one takes and returns,
so it does not have to read the JSON files. It is two read-only commands. Add `--json` to either
for machine-readable output.

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
lookup-member-savings-balance 1.3.0 [draft]
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
lookup-member-savings-balance 1.3.0 [draft]
  lookup-member-savings-balance: Look up a member by ID and read their current savings balance.
  file:    capabilities/lookup-member-savings-balance-1.3.0.json
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
  .venv/bin/python -m src.cli replay --capability capabilities/lookup-member-savings-balance-1.3.0.json --params '{"member_id": "<string>"}' --allow-draft
```

`describe` shows the highest version unless you pass `--version`. `member_id` is marked `pii`,
and sensitive inputs are not allowed an example value, so the invoke line has a placeholder.
Fill it in and run it:

```bash
.venv/bin/python -m src.cli replay --capability capabilities/lookup-member-savings-balance-1.3.0.json --params '{"member_id": "100001"}' --allow-draft --redact 100001
```

```
evidence/20260914-145441-4f20
exit: 0
```

```
$ .venv/bin/python -c "import json; r = json.load(open('evidence/20260914-145441-4f20/result.json')); print(r['kind'], r['outputs'], [s['locator_strategy_used'] for s in r['steps']])"
success {'savings_balance': 4182.55} [None, 'role_name', 'role_name', 'role_name']
```

Running a capability goes through `replay`, so there is only one way to run one and the catalog
never changes anything. Your run folder name will be different.

## Exit codes

One code per result type, ten apart, so a caller can branch on the code without reading any
JSON. A mistyped command never starts a run and exits `2` from argparse. A missing API key does
start a run, and ends as a failure with exit 40.

| Code | Result | Meaning |
|---|---|---|
| 0 | `Success` | The task finished and the success check passed. Outputs are on the result |
| 10 | `BusinessOutcome` | The app gave an expected answer such as not found, permission denied or validation rejected. Not a fault |
| 20 | `NeedsHuman` | The run stopped and asked for a person, or asked and nobody answered |
| 30 | `PolicyBlocked` | The allowlist refused an action. The result names the rule and the action |
| 40 | `Failure` | Something broke: a control that could not be found, a failed check, a timeout, or a 5xx from the app |

## Repo layout

| Path | Contents |
|---|---|
| `src/models/` | The Pydantic models: `Capability`, `LocatorBundle`, the five result types, policy config |
| `src/surface/` | The `Surface` protocol, the `Observation` built from the accessibility tree, `WebSurface`, locators, action types |
| `src/policy/` | `PolicyGate`, loading and checking the allowlist, risk classes, redaction |
| `src/discovery/` | The model loop, tool definitions, the system prompt, the transcript, stop conditions, `ModelClient` |
| `src/recorder/` | Turns a transcript into a `Capability` |
| `src/replay/` | Replays a capability, the checks before a run starts, resuming after a handoff. Imports no model client |
| `src/catalog.py` | `catalog list` and `catalog describe`, which only read `capabilities/` |
| `src/escalation/` | `ControlLease`, `Session`, stored handoff requests, recording what the person did, the operator page |
| `src/evidence/` | The evidence writer, run metadata, failure files, the run index |
| `target_app/` | The fake legacy app. One version only; see below |
| `capabilities/` | Saved capabilities |
| `evidence/` | One folder per run. `evidence/curated/` holds the sample runs that are committed |
| `interventions/` | Handoff requests and the lease file |
| `evidence-inputs/` | Scripted model replies for the dry run |
| `config/` | `policy.json`, the allowlist |
| `schemas/` | JSON Schema generated from the Pydantic models |
| `scripts/` | A model smoke test, and the script that produced the sample runs |
| `tests/` | Tests |

## Tests

```bash
make test        # all tests, no network
make schemas     # regenerate schemas/ from the Pydantic models
.venv/bin/mypy   # strict type check on the typed packages
```

The tests drive a real Chromium against the target app, so Chromium has to be installed. None
of them need an API key.

## What is missing or simplified

The reasons are in `REPORT.md` under Cuts. The short version:

**The operator page is basic, and there is no screen streaming.** The person works in the
Chromium window the run opened, with their own mouse and keyboard. The operator page shows the
request and passes control back and forth. The downside is that the person has to be at the
machine running the browser. What still works is the part that matters for a handoff: control
moves between the run and the person on one browser session, and what the person did is saved.

**There is no desktop version.** `Surface` is a protocol and `Observation` comes from an
accessibility tree, so a desktop version could read Windows UI Automation or the macOS
accessibility API into the same model without changing the capability format. I have not built
it, so that is a design claim, not something shown working.

**Multi-tenant support is a `variant_id` field and an `overrides` map.** There is no tenant
registry or rollout tooling. **I never built a second version of the target app.** `seed.py`
defines one variant, `a`, and all visible text comes from it, so a second tenant would be a new
entry there rather than a copy of the templates. Nobody has written that entry, so starting the
app with `VARIANT=b` fails with `KeyError: 'b'` on every request. The override fields are
validated but never used by a real run.

Two more things are left out: replay does not fall back to a model when a locator fails, and I
have not measured how often replays fail over many runs. Both are covered in `REPORT.md`.

## Where to look next

- `evidence/curated/` has ten sample runs and an `INDEX.md`. The capability compiled from the
  real discovery run is saved next to it in `01-discovery-real/`. `evidence/README.md` says
  which part of the brief each run covers.
- `REPORT.md` is the design write-up, under the brief's seven headings.
- `DECISIONS.md` has one entry per decision, with what I rejected and the weak spot of what I
  picked.
- `target_app/README.md` describes the fake app.
