.PHONY: install test lint fmt demo

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"

test:
	$(PY) -m pytest

lint:
	$(PY) -m ruff check src tests

fmt:
	$(PY) -m ruff format src tests

demo:
	$(VENV)/bin/escaner demo
