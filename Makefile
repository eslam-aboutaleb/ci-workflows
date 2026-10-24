PYTHON ?= python3

.PHONY: ci-install ci-quality check test lint
ci-install:
	$(PYTHON) -m pip install -r requirements/ci.txt

ci-quality: check test

check:
	$(PYTHON) scripts/maintenance/check_workflows.py

test:
	$(PYTHON) -m unittest discover -s tests -v

lint:
	actionlint -color .github/workflows/*.yml
