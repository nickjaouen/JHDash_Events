from __future__ import annotations

import json
import os
import uuid
from typing import Any

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings
from langchain_postgres import PGEngine, PGVectorStore

_ = load_dotenv()

TABLE_NAME = "canonical_names"
EVENTS_TABLE_NAME = "events"
SUPPORTED_NAME_TYPES = {"venue_name", "host_organization_name"}
EVENT_METADATA_COLUMNS = [
    "event_name",
    "event_date",
    "event_time",
    "source_url",
    "main_url",
    "secondary_url",
    "event_address",
    "venue_name",
    "host_organization_name",
    "event_description",
    "event_status",
]

_pg_engine: PGEngine | None = None
_name_store: PGVectorStore | None = None
_event_store: PGVectorStore | None = None
_embeddings: OpenAIEmbeddings | None = None


def _events_database_dsn() -> str:
    postgres_url = os.environ.get("POSTGRES_URL_2") or os.environ.get("POSTGRES_URL")
    if not postgres_url:
        raise ValueError("POSTGRES_URL_2 or POSTGRES_URL must be set for events DB access")
    if postgres_url.startswith("postgresql+asyncpg://"):
        return postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return postgres_url


def _normalize_name_type(name_type: str) -> str:
    normalized_name_type = (name_type or "").strip()
    if normalized_name_type not in SUPPORTED_NAME_TYPES:
        raise ValueError(f"Unsupported name_type: {name_type}")
    return normalized_name_type


def _normalize_name_value(name_value: str) -> str:
    return (name_value or "").strip().casefold()


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized_value = str(value).strip()
    return normalized_value or None


def build_event_composite(
    event_date: str | None,
    event_name: str | None,
    host_organization_name: str | None,
) -> str:
    composite_parts = [
        _normalize_optional_text(event_date),
        _normalize_optional_text(event_name),
        _normalize_optional_text(host_organization_name),
    ]
    return " | ".join(part for part in composite_parts if part)


async def _get_name_store() -> tuple[PGVectorStore, OpenAIEmbeddings]:
    global _pg_engine
    global _name_store
    global _embeddings

    if _name_store is not None and _embeddings is not None:
        return _name_store, _embeddings

    postgres_url = os.environ.get("POSTGRES_URL_2") or os.environ.get("POSTGRES_URL")
    if not postgres_url:
        raise ValueError("POSTGRES_URL_2 or POSTGRES_URL must be set for PGVectorStore")

    _embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    _pg_engine = PGEngine.from_connection_string(url=postgres_url)
    _name_store = await PGVectorStore.create(
        engine=_pg_engine,
        table_name=TABLE_NAME,
        embedding_service=_embeddings,
        id_column="id",
        content_column="raw_name",
        embedding_column="embedding",
        metadata_columns=["name_type", "canonical_name", "website_url"],
    )
    return _name_store, _embeddings


async def _get_event_store() -> tuple[PGVectorStore, OpenAIEmbeddings]:
    global _event_store

    if _event_store is not None and _embeddings is not None:
        return _event_store, _embeddings

    _, embeddings = await _get_name_store()
    if _pg_engine is None:
        raise ValueError("PG engine is not initialized")

    _event_store = await PGVectorStore.create(
        engine=_pg_engine,
        table_name=EVENTS_TABLE_NAME,
        embedding_service=embeddings,
        id_column="id",
        content_column="event_composite",
        embedding_column="embedding",
        metadata_columns=EVENT_METADATA_COLUMNS,
    )
    return _event_store, embeddings


def _event_record_from_doc(doc: Document) -> dict[str, Any]:
    metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
    return {
        "id": doc.id,
        "event_composite": doc.page_content,
        "event_name": metadata.get("event_name"),
        "event_date": metadata.get("event_date"),
        "event_time": metadata.get("event_time"),
        "source_url": metadata.get("source_url"),
        "main_url": metadata.get("main_url"),
        "secondary_url": metadata.get("secondary_url"),
        "event_address": metadata.get("event_address"),
        "venue_name": metadata.get("venue_name"),
        "host_organization_name": metadata.get("host_organization_name"),
        "event_description": metadata.get("event_description"),
        "event_status": metadata.get("event_status"),
    }


async def find_existing_by_raw_name_and_type(
    raw_name: str,
    name_type: str,
) -> dict[str, Any] | None:
    normalized_raw_name = (raw_name or "").strip()
    normalized_name_type = _normalize_name_type(name_type)
    if not normalized_raw_name:
        return None

    name_store, embeddings = await _get_name_store()
    query_vector = embeddings.embed_query(normalized_raw_name)
    filter_value = {"name_type": {"$eq": normalized_name_type}}
    docs = await name_store.asimilarity_search_by_vector(query_vector, k=10, filter=filter_value)

    target_raw_name = _normalize_name_value(normalized_raw_name)
    for doc in docs:
        candidate_raw_name = _normalize_name_value(str(doc.page_content or ""))
        if candidate_raw_name != target_raw_name:
            continue

        metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
        return {
            "id": doc.id,
            "raw_name": doc.page_content,
            "name_type": metadata.get("name_type"),
            "canonical_name": metadata.get("canonical_name"),
            "website_url": metadata.get("website_url"),
        }

    return None


async def vector_search_canonical_names(
    query: str,
    name_type: str,
    k: int = 3,
) -> list[dict[str, Any]]:
    normalized_name_type = _normalize_name_type(name_type)
    normalized_query = (query or "").strip()
    if not normalized_query:
        return []

    name_store, embeddings = await _get_name_store()
    query_vector = embeddings.embed_query(normalized_query)
    filter_value = {"name_type": {"$eq": normalized_name_type}}
    docs = await name_store.asimilarity_search_by_vector(query_vector, k=k, filter=filter_value)

    results: list[dict[str, Any]] = []
    for doc in docs:
        metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
        results.append(
            {
                "id": doc.id,
                "raw_name": doc.page_content,
                "name_type": metadata.get("name_type"),
                "canonical_name": metadata.get("canonical_name"),
                "website_url": metadata.get("website_url"),
            }
        )
    return results


async def vector_search_events_by_composite(
    event_composite: str,
    k: int = 3,
) -> list[dict[str, Any]]:
    normalized_event_composite = _normalize_optional_text(event_composite)
    if not normalized_event_composite:
        return []

    event_store, embeddings = await _get_event_store()
    query_vector = embeddings.embed_query(normalized_event_composite)
    docs = await event_store.asimilarity_search_by_vector(query_vector, k=k)
    return [_event_record_from_doc(doc) for doc in docs]


def _event_metadata_payload(event_record: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_name": _normalize_optional_text(event_record.get("event_name")),
        "event_date": _normalize_optional_text(event_record.get("event_date")),
        "event_time": _normalize_optional_text(event_record.get("event_time")),
        "source_url": _normalize_optional_text(event_record.get("source_url")),
        "main_url": _normalize_optional_text(event_record.get("main_url")),
        "secondary_url": _normalize_optional_text(event_record.get("secondary_url")),
        "event_address": _normalize_optional_text(event_record.get("event_address")),
        "venue_name": _normalize_optional_text(event_record.get("venue_name")),
        "host_organization_name": _normalize_optional_text(
            event_record.get("host_organization_name")
        ),
        "event_description": _normalize_optional_text(event_record.get("event_description")),
        "event_status": _normalize_optional_text(event_record.get("event_status")),
    }


async def upsert_event_record(
    event_record: dict[str, Any],
    event_id: str | None = None,
) -> str:
    event_store, _ = await _get_event_store()
    normalized_metadata = _event_metadata_payload(event_record)
    event_name = normalized_metadata.get("event_name")
    if event_name is None:
        raise ValueError("event_name must be non-empty")

    event_composite = build_event_composite(
        event_date=normalized_metadata.get("event_date"),
        event_name=event_name,
        host_organization_name=normalized_metadata.get("host_organization_name"),
    )
    if not event_composite:
        raise ValueError("event_composite must be non-empty")

    persisted_event_id = _normalize_optional_text(event_id) or str(uuid.uuid4())
    event_doc = Document(
        id=persisted_event_id,
        page_content=event_composite,
        metadata=normalized_metadata,
    )
    await event_store.aadd_documents([event_doc])
    return persisted_event_id


async def insert_canonical_name(
    raw_name: str,
    canonical_name: str,
    name_type: str,
    website_url: str | None = None,
) -> str:
    normalized_name_type = _normalize_name_type(name_type)
    normalized_raw_name = (raw_name or "").strip()
    normalized_canonical_name = (canonical_name or "").strip()
    if not normalized_raw_name:
        raise ValueError("raw_name must be non-empty")
    if not normalized_canonical_name:
        raise ValueError("canonical_name must be non-empty")

    existing_record = await find_existing_by_raw_name_and_type(
        raw_name=normalized_raw_name,
        name_type=normalized_name_type,
    )
    if existing_record and existing_record.get("id"):
        return str(existing_record["id"])

    name_store, _ = await _get_name_store()
    inserted_id = str(uuid.uuid4())
    metadata = {
        "name_type": normalized_name_type,
        "canonical_name": normalized_canonical_name,
        "website_url": (website_url or "").strip() or None,
    }
    docs = [
        Document(
            id=inserted_id,
            page_content=normalized_raw_name,
            metadata=metadata,
        )
    ]
    try:
        await name_store.aadd_documents(docs)
    except Exception as insert_error:
        error_message = str(insert_error)
        duplicate_raw_name_type_violation = (
            "raw_name_name_type" in error_message
            or "(raw_name, name_type)" in error_message
        )
        if not duplicate_raw_name_type_violation:
            raise

        existing_after_race = await find_existing_by_raw_name_and_type(
            raw_name=normalized_raw_name,
            name_type=normalized_name_type,
        )
        if existing_after_race and existing_after_race.get("id"):
            return str(existing_after_race["id"])
        raise

    return inserted_id


async def update_event_occurrences(
    event_id: str,
    occurrences: list[dict[str, str | None]],
) -> int:
    normalized_event_id = _normalize_optional_text(event_id)
    if normalized_event_id is None:
        raise ValueError("event_id must be non-empty")

    import asyncpg

    serialized_occurrences = json.dumps(occurrences)
    connection = await asyncpg.connect(dsn=_events_database_dsn())
    try:
        update_result = await connection.execute(
            """
            UPDATE events
            SET occurrences = $1::jsonb
            WHERE id = $2::uuid
              AND occurrences = '{}'::jsonb
            """,
            serialized_occurrences,
            normalized_event_id,
        )
    finally:
        await connection.close()

    updated_count = int(update_result.split(" ")[-1])
    if updated_count == 0:
        raise ValueError(
            f"No event row updated for id={normalized_event_id}; occurrences was not empty JSON"
        )
    return updated_count


@tool
async def vector_search_canonical_names_tool(
    query: str,
    name_type: str,
    k: int = 3,
) -> list[dict[str, Any]]:
    """Vector search canonical names by query and name_type."""
    return await vector_search_canonical_names(query=query, name_type=name_type, k=k)


@tool
async def vector_search_events_by_composite_tool(
    event_composite: str,
    k: int = 3,
) -> list[dict[str, Any]]:
    """Vector search events using event_composite text."""
    return await vector_search_events_by_composite(event_composite=event_composite, k=k)


@tool
async def upsert_event_record_tool(
    event_record: dict[str, Any],
    event_id: str | None = None,
) -> str:
    """Insert or update an event record by id."""
    return await upsert_event_record(event_record=event_record, event_id=event_id)


@tool
async def insert_canonical_name_tool(
    raw_name: str,
    canonical_name: str,
    name_type: str,
    website_url: str | None = None,
) -> str:
    """Insert a new canonical name record into PGVectorStore."""
    return await insert_canonical_name(
        raw_name=raw_name,
        canonical_name=canonical_name,
        name_type=name_type,
        website_url=website_url,
    )


@tool
async def update_event_occurrences_tool(
    event_id: str,
    occurrences: list[dict[str, str | None]],
) -> int:
    """Update events.occurrences JSONB by event id."""
    return await update_event_occurrences(event_id=event_id, occurrences=occurrences)

