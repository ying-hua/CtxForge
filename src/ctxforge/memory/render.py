from __future__ import annotations

from ctxforge.context import ContextSection
from ctxforge.memory.models import MemoryHit, SessionSummary, WorkingMemoryItem


def render_retrieved_memories(hits: list[MemoryHit]) -> ContextSection:
    if hits:
        content = "\n".join(
            f"- [{hit.record.kind}/{hit.record.scope} score={hit.score:.2f} "
            f"source={hit.record.source}] {hit.record.content}"
            for hit in _sort_hits(hits)
        )
    else:
        content = "No retrieved memories."
    return ContextSection(
        name="memory.retrieved",
        stability="dynamic",
        priority=40,
        source="memory.search",
        content=content,
    )


def render_working_memory(items: list[WorkingMemoryItem]) -> ContextSection:
    if items:
        content = "\n".join(
            f"- [{item.key} priority={item.priority} source={item.source}] {item.content}" for item in items
        )
    else:
        content = "No working memory."
    return ContextSection(
        name="session.working_memory",
        stability="dynamic",
        priority=35,
        source="memory.working",
        content=content,
    )


def render_session_summary(summary: SessionSummary | None) -> ContextSection | None:
    if summary is None:
        return None
    return ContextSection(
        name="session.summary",
        stability="dynamic",
        priority=32,
        source="memory.summary",
        content=f"[source={summary.source} turns={summary.turn_count}] {summary.summary}",
    )


def _sort_hits(hits: list[MemoryHit]) -> list[MemoryHit]:
    return sorted(
        hits,
        key=lambda hit: (-hit.score, hit.record.kind, -hit.record.created_at.timestamp(), hit.record.id),
    )
