# evidence-inputs

Scripted model transcripts for `--dry-run`. Each drives the real WebSurface, the real policy
gate and the real evidence writer against the live target app. Only the model is replaced.

| Script | Exit | What it proves |
|---|---|---|
| `happy-path.json` | 0 | The whole vertical slice with no model: navigate, click, type, search, then a `finish` whose checkpoint is evaluated and whose declared output is actually extracted from the live page |
| `failure-path.json` | 20 | The policy gate refusing a route, the refusal being fed back to the model rather than crashing the run, and the stall detector firing when three consecutive observations are identical |

The refs in `happy-path.json` (`e19`, `f1e34`, `f1e38`, `f3e28`) are per-snapshot handles, and
they are only reproducible because the navigation sequence is fixed and the target app is
deterministic. They are not durable identifiers and nothing in `capabilities/` may contain
one. That is invariant 9, and it is the reason a scripted transcript can hardcode a ref while
a recorded artifact never can.
