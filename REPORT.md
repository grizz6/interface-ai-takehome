## Architecture

## Artifact schema

## Determinism & error handling

## Heterogeneity & multi-tenant

## Escalation & handoff

## Safety

<!-- Written in phase 10. The paragraph below was required by phase 8 and is placed here now
     so the limitation is recorded where it belongs rather than only in DECISIONS.md 0037. -->

**Screenshot masking is best effort, and here is where it stops.** Any field bound to a
parameter the capability declares `pii` or `secret` is blacked out at capture time, using the
live page geometry rather than the box recorded during discovery. That covers the field the
artifact knows about. It does not cover the same value rendered anywhere the artifact has no
record of: a confirmation banner, a page title, a summary row, a tooltip, a validation message
quoting what was entered. In a legacy back office application those are common, and no
improvement to the masking will catch them, because the artifact has no idea the value appears
there.

So the honest recommendation is not better masking. The safer production default is aria
snapshots with field level redaction as the primary visual record, because a snapshot is text
and therefore passes the same Redactor as everything else, with screenshots captured only on
explicit operator request. Screenshots are kept here because this is an assessment and a
reviewer should be able to see the screens, not because the trade is right in production.

## Cuts
