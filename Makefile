.PHONY: app discover record replay operator test schemas

# Every target uses the project venv. `python3` alone is the system interpreter, which
# does not have this project's dependencies, so `make app` in a fresh clone failed.
PY := .venv/bin/python

# run the local target app (variant A on 8080, variant B on 8081)
app:
	$(PY) target_app/app.py

# Override either on the command line:
#   make discover GOAL="..." TARGET=http://localhost:8080/member/100001
GOAL ?= look up member 100001 and read their current savings balance
TARGET ?= http://localhost:8080

discover:
	$(PY) -m src.cli discover --goal "$(GOAL)" --target "$(TARGET)"

# compile a transcript from a finished run into a capability
TRANSCRIPT ?= evidence/curated/01-discovery-real/transcript.json

record:
	$(PY) -m src.cli record --transcript "$(TRANSCRIPT)" --out capabilities/

CAPABILITY ?= capabilities/lookup-member-savings-balance-1.2.0.json
PARAMS ?= {"member_id": "100001"}

replay:
	$(PY) -m src.cli replay --capability "$(CAPABILITY)" --params '$(PARAMS)' --allow-draft

PORT ?= 8090

operator:
	$(PY) -m src.cli operator --port $(PORT) --interventions-dir interventions

test:
	$(PY) -m pytest

# export JSON Schema for Capability and RunResult into schemas/
schemas:
	$(PY) -m src.models.export_schemas
