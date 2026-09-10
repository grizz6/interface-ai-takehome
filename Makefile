.PHONY: app discover replay operator test

# Phase 0 stubs. Each target echoes and exits until its phase lands.
# The command each one will run is recorded above it.

# run the local target app (variant A on 8080, variant B on 8081)
app:
	python target_app/app.py

# python -m src.cli discover --goal "..." --target http://localhost:8080
discover:
	@echo "not implemented: discovery loop lands in phase 4"

# python -m src.cli replay --capability capabilities/<id>.json --params '{...}'
replay:
	@echo "not implemented: replay engine lands in phase 6"

# python -m src.cli operator
operator:
	@echo "not implemented: operator surface lands in phase 7"

# pytest
test:
	@echo "not implemented: tests land alongside the models in phase 2"
