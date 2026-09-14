# target_app

A made-up credit union back-office app, standing in for the kind of old system this project is
meant to automate. It only exists to give the automation something realistic to work against,
so it is kept small: one Flask app, one seed file, one stylesheet and the templates the two flows
need. No database, no login, no JavaScript framework and no tests of its own.

All data is invented. No real people, accounts or banks.

## Running it

    python3 -m venv .venv
    .venv/bin/pip install -e .
    make app

Serves the app on http://localhost:8080. `PORT` and `VARIANT` come from the environment, but
only variant `a` exists.

## Why it looks like this

Every old-fashioned detail is on purpose. Server-rendered Jinja with full page loads. Layout
made of nested tables instead of grid or flexbox. The main part of the member page loaded in an
iframe. ASP.NET style element ids such as `ctl00_ContentPlaceHolder1_txtMemberId`. No
`data-testid` anywhere. One navigation control is a `<span>` with an inline onclick, which
anything looking for a link or a button will miss.

All visible text comes from the variant dict in `seed.py` and templates hardcode none of it, so
a second tenant would be a new entry there, not a copy of the templates.

## Locator tiers

Each locator tier is needed by something real in the markup, and each place has a comment in
the template saying which tier it is for.

| Tier | Where | Why that tier is needed |
|---|---|---|
| 1, role and accessible name | Member ID field and Search button on `/search`; Account Type, Initial Deposit and Statement Delivery on the sub-account form | They have proper labels or button text, so role and name are enough |
| 2, label next to the field | Nickname field on the sub-account form | No label, aria-label, title or placeholder, so it has no accessible name. The only clue is the text in the table cell to its left |
| 3, container and position | The two Select buttons on the member panel | Same name, in two different account tables, so tiers 1 and 2 cannot tell them apart. They go to different pages on purpose, Deposit to the sub-account form and Loan to loan servicing, so clicking the wrong one shows up |
| 4, visible text | The "Open Sub-Account" span on the member panel | It has no role at all, so only its text identifies it |

## Routes

| Method | Path | What it does |
|---|---|---|
| GET | `/` | Home page with navigation |
| GET | `/search` | Member search form |
| POST | `/search` | Goes to the member page, or shows the form again with an error if the field is empty |
| GET | `/member/<id>` | Member page. Holds the iframe |
| GET | `/member/<id>/panel` | The iframe: name, status, branch, both account tables, the two Select buttons, the onclick span |
| GET | `/member/<id>/loan-servicing` | Where the Loan Accounts Select goes. Not the sub-account form |
| GET | `/member/<id>/subaccount` | Sub-account form |
| POST | `/member/<id>/subaccount` | Validates the form. Review page if it is fine, the form with errors if not |
| POST | `/member/<id>/subaccount/confirm` | The irreversible step. Creates an account number and shows the confirmation |
| GET | `/dev/faults` | Fault page |
| POST | `/dev/faults` | Turns a fault on or off |
| GET | `/dev/reset` | Puts the app back to its starting state: removes opened sub-accounts, resets the account number counter, clears any fault |
| GET | `/maintenance` | Maintenance notice with a Continue button |
| GET | `/maintenance/continue` | Closes the notice and goes back to the page you asked for |
| GET | `/session-expired` | Session expired page |

## Seed members

The not found, restricted and validation states come from the data, not from a switch.

| Member ID | Name | What it is for |
|---|---|---|
| 100001 | Marcus Webb | Happy path. Savings and Checking under Deposit Accounts, one Auto Loan under Loan Accounts. Use this one for discovery |
| 100002 | Dana Ruiz | Normal member with a Savings account and no loans |
| 100003 | Priya Shah | Restricted. Shows the permission denied page |
| 100004 | Alan Whitfield | Several deposit and loan accounts |
| anything else, e.g. 999999 | not seeded | Shows the no member found page |

**The demo uses member 100001** for both discovery and replay. It is the only member with a
Savings account, a second deposit account and a loan at the same time, which the two Select
buttons need in order to mean anything.

**Opened sub-accounts are kept in memory, not a database.** They live in the `OPENED` dict in
`app.py` and show up in the member's Deposit Accounts table as soon as they are confirmed. They
last until the app restarts. `/dev/reset` does the same without a restart: it removes opened
sub-accounts, resets the account number counter and clears any fault, so two replays of the same
capability give the same outputs.

Not found and permission denied both return HTTP 200 with their own page, not a 404 or 403. That
is on purpose. They are business outcomes, and the automation should read the screen rather than
the status code, because that is all it gets from the systems this imitates.

Validation is the third state. Submit the sub-account form with an initial deposit of `0`, a
negative number or text, or with Nickname empty, and the form comes back with an error next to
the field.

## Turning on a fault

Faults simulate problems at runtime and are separate from the three states above. Go to
`/dev/faults` and press Arm on the one you want. It is saved in the Flask session, fires once on
the next page load and then turns itself off. Clear Armed Fault cancels it without firing.

| Fault | What happens |
|---|---|
| `interstitial` | A maintenance notice shows before the page you asked for. Continue takes you on to it |
| `session_expired` | Sends you to the session expired page |
| `slow` | About six seconds of delay, then the page |
| `server_error` | A 500 error page |
| `confirm_dialog` | The page opens a browser `confirm()` pop-up asking "Stay signed in?" |
| `alert_dialog` | The page opens a browser `alert()` pop-up about scheduled maintenance |

Faults only fire on GET requests; `DECISIONS.md` 0002 explains why and what that rules out. The
fault page, the pages a fault sends you to, and static files never fire a fault, so you can
always get back to the fault page to turn one off.
