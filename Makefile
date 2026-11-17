.PHONY: setup lint format-check test manifest ingest-sample dbt-parse dbt-compile dbt-docs run all

VENV ?= .venv
PYTHON := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff
PYTEST := $(VENV)/bin/pytest
DBT := $(VENV)/bin/dbt

setup:
	uv sync --frozen --extra dev

lint:
	$(RUFF) check src/ tests/

format-check:
	$(RUFF) format --check src/ tests/

test:
	$(PYTEST) -v

manifest:
	$(PYTHON) -m standby verify-manifest --manifest data/manifest.csv
	$(PYTHON) -m standby verify-manifest --manifest data/sample/manifest.csv

ingest-sample:
	$(PYTHON) -m standby run --hub ORD --years 2019,2023-2026 --sample --dry-run

dbt-parse:
	$(DBT) parse --project-dir dbt --profiles-dir dbt --no-send-anonymous-usage-stats

dbt-compile:
	$(DBT) compile --project-dir dbt --profiles-dir dbt --no-send-anonymous-usage-stats

dbt-docs:
	$(DBT) docs generate --empty-catalog --project-dir dbt --profiles-dir dbt --no-send-anonymous-usage-stats

run:
	$(PYTHON) -m standby run --hub ORD --years 2019,2023-2026

all: lint format-check test dbt-parse dbt-compile dbt-docs run
