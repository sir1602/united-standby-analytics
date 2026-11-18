.PHONY: setup lint format-check test manifest ingest-sample score-sample validate-index-sample validate-index export-tableau-sample export-tableau dbt-parse dbt-compile dbt-docs dbt-build-duckdb-sample dbt-build-duckdb run all

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

score-sample:
	$(PYTHON) -m standby score --hub ORD --years 2019,2023-2026 --sample --recompute

validate-index-sample:
	$(PYTHON) -m standby validate-index --hub ORD --years 2019,2023-2026 --sample

validate-index:
	$(PYTHON) -m standby validate-index --hub ORD --years 2019,2023-2026 --summary-path docs/phase4_validation_summary.json

export-tableau-sample:
	$(PYTHON) -m standby export-tableau --hub ORD --years 2019,2023-2026 --sample

export-tableau:
	$(PYTHON) -m standby export-tableau --hub ORD --years 2019,2023-2026

dbt-parse:
	$(DBT) parse --project-dir dbt --profiles-dir dbt --no-send-anonymous-usage-stats

dbt-compile:
	$(DBT) compile --project-dir dbt --profiles-dir dbt --no-send-anonymous-usage-stats

dbt-docs:
	$(DBT) docs generate --empty-catalog --project-dir dbt --profiles-dir dbt --no-send-anonymous-usage-stats

# Free local dbt build on DuckDB (no GCP account). Reads the dry-run Parquet that the pipeline writes.
dbt-build-duckdb-sample: ingest-sample
	STANDBY_DUCKDB_PATH=data/processed/sample.duckdb $(DBT) build --project-dir dbt --profiles-dir dbt --target duckdb --vars '{raw_parquet_dir: data/processed/sample}' --no-send-anonymous-usage-stats

# Full data: run `make run` first (writes data/processed/full/*.parquet).
dbt-build-duckdb:
	$(DBT) build --project-dir dbt --profiles-dir dbt --target duckdb --no-send-anonymous-usage-stats

run:
	$(PYTHON) -m standby run --hub ORD --years 2019,2023-2026 --dry-run

all: lint format-check test dbt-parse dbt-compile dbt-docs run
