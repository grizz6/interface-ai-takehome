# computer-use-automation

An LLM figures out how to complete a task inside a real UI that has no API. The successful run
is saved as a typed, versioned capability. From then on that capability replays deterministically
with no model in the decision loop.

The target environment is back-office software at US banks and credit unions: screens that change
slowly, no API to integrate against, real runtime errors during any given run, and hundreds of
institutions running the same vendor product configured differently.

Built for the interface.ai take-home project.

## Status

Scaffolding. The deliverable paths and the approach decisions are in place. The agent loop, the
artifact schema, the replay engine, the escalation path, and the evidence runs are not built yet.
This section gets replaced as each piece lands.

## The idea

The model discovers. The run becomes a reusable capability. Deterministic replay is how a calling
agent invokes it in production.

Discovery is expensive and non-deterministic, so it happens once. Production execution is cheap
and repeatable, so it happens from a recorded artifact. The seam between those two is the part
worth designing carefully, and it is where most of the work here goes.

## Layout

```
README.md      setup, how to run, the demo path
REPORT.md      the design write-up
evidence/      artifact plus logs from a real discovery run and a replay run
src/           implementation (not present yet)
```

## Setup

Not runnable yet. When it is, this section covers the model API key, the local target app, and
how to exercise replay without any live service.

Secrets stay out of the repo. Configuration goes in a local `.env`, and `.env.example` lists the
keys it expects.

## Demo path

The shape being built toward, not yet live:

```
# 1. discovery: give the agent a goal and a target, let it work the UI
run discover --goal "look up member 12345 and read the savings balance" --target <url>

# 2. replay: run the saved capability with input params, no model involved
run replay --capability member-savings-balance --input memberId=12345
```

Two commands, two very different execution paths. The first one calls a model. The second one
must never need to.

## Planned approach

Four decisions made up front, with the reasoning, because they shape everything after.

**Perception through the accessibility tree, not CSS selectors.** The apps described in the brief
have framesets, deeply nested tables and no test IDs, so any locator built on markup structure is
a bad bet. The accessibility tree gives role plus accessible name, which is much closer to what a
human operator actually reads on the screen. The same representation exists on desktop through
the OS accessibility APIs, so the perception layer stays swappable rather than welded to a browser.

**A local stand-in for the target app, not a public demo site.** The interesting requirement is
the error taxonomy: record not found, validation failure, permission denial, session timeout, an
unexpected confirmation dialog. A public site will not produce those on demand. A local app that
can be told to fail lets the evidence show both the happy path and each exceptional state, and it
avoids pointing automation at someone else's service.

**TypeScript.** The artifact schema is the load-bearing piece, so it needs to be typed at the
boundary and validated at runtime from one definition rather than two that drift apart. Playwright's
TypeScript binding is also the best supported one.

**The model runs during discovery only.** Replay never calls it. That separation is the whole
point of the system, so it is enforced by the code structure rather than by discipline.

## Next

Artifact schema first, since replay, evidence and the escalation path all read from it. Then the
discovery loop, then replay with the error taxonomy, then the human handoff.
