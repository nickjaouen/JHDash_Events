from __future__ import annotations

import argparse
import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

try:
    from typing import NotRequired as TypingNotRequired
except ImportError:  # Python < 3.11 fallback
    from typing_extensions import NotRequired as TypingNotRequired

from dotenv import load_dotenv

_ = load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_NOTEBOOK_PATH = PROJECT_ROOT / "main.ipynb"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Cells with top-level side effects to skip during script bootstrap.
SKIP_SOURCE_SNIPPETS = (
    "Image(graph.get_graph().draw_png())",
    "pipeline_result = await graph.ainvoke(",
    "final_events = pipeline_result.get(",
)

_pipeline_namespace_cache: dict[str, Any] | None = None


def _load_notebook_cells(notebook_path: Path) -> list[str]:
    with notebook_path.open("r", encoding="utf-8") as notebook_file:
        notebook_payload = json.load(notebook_file)

    source_cells: list[str] = []
    for cell in notebook_payload.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source_text = "".join(cell.get("source", []))
        if not source_text.strip():
            continue
        source_text = source_text.replace(
            "from typing import Annotated, Any, NotRequired, TypedDict",
            "from typing import Annotated, Any, TypedDict\nfrom typing_extensions import NotRequired",
        )
        stripped_source_text = source_text.strip()
        if stripped_source_text.startswith("starting_page_url ="):
            continue
        if stripped_source_text in {"final_events", "print(final_events)"}:
            continue
        if any(skip_snippet in source_text for skip_snippet in SKIP_SOURCE_SNIPPETS):
            continue
        source_cells.append(source_text)
    return source_cells


def _load_pipeline_namespace() -> dict[str, Any]:
    global _pipeline_namespace_cache
    if _pipeline_namespace_cache is not None:
        return _pipeline_namespace_cache

    if not PIPELINE_NOTEBOOK_PATH.exists():
        raise FileNotFoundError(f"Notebook not found: {PIPELINE_NOTEBOOK_PATH}")

    module_name = "__main_extractor_notebook__"
    runtime_module = types.ModuleType(module_name)
    sys.modules[module_name] = runtime_module
    pipeline_namespace = runtime_module.__dict__
    pipeline_namespace.update(
        {
            "__name__": module_name,
            "NotRequired": TypingNotRequired,
        }
    )
    for source_text in _load_notebook_cells(PIPELINE_NOTEBOOK_PATH):
        exec(source_text, pipeline_namespace)

    graph = pipeline_namespace.get("graph")
    if graph is None:
        raise RuntimeError("Failed to initialize pipeline graph from notebook code")

    _pipeline_namespace_cache = pipeline_namespace
    return pipeline_namespace


async def run_pipeline_for_url(starting_page_url: str) -> list[dict[str, Any]]:
    normalized_url = (starting_page_url or "").strip()
    if not normalized_url:
        raise ValueError("starting_page_url must be non-empty")

    pipeline_namespace = _load_pipeline_namespace()
    graph = pipeline_namespace["graph"]

    pipeline_result = await graph.ainvoke(
        {
            "starting_page_url": normalized_url,
            "final_events": [],
        }
    )
    final_events = pipeline_result.get("final_events", [])
    if not isinstance(final_events, list):
        raise RuntimeError("Pipeline returned an invalid final_events payload")
    return final_events


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the event extraction pipeline for one starting page URL."
    )
    parser.add_argument(
        "--starting-page-url",
        required=True,
        help="Starting page URL to process.",
    )
    parser.add_argument(
        "--output-json-path",
        default="",
        help="Optional path to write final events as JSON.",
    )
    return parser.parse_args()


async def _async_main() -> int:
    arguments = parse_args()
    final_events = await run_pipeline_for_url(arguments.starting_page_url)

    print(
        json.dumps(
            final_events,
            ensure_ascii=True,
            indent=2,
        )
    )

    output_json_path_text = (arguments.output_json_path or "").strip()
    if output_json_path_text:
        output_json_path = Path(output_json_path_text).expanduser().resolve()
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        output_json_path.write_text(
            json.dumps(final_events, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        print(f"[main_extractor] Wrote output to {output_json_path}")

    print(f"[main_extractor] Completed with {len(final_events)} final event(s)")
    return 0


def main() -> None:
    raise_code = asyncio.run(_async_main())
    if raise_code != 0:
        raise SystemExit(raise_code)


if __name__ == "__main__":
    main()
