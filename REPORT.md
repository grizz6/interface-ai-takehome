# Report

Numbers in brackets point to the entry in `DECISIONS.md` with the full reasoning.

## Architecture

It is one Python process plus files on disk. The brief does not reward scaling
infrastructure, so even the lease two processes share is just a file they poll (0029). The main
split is between reading the screen and the saved flow. A `Surface` returns an `Observation`, a
cleaned-up accessibility tree, and nothing above that layer ever sees a page or a selector, only
`Observation` and `LocatorBundle`. `describe()` turns a snapshot's short-lived element ref into a
locator that lasts. It lives in the surface because it needs the live page to check that each
candidate matches exactly one element (0006). The downside is that every new surface has to
reimplement the locator tiers. The model sits behind `ModelClient`, so switching from Anthropic
to Gemini to save money touched one module (0016). The free Gemini model sometimes picks the
wrong element, and `describe()` will then build a perfectly good locator for the wrong thing
(0011).

## Artifact schema

A capability has typed inputs and outputs, ordered steps, a success check, expected business
outcomes, recovery rules and per-tenant overrides. It is versioned and validated by Pydantic.
Outputs are declared once at the top instead of being read inside a step, so everything a
caller gets back is listed in one place. The catch is that a value shown only halfway through a
flow cannot be captured (0003). There are checks after each step and one for the whole flow,
because they catch different problems, though nothing stops them from repeating each other.
Expected outcomes belong to each capability because "member not found" is a property of one
screen. The price is that two capabilities can word the same outcome differently (0003).

`LocatorBundle` stores several ways to find a control, in order. Each tier exists because a real
control in the target app needed it: role and name for Initial Deposit, the label next to it for
Nickname (which has no accessible name), container and position for two identical Select
buttons in different tables, and visible text for a clickable span with no role. CSS is only a
last resort. Text ranks below structure because a rebrand changes wording first (0006, 0007).

Two validators check policy when a capability is built: an irreversible step must have a check
after it, and a `pii` input cannot have an example value. Anything that can be caught from the
file alone is caught at build time, since an approved capability runs with nobody watching
(0004). Screen-shape templates only partly fit this, because their YAML can be checked but not
whether they describe the right screen (0005).

## Determinism & error handling

Replay uses no model. Each step goes through the policy check, the action with a limited retry,
recovery rules, expected outcomes and then its own check (0023). Outcomes come before the check
because a "no such member" page fails every check, and checking first would page a person for
what is really an answer, which the brief names as the most common mistake. Sample run 08 found a
hole: a wait that timed out was reported before outcomes were looked at. Timeouts now look at
outcomes first (0045). Results are `Success` 0, `BusinessOutcome` 10, `NeedsHuman` 20,
`PolicyBlocked` 30 and `Failure` 40. A recovery is noted on a success, not a result of its own.

Irreversible steps are never retried, because a timeout looks the same as an action that went
through without showing it (0024). A locator that matches nothing is retried while the page
loads, but one that matches two elements stops straight away, because guessing could pick the
wrong account. The waiting fixed a real bug where a control that had not rendered yet quietly
fell through to a weaker tier. An expired session restarts the flow from the first page, but
never after an irreversible step and never more times than the rule allows (0046). Each of the
six runtime conditions in brief 3.3 has a sample run. Drift is handled more loosely: if the first
page does not match its saved fingerprint the run stops before step 0, unless the page is a
declared outcome. That can stop a run on a harmless rebrand (0025, 0045).

## Heterogeneity & multi-tenant

There is not much code behind this section, so I want to be clear about what is argued and what
is shown. `Surface` is a small protocol and `Observation` holds no selector or DOM node. Because
`describe()` lives in the surface, a `DesktopSurface` could fill the same `Observation` from
Windows UI Automation or the macOS accessibility API and use its own lasting handles, with no
change to the capability format. On desktop, the accessibility tree is the only general way to
read another app's screen. A design built on CSS selectors would have nothing to carry over,
while "the control with this role and name" works the same on a new backend.

For the same reason the middle tiers compile to XPath without being brittle. The label tier asks
for "the textbox in the row labelled Nickname", which survives restyling and renamed ids, while
the CSS tier names one generated id. I tried Playwright's `filter` first and dropped it after it
matched two tables where the XPath matched one. The limit is that this XPath names HTML rows, so
a desktop surface needs its own version (0006). Old web apps are what the tiers already handle:
the target app has an iframe, table-only layout, an input with no accessible name and a control
with no role, and real replays find all of them.

For tenants, one base capability holds per-variant overrides for steps, extra steps, outputs and
outcome text. A vendor release then means reviewing a diff rather than re-recording every tenant.
Drift is detected by screen shape as well as text, since an extra confirmation step changes the
shape, though shape templates break easily and should only be used on checkpoints (0005). Text
locators are the most likely to need overrides, because rebranding changes labels, and they fail
without any warning (0007). I never built variant B of the target app, so this is a design, not
a demonstration.

## Escalation & handoff

A run is stuck for a named reason: a locator that matches several elements or none, a page it
does not recognise, a risky action that needs approval, recovery that ran out, a timeout, or too
many steps. Discovery also stops when the page looks the same three times in a row, or after
three policy refusals in a row (0015). Control is a lease with fixed state changes, and every
`act()` and `resolve()` checks it before touching the browser. It is a polled file, so either
process can restart (0029). The person works in the same visible browser window. I cut screen
streaming, so they have to be at that machine (0030). The request includes the capability, step,
reason, screenshot and page snapshot, and the person's clicks and changed fields are recorded.
When control comes back, the run checks the page before believing what it was told (0031), and it
refuses to retry an irreversible step, because the person may already have done it (0032).

## Safety

The allowlist is enforced in the surface code, not in the prompt, because a model can talk its
way around a prompt but not around Python. Denied paths win over allowed ones. The maintenance
and session-expired pages are allowed because recovering from them means landing on them
(0009). Irreversible buttons stop for approval. Redaction runs on text as it is written, saved
records keep parameter names but not values, and what a person types is never stored (0033). A
scan checks tracked files and sample runs for anything shaped like a key or a redacted value.

The gaps: blacking out a field in a screenshot misses the same value if it also appears in a
banner or title, so a real deployment should rely on text snapshots (0037). A live API key once
reached a public commit because the scan skipped tracked files. I fixed the scan, rewrote history
and revoked the key. The scan later caught a member id in a handoff record (0041). Discovery
evidence is the least protected, because nothing is marked sensitive until a capability exists
(0040).

## Cuts

No live screen streaming on the basic operator page (0030), no desktop surface, and no tenant
setup beyond overrides, all of which the brief allows. No model fallback during replay, because
guessing a locator in the middle of a run puts judgment exactly where there is least to go on. I
also have not measured failure rates over many replays. I took one stretch goal, the capability
catalog. Approval is half done: drafts are gated, but there is no scoring or promotion (0026).
Next, in order: approve a capability only after it replays with new inputs (0014), reject success
checks that also pass on unrelated pages (0013), build variant B, make text snapshots the default
visual record, and allow reading outputs partway through a flow (0003).
