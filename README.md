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

`--dry-run` takes a JSON list of model turns and feeds them through the same code path a real
model uses, so the loop, the policy gate, the locator machinery and the evidence writer all run
with no key and no network:

```json
[
  {
    "text": "Opening the member record.",
    "tool_calls": [
      {"id": "c1", "name": "navigate",
       "arguments": {"url": "http://localhost:8080/member/100001"}}
    ],
    "stop_reason": "tool_use"
  }
]
```

```bash
.venv/bin/python -m src.cli discover \
  --goal "look up member 100001" \
  --target http://localhost:8080/member/100001 \
  --dry-run script.json
```

This is how the test suite exercises the system end to end, and it is the fastest way to see
the pieces work without spending a request.

### 2. Replay: pending

Not built yet. Phase 6. When it lands, this section documents replaying a saved capability from
`capabilities/` with input parameters, and the command in the design rules, section 11 becomes real:

```
make replay    python -m src.cli replay --capability capabilities/<id>.json --params '{...}'
```

### 3. Operator handoff: pending

Not built yet. Phase 7. When it lands, this section documents raising an intervention, taking
control of the live session, and handing it back.

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
