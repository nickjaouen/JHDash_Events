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

## Run

```bash
uv run python main.py
```

Activate the environment manually if you prefer:

```bash
source .venv/bin/activate
python main.py
```

## Add dependencies

```bash
uv add <package-name>
```
