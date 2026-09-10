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
