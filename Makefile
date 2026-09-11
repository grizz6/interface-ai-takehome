.PHONY: app discover replay operator test schemas

# Phase 0 stubs. Each target echoes and exits until its phase lands.
# The command each one will run is recorded above it.

# run the local target app (variant A on 8080, variant B on 8081)
app:
	python3 target_app/app.py

# Override either on the command line:
#   make discover GOAL="..." TARGET=http://localhost:8080/member/100001
GOAL ?= look up member 100001 and read the current savings balance
TARGET ?= http://localhost:8080/search

discover:
	python3 -m src.cli discover --goal "$(GOAL)" --target "$(TARGET)"

# python -m src.cli replay --capability capabilities/<id>.json --params '{...}'
replay:
	@echo "not implemented: replay engine lands in phase 6"

# python -m src.cli operator
operator:
	@echo "not implemented: operator surface lands in phase 7"

# pytest
test:
	pytest

# export JSON Schema for Capability and RunResult into schemas/
schemas:
	python3 -m src.models.export_schemas
