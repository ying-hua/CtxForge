from __future__ import annotations

from ctxforge.memory import MemoryManager, MemoryStore


def test_memory_manager_returns_dynamic_sections_and_report(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    store.add_record(
        content="Use sqlite3 in Phase 2.",
        source="test",
        scope="project",
        kind="decision",
        project_dir=str(tmp_path),
    )
    store.upsert_working_item(
        project_dir=str(tmp_path),
        session_id="session-1",
        key="todo",
        content="Wire memory into context.",
        source="test",
        priority=5,
    )
    store.upsert_session_summary(
        project_dir=str(tmp_path),
        session_id="session-1",
        summary="Memory manager is under test.",
        source="test",
        turn_count=2,
    )

    context = MemoryManager(store).retrieve_for_context(
        task="How should Phase 2 memory work?",
        cwd=tmp_path,
        session_id="session-1",
    )

    sections = {section.name: section for section in context.sections}
    assert sections["memory.retrieved"].stability == "dynamic"
    assert sections["memory.retrieved"].source == "memory.search"
    assert sections["session.working_memory"].priority == 35
    assert sections["session.summary"].source == "memory.summary"
    assert context.report.status == "ok"
    assert context.report.retrieved_count == 1
    assert context.report.working_count == 1


def test_memory_manager_reports_empty_store(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()

    context = MemoryManager(store).retrieve_for_context(
        task="nothing yet",
        cwd=tmp_path,
        session_id="session-1",
    )

    assert context.report.status == "empty"
    assert context.report.retrieved_count == 0
    assert "No retrieved memories." in context.sections[0].content
