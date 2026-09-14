# Decisions

Notes on the choices I made while building this, written at the time rather than afterwards.
Each entry says what I picked, what I turned down, and where the choice is weak. REPORT.md is
put together from these. Anything I stubbed, mocked or left out is noted here when it happens.

## 0001. Python, the Playwright sync API and Pydantic v2

Stage 0.

Python 3.11, Playwright's sync API for the browser, Pydantic v2 for every schema, and the model
SDK only inside the discovery loop. The capability format matters more than anything else here,
and Pydantic gives one definition that parses, validates at runtime and exports the JSON Schema
in `schemas/`. So the schema a reviewer reads is the one the code enforces. Python also keeps the
discovery loop, replay and the Flask target app in one language.

Rejected: TypeScript with Playwright and Zod. It was a close call. Playwright's TypeScript
binding gets features first, Zod infers types from the same declaration, and the compiler would
check the result union instead of an optional tool.

Weak spots: Python typing is only advisory, so `mypy --strict` has to be run on purpose and only
checks what is annotated. And the sync API blocks the calling thread, which makes the handoff
harder: the browser has to stay open and usable by a person while Python is not calling into it.
That is why the control lease had to be its own explicit object (stage 7).

## 0002. Real error states and simulated faults come from different places

Stage 1.

Not found, permission denied and validation errors are real. They come from the seed data and
the form input, with no switch: ask for a member that was never seeded and you get the not found
page, ask for 100003 and you get permission denied, submit a deposit of zero and the server
rejects it. The four faults on `/dev/faults` (maintenance page, session expiry, slow response,
server error) are simulated: turned on by hand, kept in the Flask session, fired once. The line
is whether the condition belongs to the data or to the runtime.

Rejected: make all seven switches on the fault page. Simpler, one place to look. But a "not
found" made by a switch proves nothing. Replay would be detecting a flag the test set, not
something the app did, and telling a business outcome apart from a failure is the exact thing
the brief says people get wrong.

Weak spots: faults fire on GET only, so a session expiring in the middle of a form POST, probably
the most realistic version, cannot be produced. Firing on a POST throws away the submission and
leaves Continue with nothing to go back to. Faults live in the session cookie, so only the browser
that set one sees it. And with no database there is no way to fail halfway through a transaction.

## 0003. Three choices about the shape of a capability

Stage 2.

### Outputs are declared, not read in a step

Reading a value is declared once, on `OutputSpec.extraction`. Steps only act. That keeps
everything a caller gets back in one place, lets replay read outputs without walking the steps
again, and means a tenant whose confirmation page puts the account number somewhere else needs
an `output_overrides` entry, not a new recording.

Rejected: an `extract` step type. Smaller, one list to run, and it makes the order of reads
explicit.

Weak spot, and a real one: every output is read at the same moment, after the last step. A value
that is only on screen halfway through, like a reference number that disappears on the next page,
cannot be captured. The fix would be an `extract_after_step` field, not an extract step.

### Checks exist per step and for the whole flow

`Step.postcondition` checks that one action did what it should. `Capability.checkpoint` checks
that the whole flow reached the goal. They fail differently: a failed postcondition points at one
step, while a failed checkpoint after every step passed means the flow is wrong as a whole. It is
also why an irreversible step must have a postcondition, because it has to prove what it did right
away, not at the end.

Rejected: a checkpoint for the whole flow only. Less to write and arguably all a caller cares
about.

Weak spot: two places to be wrong, and they can repeat each other. In the test fixture the last
step's postcondition and the checkpoint check the same text, and nothing flags it.

### Expected outcomes belong to each capability

`BusinessOutcomeSpec` hangs off the capability. "Member not found" only makes sense for a flow
that looks a member up, and how you detect it depends on one screen. A shared registry would need
to be keyed by app and screen anyway. Keeping them local also means one file lists every answer a
caller can get.

Rejected: a shared list of outcome codes that capabilities point to. It would keep
`member_not_found` meaning the same thing everywhere and avoid writing the same check many times.

Weak spot: that duplication. Ten capabilities on the same app will each declare
`member_not_found` with their own check, nothing makes them agree, and when the app's wording
changes they break one by one. Moving to a shared list later is mechanical.

## 0004. Anything that can be caught from the file is caught when it is built

Stage 2.

The stage 2 validators all follow one rule: if a problem can be seen from the capability file
alone, the schema rejects the file instead of leaving replay to find it. Regexes are compiled on
construction. An outcome that says it checks after step 9 in a six-step flow is rejected, as is a
recovery for a step that does not exist, a required input nothing uses, a risky step with no check
after it, and a sensitive input with an example value.

The reason is approval. A person reviews a draft, approves it, and from then on it runs with
nobody watching. If it then breaks at step four, the review could never have caught it, and the
failure turns up against a live banking system instead of at someone's desk.

Rejected: check lazily and let replay report a bad pattern as a normal failure. Less schema code,
and replay has to handle errors anyway. But a failure at replay is harder to diagnose, comes with
a half-finished flow, and for an irreversible step may come after the damage.

Weak spots: this only catches a file that contradicts itself, never a file that no longer matches
the app. A locator for a button renamed last week is still valid. Also, most of these rules cannot
be expressed in JSON Schema, so anything validating against `schemas/capability.schema.json`
alone will accept files Pydantic rejects. And at the time `ParamSpec.pattern` was not compiled at
build time, which it should be.

## 0005. A screen-shape check, added before anything used it

Stage 2.

`SignalKind.aria_matches` and `Signal.aria_template` let a check compare the shape of a screen
instead of looking for a string, and `SurfaceFingerprint.aria_template` records that shape. A text
check asks "is this string somewhere on the page". A shape check asks "is this the screen I
recorded". For a checkpoint the second is the real question: "Sub-Account Opened" in a hidden
template or a breadcrumb passes `text_present` while the flow is nowhere near done. For tenants, a
renamed button is a text change but an extra confirmation step is a shape change.

I added it before anything needed it because it was free then. No capability had been saved yet.
Once approved capabilities exist, the same change needs a version bump and a migration.

Rejected: wait until the recorder needs it. Normally the right call, since a field designed before
anyone uses it is often the wrong shape. But a wrong field on an unused schema is a free edit,
while a right field added later is a migration.

Weak spots: shape checks break on harmless markup changes, like a heading wrapped in a new div, so
they belong on checkpoints and fingerprints, not step postconditions. Nothing enforces that. And
templates are only checked as valid YAML. Whether a template describes a real screen is only found
out at replay, because Playwright has no public parser for its template format.

## 0006. Where describe() lives, and why the XPath tiers are not a CSS fallback

Stage 3.

### describe() belongs to the surface

Turning a snapshot ref into a lasting `LocatorBundle` is a method on `Surface`. It needs the
snapshot that produced the ref and the live page, to check that each candidate matches exactly
one element right now. A recorder working from a transcript later has neither, because refs are
dead after the next snapshot. It also splits the work well for other surfaces: a desktop surface
would write its own `describe()` against UI Automation, and the recorder just collects bundles.

Rejected: keep the surface thin and do this in the recorder. Cleaner layers on paper, but the
recorder would then need a live handle back into the surface to check candidates, or it would save
unchecked bundles. An unchecked bundle is a guess that fails at replay instead of at recording.

Weak spots: the surface now reads the page and interprets it, and `web.py` grew past the 300 line
guideline. A second surface would reimplement every tier. If a third ever shows up, tier selection
should move into shared code that takes an `Observation`.

### XPath that names a relationship is not the same as a CSS selector

Tiers 2 and 3 compile to XPath, but they are not brittle like the CSS tier. What matters is what
the expression names. Tier 2 compiles to `//tr[./*[normalize-space(.)="Nickname"]]` and then asks
`get_by_role` for the textbox inside: the field in the row labelled Nickname. That survives
renamed ids, restyling and a different widget with the same role. The CSS tier compiles to
`#ctl00_ContentPlaceHolder1_txtNickname`, one generated id. This matters because the tier that
matched is recorded on every run. If XPath counted as brittle, every tier 2 match would look like
degradation and nobody would read the signal.

Rejected: build tiers 2 and 3 from Playwright's `filter` and `has`. Reads better. But
`filter(has_text="Deposit Accounts")` matched two tables here, because an outer table also contains
the text, while `ancestor::table[1]` matched one. "Nearest enclosing table" needs an axis, and the
locator API has none.

Weak spots: XPath 1.0 has no escape character, so quoting goes through a `concat()` helper.
`normalize-space(.)` on a row matches all text inside it and could over-match on a busier page.
And it names a `tr`, so it is HTML-specific.

### A conflict found here, resolved in 0007

A control whose only possible locator is CSS could not be recorded. The one real case is the span
with an inline onclick: it has no role, so `get_by_role` cannot see it and tiers 1 to 3 fail. But
`LocatorBundle` refuses a CSS primary. Both rules are on purpose, so `describe()` raised
`LocatorUnresolved` and I wrote the conflict down instead of quietly loosening a validator. The
options were to allow a brittle primary with a reason, to fix the app (in real life, ask a vendor
and wait), or to add a tier that finds controls by visible text. The third was taken in 0007.

## 0007. A visible text tier

Stage 3.

`TextRelationLocator` finds a control by its visible text, optionally inside a named container,
and can be a primary. The tier order is now role_name, label_relation, container_ordinal,
text_relation, css_fallback, and only CSS is still refused as a primary. The onclick span now
records as a text_relation primary and resolves to the same element.

Allowing a CSS primary would have meant approved capabilities depending on
`#ctl00_ContentPlaceHolder1_lnkOpenSub`, which changes whenever someone reorders the page. Fixing
the app works when you own it, but in the real setting the app belongs to a vendor. Visible text
is the handle a person would use, which is the standard for the other tiers too.

It sits below container_ordinal. Role and name is something the app promises to screen readers,
and container plus position survives copy changes. Text survives neither rewording nor
translation, so it only gets used when the accessibility tree has nothing better.

Rejected: a tier matching text plus position among siblings, which would survive rewording. Too
early: it needs a sibling index, which is as brittle as an ordinal without the container, and only
one control in the app needs this tier.

Weak spot: text is exactly what rebranding changes. The brief describes the same vendor product
"configured, branded, and versioned differently" across many institutions. A text_relation
primary is the most likely locator to need a per-tenant override, and it fails quietly: the button
is still there and still works, but someone renamed "Open Sub-Account" to "New Sub-Account".
`VariantOverride.step_overrides` can hold a replacement, and since the winning tier is recorded on
every run, text_relation primaries are the first place to look when an upgrade breaks things.

## 0008. Gemini through the Interactions API, without server-side state

Stage 4. (Partly reversed in 0018.)

Switching from Anthropic to Gemini was a given (see 0016). The decision here was which Gemini API
to use. I checked the current docs: `client.models.generate_content` still works, but its
function calling guide is now under a Legacy heading, and the Interactions API is the recommended
path for new projects since June 2026. Building a new client on the legacy API would be hard to
justify in six months.

The Interactions API keeps conversation state on the server by default. I planned to pass
`store=False` and send the whole transcript every turn, because the recorder compiles the
transcript so it has to be ours, evidence has to live in `evidence/` rather than on a vendor's
server, and `ModelClient.complete(system, messages, tools)` is stateless, which is what lets
`ScriptedClient` stand in for the real model.

Rejected: server-side state with `previous_interaction_id`. Less data per turn, and what the API
is built around.

Also rejected, and ruled out by the brief for this step: the OpenAI compatibility endpoint.

Weak spot: the translation code was written against the SDK's type definitions and had never
made a live call. The first real run was where it would be tested, and it did need fixing (0018).

## 0009. The error pages are on the allowlist

Stage 4.

`config/policy.json` allows `/maintenance`, `/maintenance/continue` and `/session-expired`. That
looks wrong, since they are all error states. But the maintenance recovery works by landing on the
notice and clicking Continue. If those paths were denied, the policy would block the recovery: the
run lands on the page, the check refuses the URL it is already on, and a recoverable condition
becomes a policy violation. Denying a page you will land on anyway does not stop you landing there,
it only stops you doing anything about it. The allowlist describes where a run is allowed to be,
not where things are going well.

Rejected: deny the error pages and let known recovery steps bypass the policy. The allowlist looks
cleaner. But a bypass is a hole, even with a good reason, and the policy only means something if
nothing can get around it.

Weak spot: the list now mixes normal routes with error pages and does not say which is which. A
`reason` field per pattern would fix that, at the cost of the file no longer being a plain
`PolicyConfig` dump, which is what gives free validation on load.

## 0010. The model gets no wait tool

Stage 4.

The model has eight tools and none of them waits. The prompt says so, because a model trained on
browser automation will expect one.

Waiting already happens in the surface, with limits. `resolve()` retries every tier within a time
budget before deciding nothing matched, and `act()` waits for the page to settle before returning.
A wait tool would duplicate that.

The bigger reason is that a model that is unsure has an easy way out in waiting. It is cheap, never
fails, and puts off the decision. The failure is a run that spends eight of its steps waiting and
runs out before trying another route. Without the tool, it has to look again or do something
different, and both give it new information.

Rejected: a short capped wait for cases like a spinner the model can see. A real case, but seeing a
spinner should make the model look again, which costs the same turn and returns a real
observation.

Weak spot: this only works while the surface's waiting is right. If some future surface updates
without any change in the accessibility tree, the model will look, see the same screen and give
up. The fix then is better waiting in the surface, not a tool.

## 0011. The model points at refs and never writes a locator

Stage 4.

No tool the model sees takes a `LocatorBundle` or anything that could carry one. `finish` takes a
`ref` where `OutputSpec` takes a locator, and its checkpoint only offers the check types that need
no locator. The model says which element it means by ref, and `describe()` picks and verifies the
locator before anything is saved.

Choosing a locator tier decides whether a recording still works next month, and it is the first
thing the brief grades. If the model wrote locators, that choice would live in the prompt, where the
model can ignore it or make up a plausible CSS selector. In `describe()` it is Python, and every
candidate is checked against the page. It is the same reason the policy check lives in code.

Rejected: let the model suggest a locator and have the surface check it. Not a bad design, and the
model's reading of the page is useful. But a locator that checks out today is not the same as one
that lasts. The model cannot know `#ctl00_ContentPlaceHolder1_lnkOpenSub` will break next release,
and the tier order already encodes that.

Weak spot: the model can still point at the wrong element, and `describe()` will build a perfect
locator for it. The tiers guarantee the thing can be found again, not that it was the right thing.
The checkpoint is also written by the model, so a run can be consistently wrong from start to end.

## 0012. What the loop tells the model and what it leaves out

Stage 4.

### A refusal names the rule and nothing more

When the policy refuses an action, the model is told it was refused, which rule refused it, and
that the refusal is final. It is not told which pattern matched. `PolicyViolation` keeps `rule` and
`reason` separate so the loop can pass one and drop the other, and a test checks the pattern never
reaches a tool result.

A model told the boundary will explore it. Told that `/dev/faults` matched `^/dev(/.*)?$`, it will
try `/dev` without the slash, or a redirect. That is not malice, just the model using what it has,
and every attempt is a wasted step.

Rejected: say only "refused". Leaks less. But a bare refusal looks like a temporary error, and the
right response to a temporary error is to retry, which is what the three-block limit exists to
catch.

Weak spot: the rule name is a small leak. `denied_path_patterns` tells the model it was the path.
An opaque code would leak less but make every person debugging a run look it up.

### Only the last two snapshots are sent in full

Every action stays in the history. Only the two latest observations are sent in full, and older
ones shrink to a line with the page name and URL. A snapshot of this app is about five thousand
characters, so twenty of them would bury what matters, which is what the model did and what came
back. Two is the least that lets the model compare before and after an action.

Rejected: have the model summarise old observations. More informative, but it is another model
call that can be wrong, can fail, and uses up a rate limit a long run is already close to.

Weak spot: if the model needs to remember a form from eight steps ago, it cannot, and has to go
back and look, which may be impossible after an irreversible step. Nothing detects this. It would
look like a run giving up for no visible reason.

## 0013. finish is checked, not trusted

Stage 4.

When the model calls `finish`, the checkpoint is parsed into a `Signal` and evaluated against the
live page, and every declared output is turned into a locator and actually read. Only if all of
that works is there a success.

The checkpoint is the only test on every future replay of the capability, with nobody watching. A
checkpoint that has never once passed is a guess. Checking it costs one evaluation.

Reading the outputs is easy to skip, which is why I call it out. An output that cannot be read
from the page it was declared on is already broken, and without this check it would show up in
production as a success that returns nothing. Checking here tells the model straight away, while
it can still point somewhere better.

Rejected: trust `finish` and let replay find problems. Less code. But at discovery the cost is one
retry, and at replay the capability has been approved and is running against a live system.

Weak spot: this proves the checkpoint passes now, not that it tells screens apart. A string that is
on every page, like the footer, passes here and on every replay wherever the flow ends up. The fix
is to also test the checkpoint against an earlier observation and reject it if it passed there too.
Not built.

## 0014. The model declares inputs and outputs, and the schema checks them

Stage 4.

The model declares inputs and outputs at `finish`. It is the only one that knows which typed
values came from the goal and which it read off the screen. Every declaration goes through the
same validators as a saved capability: snake_case names, no example on a pii input, no unused
required input, and outputs that actually read.

Rejected: infer parameters by comparing typed values with the goal text. Needs nothing from the
model. But a typed member id could be a parameter or a constant, and you cannot tell from outside.
A wrong guess gives a capability that is quietly hardwired or has parameters it should not. The
comparison still runs, as a warning in the transcript.

Weak spot: a parameter with the wrong type passes everything. Declare `initial_deposit` as a
string or `member_id` as an integer when the app zero-pads it, and nothing objects. It shows up on
the first replay with real values. The fix is replaying with a second set of inputs before
approval, which is what the approval stretch goal is for.

## 0015. A blocked action goes back to the model instead of ending the run

Stage 4.

A refused action returns a message saying that direction is closed. The run only ends after three
refusals in a row, and the count resets whenever an action succeeds.

One block is usually the model being reasonable and wrong, like trying a link to the fault page.
That is the policy doing its job, and the model can use it. Ending the run there would throw away a
discovery that was otherwise fine.

Three in a row means the model has decided the blocked route is the only way and is trying
variations. More turns will not help, so the run stops with `PolicyBlockedResult`. It is "in a row"
and not a total, because a run that hits one dead end early and then finishes is a good run.

Rejected: stop on the first block. Defensible somewhere stricter. But then every over-cautious
allowlist entry ends a run, and the pressure becomes loosening the allowlist.

Weak spot: three is a guess, not a measurement. It also counts per run, not per rule, so three
different rules refusing once each looks the same as one rule refusing three times.

## 0016. Gemini, for cost

Stage 4. See 0008 for which Gemini API.

I moved from Anthropic to Gemini because Gemini has a usable free tier, this is a take-home, and a
discovery run can be twenty or more multimodal turns.

It was cheap to do because `ModelClient` already kept everything provider-specific in one module.
The switch changed `client.py` and nothing else: not the loop, the tools, the transcript, the prompt
or any test. I built that interface so `ScriptedClient` could replace the model in tests, and the
portability came along with it.

Rejected: stay on Anthropic and pay, or support both and pick at runtime. The second is the kind of
early generality I wanted to avoid: two providers behind one interface with one never used.

Weak spot: the free model is weaker. It needs more turns, misreads dense tables more often, and
points at the wrong ref more often, which `describe()` will turn into a perfect locator for the
wrong control. A failed run says less about the system than it would with a stronger model. The
free tier also allows about ten requests a minute, so the client retries 429s with backoff.

## 0017. The redactor cannot see inside a screenshot

Stage 4. (Addressed in part by 0037.)

Every text write in the evidence writer is serialised first and redacted second, so a sensitive
value cannot hide in a field nobody thought to redact. Screenshots skip that, because a PNG is bytes
and the redactor reads text. A screenshot of the member page shows the name, account numbers and
balances. The data here is made up, but in a real deployment the evidence folders would be full of
financial data no text redactor can touch.

Options: no screenshots, which loses the richer failure signal the brief asks for; treat evidence
as regulated data with access controls, which a real deployment would need anyway; or black out
fields filled from pii parameters before writing. The third is doable because observations record
bounding boxes. Done in stage 8, see 0037.

## 0018. The conversation is continued on the server after all

Stage 4, after the first real run. Reverses part of 0008.

The first run against the real API failed. The Interactions API does not accept `model_output` or
`function_call` as input items, so a tool-calling conversation cannot be resent statelessly. Only
the server can hold the model's side. `GeminiClient.complete()` now sends only the messages added
since the last call and passes `previous_interaction_id`.

Our transcript is unchanged. It is still ours, still what the recorder compiles, and still what
goes into evidence. What moved to the server is the model's own copy of the conversation, not our
record of it.

The same run found the input shape was wrong too: `text` and `image` are content parts, not
top-level input items. A single bare text is accepted, which is why a one-shot call worked and a
conversation did not. History has to wrap parts in a `user_input` envelope. The API's 400 named the
last item, which pointed at the wrong place.

Weak spot: `ScriptedClient` is still stateless, so tests do not cover the server-side continuation.
A break there only shows up in a real run.

## 0019. The recorder copies locator bundles as they are

Stage 5.

`compile` copies `ActionRecord.bundle` into the saved step unchanged. It does not re-rank tiers or
run `describe` again.

A bundle was checked against the live page at the moment of the action. By the time the recorder
runs, that page is gone. Rebuilding would mean checking against whatever the browser shows now, or
against nothing, and the result would be a locator that was never verified. The only moment a
locator is known to be good is when it is built.

Rejected: rebuild at compile time so bundles reflect the final page. That turns a verified locator
into a guess that looks like a recording.

Weak spot: the recorder cannot spot a bad bundle. If `describe` picked something unique at that
moment but not stable, like the balance cell in the real run whose `role_name` primary is the
balance figure itself, the recorder copies the mistake. Nothing checks whether a bundle names
something that will still be true next month. That is the biggest gap in stage 5.

## 0020. A declared input that matches nothing is a compile error

Stage 5.

If a declared input cannot be matched to any value typed during the run, compilation fails with
`INPUT_MATCHES_NO_LITERAL` instead of warning.

An input promises the caller it will be used. An input the flow ignores means a caller passing a
member id gets a result for whatever id was baked in at recording, which looks like success. The
schema would reject an unused required input anyway. Failing in the compiler just gives a better
message: it can say no recorded step used a value, or that two inputs could not be told apart.

Rejected: warn and drop the input. Always gives a capability, but it silently changes what the
model declared, and nobody reading it later would know.

Weak spot: matching is inference. The model never says which parameter a typed value came from, so
the recorder works it out from examples first and the goal text second. When that is ambiguous it
refuses, so a real two-parameter flow whose values are not in the goal will not compile until
someone adds examples.

## 0021. Compiled capabilities have no expected outcomes

Stage 5.

The compiled capability declares no business outcomes, and the compile report says so.

The app has three real ones, and adding them to every capability would look thorough. It would also
be made up. The run being compiled never saw any of them, so nothing shows their checks are right.
A declared outcome is checked on every replay, and a check nobody has seen match is a guess, like a
checkpoint that never passed (0013). Evidence here only comes from real runs.

Rejected: fill them in from the target app's error pages, since I wrote the app. That does not work
for a vendor app nobody has the source for.

Weak spot: a capability compiled from one happy path treats a "no such member" page as a failed
check, not an answer. That is exactly the confusion the result types exist to prevent. The fix is
more recordings, one per outcome, or a person adding them (0027).

## 0022. First compile is always a draft

Stage 5.

Every compiled capability is `draft`, and there is no flag to change that.

A compiled capability has run once, forwards, with the model making every decision. That says
nothing about replay, which has no model and finds controls from saved bundles. The first thing that
could justify `approved` is a successful replay.

Rejected: mark it approved when discovery verified its checkpoint and outputs. That is a real
signal, but it shows the finish claim matched the page the model was on, not that a locator saved
mid-run works on a fresh load, which is what actually breaks.

Weak spot: nothing moves a capability from draft to approved yet, so the field does nothing until
replay refuses drafts (0026).

## 0023. Outcomes are checked before a step's own check

Stage 6.

Each step runs in a fixed order: policy check, find and act, recoveries, expected outcomes, then
the step's postcondition. Outcomes coming before the postcondition is the part that matters.

A "no member record matches" page fails the search step's postcondition and the checkpoint. If the
postcondition came first, every not-found lookup would be exit 40, a person paged and a DOM dump
written, to deliver an answer the run already had.

Recoveries come first too. A maintenance page covering the screen makes every other question
meaningless: no check passes and no outcome matches. Clearing it first means the judgement is about
the flow, not the interruption.

Rejected: check outcomes only after the last step, where the answer usually is. A not-found page
shows at step 3 of 4, step 4 then fails to find its control, and the real answer is never reported.
The flow can end early, so outcomes are checked after every step.

Weak spot: `check_after_step` means exactly that step, not that step or later. `None` means every
step. (An earlier version of this entry said "or later", which the engine never did.) An outcome
that can appear at more than one point has to use `None`, or it is missed. 0045 is what that looks
like.

## 0024. Irreversible steps are never retried

Stage 6.

`_retry_budget` returns 0 for any `risky_irreversible` step, whatever its wait settings say. A
timeout on one escalates to a person.

From outside, a timeout and an action that went through without showing it look the same. The
thing that would tell them apart is behind the thing that timed out. Retrying risks opening the
account twice. Escalating costs a person two minutes.

Rejected: look at the page after the timeout and retry only if the action clearly did not happen.
Usually works, but fails exactly when it matters: a slow confirmation page looks the same as one
that will never load.

Rejected: a per-step retry setting. The person who turns it on is not the person who deals with a
duplicate transfer, and that setting gets changed during an incident.

Weak spot: a step wrongly marked safe at recording can still be retried into a duplicate. Risk comes
from the policy's `risky_control_names` and the recorder, and neither is perfect. At least the same
marking drives the approval gate, so a mistake is visible in the file.

## 0025. A fingerprint mismatch stops the run

Stage 6.

If the saved fingerprint does not match what the run sees before it starts, it stops before step 0
instead of trying and reporting drift after.

A mismatch means this is not the app the capability was recorded on. Either it is another tenant,
and the right move is to use that tenant's overrides, or the app changed and the saved locators
describe a screen that is gone. Carrying on means running steps whose meaning is unknown on a real
member's account. Stopping wrongly costs a person confirming a cosmetic change. Carrying on wrongly
means acting on the wrong screen.

Rejected: warn and continue if enough of the fingerprint matched. Nobody can defend a threshold.
Two of three landmarks matching is more likely a sign the page changed in the one spot not checked.

Weak spot: the fingerprint is the title, brand text, landmarks and the shape of the page frame. The
frame is what a reskin changes first and it rarely changes what the flow means, so harmless rebrands
will stop runs. The answer is an override for the reskinned app. There is no automatic
re-fingerprinting, because a drift check that updates itself stops being a drift check.

## 0026. Drafts do not replay without a flag

Stage 6.

`check_approval` refuses to run a `draft` unless `--allow-draft` is passed, before the browser opens.

A compiled capability has run once, with a model choosing everything from a live snapshot. Replay
is a different path. The most common way it fails is a locator that was unique where the model was
standing but not on a fresh load, and only a replay finds that. The flag marks the line between
"worked once in another mode" and "worked in this mode".

Rejected: allow draft replays and mark the result as advisory. Callers read the exit code, and an
advisory success is still exit 0.

Weak spot: nothing promotes a draft to approved, and every replay in `evidence/` used
`--allow-draft`. So for now the gate slows a person down rather than controlling automation. Fixing
that needs a promotion path: several successful replays recorded on the capability and a person
signing off. That is the approval stretch goal, and it is not in this submission.

## 0027. member_not_found and member_restricted were added by hand

Stage 6.

The discovery run only saw member 100001, who exists, so compiled 1.0.0 had no outcomes (0021). I
wrote both outcomes by hand against the live pages: `member_not_found` in 1.1.0, and
`member_restricted` plus the maintenance recovery in 1.2.0. `Provenance.human_edited` is true on
both, so a reviewer can tell what the model saw from what a person added without comparing against
a transcript.

The risk with a hand-written check: if the text is wrong, the outcome never fires and the run reports
a failed check. The file looks right and does nothing.

For the lookup capability that is covered. `test_unknown_member_is_a_business_outcome_not_a_failure`
and `test_restricted_member_is_a_business_outcome_not_a_crash` replay against the live app and check
the codes, and `test_interstitial_is_dismissed_and_the_run_still_succeeds` turns on the real fault
and checks the recovery is listed. Sample runs 07 and 08 also show `member_restricted` and
`validation_rejected` firing.

I did not apply the same lesson to `open-member-subaccount-1.0.0.json`, which I wrote by hand later
with three outcomes and no tests. None of them could fire. See 0045.

## 0028. Navigate steps store a path, not a URL

Stage 6.

A navigate step saves `/member/{member_id}`. The host comes from `surface.base_url` at replay time.

The recorder first saved the full URL, so the first capability had `http://localhost:8080/` in it,
tying it to one machine. The brief wants one capability to work on the same app deployed in
different places, which is normal here: the same vendor app at a different host for each credit
union.

Found by a test that pointed `base_url` at a random port and saw the run go to 8080 anyway.
Recompiling the saved transcript after the fix gave the committed 1.0.0 byte for byte apart from
that field.

Weak spot: only the base URL prefix is stripped, so a run that wandered to another host would save
a full URL for that step. `allowed_hosts` makes that hard, and nothing rejects a capability whose
navigate step names a host.

## 0029. The control lease is a file both sides poll

Stage 7.

Who may touch the browser is one small JSON file, written atomically and read by both sides. No
queue, socket, database or lock.

There are two processes and one fact they share. A file holds it, `os.replace` makes each write
atomic, and both read it when they need to. Either side can restart and the state survives, because
it is not in either process. You can `cat` it, and tests can check it without a broker. When no
operator page is running, the run still holds a lease through `InProcessLease`, the same protocol
with one participant, so the "no browser action without the lease" rule still applies.

Rejected: an HTTP call or socket from the run to the operator page. Then the run cannot start
unless the page is up, and restarting the page loses the session. Rejected: a queue or database.
The brief does not reward them and they add nothing. Polling every half second is plenty when a
person takes minutes.

No lock, because each state has exactly one side allowed to write it: automation owns running and
resuming, the operator owns paused and human_control. Two writers never compete for the same change.

Weak spot: `deadline_at` is compared with the clock of whichever machine reads it, so skewed clocks
would disagree. Both sides are local here.

## 0030. The visible browser window is the live session

Stage 7.

The operator page shows the request, screenshot, accessibility snapshot and parameter names, and
moves the lease. It does not show the live page. The person works in the Chromium window the run
opened, with their own mouse and keyboard.

This is a real cut: no co-browsing, WebRTC, VNC or screencast. Streaming is a lot of infrastructure
and shows nothing about what is being assessed, which is whether control changes hands safely: the
run stops, the person gets enough context, it is the same session and not a new one, and what the
person did is recorded. All four work without streaming.

What is lost: the person has to be at the machine running the browser. Streaming could be added
later without changing the lease, which is the only thing the two sides share.

One consequence: `POST /take` cannot install the recorder that captures what the person does,
because the Flask process has no browser handle. `Session.escalate` installs it before the handover.
The route only moves the lease.

## 0031. Resuming always checks the page, whatever the person said

Stage 7.

When control comes back, the run looks at the page and checks the current step's postcondition (or
the checkpoint if the step has none) before reading the person's answer. `completed_manually` is
refused if the page does not show the step done.

The person's answer is a claim about a screen, and the screen is right there. One observation rules
out a whole class of mistakes: they clicked Cancel instead of Confirm, fixed the wrong member, or got
interrupted and thought they had finished. In a banking flow, the step after a wrongly skipped one
acts on the wrong screen.

The person may also have left the browser anywhere, on another member or with a dialog open. Even
for `approved`, the page may not be where the run paused. So the check always happens.

Rejected: trust `completed_manually` because it came from a person. That makes correctness depend on
someone's memory of what they did in a clunky UI minutes ago. Their note is kept, and it appears in
the failure message when the page disagrees, so both sides of the story are there.

Weak spot: this is only as good as the declared check. A step with no postcondition and no
checkpoint cannot be verified, so `completed_manually` is refused there. Strict, but in the safe
direction.

## 0032. retry_step is refused on an irreversible step

Stage 7.

The operator page does not offer `retry_step` on a `risky_irreversible` step, and
`refuse_unsafe_outcome` rejects it even if the page is bypassed.

This is 0024 with a person involved, which makes it worse. A person who had the session for five
minutes may well have clicked the button themselves. Doing it again opens the account twice. They
have three safe choices: approve and let the run do it, say they did it and have that checked, or
abort.

It is enforced in two places. Hiding the button helps the operator. The refusal in
`refuse_unsafe_outcome` is the real control, because a resolution file can be written by hand, by a
script, or by some other page, so the rule has to be where the resolution is read. Each has a test.

Rejected: allow retry after a confirmation dialog. A dialog is UI, and the rules that matter live in
code. Rejected: allow retry if the postcondition does not hold. Same problem as 0024: a slow
confirmation page looks the same as one that never loads.

## 0033. What the person types is never recorded

Stage 7.

The recorder added before a handoff captures clicks, navigations and which field changed. It never
captures what was typed. The `change` handler reads which element changed and never its value.

The rule that pii never reaches disk applies whether a model or a person typed it. This app's fields
take account numbers, names and amounts, and an intervention file stays on disk after the run.
Capturing values would be a second, quieter way for that data to reach disk, since nobody would think
to `--redact` what a person typed. Which field changed is what an auditor needs anyway. The value
itself is visible in the after snapshot.

Those before and after snapshots are why `Session` takes a redactor. They show a real screen and may
contain declared sensitive values, so they go through the same redaction as evidence. Without that,
`interventions/` would have been a quieter leak than `evidence/`.

Weak spot: redaction only replaces declared values. An undeclared sensitive value reaches the
snapshot, just as it would reach evidence. `params_redacted` is built from the capability's declared
inputs, not from the values passed in, so that path cannot leak a parameter value.

## 0034. The fingerprint check loads the first page before comparing

Stage 7, fixing a stage 6 bug.

`check_fingerprint` now goes to the entry page before comparing anything.

It used to look straight away, which on a new browser meant `about:blank`. An empty title matches no
saved title, so every capability with a fingerprint failed the check, and only capabilities with an
empty fingerprint passed. The only capability in the repo at the time had an empty one, so the check
passed everywhere and had never compared anything. A drift check that cannot fire is worse than none,
because passing it looks like proof.

Found by writing a capability by hand with a real fingerprint and watching it fail against the app it
was written for.

Weak spot: titles are compared exactly, and titles change often in old apps. The answer is an
override (0025), not loosening to "contains", which would pass on any page sharing a brand prefix.

## 0035. The test operator acts on the thread that owns the browser

Stage 7.

The end-to-end handoff test plays the operator from inside `await_return`, through a `Session`
subclass, not from a second thread.

Playwright's sync API ties a page to the thread that created it. A second thread touching it raises
at once, so a simulated person in another thread cannot click anything. The alternatives were a second
Playwright client over CDP, which means launching Chromium with a debugging port just for a test, or
acting from inside the wait.

This loses realistic timing, since the operator acts at a fixed moment. It keeps what matters. The
lease moves through the operator page's real HTTP routes. The clicks go straight to the page with no
`LocatorBundle`, policy check or step index, so they are not automation. And the test checks the
browser context and page objects are the same before and after.

Rejected: skip the end-to-end test and only check files. That tests the bookkeeping and leaves the
main claim, that control changes hands on one live session, untested.

## 0036. failure/ is written for business outcomes too

Stage 8.

Any result that is not `success` gets a `failure/` folder, including `business_outcome`. The name is
wrong for that case, but the contents are useful.

A not-found lookup is a correct answer, and calling it a failure is the thing I have been trying to
avoid. But skipping the folder loses the screen, the DOM and the matched check, which is what you need
when an outcome fires that should not have. That is a real risk, since two of the three declared
outcomes were written by hand (0027).

So the files are written and `context.json` holds the real `result_kind`. Only the folder name is
misleading, and a test checks that field.

Rejected: rename the folder to `diagnostics/`. I would have preferred it, but the plan named
`failure/`, and a folder name was not worth changing without asking. Rejected: skip it for business
outcomes. That fixes the name at the cost of the evidence.

## 0037. Screenshots are masked using the live page

Stage 8.

Fields filled from a `pii` or `secret` parameter are blacked out when the screenshot is taken, with
Playwright's `mask=` option, which finds the field on the page as it is. The plan said to use
`geometry_hint`.

`geometry_hint` is the box recorded during discovery, and the schema's own `Rect` docstring says it is
only a hint for a person reading evidence, never a locator. If the page reflows (a longer name, an
error above the field) the black box lands next to the value. A screenshot that looks redacted but is
not is worse than an obvious gap. Live masking puts the box where the field is.

It costs one locator per masked field per screenshot, built without waiting. Playwright ignores a mask
that matches nothing, which is the usual case for a field from another step.

The limit: masking covers the field the capability knows about. It cannot cover the same value shown
somewhere else, like a confirmation banner, the page title, a summary table or an error message
quoting the input. So for a real deployment I would not rely on better masking. I would make redacted
text snapshots the main visual record, since they go through the redactor like everything else, and
take screenshots only when an operator asks.

Weak spot beyond that: the surface applies the mask, so a screenshot taken any other way skips it.
Nothing does that today.

## 0038. Every run folder records the commit and the policy hash

Stage 8.

`meta.json` has `git_commit`, `policy_sha256` and the policy path.

Evidence gets read later by someone who was not there. Two things decide how to read the rest and
cannot be worked out afterwards: which code ran and which allowlist it used. A block from six weeks
ago means little unless you know whether that rule still exists.

Both path and hash are needed. `config/policy.json` gets edited, so the path alone does not say which
version ran. A hash alone does not say what file it belongs to. Together they pin it, and a test
hashes the file to check.

`git_commit` gets `-dirty` when the tree had changes, as a sign not to take the commit as the whole
story.

Rejected: copy the whole policy into `meta.json`. It is small enough, but the copies would drift with
nothing to notice. A hash cannot drift.

Weak spot: `git_commit` falls back to `"unknown"` if git is not available, instead of failing the run.

## 0039. One writer for every run folder

Stage 8.

Discovery, replay and handoffs all write through `EvidenceWriter`. Nothing else creates files in a
run folder.

Merging them found a real bug. Discovery collects events on the transcript instead of writing them as
they happen, and a discovery run only had `run.jsonl` because `cmd_discover` remembered to copy the
events into the writer. Any other caller got a folder with no event log. The test comparing a
discovery folder with a replay folder caught it. `write_transcript` now writes the events, so the
layout no longer depends on the caller.

For the same reason `write_failure_artifacts` moved from the replay engine to
`src/evidence/failure.py`. Discovery needs the same thing, and two copies would drift quickly.

Rejected: a base class or mixin. A plain function that takes a surface and a writer is smaller, and
discovery can pass no `Step` instead of inventing one.

Weak spot: `interventions/` is still written by `InterventionStore`. That is on purpose, since
interventions outlast a run and are read by another process, but it means two things write to disk.
Both use the same redactor and the secret scan checks both.

## 0040. The real discovery run is kept unredacted and not re-run

Stage 9.

`evidence/curated/01-discovery-real` is older than the stage 8 writer. It has no `meta.json` and has
member id `100001` in plain text in eight places. I kept it as recorded.

Re-running it costs a real model call and gives a different run, since the model is not
deterministic. It is the one thing in the repo that cannot be reproduced, so I did not trade it for
tidiness, and would not have without asking.

Why it is unredacted matters more. The later runs were made with `--redact` and contain no raw id. At
discovery there is no capability, so nothing marks `member_id` as `pii`, and the goal sentence names
the member. Redaction only protects values someone has marked, and during discovery nothing is marked.

That gap would exist in production too. The fix is not redacting harder afterwards. A discovery run on
real data needs its sensitive inputs declared before the goal is written. Nothing here does that, so
discovery evidence is the least protected evidence the system produces.

Nothing real is exposed: 100001 is a made-up member in the local test app that ships with the repo.

## 0041. Recorded human actions include a URL, and it was not redacted

Stage 9, fixing a stage 7 bug.

The stage 9 secret scan found the raw member id in `06-escalation-handoff/intervention.json`, in
`resolution.human_actions[].url`.

`Session._collect_resolution` redacted `url_after` and `aria_after` but passed the recorded actions
through untouched. Each action carries the URL it happened on, and `/member/100001/subaccount` has a
member id in it. The action's visible `text` has the same problem: a link labelled with an account
number is an account number.

Fixed by redacting the serialised action and parsing it back, not by redacting the two string fields
that exist today. The evidence writer does the same, because listing fields means remembering every
new one, and this leak is what forgetting looks like.

How it was found is worth noting. Stage 7 had a test that no typed value reached the recorded actions,
and it passed, because it only checked typed values. Nobody typed the URL. A scan of every byte of a
finished run caught what a targeted test missed, which is the case for having both.

## 0042. Tests on the saved runs read the sample evidence and never skip

Final fixes.

`tests/test_evidence_invariants.py` reads `evidence/curated/*/transcript.json`. It used to look in
`evidence/*/transcript.json` and skip when nothing matched, which on a clean clone was always, since
the sample transcripts are one level deeper. So "no snapshot refs in saved capabilities" was never
checked against a real model run except on my machine.

A skip is not harmless here. A green run with two skipped tests reads as "this holds" when it means
"nobody looked". The tests now read committed data, a guard test fails if that data is missing, and a
run with no refs passes instead of skipping.

Rejected: keep reading `evidence/` so local runs are checked too. Then test results depend on whatever
someone ran last, which is what broke the suite in the fresh clone check after following the README's
dry run.

Weak spot: only one sample run has a transcript, so this is checked against one real model run.

## 0043. allow_draft is recorded by the caller and by the engine

Final fixes.

Replaying a draft is only allowed with `--allow-draft`, and the sample runs replayed drafts without
saying so. I checked the gate instead of assuming: the same command without the flag exits 40 before
any step runs.

`meta.json` records `allow_draft` from the caller, and the engine's `preflight` event records the value
it actually used. Two records, because `meta.json` is written by whoever creates the writer, and
evidence that only rests on the caller's word is weaker.

Runs 02 to 09 all carry the field. 02 was re-run first, and 03 to 06 were re-run afterwards for the
same reason.

## 0044. The capability catalog only reads, and running stays in replay

Final fixes. The brief's first stretch goal.

`src/catalog.py` and two commands, `catalog list` and `catalog describe <id>`. What it prints is the
capability read back: inputs with types and sensitivity, outputs, business outcomes, which steps need
approval, exit codes, and the exact command to run it. Every file is validated on load, and a broken
one is an error naming the file, because an agent told a capability does not exist when it is really
broken will work around a fault nobody knows about. Versions sort numerically, so 1.10.0 is newer than
1.2.0.

Rejected: a `catalog invoke` command. It would be a second way to run a capability with its own
arguments to keep in sync. `describe` prints the `replay` command instead.

Rejected: an HTTP endpoint. No server is needed, and it would expose the same information.

Weak spot: the printed command assumes this repo layout, `.venv/bin/python` from the root. An agent
elsewhere has to adjust it, and nothing checks the values it fills in against the declared types
before the run.

## 0045. Three bugs meant the sub-account outcomes could never fire

Final fixes. Found while producing sample run 08.

The sub-account capability declares `member_not_found`, `member_restricted` and `validation_rejected`.
I wrote all three by hand and never replayed them. Run 08 showed none could fire.

**The validation check looked for text the app never shows.** It looked for "Correct the highlighted
fields". The app's messages are per field, like "Initial deposit must be greater than zero.", and that
phrase is nowhere in the app. The same mistake 0027 warns about, made after writing 0027.

**A step's wait timed out before outcomes were checked.** Step 4 waits for "Review Sub-Account
Request". A rejected form never shows it, so `act()` timed out after ten seconds and the engine
returned a `timeout` failure immediately. Recoveries and outcomes were only checked after a step
succeeded, so even a correct check would not have been read. The lookup capability avoided this only
because its search step waits for page load, not for text. 0023 put outcomes before postconditions but
said nothing about wait timeouts.

**The fingerprint check treated an outcome page as drift.** For a restricted or unknown member, the
check from 0034 loads the entry page, gets an "Access Restricted" or not found title, and stops before
step 0. The two member outcomes in this capability check after step 0, so they could never be reached.

**Fixed, each with a test that fails on the old code:**

1. When a step's wait times out, the engine checks that step's outcomes before retrying or reporting a
   timeout. The run log shows `wait_timed_out_on_outcome`.
2. The fingerprint check no longer calls a mismatch drift when the page matches an outcome declared
   for step 0. A title change with no matching outcome still stops the run, and a test checks that.
3. The capability is published as 1.1.0 with a pattern that matches the app's real field errors. 1.0.0
   is unchanged because sample run 06 used it. A test checks the pattern against every message in the
   app's error table.

Run 08 was then re-run against 1.1.0 and returns `validation_rejected` with exit 10.

Rejected: fix only the text check. It would still have timed out, and the more important bug was in
the engine.

Weak spot: an outcome is only recognised at the step it names. A rejected form at a step that does not
list it still comes back as a timeout. That is right for an undeclared screen, but looks similar in a
log.

## 0046. An expired session restarts the flow

Final fixes.

`RecoveryAction.reauthenticate` had been in the schema since stage 2 and did nothing at replay, so
session timeout, one of the six conditions in the brief, was not handled. Lookup 1.3.0 now declares
`reauthenticate_after_session_expiry`: when the page says "Your session has expired", the engine goes
back to the first page and starts again from step 0. Sample run 09 shows it, with the recovery listed
in `recoveries_applied`.

It starts over instead of resuming because an expired session loses whatever the flow built up: a
half-filled form, a selected record, a place in a wizard. There is nothing to resume. This app has no
login, so here reauthenticating is just the restart. A real app would sign in first, and that part
belongs in the surface.

Two limits, enforced in the engine. No restart after an irreversible step has run, because starting
over could do it twice (0024 again). And at most `max_attempts` restarts per rule, so a check that never
clears cannot loop forever. Either case escalates as `recovery_exhausted`, and both have a test.

Recoveries are now also checked when a step's wait times out. Otherwise a step waiting for specific text
would sit behind the expired page until the wait failed, and report a timeout.

Rejected: resume at the interrupted step. That assumes the app kept state the expiry threw away, and on
a real screen it would type into a form that is gone.

Weak spot: the fault fires on the first page load, so run 09 restarts before doing anything. An expiry
later in the flow takes the same code path but is only covered by the limit tests, not a sample run.
The sub-account capability does not declare the rule.

## 0047. A model that cannot be reached is a failure, not a crash

Final fixes.

Running discover with no API key used to end with the SDK's `ValueError` as a traceback and exit 1,
outside the five result types and with no `result.json`. The same happened for any non-retryable API
error, and for 429s or 5xx past the retry limit.

`GeminiClient` now raises `ModelUnavailable` for all three, and the loop turns it into a
`FailureResult` with exit 40, so the run leaves the same evidence as any other failure. The message is
either the SDK's local "No API key was provided", which is produced before any request and contains no
value, or an HTTP status. The provider's response body is never copied in, because I cannot be sure it
has nothing sensitive in it.

Rejected: check whether the key is set before creating the client, which would also avoid the SDK's
cleanup warning. Checking whether it is empty means reading its value, and nothing here does that.

Weak spot: it uses the `internal` failure class, the closest existing one. A dedicated class would be
clearer, but that is a schema change, and I would propose it before making it.

## 0048. Discovery can hand the browser to a person too

Final fixes.

The discovery loop had code to hand over to a person, but nothing could reach it: `run_discovery`
took no session and `discover` never made one. So a stuck discovery run, which the brief lists
first under 3.6, could only stop with NeedsHuman. `discover` now takes the same `--lease-path`,
`--interventions-dir` and `--intervention-timeout` options as `replay`, and passes a Session in.

With a session, four things hand over instead of stopping: the model giving up, the screen not
changing three times in a row, a control that matches several elements or none, and a step that
times out. The request carries the goal, the step count, the reason and a screenshot. When the
person hands back, the model is told to look again, because they may have changed the screen.
Aborting, or nobody answering, still ends the run with NeedsHuman, now naming the real request.

Rejected: resume at the exact action that got stuck. Discovery has no recorded step to resume,
and the model is better placed than the loop to decide what to do on the screen the person
left behind.

Weak spot: there is no sample run of it. It is covered by a test on the real browser with a
scripted operator, a test for the stuck locator case, and a CLI test that checks the request
file is written.
