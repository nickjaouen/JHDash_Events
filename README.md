# JHDash_Events

A Python project managed with [uv](https://docs.astral.sh/uv/).

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed

## Setup

Create the virtual environment (if you do not already have `.venv`):

```bash
uv venv
```

Install dependencies from `pyproject.toml`:

```bash
uv sync
```

Install Playwright
.venv/bin/playwright install chromium

## Run

```bash
uv run python main.py
```

Activate the environment manually if you prefer:

```bash
source .venv/bin/activate
```

## Add dependencies

```bash
uv add <package-name>
```
