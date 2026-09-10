# target_app

A fictional credit union member services console, standing in for the legacy back-office
systems this project exists to automate. It is here only to give the automation something
realistic to drive. It is not a deliverable and is deliberately capped: one Flask app, one
seed file, one stylesheet, and the templates the two flows need. No database, no auth, no
JavaScript framework, no tests of its own.

All data is fabricated. No real person, account, or institution.

## Running it

    python3 -m venv .venv
    .venv/bin/pip install flask
    make app

Serves variant A on http://localhost:8080. `PORT` and `VARIANT` are read from the
environment, so a second variant will later run on 8081 without a code change. Only
variant "a" exists today.

## Why it looks like that

Every legacy characteristic is deliberate. Server-rendered Jinja with full page loads and
no SPA behaviour. Layout built from nested tables rather than grid or flexbox. The main
content of the member detail page served as a separate document inside an iframe. Element
ids in ASP.NET style, for example `ctl00_ContentPlaceHolder1_txtMemberId`. No `data-testid`
anywhere. Exactly one navigation control implemented as a `<span>` with an inline onclick
that breaks the frame, which is invisible to anything looking for a link or a button.

Every user-visible string is read from the variant dict in `seed.py`. Templates never
hardcode text, so a second tenant variant is a config entry rather than a forked template.

## Locator tiers

The three tiers in design rules section 6 are each forced by something real in the markup.
Each site carries a comment in the template saying which tier it is there to exercise.

| Tier | Where | Why that tier is required |
|---|---|---|
| 1, role plus accessible name | Member ID field and Search button on `/search`; Account Type, Initial Deposit and Statement Delivery on the sub-account form | Real label association or button text, so role plus name resolves them alone |
| 2, label text relation | Nickname field on the sub-account form | No label association, no aria-label, no title, no placeholder. It has no accessible name at all. The only thing identifying it is the text in the table cell to its left |
| 3, container scope plus ordinal | The two Select buttons on the member panel | Identical accessible names in two different account containers. Neither tier 1 nor tier 2 can tell them apart. They lead to different screens on purpose, the Deposit one to the sub-account form and the Loan one to loan servicing, so resolving the wrong control is observable rather than silent |

## Routes

| Method | Path | What it does |
|---|---|---|
| GET | `/` | Home with navigation |
| GET | `/search` | Member search form |
| POST | `/search` | Redirects to member detail, or returns the form with an inline error if the field is empty |
| GET | `/member/<id>` | Member detail. Hosts the iframe |
| GET | `/member/<id>/panel` | Iframe content: name, status, branch, both account tables, the two Select buttons, the onclick span |
| GET | `/member/<id>/loan-servicing` | Where the Loan Accounts Select lands. Not the sub-account form |
| GET | `/member/<id>/subaccount` | Sub-account form |
| POST | `/member/<id>/subaccount` | Validates. Review screen on success, form with inline errors on failure |
| POST | `/member/<id>/subaccount/confirm` | The irreversible step. Issues an account number and shows the confirmation |
| GET | `/dev/faults` | Fault console |
| POST | `/dev/faults` | Arms or clears a fault |
| GET | `/maintenance` | Interstitial notice with a Continue control |
| GET | `/maintenance/continue` | Dismisses the interstitial and resumes the original page |
| GET | `/session-expired` | Session expired screen |

## Seed members

Real exceptional states come from this data, not from a toggle.

| Member ID | Name | What it demonstrates |
|---|---|---|
| 100001 | Marcus Webb | Happy path. Savings and Checking under Deposit Accounts, one Auto Loan under Loan Accounts. Use this one for the discovery run |
| 100002 | Dana Ruiz | Normal member with a Savings account and an empty Loan Accounts container |
| 100003 | Priya Shah | Restricted record. Returns the permission denied screen |
| 100004 | Alan Whitfield | Multiple deposit and loan accounts |
| anything else, for example 999999 | not seeded | Returns the no member found screen |

Not found and permission denied both return HTTP 200 with a distinct screen rather than a
404 or a 403. That is on purpose: these are business outcomes, and the automation is meant
to read the screen rather than the status line, because that is what it will have to do
against the systems this stands in for.

The third real state is validation. Submit the sub-account form with an initial deposit of
`0`, a negative number, or text, or leave Nickname blank, and the server returns the form
with an inline error next to the offending field.

## Arming a fault

Faults are simulated runtime conditions, kept separate from the three real states above.
Go to `/dev/faults` and press the Arm button for the one you want. It is stored in the
Flask session, fires once on the next page load, then disarms itself. Clear Armed Fault
cancels one without firing it.

| Fault | What happens |
|---|---|
| `interstitial` | A maintenance notice appears before the page you asked for. Continue returns you to it |
| `session_expired` | Redirects to the session expired screen |
| `slow` | Roughly six seconds of delay, then the page you asked for |
| `server_error` | A 500 error page |

Faults fire on GET only. See DECISIONS.md entry 0002 for why, and for what that rules out.
The fault console, the screens a fault lands on, and static assets never fire a fault, so
you can always reach the console to disarm.
