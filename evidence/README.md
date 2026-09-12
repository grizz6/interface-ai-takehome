# evidence/

One directory per run. Discovery, replay and escalation all write through
`src/evidence/writer.py`, so the shape below is the same whichever produced it. That is the
point: a tool or a person that can read one of these can read all of them.

`evidence/*` is gitignored. Curated runs are force added under `evidence/curated/` in phase 9,
and everything else here is whatever a developer last ran.

## Directory shape

```
evidence/<run_id>/
    meta.json          what produced this run
    run.jsonl          what happened, one event per line
    result.json        the RunResult the caller received
    transcript.json    discovery only: the full model transcript
    screenshots/
        000.png        in capture order
    failure/           only when the result is not success
        dom.html
        aria.yaml
        screenshot.png
        context.json
```

`<run_id>` is `YYYYMMDD-HHMMSS-xxxx`. It sorts chronologically and the suffix survives two runs
starting in the same second.

## What each file is for

### meta.json

Which code and which allowlist produced this run. Neither is recoverable afterwards, and both
change the meaning of everything else in the directory.

| field | why it is here |
| --- | --- |
| `run_id`, `kind` | which run, and whether it was `discovery` or `replay` |
| `started_at`, `finished_at` | wall clock bounds; the index derives duration from them |
| `capability_id`, `capability_version` | which artifact was replayed, if any |
| `params_redacted` | parameter NAMES and sensitivities. Never values. Built from the capability's declared inputs rather than from the supplied dict, so the code path that could leak one does not exist |
| `model` | which model drove discovery, or absent for replay |
| `git_commit` | the commit that ran, suffixed `-dirty` if the tree was not clean |
| `policy_path`, `policy_sha256` | which allowlist, and exactly which version of it. The path alone is worthless because the file is edited; the hash alone is unreadable because nobody knows which file it belonged to |
| `schema_version` | the shape of this directory, so a reader can tell whether it predates a field |
| `result_kind`, `exit_code` | stamped when the run finishes |

### run.jsonl

One JSON object per line, appended as the run happens: policy blocks, retries, recoveries,
business outcomes, escalations, resumes. Everything passes the Redactor on the serialized
line, so a sensitive value cannot slip through in a field nobody thought about.

### result.json

The `RunResult` union from the design rules, section 7, exactly as the caller received it. Its `kind`
maps to the process exit code: success 0, business_outcome 10, needs_human 20, policy_blocked
30, failure 40.

### transcript.json

Discovery only. Every observation, model turn, tool call and action, in order. This is the
input the recorder compiles into a capability, so a capability in `capabilities/` can always be
traced back to the run that produced it.

### screenshots/

In capture order. Any field bound to a parameter the capability declares `pii` or `secret` is
blacked out at capture time using Playwright's own masking. This is best effort and its limits
are stated in `DECISIONS.md` 0037 and `REPORT.md` section 6.

### failure/

Written whenever the result is not `success`. Section 3.5 asks for at least one signal richer
than the log; there are three, plus the one that explains them.

| file | what it answers |
| --- | --- |
| `dom.html` | what the page actually was |
| `aria.yaml` | what it looked like to the accessibility tree, which is what the system perceives |
| `screenshot.png` | what it looked like to a person |
| `context.json` | why that was not what was expected |

`context.json` is the one worth reading first. It carries the step index, the action, what was
expected, what was observed, and `strategies_tried`: every tier of the locator bundle with the
number of elements it actually matched. A screenshot of a legacy screen cannot tell you that
tier one matched three elements when it should have matched one, and that is usually the whole
explanation.

A **business outcome is not a failure**, and this directory is written for one anyway, because
the screen that produced the outcome is worth keeping. `context.json` carries the real
`result_kind`, so the folder name is never read as the verdict. See `DECISIONS.md` 0036.

## Which file satisfies which requirement

| requirement | where to look |
| --- | --- |
| 3.1 discovery drives a real UI | `transcript.json`, and `screenshots/` |
| 3.2 the successful run becomes an artifact | `transcript.json` plus the matching file in `capabilities/` |
| 3.3 replay is deterministic and has no model | `result.json` `steps[].locator_strategy_used`, identical across runs; `tests/test_replay_isolation.py` for the no-model claim |
| 3.4 business outcomes are results | `result.json` `kind: business_outcome` with exit code 10 |
| 3.5 a richer signal on failure | `failure/`, all four files |
| 3.6 escalation carries enough context | `interventions/<id>.json`, and the `escalated` and `resumed` lines in `run.jsonl` |
| 6 safety and data handling | `meta.json` `params_redacted`, the masked `screenshots/`, and `tests/test_secret_guard.py` walking every byte of a completed run |

## Regenerating a run

The target app must be running first:

```bash
make app
```

**Replay, success.** Exits 0 and extracts `savings_balance`.

```bash
python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "100001"}' --allow-draft
```

**Replay, business outcome.** Exits 10. Not a failure.

```bash
python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "999999"}' --allow-draft
```

**Replay, restricted record.** Exits 10, code `member_restricted`.

```bash
python -m src.cli replay \
  --capability capabilities/lookup-member-savings-balance-1.2.0.json \
  --params '{"member_id": "100003"}' --allow-draft
```

**Escalation and handoff.** Console in one terminal, run in another. The run stops at the
irreversible Confirm and waits.

```bash
python -m src.cli operator --port 8090 --interventions-dir interventions
```

```bash
python -m src.cli replay \
  --capability capabilities/open-member-subaccount-1.0.0.json \
  --params '{"member_id":"100001","account_type":"Savings","nickname":"Vacation","initial_deposit":"250.00"}' \
  --allow-draft --headed --redact 100001 \
  --lease-path interventions/lease.json --interventions-dir interventions
```

**Discovery.** Needs `GEMINI_API_KEY` in `.env`.

```bash
python -m src.cli discover \
  --goal "look up member 100001 and read their current savings balance" \
  --target http://localhost:8080 --record
```

Add `--redact <value>` to any of the above for a value the capability does not declare
sensitive. Declared `pii` and `secret` parameters are handled by the schema; `--redact` is for
anything else the caller knows is sensitive.

## Reading the whole directory at once

```bash
python -c "from src.evidence.index import collect, render; print(render(collect('evidence')))"
```

One row per run: run id, kind, result kind, exit code, duration, and the single line that says
what happened. Phase 9 writes this to `evidence/curated/INDEX.md`.
