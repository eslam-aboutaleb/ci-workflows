UV ?= uv
PYTHON ?= 3.12
VENV_PYTHON ?= .venv/bin/python

.PHONY: ci-install ci-quality check test lint lock
ci-install:
	$(UV) venv --python $(PYTHON) --allow-existing .venv
	$(UV) pip sync --python $(VENV_PYTHON) --require-hashes requirements/ci.txt

lock:
	$(UV) pip compile --universal --python-version 3.11 --generate-hashes requirements/ci.in -o requirements/ci.txt

ci-quality: check test

check:
	$(VENV_PYTHON) scripts/maintenance/check_workflows.py

test:
	$(VENV_PYTHON) -m unittest discover -s tests -v

lint:
	actionlint -color .github/workflows/*.yml
