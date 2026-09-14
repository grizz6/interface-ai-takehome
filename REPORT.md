# Report

Drawn from `DECISIONS.md`; bracketed numbers cite the entry behind a claim.

## Architecture

One Python process and files on disk: the brief rewards simplicity, not scaling infrastructure,
so even the lease shared by two processes is a polled file (0029). The central seam separates
perceiving a surface from the recorded flow. A `Surface` yields an `Observation`, a normalized
accessibility tree, and everything above it works with `Observation` and `LocatorBundle`, never a
page or selector. `describe()` turns a snapshot's temporary element reference into a durable
bundle and lives in the surface, because it needs the live page to confirm each candidate finds
exactly one element (0006); the cost is that each new surface reimplements the locator tiers. The
model sits behind `ModelClient`, which made the cost-driven move from Anthropic to Gemini a
one-module change (0016). The weaker free-tier model can point at the wrong element, and
`describe()` will build a perfect locator for it (0011).

## Artifact schema

A capability declares typed inputs and outputs, ordered steps, a success checkpoint, expected
business outcomes, recovery rules and per-tenant overrides, versioned and validated by Pydantic.
Outputs are declared, not read in a step, so the return contract sits in one place; the cost is
that a value shown only mid-flow cannot be captured (0003). Checks exist per step and for the whole
flow because they catch different faults, though nothing stops the two from repeating each other.
Outcomes live on each capability because "member not found" belongs to one screen, at the price of
capabilities wording shared outcomes inconsistently (0003).

`LocatorBundle` ranks ways to find a control, each tier forced by a real control in the target
app: role and name for Initial Deposit; label relation for Nickname, which has no accessible name;
container and position for two identical Select buttons under different tables; visible text for a
clickable span with no role. CSS is only a fallback, and text ranks below structure because
rebranding changes wording first (0006, 0007).

Two validators enforce policy: an irreversible step must carry a postcondition, and a `pii` input
may not hold an example value. Both follow the rule that anything knowable from the artifact is
rejected when it is built, since an approved capability runs unattended (0004). Screen-shape
templates meet this only partly: their YAML is checked but their meaning cannot be (0005).

## Determinism & error handling

Replay uses no model. Each step passes the policy gate, the action with a bounded retry, recovery
rules, declared outcomes and then its own postcondition (0023). Outcomes come first because a "no
such member" page fails every check, and asking those first turns an answer into a paged human,
the mistake the brief calls most common. Sample run 08 exposed a hole, a timed-out wait reported
before outcomes were checked; that wait now checks outcomes first (0045). Results are `Success` 0,
`BusinessOutcome` 10, `NeedsHuman` 20, `PolicyBlocked` 30 and `Failure` 40, and a recovery is
metadata on a success.

Irreversible steps are never retried, because a timeout looks identical to an action that completed
silently (0024). A locator matching nothing is retried while the page loads; one matching two stops
at once, since guessing picks an account. That waiting fixed a real bug where an unrendered control
slid silently to a weaker tier. An expired session restarts the flow from the entry page, but never
after an irreversible step or beyond the rule's limit (0046). All six runtime conditions in brief
3.3 have a sample run. Drift is secondary: a fingerprint mismatch stops the run before step 0
unless the page is a declared outcome, at the cost of false stops on a harmless rebrand (0025, 0045).

## Heterogeneity & multi-tenant

Little code sits behind this criterion, so what is argued is worth separating from what is shown.
`Surface` is a small protocol and `Observation` holds no selector or DOM node. Because `describe()`
lives in the surface, a `DesktopSurface` could fill the same `Observation` from UI Automation or
the macOS accessibility API and pick its own durable handles, with the capability format unchanged.
On desktop the accessibility tree is the only general way to read another application's screen, so
a design built on CSS selectors has nothing to carry over, while "the control with this role and
name" moves to a new backend intact.

For the same reason the middle tiers compile to XPath without being brittle: the label tier asks
for "the textbox in the row labelled Nickname", which survives restyling and renamed ids, while the
CSS tier names one generated id. Playwright's `filter` was rejected after matching two tables where
the XPath matched one. The limit is that this XPath names HTML rows, so a desktop surface needs its
own (0006). Legacy web is what the tiers already handle: the target app has an iframe, table-only
layout, an input with no accessible name and a control with no role, and real replays resolve them.

For tenants, one base capability carries per-variant overrides for steps, inserted steps, outputs
and outcome text, so a vendor release means reviewing a difference, not re-recording every tenant.
Drift is detected by screen shape as well as text, since an added confirmation step changes shape,
though shape templates are fragile and belong only on checkpoints (0005). Text locators are the
likeliest to need overrides, because relabelling is what branding does, and they fail quietly
(0007). Variant B of the target app was never built, so this is designed, not demonstrated.

## Escalation & handoff

Stuck has named reasons: ambiguous or missing locator, unknown state, a risky action needing
approval, exhausted recovery, a timeout, or too many steps. Discovery calls three identical
observations a stall, and ends after three policy refusals in a row (0015). Control is a lease with
fixed transitions, its holder derived from its state, checked by every `act()` and `resolve()`
before touching the browser. It is a polled file, so either process can restart (0029). The person
works in the same headed browser window; streaming was cut, so they must sit at that machine (0030).
The request carries capability, step, reason, screenshot and page snapshot, and the person's clicks
and changed fields are recorded. On return, automation checks the page before believing the answer
(0031), and retrying an irreversible step is refused in code, because the person may already have
done it (0032).

## Safety

The allowlist is enforced in the surface, not the prompt, because a model can argue with a prompt
but not with Python. Denied paths beat allowed ones, and the maintenance and session-expired pages
are allowed because recovery means landing on them (0009). Irreversible controls stop for approval.
Redaction runs on text as it is written, records list parameter names without values, and a
person's typing is never stored (0033). A scan checks tracked files and sample runs for key-shaped
strings and redacted values.

Its limits: screenshot masking misses the same value shown in a banner or title, so production
should prefer text snapshots (0037). A live API key once reached a public commit because the scan
skipped tracked files; that was fixed, history rewritten and the key revoked. The scan later caught
a member id in a handoff record (0041), and discovery evidence stays least protected, because
nothing is marked sensitive before a capability exists (0040).

## Cuts

No live streaming in the minimal operator console (0030), no desktop surface, and no multi-tenant
plumbing beyond overrides, all as the brief allows. No assisted LLM fallback, since improvising a
locator mid-replay puts judgment where evidence is thinnest, and no measured failure rate across
many replays. One stretch goal was taken, the capability catalog; approval is half done, with drafts
gated but no scoring or promotion (0026). Next, in order: approval after a replay with fresh inputs
(0014), rejecting success checks that also pass on unrelated pages (0013), building variant B, text
snapshots as the default visual record, and reading outputs mid-flow (0003).
