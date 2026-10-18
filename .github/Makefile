.PHONY: ci-install ci-quality

ci-install:
	python -m pip install -r requirements-ci.txt

ci-quality:
	python scripts/check_workflows.py
	python -m unittest discover -s scripts -p 'test_*.py' -v
