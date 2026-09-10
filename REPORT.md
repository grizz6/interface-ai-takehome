# Design write-up

Skeleton. The seven headings below are fixed by the brief and each one is filled in as the
corresponding piece of the system is built. Notes under each heading record what belongs there.

## 1. Architecture

The architecture and the key decisions, with the trade-offs.

Covers: the split between the discovery path and the production replay path, why the model is
structurally excluded from replay rather than just left uncalled, process and boundary choices,
and where simplicity was chosen over generality.

## 2. Artifact schema

The schema, and why it is shaped that way.

Covers: the capability contract (ordered steps, how each control is identified, typed inputs,
typed outputs, success condition), versioning, and what makes it readable to a human reviewer and
callable by an agent at the same time.

## 3. Determinism & error handling

How replay stays deterministic, and how runtime errors and exceptional states are detected and
handled.

Covers: locator and wait strategy, checkpoint verification, and the error taxonomy. The taxonomy
is the part that matters most: expected business outcomes the caller needs (no such member),
recoverable conditions (dismiss a known interstitial, retry a slow load), and hard failures that
stop and surface a debuggable error. Conflating the first with the third is the mistake the brief
calls out by name.

## 4. Heterogeneity & multi-tenant

How the design extends to legacy web and desktop surfaces, and to reuse across institutions
running the same application.

Covers: the seam between perceiving and acting on a surface and the recorded flow itself, and how
one artifact is specialized per tenant rather than re-recorded per tenant.

## 5. Escalation & handoff

How a stuck state is detected, how a human takes control of the live session, and how control
comes back.

Covers: what raises an intervention request and what context travels with it, the control-transfer
model on a single live session, and how the human's actions are recorded.

## 6. Safety

The guardrail model and its limits.

Covers: allowlist enforcement, the line between reversible and irreversible actions and how the
risky class is handled, redaction of regulated financial data, and an honest account of what the
guardrails do not stop.

## 7. Cuts

What was deliberately left out, and what comes next with more time.
