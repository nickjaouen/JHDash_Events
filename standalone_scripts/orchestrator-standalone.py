"""Run main_extractor for each configured URL, then date_expander apply.

Expects ACTIVE_STARTING_PAGE_URLS in the environment (comma- or newline-separated URLs).
Loads .env from the project root via dotenv.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


def resolve_project_paths() -> tuple[Path, Path]:
    scripts_dir = Path(__file__).resolve().parent
    project_root = scripts_dir.parent
    if scripts_dir.name != "standalone_scripts":
        raise RuntimeError(
            f"Expected orchestrator.py inside standalone_scripts/; got {scripts_dir}."
        )
    return project_root, scripts_dir


def parse_starting_page_urls(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        raise RuntimeError(
            "ACTIVE_STARTING_PAGE_URLS is not set or empty. "
            "Set it to one or more URLs separated by commas (or newlines)."
        )
    urls: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        url = chunk.strip()
        if url:
            urls.append(url)
    if not urls:
        raise RuntimeError(
            "ACTIVE_STARTING_PAGE_URLS contained no non-empty URLs after parsing."
        )
    return urls


def run_command(command_parts: list[str], label: str, cwd: Path) -> int:
    print(f"\n[{label}] Running command:")
    print(" ".join(command_parts))

    started_at = time.perf_counter()
    completed_process = subprocess.run(
        command_parts,
        cwd=str(cwd),
        check=False,
    )
    elapsed_seconds = time.perf_counter() - started_at
    print(
        f"[{label}] Exit code={completed_process.returncode} elapsed={elapsed_seconds:.1f}s"
    )
    return completed_process.returncode


def main() -> int:
    project_root, scripts_dir = resolve_project_paths()
    _ = load_dotenv(project_root / ".env")

    parser = argparse.ArgumentParser(
        description="Run main_extractor per URL then date_expander apply."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Passed to date_expander.py --limit (default: 500)",
    )
    args = parser.parse_args()

    main_extractor_path = scripts_dir / "main_extractor.py"
    date_expander_path = scripts_dir / "date_expander.py"

    venv_python_path = project_root / ".venv" / "bin" / "python"
    python_runner = str(venv_python_path if venv_python_path.exists() else Path(sys.executable))

    starting_urls = parse_starting_page_urls(os.environ.get("ACTIVE_STARTING_PAGE_URLS"))

    print("Starting orchestrator")
    print(f"[orchestrator] Project root: {project_root}")
    print(f"[orchestrator] Python: {python_runner}")

    if not main_extractor_path.exists():
        raise FileNotFoundError(f"Missing script: {main_extractor_path}")
    if not date_expander_path.exists():
        raise FileNotFoundError(f"Missing script: {date_expander_path}")

    print(f"[orchestrator] Active URLs: {len(starting_urls)}")

    url_results: list[dict[str, object]] = []
    for url_index, starting_page_url in enumerate(starting_urls, start=1):
        url_label = f"URL {url_index}/{len(starting_urls)}"
        print(f"\n[orchestrator] {url_label} -> {starting_page_url}")

        command_parts = [
            python_runner,
            str(main_extractor_path),
            "--starting-page-url",
            starting_page_url,
        ]
        return_code = run_command(command_parts, url_label, project_root)

        url_results.append(
            {
                "url": starting_page_url,
                "return_code": return_code,
                "status": "ok" if return_code == 0 else "failed",
            }
        )

    print("\n[orchestrator] URL pipeline summary")
    for result in url_results:
        print(f"- {result['status']}: {result['url']} (code={result['return_code']})")

    failed_count = sum(1 for result in url_results if result["return_code"] != 0)
    print(
        f"[orchestrator] URL run complete. total={len(url_results)} failed={failed_count}"
    )

    if failed_count:
        return 1

    print("\n[orchestrator] Running date expansion apply step")
    date_expander_command_parts = [
        python_runner,
        str(date_expander_path),
        "--mode",
        "apply",
        "--limit",
        str(args.limit),
    ]
    date_expander_return_code = run_command(
        date_expander_command_parts,
        "date_expander",
        project_root,
    )

    if date_expander_return_code != 0:
        print(
            f"[orchestrator] date_expander.py failed with return code {date_expander_return_code}",
            file=sys.stderr,
        )
        return date_expander_return_code

    print("\n[orchestrator] Done")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"[orchestrator] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
