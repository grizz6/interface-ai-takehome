# evidence-inputs

Scripted model replies for `--dry-run`. They run against the live target app using the real
WebSurface, policy check and evidence writer. Only the model is swapped out.

| Script | Exit | What it shows |
|---|---|---|
| `happy-path.json` | 0 | A full run with no model: navigate, click, type, search, then `finish`, where the success check runs on the live page and the declared output is read from it |
| `failure-path.json` | 20 | The policy refusing a route, the refusal going back to the model as a message instead of crashing the run, and the run stopping after the page looks the same three times in a row |

The refs in `happy-path.json` (`e19`, `f1e34`, `f1e38`, `f3e28`) only exist within one snapshot.
They come out the same every time only because the steps are fixed and the app always renders the
same way. A saved capability must never contain one, which is why a script can hardcode a ref and
a recorded capability cannot.
