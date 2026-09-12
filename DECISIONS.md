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

## 0019. The recorder carries locator bundles through unchanged

Phase 5.

`compile` copies `ActionRecord.bundle` into the compiled `Step` verbatim. It does not re-rank
the tiers, does not re-run `describe`, and does not try to improve anything.

The reason is that a bundle is a claim about one page at one instant, and it was verified at
that instant: `describe` checked every candidate tier against the live page before the bundle
existed at all. By the time the recorder runs, that page is gone. Re-deriving would mean
deriving against whatever the browser happens to be showing now, or worse against no page at
all, and the result would be a locator that has never been checked against anything. The one
moment a locator can be known to be true is the moment it is built, which is why invariant 9
puts the conversion at the action rather than at the recording.

Rejected: re-derive at compile time so the bundle reflects the final state of the page, which
would let a later step's locator benefit from the page having settled. It was rejected because
it inverts the guarantee. A bundle built during the action is one that resolved uniquely then;
a bundle built afterwards is a guess dressed as a recording, and nothing downstream could tell
the two apart.

Known weakness: the bundle is only as good as the moment it was captured, and the recorder has
no way to notice a bad one. If `describe` picked a locator that happened to be unique on that
render but is not stable, for instance the balance cell in the real recorded run, whose
`role_name` primary is the balance figure itself, the recorder copies that mistake through
faithfully. Nothing here inspects a bundle for whether it names something that will still be
true next month. That is a real gap and the sharpest one in this phase.

## 0020. An unmatched declared input is a compile error, not a warning

Phase 5.

If a declared input cannot be matched to any recorded literal, compilation fails with
`INPUT_MATCHES_NO_LITERAL` rather than emitting a warning and continuing.

An input is a promise to the caller: supply this and it will be used. An input the flow never
consumes is a false promise, and a caller passing a member id to a capability that ignores it
gets a confident result computed from whatever was baked in at record time. That is worse than
an error, because it looks like success.

The schema would catch it anyway. Cross field validator 7 rejects a required input that no
step references, so the artifact could never be constructed regardless. Failing here rather
than there is purely about the message: the validator can say only that an input is
unreferenced, while the compiler knows why and can say that no recorded step used a value at
all, or that two inputs could not be told apart.

Rejected: warn and drop the unmatched input, producing a valid capability with one fewer
parameter. Tempting because it always yields an artifact. Rejected because it silently changes
the contract the model declared, and the person reading the capability later would have no way
to know an input had been removed on their behalf.

Known weakness: the matching itself is inference, not fact. The model types a value and never
says which parameter it came from, so the recorder reconstructs the mapping from the example
field first and the goal text second. Where that leaves a real choice it refuses, which means
a legitimate two parameter flow whose values do not appear in the goal will fail to compile
until someone adds examples. That is the right way round, but it is a cost.

## 0021. known_outcomes is left empty rather than populated with plausible defaults

Phase 5.

The compiled capability declares no business outcomes at all, and the compile report says so.

The target application has three real ones: no member found, permission denied, and validation
rejected. It would be easy, and would look thorough, to add them to every capability compiled
against this app. It would also be fabrication. The run being compiled never encountered any
of them, so nothing in the transcript is evidence that the detection signal for any of them is
correct. A declared outcome carries a Signal that replay will evaluate on every future run, and
a signal nobody has ever seen match is the same kind of guess as a checkpoint that has never
held, which DECISIONS 0013 refuses for exactly this reason.

design rules section 10 also says evidence is produced by real runs and never generated. An
outcome declaration is a claim about what the application does; inventing one is generating
evidence with extra steps.

Rejected: seed the outcomes from the target app's known failure screens, since we wrote the app
and know them. Rejected because it does not generalize past the one application we happen to
have written. The real environment is a vendor product nobody here has the source of, and a
recorder that only works when you already know every error screen is not a recorder.

What it costs, stated plainly: a capability compiled from one happy path will treat a
"no such member" screen as a checkpoint failure rather than as the business outcome it is.
That is the exact confusion invariant 5 exists to prevent, and this phase ships with it
present. The fix is more recordings, one per outcome, merged into the artifact, or a human
adding the declarations by hand. Both are phase 9 or later.

## 0022. status is always draft on first compile

Phase 5.

Every compiled capability comes out `draft`, and there is no argument that changes it.

A capability that has been compiled has run exactly once, forwards, with the model in the loop
making every decision. Nothing about that establishes it replays: the whole point of the
artifact is that replay is a different execution path, with no model, resolving locators from
recorded bundles rather than from a live snapshot. The first thing that could justify
`approved` is a successful replay, and phase 6 has not happened.

Rejected: mark it approved when the discovery run verified its own checkpoint and outputs,
since that verification did happen against the live page. It is a real signal and it is why
the artifact is worth having. It is not the same signal: verification proves the finish payload
described the page the model was standing on, not that a locator recorded mid-run resolves on
a fresh load, which is the thing that actually breaks.

Known weakness: nothing in this repo yet moves a capability from draft to approved, so the
field is currently write-once and decorative. It becomes load bearing the moment unattended
replay exists and has to refuse anything not approved, which is the confidence and approval
stretch goal.

## 0023. outcomes are evaluated before postconditions

Phase 6.

Each step is judged in a fixed order: the policy gate, then resolve and act, then recoveries,
then declared business outcomes, then the step postcondition. The order is the decision. The
part that matters is that a declared outcome is checked before the postcondition, not after.

A "no member record matches" screen fails the postcondition of the step that submitted the
search, and it fails the run checkpoint too. If the postcondition were asked first, every
not-found lookup would be reported as a checkpoint failure: exit 40, a human paged, a DOM
snapshot written, all to deliver an answer the system already had and could have returned in
milliseconds. That is invariant 5 violated in the most expensive direction, because the cost
lands on a person.

Recoveries come first for the same reason in a different shape. An interstitial that replaced
the page makes every subsequent question meaningless: the postcondition does not hold, no
outcome signal matches, and the truthful description of the state is "something got in the
way". Clearing it before anything is judged means the judgment is about the flow rather than
about the interruption.

Rejected: evaluate outcomes only after the last step, since that is where the run's answer
normally is. It reads cleanly and it is wrong. A not-found screen appears at step 3 of 4, and
the remaining step clicks a control that no longer exists. The run would fail with a locator
error at step 4 and the real answer, which was on screen one step earlier, would never be
reported. Outcomes are checked after every step because the flow can end early.

Known weakness: `check_after_step` is a lower bound, so an outcome declared for step 3 is also
tested at step 4. For terminal outcomes that is harmless, because the first match returns. For
a non-terminal outcome it would mean repeated evaluation, and nothing yet declares one.

## 0024. irreversible steps get zero retries

Phase 6.

`_retry_budget` returns 0 for any step marked `risky_irreversible`, whatever its WaitSpec asks
for. A timeout on such a step is never retried; it escalates as `needs_human`.

From outside the browser, a transient timeout and a completed action that simply did not
report back look identical. There is no observation that separates them, because the evidence
that would separate them is on the far side of the thing that timed out. Retrying resolves the
ambiguity in the direction that opens the account twice, transfers the funds twice, or files
the request twice. Escalating resolves it in the direction that costs a human two minutes.
The asymmetry is not close, so the choice is not close.

Rejected: read the page after the timeout and retry only if the action clearly did not land.
This is the tempting one, because usually it works. It fails exactly when it matters: the
page you would read is the page that was not responding, and a confirmation screen that is
slow to render is indistinguishable from one that will never render. A check that is reliable
except during the failure it exists to handle is not a check.

Rejected: make the retry budget configurable per step so an operator can opt in. The operator
opting in is not the person who eats a duplicated transfer, and a knob like this gets turned
during an incident, which is the worst possible moment to be making that trade.

Known weakness: a safe step can still be retried into a duplicate if it was misclassified at
record time. Risk classification comes from the policy's `risky_control_names` and the
recorder's judgment, and neither is infallible. The mitigation is that the same classification
also drives the approval gate, so a misclassification is visible in the artifact rather than
buried in engine behaviour.

## 0025. fingerprint mismatch is a hard stop

Phase 6.

If the recorded surface fingerprint does not match what pre-flight sees, the run stops before
step 0. It does not attempt the flow and report drift afterwards.

A fingerprint mismatch means the artifact is being replayed against something other than the
application it was recorded against. There are two ways that happens and both argue for
stopping. Either it is a different tenant's variant, in which case the correct move is to
select the override for that variant rather than run the base steps and hope, or the
application changed under the artifact, in which case the recorded locators describe a screen
that no longer exists. Continuing means executing a sequence of steps whose meaning is
unknown, against a real back-office system, on someone's real account.

The cost of stopping wrongly is a human confirming that a cosmetic change is cosmetic. The
cost of continuing wrongly is an action taken on the wrong screen. In a banking back office
those are not comparable.

Rejected: warn and continue, gated on how much of the fingerprint matched. A partial-match
threshold is a number nobody can defend. Two of three landmarks matching is not evidence that
the third is unimportant; it is more likely evidence that the page changed in the one place
the artifact was not looking.

Known weakness: the fingerprint is title, brand text, landmark signals and an aria template of
the chrome. Chrome is the part of a legacy app most likely to be reskinned and least likely to
change what the flow means, so this will produce false stops on a harmless rebrand. The
intended answer is a variant override recorded for the reskinned surface, which turns a false
stop into a declared difference. There is no automatic re-fingerprinting, deliberately: a
system that quietly updates its own drift detector no longer has one.

## 0026. draft capabilities do not replay unattended

Phase 6.

`check_approval` refuses to run anything still marked `draft` unless the caller passes
`--allow-draft`. The refusal happens before the browser is touched.

Per 0022, a compiled capability has been executed exactly once, forwards, with a model making
every decision from a live snapshot. Replay is a different execution path: no model, locators
resolved from recorded bundles against a page loaded fresh. Nothing about the discovery run
establishes that the second path works. The most common way it fails is the most boring one, a
locator that was unique in the state the model was standing in and is not unique on a clean
load, and the only thing that finds it is a replay.

So the flag is not ceremony. It marks the boundary between "this has been observed to work
once, in a mode that is not this mode" and "this has been observed to work in the mode you are
about to run it in".

Rejected: allow draft replay but downgrade the result to advisory. Results are consumed by
exit code, and an advisory success is exit 0. Anything reading the exit code, which is the
documented integration point, cannot see the caveat.

Known weakness: nothing in the repo promotes draft to approved. `--allow-draft` is how every
replay in this phase was run, including the ones in `evidence/`, so in practice the gate is
currently a speed bump for a human rather than a control on automation. Closing it means a
promotion path: N successful replays against a known surface, recorded on the artifact, and a
human signing the transition. That is the confidence and approval stretch goal, and it is not
in this submission.

## 0027. the member_not_found and member_restricted outcomes were added by hand

Phase 6.

Per 0021, the recorder cannot declare an outcome it never saw, and the discovery run only ever
saw member 100001, which exists. So the compiled 1.0.0 artifact shipped with `known_outcomes`
empty. Both outcomes now in the artifact were written by a human against the live screens:
`member_not_found` in 1.1.0, `member_restricted` and the interstitial recovery in 1.2.0.

`Provenance.human_edited` is set to true on both. That flag exists so a reviewer can tell,
without diffing against a transcript, which parts of an artifact a model actually observed and
which parts a person asserted. Those two have different failure modes and deserve different
levels of trust, and hiding the difference would make the provenance record decorative.

The weakness this introduced, stated when it was introduced: a hand-added detect signal is a
claim about a screen nobody re-checked. If the text does not match, the outcome never fires,
and the run reports a checkpoint failure instead. The declaration looks correct in the
artifact and does nothing at runtime, which is worse than an obvious error.

That weakness is now closed for both. `test_unknown_member_is_a_business_outcome_not_a_failure`
and `test_restricted_member_is_a_business_outcome_not_a_crash` drive real replays against the
live app and assert the outcome codes, and
`test_interstitial_is_dismissed_and_the_run_still_succeeds` arms the real fault and asserts the
recovery is named in `recoveries_applied`. Every hand-authored declaration in the artifact is
now exercised by a replay that would fail if the signal were wrong. The general point survives:
a hand-added declaration is unverified until something hits it, so it needs a test at the
moment it is written, not later.

## 0028. navigate steps store a path, not a URL

Phase 6.

A navigate step records `/member/{member_id}`. The host comes from `surface.base_url` at replay
time, and the engine joins them.

The recorder originally stored the observed URL whole, so the first artifact carried
`http://localhost:8080/`. That pins the capability to the machine that recorded it. The brief
asks for one artifact to run against the same application deployed differently, which is the
ordinary case in this domain: the same vendor console at a different host per credit union.
An absolute URL makes that impossible without hand-editing the artifact, which defeats the
point of having one.

Found by a replay test that repointed `base_url` at a random port and watched the run navigate
to port 8080 anyway. Fixing the recorder and recompiling from the stored transcript reproduced
the committed 1.0.0 byte for byte apart from that field, which is the check that the fix
changed one thing.

Known weakness: only the prefix is stripped, so a discovery run that wandered onto a different
host would still record an absolute URL for that step. The policy gate's `allowed_hosts` makes
that hard to reach rather than impossible, and nothing currently rejects an artifact whose
navigate step names a host.

## 0029. the control lease is a polled file

Phase 7.

Who is allowed to touch the browser is one small JSON file, written atomically and polled by
both sides. No queue, no socket, no database, no lock.

There are exactly two processes and exactly one mutable fact between them. A file holds that
fact, `os.replace` makes each write atomic, and both sides read it whenever they need to know.
That is the entire concurrency design, and it fits in one module you can read in a minute.

It is also more robust than the alternatives for this particular shape. Either side can be
restarted and the state survives, because the state is not in either process. A reviewer can
`cat` it. A test can assert on it without standing up a broker. And when the console is not
running at all, the run still holds a lease and invariant 10 still means something, which is
why `InProcessLease` exists: not a stub, but the same protocol with one participant.

Rejected: a socket or an HTTP call from the run to the console. It inverts the dependency,
so the run cannot start unless the console is already up, and it turns an operator restart
into a lost session. Rejected: a queue or a database. Section 8 names both as explicitly not
rewarded, and neither buys anything here. The polling interval is half a second against a
human who takes minutes.

No locking, deliberately. The transition table gives each state exactly one legal writer:
automation owns running and resuming, the operator owns paused and human_control. Two writers
never contend for the same transition, so a lock would guard a case the protocol has already
made illegal.

Known weakness: `deadline_at` is compared against wall clock time on whichever machine reads
it, so two machines with skewed clocks would disagree about expiry. Both sides are local here.

## 0030. the headed browser window is the live session

Phase 7.

The operator console shows the intervention, the screenshot, the accessibility snapshot and
the parameter names, and it moves the lease. It does not show the live page. The human works
in the Chromium window that automation already opened, with their own mouse and keyboard.

This is the cut, and it is a real one: there is no co-browsing, no WebRTC, no VNC, no
screencast. Section 8 rules those out by name, and the reason survives the rule. Streaming the
session is a large amount of infrastructure that demonstrates nothing about the thing being
assessed. What is being assessed is whether control can actually change hands safely: whether
automation stops, whether a human gets enough context to act, whether the same session is
handed over rather than a new one, and whether what the human did comes back as evidence. All
four of those are real here and none of them needs a pixel stream.

What is genuinely lost: the operator has to be at the machine running the browser. A remote
operator cannot use this. That is a deployment limitation, not a design one, and the seam it
would attach to is the lease, which is already the only thing the two sides share.

Consequence for the console: `POST /take` cannot install the page recorder, because the Flask
process holds no browser handle. Installation happens in `Session.escalate`, before the
handover, which is the only side of the boundary that has a page. The route only moves the
lease.

## 0031. resume always re-verifies, and never trusts the report

Phase 7.

When control comes back, automation re-observes the page and evaluates the current step's
postcondition, or the capability checkpoint if the step has none, before it looks at what the
operator said. `completed_manually` is refused outright if the page does not show the step as
done.

An operator's answer is a claim about a screen. The screen is right there. Checking costs one
observation and removes an entire class of failure where the run proceeds from a state nobody
established: the human meant to click Confirm and clicked Cancel, or fixed a different member's
record, or was interrupted halfway and came back thinking they had finished. In a back office
banking flow the step after a wrongly skipped one operates on the wrong screen.

There is a second reason, which is that a human who has had the session may have moved it
anywhere. They may have navigated away, opened another member, or left a modal open. Even for
`approved`, where nobody claims to have done anything, the page automation resumes onto is not
necessarily the page it paused on. So the re-observation is unconditional rather than tied to
one outcome.

Rejected: trust `completed_manually` and carry on, treating the operator as authoritative
because they are the human. It makes the system's correctness depend on a person's memory of
what they did several minutes ago in a UI they were fighting. The operator's note is kept, and
it is kept as a note: it appears in the failure message when the page disagrees, so the person
reading the failure can see both accounts of what happened.

Known weakness: verification is only as good as the declared postcondition. A step with no
postcondition falls back to the capability checkpoint, and a step with neither cannot be
verified at all, so `completed_manually` on such a step is refused rather than assumed. That is
strict, and it is the right direction to be strict in.

## 0032. retry_step is refused on an irreversible step

Phase 7.

The console does not offer `retry_step` when the step is `risky_irreversible`, and
`refuse_unsafe_outcome` rejects it even when the console is bypassed entirely.

This is 0024 with a human added, and the human makes it worse rather than better. Automation
timing out on an irreversible step cannot tell a slow response from a completed action. A
person who has been inside that session for five minutes has had every opportunity to click
the thing themselves, and may well have, possibly without remembering clearly. Re-performing
opens the account twice. The operator has three outcomes that are all safe: approve it and let
automation do it, say they did it and have that verified against the page, or abort.

Enforced in two places on purpose. Hiding the option in the UI is a courtesy to the operator.
The refusal in `refuse_unsafe_outcome` is the control, because a resolution file can be written
by hand, by a script, or by some future second console, and the rule has to live where the
resolution is read rather than where it is offered. There is a test for each.

Rejected: allow retry with a confirmation dialog. A dialog is a UI element, and the whole point
of invariant 3 is that constraints which matter live in Python rather than in a prompt or a
screen. Rejected: allow retry if the postcondition does not hold, on the grounds that the
action evidently did not happen. Tempting and wrong for the same reason as 0024: a
confirmation screen that is slow to render is indistinguishable from one that never will be,
and this is the case where the page cannot be trusted to be finished.

## 0033. what the human typed is never recorded

Phase 7.

The recorder injected before a handoff captures clicks, navigations, and which field changed.
It never captures the value that went into a field. The `change` handler reads the element's
identity and does not touch its value.

Invariant 6 does not stop applying because a person did the typing rather than a model. The
fields in this application take account numbers, member names and dollar amounts, and an
intervention file is a durable artifact on disk that outlives the run. A capture that included
values would be a second path to disk for exactly the data the redaction layer exists to keep
off it, and a quieter one, because nobody would think to point `--redact` at what a human typed.

Field identity is the part with audit value anyway. "The operator changed the nickname field
and then clicked Confirm" is what a reviewer needs. What the nickname was is on the screen, in
the after snapshot, which is a separate and deliberate capture.

Those two aria snapshots are the exception that proves the rule, and they are why the Session
now takes a Redactor. They show a real back office screen and therefore may show declared
sensitive values, so they go through the same redaction as evidence does. Without that,
`interventions/` would have been a second and quieter route past invariant 6 than `evidence/`.

Known weakness: redaction only replaces values the caller declared. A sensitive value nobody
declared reaches the snapshot, exactly as it would reach evidence. The structural protection is
in `params_redacted`, which is built from the capability's declared inputs rather than from the
params dict, so the code path that could leak a parameter value does not exist.

## 0034. the fingerprint check loads the entry screen before comparing

Phase 7, fixing phase 6.

`check_fingerprint` now navigates to the surface's entry path before it compares anything.

It used to observe immediately, which meant it observed `about:blank` on a fresh context. An
empty title matches no recorded title, so every capability with a fingerprint failed pre-flight,
and the only capabilities that passed were the ones whose fingerprint recorded nothing at all.
The one artifact in the repo at the time was in that second category, so the check passed
everywhere and had never once compared anything. A drift detector that cannot fire is worse
than no drift detector, because the passing pre-flight reads as evidence that the surface was
checked.

Found by hand authoring a capability that actually recorded a fingerprint, and watching it fail
pre-flight against the very application it was written against.

Known weakness: the title comparison is exact equality, and a page title is one of the more
volatile parts of a legacy app. The intended answer to a changed title is a variant override,
per 0025, rather than loosening the comparison to containment. Loosening it would make the
check pass on a page that merely shares a brand prefix, which is most of them.

## 0035. the operator is simulated on the thread that owns the browser

Phase 7.

The end to end handoff test drives the operator from inside `await_return`, through a Session
subclass, rather than from a second thread.

Playwright's sync API binds a page to the thread that created it. A second thread touching that
page raises before it does anything, so a human simulated in a worker thread cannot click
anything at all. The options were a second Playwright client attached over CDP, which means
launching Chromium with a debugging port purely for a test, or acting from inside the wait.

What the subclass gives up is timing realism: the operator acts at a defined moment rather than
whenever they get to it. What it keeps is everything the handoff is about. The lease moves
through the console's own HTTP routes, so the routes are under test rather than simulated. The
human's clicks go through the raw page and touch no LocatorBundle, no policy gate and no step
index, so they are genuinely not automation. And the test asserts the browser context and page
objects are identical before and after, which is invariant 7 checked rather than claimed.

Rejected: skip the end to end test and assert only on files. That would test the bookkeeping
and leave the actual claim, that control changes hands on one live session, unverified.

## 0036. failure/ is written for a business outcome too

Phase 8.

Any result that is not `success` gets a `failure/` directory, and that includes
`business_outcome`. The name is wrong for that case and the contents are not.

A not-found lookup is a correct answer that took a fifth of a second, and invariant 5 exists to
stop the system calling it a fault. Writing it into a folder called `failure/` cuts against
that, in the one place a reviewer looks. But the alternative loses something real: the screen
that produced the outcome, the DOM behind it, and the detect signal that matched are exactly
what a person needs when an outcome fires that should not have, and that is a live risk here
because two of the three declared outcomes were hand added rather than observed (0027).

So the artifacts are written and `context.json` carries the actual `result_kind`. Anything
reading the contents sees `business_outcome`; only the directory name is misleading, and only
until you open it. A test asserts that field specifically, so the labelling cannot silently
regress.

Rejected: rename the directory to `diagnostics/`. Honest, and it would have been my choice, but
the phase spec named `failure/` and a directory name is not worth diverging on without asking.
Rejected: skip it for business outcomes. It optimizes the folder name at the cost of the
evidence, which is the wrong trade when the outcome declarations are the least verified part of
an artifact.

## 0037. screenshots are masked with live geometry, not the recorded hint

Phase 8.

Fields bound to a parameter the capability declares `pii` or `secret` are blacked out at
capture time with Playwright's own `mask=` on `page.screenshot`, which resolves the locator
against the page as it stands. The phase spec called for `geometry_hint`.

`geometry_hint` is the bounding box recorded during discovery, and `Rect`'s own docstring in
the schema says it is "a hint for a human looking at evidence, never a locator". Masking with
it makes it load bearing. A page that reflows, a longer member name, a validation error
appearing above the field, and the black box lands next to the value instead of over it. The
screenshot then looks redacted while not being redacted, which is worse than an obvious hole
because it invites trust. Live masking puts the box where the field actually is at the moment
of capture.

The cost is a locator build per masked field per screenshot. Built directly rather than
resolved, so nothing waits and nothing raises: Playwright ignores a mask locator matching zero
elements, which is the common case for a field belonging to a different step.

**The limit, stated plainly.** Box masking is best effort. It covers the field the artifact
knows about. It cannot cover the same value rendered somewhere the artifact does not know
about: a confirmation banner, a page title, a summary table, a tooltip, an error message
quoting what was entered. Any of those puts the value in the PNG in plain sight, and no amount
of masking declared fields will catch it, because the artifact has no record that the value
appears there at all.

The safer production default is therefore not better masking. It is aria snapshots with field
level redaction as the primary visual record, which is text and so passes the Redactor like
everything else, and screenshots only on explicit operator request. That is what this system
would ship with outside an assessment, and it is the honest recommendation rather than a claim
that the masking here is sufficient. Repeated in REPORT.md section 6.

Known weakness beyond that: masking is applied by the surface, so a screenshot taken by
anything that is not the surface bypasses it entirely. Nothing currently does.

## 0038. every run directory records the commit and the policy hash

Phase 8.

`meta.json` carries `git_commit` and `policy_sha256` alongside the path the policy was loaded
from.

Evidence is read later, by someone who was not there. Two facts govern how to interpret every
other file in the directory and neither can be recovered afterwards: which code ran, and which
allowlist it ran under. A replay that was blocked six weeks ago tells you nothing useful unless
you know whether the rule that blocked it still exists.

Both parts of the policy record are needed. The path alone is worthless, because
`config/policy.json` is edited in place and the file at that path today is not the file that
ran. The hash alone is unreadable, because a bare digest does not say what it is a digest of.
Together they say "this allowlist, exactly this version of it", and a reviewer can check by
hashing the file themselves. A test does exactly that.

`git_commit` is suffixed `-dirty` when the working tree was not clean, which is the honest
answer for most development runs and a signal not to trust the commit as a full description.

Rejected: embed the whole policy document in meta.json. It is small enough that this would
work, and it removes the indirection, but it also copies the allowlist into every run directory
where it will drift out of sync with nothing to detect that. A hash cannot drift.

Known weakness: `git_commit` degrades to `"unknown"` rather than raising if git is unavailable.
A run that dies because it could not shell out to git is worse than a run whose provenance is
one field short.

## 0039. one writer, and the directory shape stopped depending on the caller

Phase 8.

Discovery, replay and escalation all write through `EvidenceWriter`. Nothing else creates a
file inside a run directory.

Consolidating this found a real gap rather than merely tidying. Discovery accumulates its
events on the transcript instead of emitting them as it happens, and `run.jsonl` for a
discovery run existed only because `cmd_discover` remembered to loop over those events and
replay them into the writer. Any other caller of `run_discovery` produced a directory with no
event log at all, and the test that compares a discovery directory against a replay directory
is what surfaced it. Flushing the events is now `write_transcript`'s job, so the shape is a
property of the writer rather than of whoever called it.

The same reasoning put `write_failure_artifacts` in `src/evidence/failure.py` rather than in
the replay engine, where it started. Discovery needs the identical post mortem, and two
subsystems each building their own would drift within a phase.

Rejected: a base class or a mixin that each subsystem inherits. Section 8 rules out plugin
systems and this is the same instinct one size down. A module level function taking a duck
typed surface and sink is smaller, and it lets discovery pass no `Step` at all rather than
inventing one to satisfy an interface.

Known weakness: `interventions/` is still written by `InterventionStore`, not by the evidence
writer. That is deliberate, because interventions outlive a run and are read by a separate
process, but it does mean there are two places that write to disk. Both go through the same
Redactor, and the credential guard walks both.
