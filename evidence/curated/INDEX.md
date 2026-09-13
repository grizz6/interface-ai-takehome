| run | kind | result | exit | duration | detail |
| --- | --- | --- | --- | --- | --- |
| `01-discovery-real` | unknown | success | 0 | 80.2s | savings_balance=4182.55 |
| `02-replay-success` | replay | success | 0 | 0.2s | savings_balance=4182.55 |
| `03-replay-business-outcome` | replay | business_outcome | 10 | 0.9s | member_not_found: No member record matches the supplied member id. |
| `04-replay-recovered` | replay | success | 0 | 0.2s | savings_balance=4182.55 |
| `05-replay-hard-failure` | replay | failure | 40 | 0.4s | app_error at step 0 |
| `06-escalation-handoff` | replay | success | 0 | 33.7s | new_account_number=900001001 |
