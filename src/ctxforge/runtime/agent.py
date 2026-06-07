from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.context import ContextBuilder
from ctxforge.memory import MemoryManager, MemoryStore


@dataclass(frozen=True)
class RuntimeRequest:
    task: str
    cwd: Path
    session_id: str | None = None
    skill_names: list[str] = field(default_factory=list)
    max_tokens: int | None = None


@dataclass(frozen=True)
class RuntimeResult:
    answer: str
    session_id: str
    context_report: dict[str, object]
    cache_report: dict[str, object]
    memory_report: dict[str, object]


def run_phase1(request: RuntimeRequest, settings: CtxForgeSettings) -> RuntimeResult:
    """Build deterministic Phase 1 context while model, memory, and skills remain placeholders."""
    session_id = request.session_id or f"session-{uuid4().hex[:12]}"
    effective_settings = settings
    if request.max_tokens is not None:
        effective_settings = settings.model_copy(
            update={"context": settings.context.model_copy(update={"max_tokens": request.max_tokens})}
        )
    context = ContextBuilder(effective_settings).build(
        task=request.task,
        cwd=request.cwd,
        skill_names=request.skill_names,
    )

    return RuntimeResult(
        answer=(
            "Phase 1 context builder is ready. "
            "Memory, skills, DeepSeek calls, and cache diff analysis "
            "will be connected in later phases."
        ),
        session_id=session_id,
        context_report={
            **context.report.to_dict(),
            "selected_skills": sorted(request.skill_names),
        },
        cache_report={
            "status": "snapshot_only_in_phase_1",
            "stable_prefix_bytes": context.report.stable_prefix_bytes,
            "stable_prefix_sha256": context.report.stable_prefix_sha256,
            "section_hashes": context.snapshot.section_hashes,
            "estimated_cache_hit_ratio": None,
        },
        memory_report={
            "status": "not_available_in_phase_1",
            "db_path": str(settings.memory.resolved_db_path(request.cwd)),
        },
    )


def run_phase2(request: RuntimeRequest, settings: CtxForgeSettings) -> RuntimeResult:
    """Build Phase 2 context with SQLite-backed memory retrieval."""
    session_id = request.session_id or f"session-{uuid4().hex[:12]}"
    effective_settings = settings
    if request.max_tokens is not None:
        effective_settings = settings.model_copy(
            update={"context": settings.context.model_copy(update={"max_tokens": request.max_tokens})}
        )

    store = MemoryStore(settings.memory.resolved_db_path(request.cwd))
    store.initialize()
    memory_context = MemoryManager(store).retrieve_for_context(
        task=request.task,
        cwd=request.cwd,
        session_id=session_id,
    )
    context = ContextBuilder(effective_settings).build(
        task=request.task,
        cwd=request.cwd,
        skill_names=request.skill_names,
        extra_sections=memory_context.sections,
        include_memory_placeholders=False,
    )

    return RuntimeResult(
        answer=(
            "Phase 2 memory layer is ready. "
            "Context now includes SQLite-backed memory sections; "
            "skills, DeepSeek calls, and cache diff analysis will be connected in later phases."
        ),
        session_id=session_id,
        context_report={
            **context.report.to_dict(),
            "selected_skills": sorted(request.skill_names),
        },
        cache_report={
            "status": "snapshot_only_in_phase_2",
            "stable_prefix_bytes": context.report.stable_prefix_bytes,
            "stable_prefix_sha256": context.report.stable_prefix_sha256,
            "section_hashes": context.snapshot.section_hashes,
            "estimated_cache_hit_ratio": None,
        },
        memory_report=memory_context.report.to_dict(),
    )


def run_phase0(request: RuntimeRequest, settings: CtxForgeSettings) -> RuntimeResult:
    """Backward-compatible alias for tests and callers created during Phase 0."""
    return run_phase1(request, settings)
