from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_ = load_dotenv()

DEFAULT_BATCH_LIMIT = 20


class EventOccurrenceModelType(BaseModel):
    date: str = Field(description="Occurrence date in YYYY-MM-DD format")
    start_time: str | None = Field(default=None, description="Start time in HH:MM:SS format")
    end_time: str | None = Field(default=None, description="End time in HH:MM:SS format")


class EventOccurrencesExpansionModelType(BaseModel):
    occurrences: list[EventOccurrenceModelType] = Field(default_factory=list)


DATE_EXPANSION_PROMPT = """
You are an event date/time normalization assistant.

Reference context:
- Today is: {today_date}

Input fields:
- event_name: {event_name}
- event_date (freeform): {event_date}
- event_time (freeform): {event_time}
- event_description (optional): {event_description}

Return format:
- Return a list of objects in this exact shape:
  {{
    "date": "YYYY-MM-DD",
    "start_time": "HH:MM:SS" | null,
    "end_time": "HH:MM:SS" | null
  }}

Rules:
1) Include all date occurrences implied by the text.
2) If a year is missing, infer year using today's date as anchor.
3) Prefer upcoming/current-season interpretation over unsupported historical years.
4) For recurring text, start from the first occurrence on or after today unless source text clearly bounds a historical range.
5) For ranges crossing Dec-Jan, assign the correct year to each day.
6) If time is missing, set start_time and end_time to null.
7) If end time is missing, leave it null.
8) Use 24-hour HH:MM:SS.
9) Do not invent dates not supported by the text.
""".strip()


def _build_async_postgres_url() -> str:
    raw_postgres_url = os.environ.get("POSTGRES_URL_2") or os.environ.get("POSTGRES_URL")
    if not raw_postgres_url:
        raise ValueError("POSTGRES_URL_2 or POSTGRES_URL must be set")

    async_postgres_url = raw_postgres_url
    if async_postgres_url.startswith("postgres://"):
        async_postgres_url = async_postgres_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif async_postgres_url.startswith("postgresql://") and "+asyncpg" not in async_postgres_url:
        async_postgres_url = async_postgres_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return async_postgres_url


def _is_valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _is_valid_time_or_none(value: str | None) -> bool:
    if value is None:
        return True
    try:
        datetime.strptime(value, "%H:%M:%S")
        return True
    except ValueError:
        return False


def normalize_occurrences(
    occurrences: list[EventOccurrenceModelType],
) -> list[EventOccurrenceModelType]:
    unique_items: dict[tuple[str, str | None, str | None], EventOccurrenceModelType] = {}
    for occurrence in occurrences:
        if not _is_valid_date(occurrence.date):
            continue
        if not _is_valid_time_or_none(occurrence.start_time):
            continue
        if not _is_valid_time_or_none(occurrence.end_time):
            continue
        dedupe_key = (occurrence.date, occurrence.start_time, occurrence.end_time)
        unique_items[dedupe_key] = occurrence

    return sorted(
        unique_items.values(),
        key=lambda occurrence: (
            occurrence.date,
            occurrence.start_time or "",
            occurrence.end_time or "",
        ),
    )


def occurrences_to_payload(
    occurrences: list[EventOccurrenceModelType],
) -> list[dict[str, str | None]]:
    return [occurrence.model_dump() for occurrence in occurrences]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Expand event occurrences and write JSONB updates.")
    parser.add_argument(
        "--mode",
        choices=("dry-run", "apply"),
        default="apply",
        help="Choose dry-run to preview only, or apply to write updates.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_BATCH_LIMIT,
        help="Max rows to process (<=0 means no limit).",
    )
    parser.add_argument(
        "--today-date",
        default=datetime.now().date().isoformat(),
        help="Anchor date in YYYY-MM-DD format for year inference.",
    )
    return parser.parse_args()


async def expand_event_occurrences(
    research_model: ChatOpenAI,
    event_name: str | None,
    event_date: str | None,
    event_time: str | None,
    event_description: str | None,
    today_date: str,
) -> EventOccurrencesExpansionModelType:
    prompt = DATE_EXPANSION_PROMPT.format(
        today_date=today_date,
        event_name=(event_name or "").strip() or "null",
        event_date=(event_date or "").strip() or "null",
        event_time=(event_time or "").strip() or "null",
        event_description=(event_description or "").strip() or "null",
    )
    structured_model = research_model.with_structured_output(EventOccurrencesExpansionModelType)
    return await structured_model.ainvoke(prompt)


async def fetch_events_for_expansion(
    engine: object,
    limit: int,
) -> list[dict[str, object]]:
    is_unlimited = limit <= 0
    print(f"[fetch] Querying events with empty occurrences (limit={limit if not is_unlimited else 'ALL'})")

    base_query = """
        SELECT
            id,
            event_name,
            event_date,
            event_time,
            event_description,
            occurrences
        FROM events
        WHERE occurrences = '{}'::jsonb
           OR occurrences = '[]'::jsonb
        ORDER BY created_at DESC
    """
    if not is_unlimited:
        base_query += "\nLIMIT :limit"

    query = text(base_query)
    async with engine.connect() as connection:
        params = {} if is_unlimited else {"limit": limit}
        result = await connection.execute(query, params)
        rows = [dict(row._mapping) for row in result]

    print(f"[fetch] Retrieved {len(rows)} row(s)")
    return rows


async def update_event_occurrences_jsonb(
    engine: object,
    event_id: str,
    occurrences_payload: list[dict[str, str | None]],
) -> None:
    print(f"[update] Writing {len(occurrences_payload)} occurrence(s) for event_id={event_id}")
    update_statement = text(
        """
        UPDATE events
        SET occurrences = CAST(:occurrences_json AS jsonb)
        WHERE id = CAST(:event_id AS uuid)
          AND (
            occurrences = '{}'::jsonb
            OR occurrences = '[]'::jsonb
          )
        """
    )

    async with engine.begin() as connection:
        update_result = await connection.execute(
            update_statement,
            {
                "occurrences_json": json.dumps(occurrences_payload),
                "event_id": event_id,
            },
        )

    if update_result.rowcount == 0:
        raise ValueError(f"Event {event_id} was not updated because occurrences was not empty")
    print(f"[update] Success for event_id={event_id}")


async def dry_run_date_expansion(
    engine: object,
    research_model: ChatOpenAI,
    limit: int,
    today_date: str,
) -> list[dict[str, object]]:
    rows = await fetch_events_for_expansion(engine=engine, limit=limit)
    preview: list[dict[str, object]] = []

    for row_index, row in enumerate(rows, start=1):
        event_id = str(row["id"])
        event_name = str(row.get("event_name") or "")
        print(f"[dry-run] {row_index}/{len(rows)} expanding event_id={event_id} name={event_name!r}")

        expansion = await expand_event_occurrences(
            research_model=research_model,
            event_name=event_name,
            event_date=str(row.get("event_date") or ""),
            event_time=str(row.get("event_time") or ""),
            event_description=str(row.get("event_description") or ""),
            today_date=today_date,
        )
        normalized = normalize_occurrences(expansion.occurrences)
        payload = occurrences_to_payload(normalized)
        print(f"[dry-run] event_id={event_id} raw={len(expansion.occurrences)} normalized={len(normalized)}")

        preview.append(
            {
                "id": event_id,
                "event_name": row.get("event_name"),
                "event_date": row.get("event_date"),
                "event_time": row.get("event_time"),
                "occurrences_preview": payload,
            }
        )
    return preview


async def apply_date_expansion_updates(
    engine: object,
    research_model: ChatOpenAI,
    limit: int,
    today_date: str,
) -> list[str]:
    print(f"[apply] Starting updates (limit={limit if limit > 0 else 'ALL'})")
    rows = await fetch_events_for_expansion(engine=engine, limit=limit)
    updated_ids: list[str] = []

    for row_index, row in enumerate(rows, start=1):
        event_id = str(row["id"])
        event_name = str(row.get("event_name") or "")
        print(f"[apply] {row_index}/{len(rows)} processing event_id={event_id} name={event_name!r}")

        expansion = await expand_event_occurrences(
            research_model=research_model,
            event_name=event_name,
            event_date=str(row.get("event_date") or ""),
            event_time=str(row.get("event_time") or ""),
            event_description=str(row.get("event_description") or ""),
            today_date=today_date,
        )
        normalized = normalize_occurrences(expansion.occurrences)
        payload = occurrences_to_payload(normalized)
        print(f"[apply] event_id={event_id} raw={len(expansion.occurrences)} normalized={len(normalized)}")

        await update_event_occurrences_jsonb(
            engine=engine,
            event_id=event_id,
            occurrences_payload=payload,
        )
        updated_ids.append(event_id)

    print(f"[apply] Completed. Updated {len(updated_ids)} row(s)")
    return updated_ids


async def _async_main() -> int:
    arguments = parse_args()
    async_postgres_url = _build_async_postgres_url()
    engine = create_async_engine(async_postgres_url)
    research_model = ChatOpenAI(model="gpt-5-mini", temperature=0)

    try:
        if arguments.mode == "dry-run":
            preview = await dry_run_date_expansion(
                engine=engine,
                research_model=research_model,
                limit=arguments.limit,
                today_date=arguments.today_date,
            )
            print(json.dumps(preview[:5], ensure_ascii=True, indent=2))
            print(f"[date_expander] Dry run complete with {len(preview)} row(s)")
            return 0

        updated_event_ids = await apply_date_expansion_updates(
            engine=engine,
            research_model=research_model,
            limit=arguments.limit,
            today_date=arguments.today_date,
        )
        print(json.dumps(updated_event_ids[:10], ensure_ascii=True, indent=2))
        print(f"[date_expander] Apply complete with {len(updated_event_ids)} update(s)")
        return 0
    finally:
        await engine.dispose()


def main() -> None:
    raise_code = asyncio.run(_async_main())
    if raise_code != 0:
        raise SystemExit(raise_code)


if __name__ == "__main__":
    main()
