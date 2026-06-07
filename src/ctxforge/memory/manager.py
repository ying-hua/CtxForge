from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ctxforge.context import ContextSection
from ctxforge.memory.models import MemoryReport
from ctxforge.memory.render import render_retrieved_memories, render_session_summary, render_working_memory
from ctxforge.memory.store import MemoryStore


@dataclass(frozen=True)
class MemoryContext:
    sections: list[ContextSection]
    report: MemoryReport


class MemoryManager:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def retrieve_for_context(
        self,
        *,
        task: str,
        cwd: Path,
        session_id: str,
        limit: int = 5,
    ) -> MemoryContext:
        project_dir = str(cwd)
        hits = self._store.search_records(query=task, project_dir=project_dir, session_id=session_id, limit=limit)
        working_items = self._store.list_working_items(project_dir=project_dir, session_id=session_id)
        summary = self._store.get_session_summary(project_dir=project_dir, session_id=session_id)

        sections = [
            render_retrieved_memories(hits),
            render_working_memory(working_items),
        ]
        summary_section = render_session_summary(summary)
        if summary_section is not None:
            sections.append(summary_section)

        long_term_count = self._store.count_records(project_dir=project_dir)
        summary_count = self._store.count_session_summaries(project_dir=project_dir)
        status = "ok" if hits or working_items or summary else "empty"
        report = MemoryReport(
            status=status,
            db_path=str(self._store.db_path),
            working_count=len(working_items),
            summary_count=summary_count,
            long_term_count=long_term_count,
            retrieved_count=len(hits),
            hits=[hit.to_dict() for hit in hits],
        )
        return MemoryContext(sections=sections, report=report)
