# Report

Assembled from `DECISIONS.md`; numbers cite the entry behind each claim. Each section states the
recorded weakness, not only what the choice buys.

## Architecture

The load-bearing seam separates perceiving a surface from the flow recorded against it. A `Surface`
yields an `Observation`, a normalized accessibility tree; everything above that line consumes only
`Observation` and `LocatorBundle`, never a page or a selector. Converting an ephemeral element
reference into a durable bundle is `describe()`, which lives in the surface because it needs the
snapshot that issued the reference and the live page, to verify each tier resolves to exactly one
element right now (0006). A recorder handed a transcript afterwards has neither. The cost is that
the surface both perceives and interprets, and a second surface reimplements all five tiers.

It is one process writing files rather than services and queues, because the brief says scaling
infrastructure is not rewarded and a thin but real version of every requirement beats a polished
subset; even the cross-process control lease is a polled file (0029). The provider sits behind
`ModelClient` so a scripted client could stand in for tests, and that seam is what made the
cost-driven switch from Anthropic to Gemini a one-module change, touching `client.py` and not the
loop, tools, transcript, prompt or any test (0016). The free tier model is weaker and likelier to
point at the wrong element, which `describe()` converts into a perfect locator for the wrong control
(0011).

## Artifact schema

Extraction is declared on `OutputSpec`, not performed as a step: steps change surface state, outputs
observe it, so the return contract is legible in one place and a tenant whose confirmation screen
moves a value is an `output_overrides` entry rather than a re-recording. The cost is that every
output is read after the last step, so a value visible only mid-flow cannot be captured (0003).
Checkpoints exist at both levels because they fail differently: a failed step postcondition
localizes a defect to one index, while a capability checkpoint failing after every step passed means
the steps were fine and the flow still did not arrive. Two levels is two places to be wrong, and
nothing forbids a checkpoint that restates the last postcondition (0003). `known_outcomes` is per
capability because the signal detecting "member not found" is specific to one screen in one
application, which keeps the artifact self describing at the cost of drift, since nothing forces ten
capabilities to word it identically (0003).

`LocatorBundle` is multi-signal with ordered tiers, each existing for a control the tier above
cannot address. Role plus accessible name: the Initial Deposit field, which has a real
`<label for>`. Label relation: the Nickname input, which has no label association, no `aria-label`,
no title and no placeholder, so no accessible name at all. Container plus ordinal: two Select
buttons with identical names, under Deposit Accounts and Loan Accounts. Text relation: a navigation
control that is a span with an inline onclick and no ARIA role, so all three role-based tiers fail.
CSS is forbidden as a primary, and text sits below container scope because a role plus name is a
contract with assistive technology and a container plus ordinal survives copy changes, while text
survives neither rewording nor translation (0006, 0007).

Two validators enforce policy rather than shape. A `risky_irreversible` step must carry a
postcondition, because an irreversible action must prove what it did when it did it, not at the end
when the evidence may be gone. A `pii` or `secret` parameter cannot carry an example, so the schema
refuses to hold a sensitive value; it fired during this work. Both follow one principle: anything
knowable at record time is rejected at record time, because an approved capability replays
unattended, so a defect surfacing at step four against a live banking system has broken what
approval bought (0004). It is only partly satisfied for `aria_template`, where `yaml.safe_load`
catches malformed YAML but semantics go unchecked, because Playwright publishes no parser for its
dialect; the error message says which of the two it checked (0005). The principle also catches
internal inconsistency only, never divergence from the live surface (0004).

## Determinism and error handling

Each step is judged in a fixed order: gate, resolve and act with a bounded retry, recoveries,
declared business outcomes, then the postcondition (0023). Outcomes precede postconditions because a
"no member record matches" screen fails the postcondition of the search step and fails the
checkpoint too, so asking the postcondition first turns every not-found lookup into exit 40 and a
paged human, to deliver an answer the system already had. The brief names conflating the two as the
most common mistake in this problem. They are checked after every step rather than at the end,
because a flow can end early: the not-found screen appears at step 3 of 4 and step 4 would click a
control that no longer exists. Producing run 08 found a hole in that ordering: a step whose wait
ran out returned a `timeout` before outcomes were checked, so a form the server rejected came back
as a failure, exit 40. A wait that runs out now checks the step's declared outcomes first, and the
run returns `validation_rejected` with exit 10 (0045). Five results carry distinct exit codes, `Success` 0,
`BusinessOutcome` 10, `NeedsHuman` 20, `PolicyBlocked` 30 and `Failure` 40, spaced by ten so a
caller branches on the decade without parsing JSON, and a recoverable condition never becomes a
result kind but appears as `recoveries_applied` on a success.

Irreversible steps get zero retries whatever the `WaitSpec` says, because a transient timeout and a
completed action that did not report look identical from outside the browser, so retrying opens the
account twice while escalating costs a human two minutes (0024). Ambiguity escalates rather than
guessing, and zero and two matches are deliberately asymmetric: zero is retried against a shared
budget, because a page still rendering reports zero for a control about to exist, while two is never
waited on and never falls through, since a first-match fallback picks an account. That waiting was a
real bug: the first implementation called `count()` once without waiting, so an unrendered control
reported zero and the resolver either declared the bundle unresolved or slid quietly to a lower
tier, recording degradation that had not happened.

Drift is secondary per the brief. A fingerprint mismatch is a hard stop before step 0, because a
different tenant's variant should select an override rather than run the base steps and hope, and
there is no automatic re-fingerprinting, because a system that quietly updates its own drift
detector no longer has one; the price is false stops on a harmless rebrand (0025). One defect is
worth reporting: `check_fingerprint` observed before navigating, so it saw `about:blank` and failed
every capability that recorded a fingerprint while passing only those recording nothing, which meant
the detector had never compared anything and its passing pre-flight read as evidence the surface was
checked (0034).

## Heterogeneity and multi-tenant

The criterion with the least code behind it, so it is worth separating demonstrated from argued.

`Surface` is a protocol of `observe()`, `resolve()`, `act()`, `evaluate()` and `describe()`, and its
`Observation` holds no selector and no DOM node. That `describe()` lives in the surface rather than
the recorder is precisely what lets a `DesktopSurface` populate the same `Observation` from UI
Automation or the AX API without the artifact schema changing, because tier selection is not a
shared algorithm over a generic tree but each surface's judgment about which handles on its platform
are durable, leaving the recorder surface agnostic (0006). Every strategy has a direct analogue:
role plus name is `ControlType` plus `Name` or `AXRole` plus `AXTitle`, a label relation is
`LabeledBy` or `AXTitleUIElement`, container plus ordinal is a container element plus index.

Accessibility-first extends to desktop for a structural reason: there the accessibility tree is the
only general-purpose programmatic view of a foreign application's UI, because no document exists and
nothing can be queried with a selector language. A CSS-selector design has nothing to port, since
the concept does not exist on the other side, so the port is a rewrite sharing a JSON file, while
"the control with this role and this accessible name" ports as a change of backend.

This is why it matters that a semantic relation expressed in XPath is not a CSS fallback: the
difference is what the expression names, not its syntax (0006). The label tier compiles to
`//tr[./*[normalize-space(.)="Nickname"]]` then asks `get_by_role` for the textbox inside, naming a
relationship a person would say out loud, so renaming the element id or restyling the table leaves
it resolving, whereas the CSS tier names one element by one attribute a framework generated. It
matters practically because the winning tier is recorded every run as drift telemetry, so classing
XPath as brittle would make every label-tier resolution report degradation, and a signal that fires
constantly is one nobody reads. Playwright's own `filter` chaining was rejected on evidence, since
`filter(has_text="Deposit Accounts")` matched two tables where `ancestor::table[1]` matched one. The
honest seam is that XPath 1.0 has no escape character and the expression names a `tr`, so that tier
is semantic in intent and HTML shaped in implementation, which is exactly where a desktop surface
needs its own code.

For legacy web, framesets, table layouts and missing accessible names are what the ladder already
handles, and the target app makes that checkable. The member panel is an iframe and the aria
snapshot inlines iframe content, so `frame_path` is a bundle field rather than a separate traversal.
Every screen is nested tables with no landmarks, which is why the container tier scopes to the table
whose heading says Deposit Accounts. The Nickname input's absent accessible name is why the label
tier exists; the onclick span's absent role is why the text tier does. A real replay resolves by
`role_name`, `label_relation`, `role_name`, `role_name`.

Multi-tenant is a base capability plus per-variant overrides rather than a re-recording per tenant,
with `VariantOverride` carrying `step_overrides`, `inserted_steps`, `output_overrides` and
`outcome_overrides`. The argument is economic: re-recording means N discovery runs, N artifacts to
review and N things to fix per vendor release, while an override is a diff against one reviewed
artifact. Drift is detected structurally rather than by page title, which is what
`SurfaceFingerprint` and its `aria_template` are for: a text assertion answers "does this string
appear", a structural one "is this the screen I recorded", and a tenant inserting an extra
confirmation step is a shape difference detectable no other way. An aria template is more brittle to
benign markup change, so it belongs on checkpoints and fingerprints rather than step postconditions,
and nothing enforces that (0005).

The honest admission is `text_relation` primaries. The brief describes hundreds of institutions
running one vendor product branded differently, and relabelling controls is the most common thing
such configuration does, so a text primary is the likeliest bundle to need an override, and it fails
in the quietest way: the control is still there doing the same thing, and the locator no longer
matches because someone renamed it (0007). Two mitigations exist and neither is built:
`step_overrides` handles it once discovered, and because the winning tier is recorded every run, a
fleet-wide report of text primaries is the first place to look when a tenant upgrade breaks a batch.
Plainly: variant B was never built. `seed.py` defines one variant, so the override mechanism is
schema and validation with nothing to point at, and everything above about multi-tenant is an
argument rather than a demonstration.

## Escalation and handoff

Stuck is a taxonomy: `locator_ambiguous`, `locator_unresolved`, `unknown_state`,
`risky_action_requires_approval`, `recovery_exhausted`, `step_timeout`, `max_steps_exceeded`, so an
operator knows before opening anything whether they are approving, disambiguating, or looking at a
screen nobody understands. During discovery the detector is the stall, three consecutive
observations hashing identical, and three rather than one because a single repeated screen is
normal, since `look` called twice produces an identical snapshot, while three is where the model has
decided one route is the only route and is trying variations; policy refusals use the same threshold
and reset on any success (0015).

Control is a lease, and only these transitions are legal: `running` to `paused`, `paused` to
`human_control`, `human_control` to `resuming`, `resuming` to `running`, anything to `closed`. The
holder is derived from the state rather than stored, because a lease with an independently settable
holder can express "paused but automation is driving", the exact bug it prevents. Every `act()`
asserts it before any Playwright call, and so does every `resolve()`, because resolving drives the
browser and decides the next move; there is no unleased surface, since one built without a lease
holds an in-process lease automation owns, making the assertion unconditional including in tests,
with `observe()` the deliberate exception because escalation must capture evidence during the handoff
itself. It is a polled file rather than a queue because there are two processes and one mutable fact
between them: `os.replace` makes each write atomic, either side can restart because the state is in
neither, and a socket would invert the dependency so the run could not start without the console
(0029).

The headed browser window is literally the same live session, since the operator works in the window
automation opened while the console only shows the packet and moves the lease. Streaming was cut and
the cost is real, that the operator must be at the machine running the browser; the transfer model
is not, because automation stops and holds no lease, the packet carries enough to act on, control
returns on the same context, and what the human did comes back as evidence (0030).

Resume re-verifies rather than trusting the report: automation re-observes and evaluates the step's
postcondition, or the capability checkpoint, before it looks at what the operator said, and
`completed_manually` is refused if the page does not show the step as done, because an operator's
answer is a claim about a screen and the screen is right there. It removes the failure where the run
proceeds from a state nobody established, after the human meant to click Confirm and clicked Cancel,
and it is unconditional rather than tied to one outcome, because even for `approved` a human who had
the session may have navigated anywhere; it is only as good as the declared postcondition, so a step
with neither is refused (0031). `retry_step` is refused outright on an irreversible step, in the
console and again where the resolution is read, because this is the zero-retry rule with a human
added and the human makes it worse: someone inside the session for five minutes has had every
opportunity to click the thing and may have, without remembering clearly. Twice, because hiding the
option is a courtesy while the refusal in code is the control, a resolution file being writable by
hand (0032).

## Safety

The allowlist is enforced inside the surface layer, not in the prompt: a prompt is a request a model
may ignore, misunderstand or argue with, while `PolicyGate.check()` in Python has no conversational
interface. The same argument governs locators, which is why the model points at element references
and never authors one, since tier selection decides whether a recording works next month and in a
prompt it sits where a model can hallucinate a plausible CSS selector (0011). Deny beats allow, so a
broad allow cannot re-open something explicitly denied. `/maintenance` and `/session-expired` are
allowed despite being error states, because the interstitial recovery works by arriving at the
maintenance notice and clicking Continue, so denying it would block the system's own recovery and
convert a recoverable condition into a `PolicyViolation`: denying a path you will predictably land on
does not stop you landing there, it removes your ability to act once you have. The allowlist
describes where the agent may legitimately *be*, not where things are going well. A bypass for
recovery navigations was rejected because a hole with a good reason is still a hole; the cost is a
file that mixes ordinary routes with load-bearing error screens and marks neither (0009).

Risk is classified per step, with `risky_control_names` marking irreversible controls and
`require_approval` stopping the run before the action, approval waiving exactly one rule for exactly
that call. Redaction happens at the boundary, on the serialized string rather than the object, so a
value cannot survive in a field nobody remembered; run metadata and every intervention record
parameter names and sensitivities and never values, structurally rather than by discipline, because
the descriptor is built from declared inputs and consults the supplied dict for one boolean. What a
human typed during a handoff is never recorded, only which field changed (0033). The credential
guard walks every byte on disk, tracked files and completed run directories alike, and never prints
what it matched, because a test that fails by echoing a secret has published it.

The limits, plainly. **Screenshot box masking is best effort.** Fields bound to `pii` or `secret`
parameters are blacked out at capture using live geometry rather than the recorded `geometry_hint`,
which is documented as a hint for a human and never a locator, since masking with it puts the box
beside the value after any reflow, looking redacted without being redacted. But it covers only the
field the artifact knows about, not the same value in a confirmation banner, a page title or a
validation message quoting what was entered, so the safer production default is aria snapshots with
field-level redaction as the primary visual record, since a snapshot is text and passes the same
redactor, with screenshots only on explicit operator request (0017, 0037).

**A real key reached a public commit.** `.env.example` was committed carrying a live
`GEMINI_API_KEY`. Two failures made it possible: the guard scanned only `evidence/` and
`capabilities/` and never tracked files, so the likeliest file to leak was outside its scope, and
every rule was vendor-format-specific, so a key not matching `AIza` matched nothing. The guard now
walks every tracked file, and a provider-agnostic rule matches the shape of the assignment, a
secret-sounding name given a long opaque value, with a test asserting it flags the offending commit
so the fix is demonstrated rather than claimed. The key was revoked, history rewritten, and an audit
of 187 blobs and 50 commit messages found zero credentials. **A second leak came from the sweep, not
a test:** captured human actions carry the URL they happened on, and `/member/100001/subaccount` is a
member id in a path, while the test that should have caught it checked only values a human typed and
this one was ambient (0041). **Discovery evidence is the least protected this system produces**,
because at discovery time no capability exists, so nothing marks anything `pii` and the goal sentence
names the member outright; the curated discovery run holds a member id in plain text in eight places.
The data is fabricated, the gap is not, and the answer is declaring sensitive inputs before the goal
is written, which nothing here does (0040).

## Cuts

The operator console is minimal with no session streaming, because streaming demonstrates nothing
about whether control can safely change hands (0030). There is no desktop surface, because the brief
asks that the design accommodate one and the code not attempt it, and a half-built one would
demonstrate less about portability than the protocol boundary does. There is no multi-tenant
plumbing beyond `variant_id` and the overrides map, because the schema decides whether the approach
works while the plumbing is undifferentiated infrastructure. There is no queue, worker, service
split or database, for the same reason, and that produced a better answer rather than a smaller one:
the lease being a file is why either side can restart without losing the session. Assisted LLM
fallback on replay failure was not attempted, because a model improvising a locator mid-replay makes
the load bearing judgment at the moment there is least evidence and no human watching, while doing
it properly means the fallback proposes and a human approves, which is the escalation path that
already exists. Multi-run stability is unmeasured: two consecutive replays are asserted to produce
identical step sequences and identical resolved tiers, which is determinism, but nothing runs a
capability fifty times and reports a rate, and that is the weakest claim in this report. Session
timeout is the one runtime condition from the brief's section 3.3 with no coverage at all: no
declared outcome, no recovery rule and no run. The target app can already simulate it: armed from
`/dev/faults`, it sends the next page load to a "Your session has expired" page. Handling it would
be a recovery rule using `reauthenticate`, which the schema already defines and the replay engine
does not yet implement. This app has nothing to sign in to, so the rule would return to the entry
screen and start the flow again; a real one would sign in first. Either way it is only safe before
any irreversible step has run. It was cut for time. Each is a
cut rather than an omission because the brief is explicit that a thin but real version of every
requirement beats a polished subset, and each bought time for a requirement that is thin but real.

Next, in order. First, the draft to approved promotion path, since every capability is `draft` and
every replay uses `--allow-draft`, so the gate is a speed bump rather than a control, and promotion
also closes the sharpest schema hole, a parameter declared with the wrong type passing every
validator and surfacing on the first real replay (0026, 0014). Then checkpoint discrimination,
since a model picking a string present on every page gets a checkpoint that passes verification and
every future replay regardless of where the flow ended (0013). Then build variant B of the target app and
record a capability against it, turning the heterogeneity claim from argued into demonstrated. Then aria snapshots as
the primary visual record, the posture the safety section recommends and does not implement. Last,
`extract_after_step` on `OutputSpec`, the narrowest real gap in the schema (0003).
