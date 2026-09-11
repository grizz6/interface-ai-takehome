# Decisions

A running log of the choices made while building this system, written as they happen rather than
reconstructed at the end. Every entry names the alternative that was rejected and the known
weakness of what was chosen, so REPORT.md can be assembled from this file instead of from memory.
Deliberate stubs, mocks, and omissions are recorded here in the same session they are made, per
design rule 8.

## 0001. Python, Playwright sync API, and Pydantic v2

Phase 0.

The system is built on Python 3.11 with the Playwright sync API driving the web surface, Pydantic
v2 for every schema, and the Anthropic SDK confined to the discovery loop. The artifact schema is
what this project is graded on above all else, and Pydantic gives one definition that acts as the
parser, the runtime validator, and the source of the JSON Schema exported to `schemas/`. A
reviewer then reads the same shape the code actually enforces, rather than a document that claims
to describe it. Python also keeps the discovery loop, the replay engine, and the Flask target
application in a single language, so the accessibility tree observation model is written once and
shared.

Rejected: TypeScript with Playwright and Zod. It is a genuine contender and is arguably stronger
on type safety alone. Playwright's TypeScript binding is the reference implementation and receives
features first, Zod infers static types from the same declaration without a separate checking
step, and a discriminated union like the result contract in design rules section 7 would be verified
by the compiler rather than by an opt-in tool.

Known weakness of the choice: two, both accepted with open eyes. First, typing in Python is
advisory, so `mypy --strict` has to be run deliberately and only sees what has been annotated,
where a TypeScript build would simply refuse to produce output. The convention in the design rules
section 9 is therefore load-bearing rather than decorative. Second, the Playwright sync API blocks
the calling thread, which is the awkward part of the escalation seam in invariant 7: the browser
context has to stay alive and driveable by a human at a moment when Python is not calling into it.
That forces the control lease to be an explicit, modelled object instead of something that falls
out of an async event loop for free. Phase 7 is where that cost is paid.


## 0002. Real exceptional states and simulated faults are produced by different mechanisms

Phase 1.

The target app produces its exceptional states two different ways. Not found, permission
denied, and validation failure are real: they fall out of the seed data and the submitted
input, with no switch involved. Request a member id that was never seeded and you get the
no member found screen. Request member 100003, whose record carries a restricted flag, and
you get permission denied. Submit a non-positive initial deposit and the server rejects it
and returns the form with an inline error. The four conditions on /dev/faults, an unexpected
interstitial, session expiry, a slow response, and a server error, are simulated instead:
armed by hand, stored in the Flask session, fired once, then disarmed. The dividing line is
whether the condition is a property of the data or a property of the runtime.

Rejected: drive all seven from the fault console as uniform toggles. It is a smaller
mechanism and there would be one place to look. It was rejected because a "record not found"
produced by a toggle proves nothing about the system under test. Replay would be detecting a
flag the harness set rather than a condition the application genuinely produced, and the
distinction between a business outcome and a failure is exactly the one the brief names as
the most common design mistake. Those three have to arise from data and input or the evidence
for invariant 5 is circular.

Known weakness of the choice: three, all accepted. The simulated faults fire on GET only,
so a session expiry in the middle of a form POST, arguably the most realistic and most
painful version of that condition, cannot be produced at all. That was traded away because
an interstitial or a redirect fired on a POST discards the submission and leaves the Continue
control with nothing to resume, which is noise rather than signal. Armed faults live in the
session cookie, so they are scoped to one browser context, which conveniently keeps
concurrent runs from disturbing each other but means a fault cannot be armed out of band by
anything that does not share the cookie jar. And the real states are only as real as the
seed data: with no database there is no way to produce a genuine mid-transaction failure,
so that class of error is out of reach of this stand-in entirely.

## 0003. Three shape decisions in the capability schema

Phase 2. Three related choices about where information lives in the artifact, each with what
was rejected and what it costs.

### Extraction is declared on outputs, not performed as a step

Reading a value is declared once on `OutputSpec.extraction`. Steps only act. The split is
between changing the state of the surface and observing it, and it buys three things: the
return contract is legible in one place, so a calling agent reading the artifact learns what
it gets back without simulating the step list; replay can extract without walking the steps a
second time; and a tenant whose confirmation screen puts the account number in a different
cell is an `output_overrides` entry rather than a re-recorded flow.

Rejected: an `extract` action type, making reading just another step with a named result. It
is a smaller action model, there is one list to execute rather than two phases, and crucially
it makes extraction ordering explicit.

Known weakness, and this one is real rather than theoretical: because extraction is declared
rather than sequenced, every output is read at the same moment, after the last step. A value
that is only on screen mid-flow, such as a reference number an interstitial shows and the
next navigation destroys, cannot be captured by this schema at all. If we hit that case the
fix is an `extract_after_step` field on OutputSpec, not reintroducing an extract action,
because the declaration is the part worth keeping.

### Checkpoints exist at both step and capability level

`Step.postcondition` asserts that one action did what it claimed. `Capability.checkpoint`
asserts that the flow as a whole arrived at the goal. Both exist because they fail
differently and the caller needs to tell those failures apart: a failed postcondition
localizes the defect to one step index, while a checkpoint that fails after every step passed
means the steps were individually fine and the flow still did not reach the goal, which is a
different bug and usually a worse one. This is also why a `risky_irreversible` step is
required to carry a postcondition. An irreversible action has to prove what it did at the
moment it did it, not at the end of the run when the evidence may be gone.

Rejected: a capability-level checkpoint only. Less to write and less to keep true, and it is
defensible that the end state is the only thing a caller really cares about.

Known weakness: two levels is two places to be wrong, and the redundancy is not hypothetical.
In the test fixture the final step postcondition and the capability checkpoint assert the same
text, which is duplication, and the schema neither detects nor forbids it. A careless author
can write a flow whose checkpoint adds nothing at all, and nothing in validation will say so.

### Known outcomes are per capability, not a global catalogue

`BusinessOutcomeSpec` hangs off the Capability. "Member not found" is only meaningful for a
flow that looks a member up, and the signal that detects it is specific to one screen in one
application. A global registry would have to be qualified by app and screen anyway, which is
the same information with an extra lookup in front of it. Keeping them local is also what
makes the artifact self-describing: one file tells a calling agent every legitimate answer it
can receive, with no second document to consult.

Rejected: a global outcome catalogue keyed by code, with capabilities referencing codes.
That guarantees `member_not_found` means the same thing everywhere, makes reporting across
capabilities trivial, and stops the same detection signal being written out repeatedly.

Known weakness: duplication and drift, which is exactly what the catalogue would have
prevented. Ten capabilities against the same application will each redeclare
`member_not_found` with their own detect signal, nothing forces those signals to agree, and
when the application changes its wording they will fall out of sync one at a time rather than
all at once. Codes are only conventionally stable too: validation enforces snake_case and
uniqueness within a single capability and nothing beyond that. If the catalogue becomes
necessary the migration is mechanical, hoisting shared codes out and leaving per-capability
detect signals behind as overrides.

## 0004. Anything knowable at record time is rejected at record time

Phase 2 correction.

The validators added across phase 2 share one principle, and it is worth stating on its own
because it is what decides whether a rule belongs in the schema at all: if a defect can be
detected from the artifact alone, the schema refuses the artifact rather than leaving the
executor to discover it. A regex is compiled when the capability is constructed. A business
outcome that says it checks after step 9 is rejected when only six steps exist. A recovery
scoped to a step index nobody declared is rejected the same way. So is a required input
nothing references, a risky step with nothing to prove it happened, and a sensitive parameter
carrying an example value.

The reason is the approval model, not tidiness. A capability moves from draft to approved
because a person read it and signed it off, and from then on it replays unattended. An
artifact that passes that review and then fails the first time the executor reaches step four
has broken the thing approval was supposed to buy: the reviewer had no way to catch the
defect, and the failure surfaces in production against a live banking system rather than at a
desk. Every rule moved earlier turns a production incident into a construction error.

Rejected: validate lazily, and let the replay engine report a bad pattern or a dangling step
index as an ordinary Failure result. It is less schema code, the executor already has to
handle runtime errors so the path exists anyway, and it keeps the models closer to plain data.
It was rejected because it puts the cost in the worst possible place. A Failure at replay time
is expensive to diagnose, arrives with a half-finished flow behind it, and for an
irreversible step may arrive after the damage is done, while the same defect at record time
costs one line of output and no side effects at all.

Known weakness, three of them and the second is the sharpest.

First, the principle has a hard ceiling. It catches internal inconsistency only, never
divergence between the artifact and the live surface. A locator naming a control that was
renamed last Tuesday is perfectly valid to this schema and will still fail at replay. Record
time validation is not a substitute for the fingerprint and drift work, it is a different
guarantee that happens to look similar.

Second, the published JSON Schema cannot express most of these rules, and re-exporting after
adding regex compilation produced a byte identical file. `AfterValidator` and the cross field
model validators are runtime constraints with no JSON Schema equivalent, so anything
validating an artifact against `schemas/capability.schema.json` alone will accept artifacts
that Pydantic rejects. The exported schema is a documentation and codegen aid; the Python
model is the enforcement boundary, and any consumer that needs the real contract has to go
through the model rather than the schema file.

Third, there is a coverage gap in this pass that is worth naming rather than quietly
carrying: `ParamSpec.pattern` is also a user supplied regex and is not yet compiled at record
time, because only `Signal.pattern`, `Signal.url_pattern` and `ExtractionSpec.strip_pattern`
were in scope. The same principle applies to it and it should get the same treatment.

## 0005. A structural signal, added before anything needs it

Phase 2 revision.

`SignalKind.aria_matches` and `Signal.aria_template` let a signal assert the shape of a screen
rather than the presence of a string, and `SurfaceFingerprint.aria_template` records that
shape at recording time. A text assertion answers "does this string appear somewhere". A
structural assertion answers "is this the screen I recorded". Those are different questions,
and for a checkpoint the second is the one actually being asked: the words "Sub-Account
Opened" sitting in a hidden template, or in a breadcrumb, satisfy `text_present` while the
flow is in fact nowhere near the confirmation screen. A heading with that name, in a tree
with that shape, does not have the same failure mode. The fingerprint gets one for the same
reason on a longer timescale: a tenant renaming a button is a text difference, but a tenant
inserting an extra confirmation step is a shape difference, and only the second is detectable
without diffing every string on the page.

The timing is the part that needs defending, because nothing consumes this field yet. The
answer is that the cost of adding it is a step function and we are on the cheap side of it for
exactly one more phase. `Capability` is pinned at `schema_version` 1.0, `capabilities/` is
empty, and no discovery run has ever written an artifact, so today this is a field appearing
in a schema nobody has serialized against. The moment the recorder writes the first capability
the same edit needs a version bump, a migration, and a compatibility story for artifacts that
have already been reviewed and approved. Doing it now costs one commit. Doing it in three
phases costs all of that plus the temptation to skip it.

Rejected: wait until the recorder actually needs it, then bump to 1.1. This is ordinary YAGNI
and it is usually the right call, with a real argument behind it: a field designed before its
consumer exists is a field designed from imagination, and it will probably be the wrong shape.
It was rejected because the penalty is asymmetric. A wrongly shaped field on a schema nothing
has serialized is a free edit, while a correctly shaped field added after approved artifacts
exist is a migration. When one branch is cheap to undo and the other is not, guessing early is
the better bet.

Known weakness, two.

The first is the one that decides where this may be used. An aria template is far more brittle
to benign markup change than a text assertion is. Wrapping a heading in a div, or a framework
upgrade that adds a generic container, leaves `text_present` completely unmoved and can change
the snapshot's shape. So `aria_matches` belongs on capability checkpoints and on surface
fingerprints, where "is this the screen I recorded" is genuinely the question, and not on
ordinary step postconditions, where it would convert every cosmetic change into a replay
failure. Nothing in the schema enforces that placement. It is a convention, and validation
will cheerfully accept an `aria_matches` postcondition on all six steps of a flow.

The second was an honest contradiction with 0004, and it has since been narrowed rather than
closed. `aria_template` is now parsed with `yaml.safe_load` during schema validation, so a
template that is not well formed YAML is rejected at record time like every other defect that
is knowable from the artifact alone. What remains uncovered is semantics: a template can parse
perfectly and still describe a shape no screen will ever have, and that is only discovered when
replay tries to match it. The line falls there because Playwright exposes no public parser for
the aria template dialect, so syntax is checkable with an ordinary YAML parser while validity
is not checkable without reimplementing their matcher. The error message says which of the two
it checked, so nobody reads a clean construction as proof the template is correct.

## 0006. Where describe() lives, and why XPath here is not a CSS fallback

Phase 3.

### describe() belongs to the surface, not to the recorder

Turning a per-snapshot ref into a durable `LocatorBundle` is a method on `Surface`. Two things
force it there. It needs the observation that produced the ref, and it needs the live page, in
order to verify that each candidate tier actually resolves to exactly one element right now.
A recorder handed a transcript afterwards has neither: the refs are dead the moment the next
snapshot is taken, and the page has moved on. Invariant 9 says the conversion happens at the
moment of the action, and the only component holding the session at that moment is the surface.
Putting it there also splits the work along the right line for section 3.7: a desktop surface
reimplements `describe()` against the UI Automation tree with its own notion of a container,
while the recorder stays surface agnostic and simply collects whatever bundles it is handed.

Rejected: keep the surface a thin driver and do the interpretation in the recorder. On paper
that is cleaner layering, I/O on one side and meaning on the other. It was rejected because the
recorder would then need a live handle back into the surface to verify its candidates, which is
the same coupling with an extra hop, or it would have to emit unverified bundles. The second is
worse than it sounds: a bundle that has never successfully resolved even once is not a locator,
it is a guess that will be discovered wrong during replay rather than during recording, which is
exactly the ordering DECISIONS.md 0004 exists to prevent.

Known weakness: the surface now does two jobs, perceiving and interpreting, and `web.py` is 394
lines against the 300 line convention in section 9. The tier logic is also only notionally
shared: a second surface reimplements all four tiers rather than reusing them. If a third
surface ever appears, tier selection should be lifted into a shared component that takes an
`Observation` and returns candidate specs, leaving each surface responsible only for verifying
them. That refactor is cheap now and gets expensive once two surfaces have diverged.

### A semantic relation expressed in XPath is not the same thing as a CSS fallback

Tiers 2 and 3 compile to XPath. That does not make them brittle in the way tier 4 is, and the
difference is what the expression names rather than which syntax it uses.

Tier 2 compiles to `//tr[./*[normalize-space(.)="Nickname"]]` and then asks `get_by_role` for
the textbox inside it. What that names is a relationship a person would say out loud: the field
in the row labelled Nickname. Rename the element id, restyle the table, replace the input with a
different widget carrying the same role, and it still resolves. Tier 4 compiles to
`#ctl00_ContentPlaceHolder1_txtNickname`, which names one element by one attribute that exists
for no reason except that a framework generated it. Both are strings in a selector argument.
Only one survives the page being rebuilt.

This matters beyond pedantry because the winning tier is recorded on every run as drift
telemetry. If XPath were classed as brittle alongside CSS, every tier 2 resolution would report
degradation, and a signal that fires constantly is a signal nobody reads.

Rejected: build tiers 2 and 3 out of Playwright's own `filter` and `has` chaining and avoid raw
selector strings altogether. This was genuinely attractive and `locator("table").filter(...)`
reads better than an axis expression. It was rejected on evidence: against this app,
`filter(has_text="Deposit Accounts")` matched two tables, because an ancestor table contains the
text as well, while `ancestor::table[1]` matched exactly one. Expressing "the nearest enclosing
region" needs an axis, and the locator API has no axis.

Known weakness: XPath 1.0 is the weakest link in the chain. It has no escape character, so
quoting goes through a `concat()` helper. `normalize-space(.)` on a row matches concatenated
descendant text and will match more broadly on a denser page than it does here. And the
expression names a `tr`, which is HTML specific, so tier 2 is semantic in intent but HTML shaped
in implementation. That seam is precisely where a desktop surface will need its own code rather
than a shared one.

### A conflict this phase surfaced, since resolved in 0007

An element whose only available locator is CSS cannot currently be recorded at all. The one real
example is the navigation control implemented as a span with an inline onclick: it has no ARIA
role, so the aria snapshot reports it only as a bare text node, and `get_by_role` cannot see it.
Tiers 1 to 3 are all role based, so all three fail, leaving tier 4 alone. But `LocatorBundle`
refuses a `css_fallback` primary, so no bundle can be built.

Both rules are deliberate. The schema forbids a brittle primary so an approved artifact never
rests on a generated DOM id. Tier 4 exists because controls like this one are real. They
collide, and invariant 1 says code adapts to the schema rather than the other way round, so
`describe()` raises `LocatorUnresolved` with the reason and the conflict is recorded here
instead of being settled by quietly editing a validator. Three ways out for whoever takes it:
permit a brittle primary when the bundle records why no other tier applied, fix the control in
the target application, which in the real environment means asking a vendor and waiting, or add
a text relation tier that can address role-less elements by their visible text and their
position relative to a named neighbour. The third is probably the right answer, and it is a
schema change, which is why it is a proposal here rather than a commit.

**Resolved in 0007.** The third option was taken.

## 0007. A text relation tier, resolving the conflict left open in 0006

Phase 3 correction.

`TextRelationLocator` addresses a control by its visible text, optionally scoped to a named
container, and it is permitted as a bundle primary. The tier order is now role_name,
label_relation, container_ordinal, text_relation, css_fallback, and `css_fallback` is the only
strategy still forbidden as a primary. The onclick span that could not be described at all now
describes as a text_relation primary and resolves back to the same element.

Visible text was chosen over the two alternatives 0006 listed. Permitting a brittle primary
would have meant approved artifacts resting on `#ctl00_ContentPlaceHolder1_lnkOpenSub`, a string
that exists only because a framework generated it and that changes when someone reorders a
content placeholder; the schema forbids that for good reason and bending the rule for one
awkward control is how such rules stop meaning anything. Fixing the target application is the
right answer in a repository you own and is not available in the environment this stands in
for, where the application belongs to a vendor and the answer to "please add a role attribute"
is a support ticket and a release cycle. Visible text is the only remaining handle that a human
operator would actually use, which is the same standard the other tiers are held to.

It sits below container_ordinal rather than above it, and that ordering is the part worth
defending. Text is the most human-legible handle and also the least structural one. A role plus
an accessible name is a contract the application makes with assistive technology; a container
plus an ordinal is a statement about layout that survives copy changes entirely. Visible text
survives neither a rewording nor a translation. So text goes below anything structural and
above only the CSS tier, and it is reached exactly when the accessibility tree has nothing to
offer. Ordering it above container_ordinal would have meant preferring the more fragile handle
whenever both applied, which is the wrong default even though text reads better in a diff.

Rejected: a dedicated `role_missing` tier that matched on text plus the element's position
among its siblings, which would survive a rewording. It was rejected as premature. It needs a
sibling index, which is the same brittleness as an ordinal without the container to anchor it,
and there is exactly one control in the target application that needs this tier at all. One
example is not enough evidence to design a compound strategy around.

Known weakness: text is precisely what tenant rebranding changes. Section 1 of the brief
describes hundreds of institutions running the same vendor product "configured, branded, and
versioned differently", and relabelling controls is the most common thing such configuration
does. A bundle whose primary is text_relation is therefore the single most likely kind of
bundle to need a per-variant override, and worse, it will fail in the quietest way: the control
is still there, still in the same place, still doing the same thing, and the locator no longer
matches because someone changed "Open Sub-Account" to "New Sub-Account". Two mitigations exist
and neither is built yet. `VariantOverride.step_overrides` can already carry a replacement
bundle per tenant, which handles it once discovered. And because the winning tier is recorded
on every run, a fleet-wide report of text_relation primaries is the natural place to look first
when a tenant upgrade breaks a batch of capabilities.

## 0008. Gemini through the Interactions API, run statelessly

Phase 4, step 1. The provider switch from Anthropic to Gemini was directed rather than chosen,
so what is recorded here is the part that was actually a decision: which of Gemini's two
surfaces to build against, and how to use it.

Checked against the current documentation rather than from memory, because this changed
recently. `client.models.generate_content` still exists, but its function calling guide is now
published under a heading marked Legacy, while the Interactions API went generally available in
June 2026 and is documented as the recommended path for new projects. Building a new client
against the surface Google labels legacy would be a decision that needs defending in six months
and cannot be.

The part worth arguing is that the Interactions API is stateful by default and this client does
not use that. Interactions can hold conversation state server side and be continued with
`previous_interaction_id`. We pass `store=False` and send the whole transcript in `input` on
every turn. Three reasons, in order of weight. The recorder compiles the transcript into a
Capability, so the transcript has to be a local object we own rather than a handle to something
held elsewhere. Evidence has to be reproducible and archivable into `evidence/`, and half a run
living on a vendor's server is not evidence we can ship. And the `ModelClient` protocol is
deliberately stateless, `complete(system, messages, tools)`, which is what lets `ScriptedClient`
substitute for the real one so completely that the loop cannot tell them apart; a server side
conversation graph does not have a scriptable equivalent.

Rejected: server side state with `previous_interaction_id`. It sends less over the wire on every
turn, which on a twenty step run with a growing observation history is not a small saving, and
it is the mode the API is designed around. It was rejected because it trades an artifact we own
for a handle we do not, and this whole project is about producing an artifact.

Also rejected, and explicitly ruled out by the brief for this step: the OpenAI compatibility
endpoint. It would have made the client shape more familiar and is a dead end for function
calling fidelity.

Known weakness: the translation in `to_input_payload` and `to_model_turn` is written against
the SDK's own type definitions, which were read directly, but it has never made a live call.
There is no key in this environment and no test here touches a network, so the request shape is
asserted and the round trip is not. The first real discovery run is where that gets tested, and
it is the most likely thing in this module to need a correction. The translation functions are
module level and pure precisely so that the correction is a small one.

## 0009. The error screens are inside the allowlist

Phase 4, step 1.

`config/policy.json` permits `/maintenance`, `/maintenance/continue` and `/session-expired`,
which looks wrong at a glance: every one of them is a failure state, and an allowlist that
includes failure states reads like an allowlist that has stopped discriminating.

It is the opposite. The interstitial recovery in the artifact schema works by arriving at the
maintenance notice and clicking Continue. If those paths were denied, the gate would block the
system's own recovery: the run would land on the maintenance screen, the arrival check would
refuse the URL it had just landed on, and a condition classified as recoverable would be
converted into a `PolicyViolation`. The same holds for the session expired screen, which the
escalation path needs to reach in order to hand a human a session worth repairing. Denying a
path you will predictably land on does not stop you landing there. It only removes your ability
to do anything once you have.

The principle underneath, and the one to apply when this list grows: the allowlist describes
where the agent may legitimately BE, not where things are going well.

Rejected: deny the error paths and special case the recovery, letting the gate be bypassed for
a known set of recovery navigations. That keeps the allowlist looking pure. It was rejected
because a bypass is a hole, and a hole with a good reason attached is still the thing an
attacker or a confused model looks for. Invariant 3 says the gate is the constraint that cannot
be argued past, and a gate with a documented exception list is a gate that can be.

Known weakness: the allowlist is now a mix of two categories, ordinary application routes and
screens that only appear when something has gone wrong, with nothing in the file marking which
is which. A reader six months from now sees one flat list and cannot tell that
`/session-expired` is load bearing for escalation rather than a leftover. A `reason` field per
pattern would fix it and would also make the file self documenting, at the cost of no longer
being a direct `PolicyConfig` dump, which is what currently gives validation at load for free.

## 0010. No wait tool is offered to the model

Phase 4, step 2.

The model gets eight tools and none of them waits. There is no `wait`, no `sleep`, no
`wait_for_text`. This is deliberate and the prompt says so in as many words, because a model
that has been trained on browser automation will expect one and will otherwise invent reasons
its absence is a problem.

Waiting is the surface's job and it is already done there, bounded and configurable.
`resolve()` retries every tier against a shared budget before it will believe that nothing
matched, and `act()` settles the load state before returning. By the time a tool result comes
back, the page has settled and any control that was going to appear has appeared. So a wait
tool would not add a capability, it would duplicate one that already exists a layer down,
where it is enforced rather than requested.

The reason to actively withhold it rather than merely not need it is that it is an attractive
nuisance. A discovery run has a step budget. A model that is uncertain what it is looking at
has an obvious escape hatch in waiting, and waiting always appears to be progress: it is
cheap, it never errors, and it postpones the decision. The failure mode is a run that spends
eight of its twenty steps waiting and then hits max_steps without ever having tried the second
route. Removing the tool forces the uncertainty to resolve into either look or a different
action, both of which produce information.

Rejected: expose a bounded wait, capped at a couple of seconds, on the grounds that the model
sometimes knows something the surface cannot, such as a progress spinner it can see in the
snapshot. That is a real case. It was rejected because the same information is better used
differently: seeing a spinner should make the model call look again, which costs the same
turn and returns an actual observation rather than a delay. The tool would have been a worse
version of one we already have.

Known weakness: this holds only while the surface's waiting stays correct and bounded. If a
future surface has a genuinely asynchronous update that `resolve()` cannot see, because
nothing changes in the accessibility tree until a websocket delivers, then the model will have
no recourse at all and will look, see the same screen, and eventually give up. The honest fix
at that point is to extend the surface's wait conditions, not to hand the problem to the
model. This entry is where to start reading if that day comes.

## 0011. The model points at refs, it never authors a locator

Phase 4, step 2.

No tool schema exposed to the model contains a LocatorBundle, or any field that could carry
one, even though the artifact models the schemas are derived from are full of them.
`finish` takes a `ref` where OutputSpec takes an extraction locator, and its checkpoint offers
only the four Signal kinds that need no locator. The model says which element it means by
ref; `describe()` converts that ref into a durable bundle, choosing the tier and verifying it
resolves, before anything is recorded.

The reason is where the decision then lives. Locator tier selection is the load bearing
judgment in this whole system: it decides whether a recording still works next month, and it
is the thing the evaluation criteria name first. If the model authors locators, that judgment
moves into the prompt, where it is a request that a model may ignore, misunderstand, or
cheerfully hallucinate a plausible looking CSS selector for. Keeping it in `describe()` puts
it in Python, where every candidate is checked against the live page before it is written
down. It is the same argument invariant 3 makes about the policy gate, applied to locators.

Rejected: let the model propose a locator and have the surface validate it, refusing anything
that does not resolve uniquely. This is not a bad design. It would let the model contribute
its reading of the page, which is genuinely useful for controls whose best handle is not
obvious. It was rejected on the cost of the failure case: a proposal that validates is not the
same as a proposal that is durable, and the model has no way to know that
`#ctl00_ContentPlaceHolder1_lnkOpenSub` resolves today and rots next release, while
`describe()` knows because tier order encodes exactly that.

Known weakness: the model can still point at the wrong element, and nothing here catches that.
A ref names one node in a snapshot the model may have misread, and `describe()` will faithfully
build a perfect durable locator for the wrong control. The tier machinery guarantees that
whatever was pointed at can be found again, not that it was the right thing. What catches that
is the checkpoint, which is also model authored, so a run can be confidently and consistently
wrong end to end. Phase 9 is where that gets tested rather than argued, and it is the reason
evidence is worth more than any assertion in this file.

## 0012. What the loop tells the model, and what it withholds

Phase 4, step 3.

Two decisions about the model's context window, both of which come down to the same thing:
what the model is given shapes what it spends its steps on.

### A refusal names the rule and nothing else

When the policy gate refuses an action the model is told that the action was refused, which
rule refused it, that the refusal is final, and that the direction is closed. It is not told
which pattern matched, which path was denied, or anything about the shape of the allowlist.
`PolicyViolation` carries `rule` and `reason` as separate attributes precisely so the loop can
pass one and drop the other, and there is a test asserting the pattern never appears in a
tool_result.

The reason is that a model given the boundary will explore the boundary. Told that
`/dev/faults` matched `^/dev(/.*)?$`, a capable model will quite reasonably try `/dev` without
a slash, or a redirect, or a path that reaches the same page by another route. None of that is
malice, it is the model doing what it was asked to do with the information it has, and every
one of those attempts is a wasted step and another blocked action. Naming the rule is enough
to make the refusal legible without making it negotiable.

Rejected: say nothing at all beyond "refused". It leaks less. It was rejected because a bare
refusal is indistinguishable from a transient error, and the model's correct response to a
transient error is to retry, which is exactly the behaviour the three consecutive block limit
exists to catch. Telling it the refusal is final is what converts a wasted run into a
redirected one.

Known weakness: the rule id is itself a small leak. `denied_path_patterns` tells a model that
paths are what got refused, which narrows the search if it chooses to search. The alternative
was an opaque code, which would have made every operator debugging a run go and look the code
up. That trade favours the operator, who reads these far more often than a model probes them.

### Only the two most recent snapshots are carried in full

Every action stays in history for the whole run. Observations do not: the two most recent are
carried in full and everything older collapses to a line naming the page and its URL.

An aria snapshot of this application is around five thousand characters. Twenty of them would
be a hundred thousand characters of mostly dead screens, and the things that actually carry
the reasoning, which are what the model did and what came back, would be a rounding error
inside them. Two is the smallest number that still lets the model compare the screen before an
action with the screen after it, which is the comparison that tells it whether the action
worked.

Rejected: summarize old observations with the model itself, or keep a rolling digest of what
has been seen. Both are more informative than a URL. Both were rejected as a second place for
the run to go wrong: a summarizer is another model call that can be wrong, can fail, and costs
a request against a rate limit that a twenty step run is already brushing.

Known weakness: a flow long enough that the relevant screen fell out of the window is a flow
this loop will handle badly. If the model needs to remember what was on a form eight steps ago
it cannot, and its only recourse is to navigate back and look, which costs two steps and may
not be possible after an irreversible action. Nothing here detects that situation; it would
show up as a run that gives up for no visible reason. The fix, if it happens, is to let
finish-relevant details be written down as they are seen rather than to widen the window.

## 0013. finish is verified, never trusted

Phase 4, step 4.

When the model calls finish it is making two claims, and neither is accepted on its word. The
checkpoint is parsed into a Signal, which compiles any regex, and then evaluated against the
live page. Every declared output has its ref converted to a durable locator and its extraction
actually executed. Only if all of that succeeds does a SuccessResult exist.

The checkpoint case is the obvious one. A checkpoint is asserted on every future replay of the
capability, unattended, as the sole test of whether the run worked. A checkpoint that has never
once held is not a weak assertion, it is a guess, and it will be wrong in exactly the same way
every time it runs. Checking it costs one evaluation against the page already on screen.

Verifying the extractions is the part worth arguing for, because it is easy to leave out. An
output is a promise about what the caller receives. A declared output that cannot be read from
the very page it was declared on is broken before replay has run once, and the failure will
surface later, in production, as a capability that reports success and returns nothing. Running
the extraction here converts that into a sentence the model is told immediately, while it is
still looking at the screen and can point somewhere better.

Rejected: trust finish and let phase 6 discover the problem on the first replay. It is less
code here and replay has to handle extraction failure anyway. It was rejected because the two
failures are not the same size. At discovery the cost is one retry. At replay the capability
has been reviewed, approved and invoked by an agent against a live banking system, and the
bad news arrives with the run half done.

Known weakness: verification proves the checkpoint holds NOW, not that it discriminates. A
model that picks a string present on every page in the application, such as the footer, gets a
checkpoint that passes here and passes on every future replay regardless of where the flow
ended up. Nothing detects that. The honest fix is to evaluate a candidate checkpoint against an
earlier observation as well and reject it if it held there too, which is a phase 9 job.

## 0014. The model declares, the schema polices

Phase 4, step 4.

Inputs and outputs are declared by the model at finish, not inferred from the run. The model
is the only participant that knows which of the values it typed came from the goal and which
it read off the screen, so inference would be guesswork. What keeps that honest is that every
declaration passes through the same Pydantic validators the artifact schema uses: names must be
snake_case, a parameter marked pii cannot carry an example, a required input nothing references
is rejected, and an output must actually extract.

The division is deliberate. The model supplies intent, which it alone has. The schema supplies
constraint, which it enforces identically every time and cannot be talked out of. Neither is
asked to do the other's job.

Rejected: infer parameters by diffing typed values against the goal text. It needs no
cooperation from the model and cannot be lied to. It was rejected because the mapping is
genuinely ambiguous: a member id typed into a field might be a parameter, or it might be a
constant the flow always uses, and the two are indistinguishable from the outside. A wrong
inference produces a capability that is silently hardwired or silently over-parameterized. The
diff still runs, as a warning in the transcript, which is the right weight for a heuristic.

Known weakness, and this is the sharp one. A parameter that is genuinely used but wrongly
typed passes every validator in the system. Declare `initial_deposit` as `string` when it is a
currency, or `member_id` as `integer` when the application zero pads it, and nothing objects:
the name is valid, it is referenced by a step, it is not sensitive, and the run succeeded. The
type is a promise to the caller about what to pass and what comes back, and it is checked by
nothing at discovery time. It surfaces on the first replay with a real value, as a validation
error on a screen nobody expected, or worse as a lookup that silently finds the wrong record.
Closing it means replaying the capability with a second set of inputs before approving it,
which is what the draft to approved gate in the stretch goals is actually for.

## 0015. A blocked action is fed back, not fatal

Phase 4, step 4.

A refused action returns a tool_result telling the model the direction is closed. The run ends
only after three CONSECUTIVE refusals, and the counter resets on any action that succeeds.

A single block is usually the model being reasonable and wrong. It sees a link to the fault
console, or tries a URL it guessed from a pattern, and policy says no. That is the guardrail
working exactly as intended, and it carries information the model can use: this route is
closed, take another. Ending the run there would throw away a discovery that is otherwise
going fine, and would make the policy gate look like a failure mode rather than a boundary.

Three consecutive is the signal that something else is happening: the model has decided the
blocked route is the only route and is now trying variations of it. That is not progress and
more turns will not produce any, so the run stops with PolicyBlockedResult naming the rule.
Consecutive rather than cumulative matters, because a run that hits an early dead end, backs
out and completes the goal is a successful run, and counting its one block against it forever
would be wrong.

Rejected: terminate on the first block, on the grounds that anything touching a boundary is
suspect and a person should look. It is defensible in a stricter setting. It was rejected
because it makes the guardrail expensive to have: every over-cautious allowlist entry becomes
an abandoned run, and the pressure that creates is to loosen the allowlist, which is the
opposite of what anyone wants.

Known weakness: three consecutive is a heuristic with no evidence behind it. It is small
enough to catch a loop quickly and large enough to survive two honest mistakes, which is a
judgement rather than a measurement. It is also per run rather than per rule, so three
different rules refusing once each looks identical to one rule refusing three times, when the
first is much more likely to be a confused model and the second a determined one.

## 0016. Gemini, chosen on cost, made cheap to reverse by the protocol

Phase 4, step 4. See also 0008, which records the API surface within Gemini.

The provider moved from Anthropic to Gemini for cost. Gemini has a usable free tier, this is a
take home rather than a funded system, and a discovery loop that burns twenty multimodal turns
per run makes that difference concrete rather than theoretical.

What made it a cheap decision to make, and the reason it is recorded as a decision at all, is
that the `ModelClient` protocol had already confined every provider shape to one module. The
switch touched `client.py` and nothing else: no change to the loop, the tools, the transcript,
the prompt or any test, because none of them had ever seen a provider type. The protocol was
not built in anticipation of this, it was built so `ScriptedClient` could substitute for a real
model, and the portability fell out of it. That is the argument for the seam, and it is worth
more than the argument for either provider.

Rejected: staying on Anthropic and accepting the cost, or building an abstraction over both
and choosing at runtime. The first is a real option and would have meant no work at all. The
second was rejected outright as the kind of premature generality design rules section 8 warns
about: two providers behind one interface, with one of them never exercised, is an interface
designed from imagination.

Known weakness: a free tier model is a weaker model. It is likelier to need more turns to
reach the same goal, likelier to misread a dense table, and likelier to point at the wrong ref,
which `describe()` will faithfully convert into a perfect locator for the wrong control. The
practical consequence is that `max_steps` at 25 may prove too tight and need raising, and that
a run failing is weaker evidence about the system than it would be with a stronger model. The
rate limit compounds it: the free tier sits near ten requests a minute, which is why the client
retries 429 with backoff, and a long run pauses rather than fails.

## 0017. The redactor cannot see inside a screenshot

Phase 4, step 4.

Every text write in the evidence writer is serialized first and redacted second, on the
serialized string rather than on the object, so a sensitive value cannot survive in a field
nobody remembered to redact. Screenshots bypass that entirely, because a PNG is bytes and the
redactor reads text.

This is a real hole in invariant 6 and it is stated here rather than left to be discovered. A
screenshot of the member detail screen contains the member name, the account numbers and the
balances, rendered as pixels, written to disk unmodified. In this project the data is
fabricated, so the cost is zero. In the environment this stands in for, evidence directories
would be full of regulated financial data in a form no redactor can touch.

The options, none of which are built: do not capture screenshots at all, which loses the
richer failure signal the brief asks for; capture them and treat the evidence directory as
regulated data with the access controls that implies, which is what a real deployment would
have to do anyway; or redact the image before writing it, by blanking the boxes of elements
whose values came from parameters marked pii, which the Observation already carries the
geometry for. The third is genuinely feasible here and is the interesting one, because
`ObservedElement.box` exists precisely because bounding boxes were recorded. It is phase 8 work
and is noted there rather than done here.
