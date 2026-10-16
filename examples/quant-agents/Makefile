# Quality gates for quant-agents. `make check` runs everything except the slow mutation test.
PY ?= python3
export PYTHONDONTWRITEBYTECODE=1

.PHONY: check test lint types smoke golden props cov mutate

check: lint types test cov

lint:
	ruff check src tests scripts

types:
	mypy src

test:
	$(PY) -m pytest -q -p no:cacheprovider

smoke:
	$(PY) -m pytest -q -p no:cacheprovider tests/test_smoke.py

golden:
	$(PY) -m pytest -q -p no:cacheprovider tests/test_golden.py

props:
	PROP_EXAMPLES=2000 $(PY) -m pytest -q -p no:cacheprovider tests/test_properties.py

cov:
	$(PY) -m pytest -q -p no:cacheprovider --cov --cov-branch --cov-report=term-missing:skip-covered

# Breaks the risk-critical modules in small ways and checks the tests notice (slow: minutes).
mutate:
	$(PY) scripts/mutate.py
