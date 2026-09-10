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
