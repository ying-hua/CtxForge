from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ctxforge.memory import MemoryRecord, MemoryStore


def test_store_initializes_schema_and_persists_records(tmp_path):
    db_path = tmp_path / ".ctxforge" / "ctxforge.sqlite3"
    store = MemoryStore(db_path)
    store.initialize()

    record = store.add_record(
        content="Use sqlite3 for Phase 2 memory.",
        source="test",
        scope="project",
        kind="decision",
        project_dir=str(tmp_path),
    )

    assert db_path.exists()
    records = store.list_records(scope="project", kind="decision", project_dir=str(tmp_path))
    assert records == [record]


def test_store_rejects_records_without_source(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError, match="source"):
        store.upsert_record(
            MemoryRecord(
                id="mem-invalid",
                scope="project",
                kind="fact",
                content="content",
                source=" ",
                confidence=0.8,
                project_dir=str(tmp_path),
                created_at=now,
                updated_at=now,
            )
        )


def test_project_scope_memory_does_not_cross_project_boundaries(tmp_path):
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    store.add_record(
        content="Project A uses sqlite memory.",
        source="test",
        scope="project",
        kind="fact",
        project_dir=str(project_a),
    )
    store.add_record(
        content="Global sqlite memory preference.",
        source="test",
        scope="global",
        kind="preference",
    )

    hits = store.search_records(query="sqlite memory", project_dir=str(project_b))

    assert [hit.record.scope for hit in hits] == ["global"]


def test_working_memory_and_session_summary_are_session_scoped(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    store.upsert_working_item(
        project_dir=str(tmp_path),
        session_id="session-1",
        key="todo",
        content="Finish Phase 2 tests.",
        source="test",
        priority=10,
    )
    store.upsert_working_item(
        project_dir=str(tmp_path),
        session_id="session-2",
        key="todo",
        content="Other session.",
        source="test",
    )
    store.upsert_session_summary(
        project_dir=str(tmp_path),
        session_id="session-1",
        summary="Phase 2 memory implementation started.",
        source="test",
        turn_count=3,
    )

    working = store.list_working_items(project_dir=str(tmp_path), session_id="session-1")
    summary = store.get_session_summary(project_dir=str(tmp_path), session_id="session-1")

    assert [item.content for item in working] == ["Finish Phase 2 tests."]
    assert summary is not None
    assert summary.turn_count == 3


def test_search_sorting_is_deterministic(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    first = store.add_record(
        content="sqlite memory context",
        source="test",
        scope="project",
        kind="fact",
        project_dir=str(tmp_path),
        confidence=0.8,
    )
    second = store.add_record(
        content="sqlite memory context",
        source="test",
        scope="project",
        kind="decision",
        project_dir=str(tmp_path),
        confidence=0.8,
    )

    hits = store.search_records(query="sqlite memory", project_dir=str(tmp_path), limit=10)

    assert [hit.record.id for hit in hits] == [second.id, first.id]
    assert all("keyword_overlap" in hit.reason for hit in hits)
