from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from ctxforge.memory.models import MemoryHit, MemoryKind, MemoryRecord, MemoryScope, SessionSummary, WorkingMemoryItem


SCHEMA_VERSION = 1
VALID_SCOPES = {"global", "project", "session"}
VALID_KINDS = {"preference", "fact", "decision", "summary", "working"}


class MemoryStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_records (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    session_id TEXT,
                    project_dir TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    embedding_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_scope_kind
                ON memory_records(scope, kind);

                CREATE INDEX IF NOT EXISTS idx_memory_project_session
                ON memory_records(project_dir, session_id);

                CREATE INDEX IF NOT EXISTS idx_memory_created_at
                ON memory_records(created_at);

                CREATE TABLE IF NOT EXISTS working_memory (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    project_dir TEXT NOT NULL,
                    key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_working_memory_session
                ON working_memory(project_dir, session_id, priority DESC, updated_at DESC);

                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY,
                    project_dir TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    source TEXT NOT NULL,
                    turn_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_session_summaries_project
                ON session_summaries(project_dir, updated_at DESC);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _now().isoformat()),
            )

    def upsert_record(self, record: MemoryRecord) -> MemoryRecord:
        normalized = _validate_record(record)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_records(
                    id, scope, kind, content, source, confidence, session_id, project_dir,
                    metadata_json, embedding_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    scope = excluded.scope,
                    kind = excluded.kind,
                    content = excluded.content,
                    source = excluded.source,
                    confidence = excluded.confidence,
                    session_id = excluded.session_id,
                    project_dir = excluded.project_dir,
                    metadata_json = excluded.metadata_json,
                    embedding_json = excluded.embedding_json,
                    updated_at = excluded.updated_at
                """,
                _record_to_row(normalized),
            )
        return normalized

    def add_record(
        self,
        *,
        content: str,
        source: str,
        scope: MemoryScope = "project",
        kind: MemoryKind = "fact",
        confidence: float = 0.8,
        session_id: str | None = None,
        project_dir: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> MemoryRecord:
        now = _now()
        return self.upsert_record(
            MemoryRecord(
                id=f"mem-{uuid4().hex[:16]}",
                scope=scope,
                kind=kind,
                content=content,
                source=source,
                confidence=confidence,
                session_id=session_id,
                project_dir=project_dir,
                metadata=metadata or {},
                embedding=None,
                created_at=now,
                updated_at=now,
            )
        )

    def list_records(
        self,
        *,
        scope: MemoryScope | None = None,
        kind: MemoryKind | None = None,
        project_dir: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if scope is not None:
            clauses.append("scope = ?")
            params.append(scope)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if project_dir is not None:
            clauses.append("project_dir = ?")
            params.append(project_dir)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)

        query = "SELECT * FROM memory_records"
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"{query} ORDER BY created_at DESC, id ASC LIMIT ?"
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_record_from_row(row) for row in rows]

    def search_records(
        self,
        *,
        query: str,
        project_dir: str,
        session_id: str | None = None,
        limit: int = 5,
    ) -> list[MemoryHit]:
        records = self._candidate_records(project_dir=project_dir, session_id=session_id)
        tokens = _query_tokens(query)
        normalized_query = query.strip().lower()
        hits: list[MemoryHit] = []
        for record in records:
            score, reason = _score_record(record, tokens=tokens, normalized_query=normalized_query)
            if score > 0:
                hits.append(MemoryHit(record=record, score=score, reason=reason))

        return sorted(
            hits,
            key=lambda hit: (-hit.score, hit.record.kind, -hit.record.created_at.timestamp(), hit.record.id),
        )[:limit]

    def upsert_working_item(
        self,
        *,
        session_id: str,
        project_dir: str,
        key: str,
        content: str,
        source: str,
        priority: int = 0,
        item_id: str | None = None,
    ) -> WorkingMemoryItem:
        if not session_id.strip():
            raise ValueError("session_id is required")
        if not project_dir.strip():
            raise ValueError("project_dir is required")
        if not key.strip():
            raise ValueError("working memory key is required")
        if not content.strip():
            raise ValueError("working memory content is required")
        if not source.strip():
            raise ValueError("working memory source is required")

        existing = self._find_working_item(project_dir=project_dir, session_id=session_id, key=key)
        now = _now()
        item = WorkingMemoryItem(
            id=item_id or (existing.id if existing else f"work-{uuid4().hex[:16]}"),
            session_id=session_id,
            project_dir=project_dir,
            key=key.strip(),
            content=content.strip(),
            source=source.strip(),
            priority=priority,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO working_memory(
                    id, session_id, project_dir, key, content, source, priority, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    key = excluded.key,
                    content = excluded.content,
                    source = excluded.source,
                    priority = excluded.priority,
                    updated_at = excluded.updated_at
                """,
                (
                    item.id,
                    item.session_id,
                    item.project_dir,
                    item.key,
                    item.content,
                    item.source,
                    item.priority,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                ),
            )
        return item

    def list_working_items(self, *, project_dir: str, session_id: str, limit: int = 20) -> list[WorkingMemoryItem]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM working_memory
                WHERE project_dir = ? AND session_id = ?
                ORDER BY priority DESC, updated_at DESC, key ASC
                LIMIT ?
                """,
                (project_dir, session_id, limit),
            ).fetchall()
        return [_working_from_row(row) for row in rows]

    def clear_working_items(self, *, project_dir: str, session_id: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM working_memory WHERE project_dir = ? AND session_id = ?",
                (project_dir, session_id),
            )
            return cursor.rowcount

    def upsert_session_summary(
        self,
        *,
        session_id: str,
        project_dir: str,
        summary: str,
        source: str,
        turn_count: int = 0,
    ) -> SessionSummary:
        if not session_id.strip():
            raise ValueError("session_id is required")
        if not project_dir.strip():
            raise ValueError("project_dir is required")
        if not summary.strip():
            raise ValueError("summary is required")
        if not source.strip():
            raise ValueError("summary source is required")

        existing = self.get_session_summary(project_dir=project_dir, session_id=session_id)
        now = _now()
        item = SessionSummary(
            session_id=session_id,
            project_dir=project_dir,
            summary=summary.strip(),
            source=source.strip(),
            turn_count=turn_count,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO session_summaries(
                    session_id, project_dir, summary, source, turn_count, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    project_dir = excluded.project_dir,
                    summary = excluded.summary,
                    source = excluded.source,
                    turn_count = excluded.turn_count,
                    updated_at = excluded.updated_at
                """,
                (
                    item.session_id,
                    item.project_dir,
                    item.summary,
                    item.source,
                    item.turn_count,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                ),
            )
        return item

    def get_session_summary(self, *, project_dir: str, session_id: str) -> SessionSummary | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_summaries WHERE project_dir = ? AND session_id = ?",
                (project_dir, session_id),
            ).fetchone()
        return _summary_from_row(row) if row else None

    def count_records(self, *, project_dir: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM memory_records"
        params: list[object] = []
        if project_dir is not None:
            query = f"{query} WHERE scope = 'global' OR project_dir = ?"
            params.append(project_dir)
        with self._connect() as connection:
            return int(connection.execute(query, params).fetchone()[0])

    def count_session_summaries(self, *, project_dir: str) -> int:
        with self._connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM session_summaries WHERE project_dir = ?",
                    (project_dir,),
                ).fetchone()[0]
            )

    def _candidate_records(self, *, project_dir: str, session_id: str | None) -> list[MemoryRecord]:
        clauses = ["scope = 'global'", "(scope = 'project' AND project_dir = ?)"]
        params: list[object] = [project_dir]
        if session_id is not None:
            clauses.append("(scope = 'session' AND session_id = ? AND project_dir = ?)")
            params.extend([session_id, project_dir])
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM memory_records
                WHERE {' OR '.join(clauses)}
                ORDER BY created_at DESC, id ASC
                """,
                params,
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def _find_working_item(self, *, project_dir: str, session_id: str, key: str) -> WorkingMemoryItem | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM working_memory
                WHERE project_dir = ? AND session_id = ? AND key = ?
                """,
                (project_dir, session_id, key.strip()),
            ).fetchone()
        return _working_from_row(row) if row else None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def _validate_record(record: MemoryRecord) -> MemoryRecord:
    if record.scope not in VALID_SCOPES:
        raise ValueError(f"Invalid memory scope: {record.scope}")
    if record.kind not in VALID_KINDS:
        raise ValueError(f"Invalid memory kind: {record.kind}")
    if not record.content.strip():
        raise ValueError("memory content is required")
    if not record.source.strip():
        raise ValueError("memory source is required")
    if not 0.0 <= record.confidence <= 1.0:
        raise ValueError("memory confidence must be between 0 and 1")
    return replace(record, content=record.content.strip(), source=record.source.strip())


def _record_to_row(record: MemoryRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.scope,
        record.kind,
        record.content,
        record.source,
        record.confidence,
        record.session_id,
        record.project_dir,
        json.dumps(record.metadata, sort_keys=True),
        json.dumps(record.embedding) if record.embedding is not None else None,
        record.created_at.isoformat(),
        record.updated_at.isoformat(),
    )


def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        scope=row["scope"],
        kind=row["kind"],
        content=row["content"],
        source=row["source"],
        confidence=float(row["confidence"]),
        session_id=row["session_id"],
        project_dir=row["project_dir"],
        metadata=json.loads(row["metadata_json"] or "{}"),
        embedding=json.loads(row["embedding_json"]) if row["embedding_json"] else None,
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _working_from_row(row: sqlite3.Row) -> WorkingMemoryItem:
    return WorkingMemoryItem(
        id=row["id"],
        session_id=row["session_id"],
        project_dir=row["project_dir"],
        key=row["key"],
        content=row["content"],
        source=row["source"],
        priority=int(row["priority"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _summary_from_row(row: sqlite3.Row) -> SessionSummary:
    return SessionSummary(
        session_id=row["session_id"],
        project_dir=row["project_dir"],
        summary=row["summary"],
        source=row["source"],
        turn_count=int(row["turn_count"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _query_tokens(query: str) -> set[str]:
    lowered = query.lower()
    ascii_tokens = {token for token in re.split(r"[^a-z0-9]+", lowered) if len(token) > 1}
    cjk_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", lowered))
    return ascii_tokens | cjk_tokens


def _score_record(record: MemoryRecord, *, tokens: set[str], normalized_query: str) -> tuple[float, str]:
    content = record.content.lower()
    overlap = sum(1 for token in tokens if token in content)
    exact_phrase = bool(normalized_query and normalized_query in content)
    if overlap == 0 and not exact_phrase:
        return 0.0, "no_match"

    scope_boost = {"session": 0.30, "project": 0.20, "global": 0.10}[record.scope]
    recency_boost = _recency_boost(record.created_at)
    score = overlap + (2.0 if exact_phrase else 0.0) + (record.confidence * 0.5) + recency_boost + scope_boost
    parts = [
        "scope_match",
        f"keyword_overlap({overlap})",
        f"confidence({record.confidence:.2f})",
    ]
    if exact_phrase:
        parts.append("exact_phrase")
    if recency_boost:
        parts.append(f"recency({recency_boost:.2f})")
    return score, " + ".join(parts)


def _recency_boost(created_at: datetime) -> float:
    age = _now() - created_at
    if age.days <= 7:
        return 0.20
    if age.days <= 30:
        return 0.10
    return 0.0


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
