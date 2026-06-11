from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterator

from ctxforge.cache.models import (
    CacheBaseline,
    CacheHistoryEntry,
    CacheReport,
    CacheSectionSnapshot,
    CacheSnapshot,
)


CACHE_SCHEMA_VERSION = 1
logger = logging.getLogger(__name__)


class CacheStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cache_snapshots (
                    id TEXT PRIMARY KEY,
                    format_version INTEGER NOT NULL,
                    project_key TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_bytes BLOB NOT NULL,
                    prompt_sha256 TEXT NOT NULL,
                    messages_sha256 TEXT NOT NULL,
                    stable_prefix_sha256 TEXT NOT NULL,
                    total_estimated_tokens INTEGER NOT NULL,
                    sections_json TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    request_id TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_cache_scope_created
                ON cache_snapshots(
                    project_key, provider, base_url, model, format_version, created_at DESC
                );

                CREATE INDEX IF NOT EXISTS idx_cache_session_created
                ON cache_snapshots(project_key, session_id, created_at DESC);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO cache_schema_migrations(version, applied_at) VALUES (?, ?)",
                (CACHE_SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )

    def save(
        self,
        snapshot: CacheSnapshot,
        report: CacheReport,
        *,
        request_id: str | None,
        retention: int,
    ) -> None:
        if retention < 1:
            raise ValueError("cache retention must be at least 1")
        _validate_snapshot(snapshot)
        if report.snapshot_id != snapshot.id:
            raise ValueError("cache report snapshot id does not match snapshot")
        sections_json = json.dumps(
            [section.to_dict() for section in snapshot.sections],
            ensure_ascii=False,
            sort_keys=True,
        )
        report_json = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cache_snapshots(
                    id, format_version, project_key, session_id, provider, base_url, model,
                    prompt_bytes, prompt_sha256, messages_sha256, stable_prefix_sha256,
                    total_estimated_tokens, sections_json, report_json, request_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.id,
                    snapshot.format_version,
                    snapshot.project_key,
                    snapshot.session_id,
                    snapshot.provider,
                    snapshot.base_url,
                    snapshot.model,
                    snapshot.prompt_bytes,
                    snapshot.prompt_sha256,
                    snapshot.messages_sha256,
                    snapshot.stable_prefix_sha256,
                    snapshot.total_estimated_tokens,
                    sections_json,
                    report_json,
                    request_id,
                    snapshot.created_at.isoformat(),
                ),
            )
            connection.execute(
                """
                DELETE FROM cache_snapshots
                WHERE id IN (
                    SELECT id
                    FROM cache_snapshots
                    WHERE project_key = ?
                      AND provider = ?
                      AND base_url = ?
                      AND model = ?
                      AND format_version = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (
                    snapshot.project_key,
                    snapshot.provider,
                    snapshot.base_url,
                    snapshot.model,
                    snapshot.format_version,
                    retention,
                ),
            )

    def find_baseline(
        self,
        current: CacheSnapshot,
        *,
        allow_project_fallback: bool,
    ) -> CacheBaseline | None:
        common_params = (
            current.project_key,
            current.provider,
            current.base_url,
            current.model,
            current.format_version,
        )
        with self._connect() as connection:
            session_rows = connection.execute(
                """
                SELECT *
                FROM cache_snapshots
                WHERE project_key = ?
                  AND provider = ?
                  AND base_url = ?
                  AND model = ?
                  AND format_version = ?
                  AND session_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (*common_params, current.session_id),
            ).fetchall()
            snapshot = self._first_valid_snapshot(session_rows)
            if snapshot is not None:
                return CacheBaseline(snapshot=snapshot, scope="session")

            if not allow_project_fallback:
                return None
            project_rows = connection.execute(
                """
                SELECT *
                FROM cache_snapshots
                WHERE project_key = ?
                  AND provider = ?
                  AND base_url = ?
                  AND model = ?
                  AND format_version = ?
                ORDER BY created_at DESC, id DESC
                """,
                common_params,
            ).fetchall()
        snapshot = self._first_valid_snapshot(project_rows)
        if snapshot is None:
            return None
        return CacheBaseline(snapshot=snapshot, scope="project_fallback")

    def list_history(
        self,
        *,
        project_key: str,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[CacheHistoryEntry]:
        if limit < 1:
            raise ValueError("cache history limit must be at least 1")
        clauses = ["project_key = ?"]
        params: list[object] = [project_key]
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM cache_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        entries: list[CacheHistoryEntry] = []
        for row in rows:
            try:
                snapshot = _snapshot_from_row(row)
                raw_report = json.loads(row["report_json"])
                if not isinstance(raw_report, dict):
                    raise ValueError("cache report JSON was not an object")
                report = CacheReport.from_dict(raw_report)
                if report.snapshot_id != snapshot.id:
                    raise ValueError("cache report snapshot id does not match snapshot")
                entries.append(
                    CacheHistoryEntry(
                        snapshot=snapshot,
                        report=report,
                        request_id=row["request_id"],
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("Skipping invalid cache snapshot %s: %s", row["id"], exc)
        return entries

    def count(self, *, project_key: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM cache_snapshots"
        params: list[object] = []
        if project_key is not None:
            query += " WHERE project_key = ?"
            params.append(project_key)
        with self._connect() as connection:
            return int(connection.execute(query, params).fetchone()[0])

    def _first_valid_snapshot(self, rows: list[sqlite3.Row]) -> CacheSnapshot | None:
        for row in rows:
            try:
                return _snapshot_from_row(row)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("Skipping invalid cache snapshot %s: %s", row["id"], exc)
        return None

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


def _snapshot_from_row(row: sqlite3.Row) -> CacheSnapshot:
    raw_sections = json.loads(row["sections_json"])
    if not isinstance(raw_sections, list):
        raise ValueError("cache sections JSON was not a list")
    if any(not isinstance(item, dict) for item in raw_sections):
        raise ValueError("cache sections JSON contained an invalid item")
    sections = [CacheSectionSnapshot.from_dict(item) for item in raw_sections]
    snapshot = CacheSnapshot(
        id=row["id"],
        format_version=int(row["format_version"]),
        project_key=row["project_key"],
        session_id=row["session_id"],
        provider=row["provider"],
        base_url=row["base_url"],
        model=row["model"],
        prompt_bytes=bytes(row["prompt_bytes"]),
        prompt_sha256=row["prompt_sha256"],
        messages_sha256=row["messages_sha256"],
        stable_prefix_sha256=row["stable_prefix_sha256"],
        total_estimated_tokens=int(row["total_estimated_tokens"]),
        sections=sections,
        created_at=datetime.fromisoformat(row["created_at"]),
    )
    _validate_snapshot(snapshot)
    return snapshot


def _validate_snapshot(snapshot: CacheSnapshot) -> None:
    if sha256(snapshot.prompt_bytes).hexdigest() != snapshot.prompt_sha256:
        raise ValueError("cache prompt hash does not match prompt bytes")
    if snapshot.total_estimated_tokens < 0:
        raise ValueError("cache token estimate must not be negative")
    if snapshot.sections and snapshot.sections[0].start_byte != 0:
        raise ValueError("cache section spans must start at byte zero")

    seen_keys: set[str] = set()
    previous_end = 0
    for index, section in enumerate(snapshot.sections):
        if section.key in seen_keys:
            raise ValueError("cache section keys must be unique")
        seen_keys.add(section.key)
        if section.ordinal != index:
            raise ValueError("cache section ordinals must be contiguous")
        if section.key != f"{section.name}#{section.ordinal}":
            raise ValueError("cache section key does not match name and ordinal")
        if section.start_byte < previous_end or section.end_byte < section.start_byte:
            raise ValueError("cache section spans overlap or are reversed")
        if section.end_byte > len(snapshot.prompt_bytes):
            raise ValueError("cache section span exceeds prompt bytes")
        rendered_bytes = snapshot.prompt_bytes[section.start_byte : section.end_byte]
        if sha256(rendered_bytes).hexdigest() != section.rendered_sha256:
            raise ValueError("cache rendered section hash does not match prompt bytes")
        try:
            header, body = rendered_bytes.decode("utf-8").split("\n", 1)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("cache rendered section is not valid UTF-8") from exc
        header_prefix = (
            f'<context_section name="{section.name}" '
            f'stability="{section.stability}" priority="'
        )
        header_suffix = f'" source="{section.source}">'
        if not header.startswith(header_prefix) or not header.endswith(header_suffix):
            raise ValueError("cache section metadata does not match rendered prompt")
        footer = "\n</context_section>"
        if not body.endswith(footer):
            raise ValueError("cache rendered section footer is invalid")
        content = body[: -len(footer)]
        if sha256(content.encode("utf-8")).hexdigest() != section.content_sha256:
            raise ValueError("cache section content hash does not match prompt bytes")
        previous_end = section.end_byte

    if snapshot.sections and previous_end != len(snapshot.prompt_bytes):
        raise ValueError("cache section spans do not cover the full prompt")
    if not snapshot.sections and snapshot.prompt_bytes:
        raise ValueError("cache prompt bytes require section spans")
