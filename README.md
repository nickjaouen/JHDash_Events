# JHDash_Events

Python project managed with [uv](https://docs.astral.sh/uv/) for event extraction and date normalization workflows.

## What this project does

This repo contains an event-processing workflow centered on:

- A LangGraph-based extraction pipeline defined in `main.ipynb`
- A standalone CLI runner for that extraction graph: `standalone_scripts/main_extractor.py`
- A standalone date normalization/backfill step: `standalone_scripts/date_expander.py`
- A notebook orchestrator that runs extraction and then date expansion in sequence: `standalone_scripts/orchestrator.ipynb`

At a high level, the flow is:

1. Extract events from starting page URLs
2. Persist event records
3. Expand free-form date/time fields into normalized `occurrences` JSONB

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Python 3 (managed through the project environment)
- Environment variables in `.env` (copy from `.env-example` and fill values)

## Setup

Create the virtual environment (if `.venv` does not already exist):

```bash
uv venv
```

Install dependencies from `pyproject.toml`:

```bash
uv sync
```

Install Playwright browser binaries:

```bash
.venv/bin/playwright install chromium
```

Activate the environment manually (optional):

```bash
source .venv/bin/activate
```

## Standalone scripts

### `standalone_scripts/main_extractor.py`

Runs the extraction pipeline for one starting URL by loading graph definitions from `main.ipynb` and calling `graph.ainvoke(...)`.

Example:

```bash
uv run python standalone_scripts/main_extractor.py \
  --starting-page-url "https://example.com/events"
```

Optional JSON output:

```bash
uv run python standalone_scripts/main_extractor.py \
  --starting-page-url "https://example.com/events" \
  --output-json-path "./final_events.json"
```

### `standalone_scripts/date_expander.py`

Reads `events` rows with empty `occurrences`, calls an OpenAI model with structured output to expand date/time fields, and optionally writes normalized occurrences back to Postgres.

Dry run:

```bash
uv run python standalone_scripts/date_expander.py --mode dry-run --limit 50
```

Apply updates:

```bash
uv run python standalone_scripts/date_expander.py --mode apply --limit 500
```

### `standalone_scripts/orchestrator.ipynb`

Notebook driver that:

1. Runs `main_extractor.py` for each configured starting URL
2. Runs `date_expander.py --mode apply` as a batch step

Note: make sure `ACTIVE_STARTING_PAGE_URLS` is handled as a list of URLs in the notebook flow (not a single raw string).

## Environment notes

- Extraction depends on the environment required by the graph code in `main.ipynb`
- Date expansion requires `POSTGRES_URL_2` or `POSTGRES_URL` and OpenAI credentials
- `.env-example` shows the expected variable names
