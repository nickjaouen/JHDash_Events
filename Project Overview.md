# Project overview (standalone scripts)

This document describes **only** the code under `standalone_scripts/`: what it does, which technologies it uses, and how work flows between pieces—including how AI-related steps connect.

The repository also contains notebooks and library code elsewhere; those are out of scope here except where a standalone script **loads** them (see below).

---

## What lives in `standalone_scripts/`

| File | Role |
|------|------|
| `main_extractor.py` | CLI entry point that runs the **event extraction pipeline** (a LangGraph compiled graph) for a single starting URL and prints (optionally writes) JSON for `final_events`. |
| `date_expander.py` | CLI that reads rows from Postgres, calls an OpenAI chat model with **structured output** to normalize free-form dates/times into concrete occurrences, then updates `events.occurrences` (JSONB). |
| `orchestrator.ipynb` | Notebook driver that runs `main_extractor.py` once per configured URL, then runs `date_expander.py` in **apply** mode as a batch step. |

---

## Technologies (standalone scripts)

- **Python 3** with `asyncio` for async orchestration in the two `.py` scripts.
- **`python-dotenv`** — loads environment variables from `.env` (API keys, DB URLs, etc.).
- **`main_extractor.py` only**: reads and `exec`s selected code cells from the repo’s `main.ipynb` to build a LangGraph `graph` object, then calls `await graph.ainvoke(...)`.
- **`date_expander.py`**: **LangChain** `ChatOpenAI` with **Pydantic** models for structured LLM output; **SQLAlchemy** async engine (`create_async_engine`) with **asyncpg**-style URLs (`postgresql+asyncpg://...`).
- **`orchestrator.ipynb`**: **`subprocess.run`** to invoke the two Python scripts; prefers `.venv/bin/python` when present.

---

## How the standalone pieces chain together

### 1. Optional end-to-end driver: `orchestrator.ipynb`

Intended flow:

1. Resolve **project root** (current directory if it contains `main.ipynb` and `standalone_scripts/`, or parent if cwd is `standalone_scripts/`).
2. For each starting page URL in `ACTIVE_STARTING_PAGE_URLS` (from the environment), run:
   - `python standalone_scripts/main_extractor.py --starting-page-url <url>`

   **Note:** The notebook assigns `ACTIVE_STARTING_PAGE_URLS` from `os.environ.get(...)`, which is a **string** (or `None`). Python will iterate that string **character-by-character** unless you assign an actual list of URLs (for example by splitting a comma-separated env value inside the notebook before the loop).
3. Run date expansion on the database:
   - `python standalone_scripts/date_expander.py --mode apply --limit 500`

So the **macro pipeline** is: **extract events into the DB (via the LangGraph pipeline)** → **backfill normalized `occurrences` JSONB** for rows that still have empty occurrences.

### 2. Extraction CLI: `main_extractor.py`

This script does **not** redefine the graph in Python. It:

1. Loads `main.ipynb` as JSON, collects **code** cells in order, and skips cells that are only for interactive use (e.g. rendering a graph image or ad-hoc `ainvoke` in the notebook).
2. Executes the remaining cell sources in a dedicated module namespace until `graph` exists (a **LangGraph** `StateGraph` compiled in the notebook).
3. Invokes:

   ```text
   await graph.ainvoke({
     "starting_page_url": <CLI argument>,
     "final_events": [],
   })
   ```

4. Returns `final_events` from the result and prints JSON (and optionally writes `--output-json-path`).

So **all LangGraph nodes and edges** for extraction are defined in `main.ipynb`; `main_extractor.py` is a **headless runner** for that graph.

### 3. How the extraction graph’s AI nodes connect (loaded from `main.ipynb`)

The graph is built with **LangGraph** (`StateGraph`). The **linear** part after the start is:

```text
START
  → load_starting_page
  → broad_event_researcher
  → filter_deduplicate
  → analyze_series_candidates
  → expand_series_candidates
  → finalize_concrete_events
```

From `finalize_concrete_events`, a **conditional** node **`fan_out_event_jobs_node`** runs:

- If there are no event stubs to enrich, it routes to **`END`**.
- Otherwise it returns a list of **`Send("event_researcher", {...})`** jobs—one per stub—so LangGraph can run **`event_researcher`** **in parallel** for each event.

After enrichment, each branch continues **sequentially**:

```text
event_researcher
  → canonicalize_event_entities
  → merge_normalize
  → persist_events
  → END
```

Individual nodes use LLMs and tools as implemented in `main.ipynb` (not duplicated in `standalone_scripts/`). From the standpoint of this folder, **chaining** means: *notebook defines the graph; `main_extractor.py` executes it once per URL.*

### 4. Date expansion CLI: `date_expander.py`

This script is **not** a multi-node LangGraph. It is a **single-model structured call** per row:

1. Connect to Postgres using `POSTGRES_URL_2` or `POSTGRES_URL` (normalized to an async SQLAlchemy URL).
2. Select `events` rows where `occurrences` is empty (`{}` or `[]`), ordered by `created_at`, with an optional `--limit`.
3. For each row, build a prompt from `event_name`, `event_date`, `event_time`, `event_description`, and a `--today-date` anchor.
4. Call **`ChatOpenAI(model="gpt-5-mini", temperature=0).with_structured_output(EventOccurrencesExpansionModelType)`** and `ainvoke` the prompt.
5. Validate, dedupe, and sort occurrences; in **`apply`** mode, `UPDATE` the row’s `occurrences` JSONB only if it is still empty (guards against overwriting).

**Dry run** (`--mode dry-run`) previews expansions and prints a JSON sample without writing.

---

## Environment and operations (standalone-relevant)

- **`main_extractor.py`**: needs whatever `main.ipynb`’s pipeline expects (e.g. OpenAI and any DB/tool env vars used inside those nodes).
- **`date_expander.py`**: requires `POSTGRES_URL_2` or `POSTGRES_URL`; OpenAI credentials for LangChain’s `ChatOpenAI`.
- **`orchestrator.ipynb`**: expects `ACTIVE_STARTING_PAGE_URLS` and a working `.venv` (or falls back to `sys.executable`).

---

## Summary

- **`main_extractor.py`** chains **notebook-defined LangGraph nodes** by loading `main.ipynb` and calling `graph.ainvoke`—including a **fan-out** from `finalize_concrete_events` into parallel **`event_researcher`** work, then **canonicalize → merge → persist**.
- **`date_expander.py`** chains **data**: DB read → **one structured LLM call per event** → optional DB write.
- **`orchestrator.ipynb`** chains **processes**: run extraction per URL, then run batch date expansion.
