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
