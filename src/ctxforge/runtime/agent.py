from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.context import ContextBuilder
from ctxforge.llm import ChatCompletionRequest, ChatCompletionResult, DeepSeekClient
from ctxforge.memory import MemoryManager, MemoryStore
from ctxforge.runtime.summary import SessionSummarizer
from ctxforge.skills import SkillManager, SkillRegistry


class ChatClient(Protocol):
    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        ...


@dataclass(frozen=True)
class RuntimeRequest:
    task: str
    cwd: Path
    session_id: str | None = None
    skill_names: list[str] = field(default_factory=list)
    max_tokens: int | None = None
    model: str | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class RuntimeResult:
    answer: str
    session_id: str
    context_report: dict[str, object]
    cache_report: dict[str, object]
    memory_report: dict[str, object]
    skill_report: dict[str, object]
    llm_report: dict[str, object] = field(default_factory=dict)


def run_phase4(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    client: ChatClient | None = None,
) -> RuntimeResult:
    """Build Phase 4 context, call DeepSeek, and persist a session summary."""
    session_id = request.session_id or f"session-{uuid4().hex[:12]}"
    effective_settings = _effective_settings(request, settings)
    project_dir = str(request.cwd)

    store = MemoryStore(settings.memory.resolved_db_path(request.cwd))
    store.initialize()
    previous_summary = store.get_session_summary(project_dir=project_dir, session_id=session_id)
    memory_context = MemoryManager(store).retrieve_for_context(
        task=request.task,
        cwd=request.cwd,
        session_id=session_id,
    )
    skill_context = SkillManager(
        SkillRegistry(settings.skills.resolved_skills_dir(request.cwd))
    ).select_for_context(
        task=request.task,
        cwd=request.cwd,
        explicit_names=request.skill_names,
    )
    context = ContextBuilder(effective_settings).build(
        task=request.task,
        cwd=request.cwd,
        skill_names=[skill.name for skill in skill_context.selected_skills],
        skill_manifest_content=skill_context.manifest_content,
        extra_sections=[*skill_context.sections, *memory_context.sections],
        include_memory_placeholders=False,
    )

    selected_names = [skill.name for skill in skill_context.selected_skills]
    chat_client = client or DeepSeekClient(effective_settings.deepseek)
    completion = chat_client.complete(
        ChatCompletionRequest(
            model=request.model or effective_settings.deepseek.model,
            messages=context.messages,
            max_tokens=request.max_output_tokens or effective_settings.context.reserved_output_tokens,
            stream=False,
        )
    )

    summary = SessionSummarizer().summarize(
        task=request.task,
        answer=completion.answer,
        selected_skills=selected_names,
        memory_report=memory_context.report.to_dict(),
        previous_summary=previous_summary.summary if previous_summary else None,
    )
    store.upsert_session_summary(
        session_id=session_id,
        project_dir=project_dir,
        summary=summary,
        source="runtime.phase4.local_summary",
        turn_count=(previous_summary.turn_count + 1) if previous_summary else 1,
    )
    llm_report = {**completion.report(), "summary_written": True}

    return RuntimeResult(
        answer=completion.answer,
        session_id=session_id,
        context_report={
            **context.report.to_dict(),
            "selected_skills": selected_names,
        },
        cache_report={
            "status": "snapshot_with_api_usage_in_phase_4",
            "stable_prefix_bytes": context.report.stable_prefix_bytes,
            "stable_prefix_sha256": context.report.stable_prefix_sha256,
            "section_hashes": context.snapshot.section_hashes,
            "estimated_cache_hit_ratio": None,
            "prompt_cache_hit_tokens": completion.usage.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": completion.usage.prompt_cache_miss_tokens,
        },
        memory_report=memory_context.report.to_dict(),
        skill_report=skill_context.report.to_dict(),
        llm_report=llm_report,
    )


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
        skill_report={
            "status": "not_available_in_phase_1",
            "skills_dir": str(settings.skills.resolved_skills_dir(request.cwd)),
            "selected_count": len(request.skill_names),
            "selected": sorted(request.skill_names),
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
        skill_report={
            "status": "not_available_in_phase_2",
            "skills_dir": str(settings.skills.resolved_skills_dir(request.cwd)),
            "selected_count": len(request.skill_names),
            "selected": sorted(request.skill_names),
        },
    )


def run_phase3(request: RuntimeRequest, settings: CtxForgeSettings) -> RuntimeResult:
    """Build Phase 3 context with memory retrieval and local skill activation."""
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
    skill_context = SkillManager(
        SkillRegistry(settings.skills.resolved_skills_dir(request.cwd))
    ).select_for_context(
        task=request.task,
        cwd=request.cwd,
        explicit_names=request.skill_names,
    )
    context = ContextBuilder(effective_settings).build(
        task=request.task,
        cwd=request.cwd,
        skill_names=[skill.name for skill in skill_context.selected_skills],
        skill_manifest_content=skill_context.manifest_content,
        extra_sections=[*skill_context.sections, *memory_context.sections],
        include_memory_placeholders=False,
    )

    selected_names = [skill.name for skill in skill_context.selected_skills]
    return RuntimeResult(
        answer=(
            "Phase 3 skill layer is ready. "
            "Context now includes local skill activation and SQLite-backed memory sections; "
            "DeepSeek calls and cache diff analysis will be connected in later phases."
        ),
        session_id=session_id,
        context_report={
            **context.report.to_dict(),
            "selected_skills": selected_names,
        },
        cache_report={
            "status": "snapshot_only_in_phase_3",
            "stable_prefix_bytes": context.report.stable_prefix_bytes,
            "stable_prefix_sha256": context.report.stable_prefix_sha256,
            "section_hashes": context.snapshot.section_hashes,
            "estimated_cache_hit_ratio": None,
        },
        memory_report=memory_context.report.to_dict(),
        skill_report=skill_context.report.to_dict(),
    )


def run_phase0(request: RuntimeRequest, settings: CtxForgeSettings) -> RuntimeResult:
    """Backward-compatible alias for tests and callers created during Phase 0."""
    return run_phase1(request, settings)


def _effective_settings(request: RuntimeRequest, settings: CtxForgeSettings) -> CtxForgeSettings:
    context_settings = settings.context
    if request.max_tokens is not None or request.max_output_tokens is not None:
        context_settings = settings.context.model_copy(
            update={
                "max_tokens": request.max_tokens if request.max_tokens is not None else settings.context.max_tokens,
                "reserved_output_tokens": (
                    request.max_output_tokens
                    if request.max_output_tokens is not None
                    else settings.context.reserved_output_tokens
                ),
            }
        )

    deepseek_settings = settings.deepseek
    if request.model is not None:
        deepseek_settings = settings.deepseek.model_copy(update={"model": request.model})

    if context_settings is settings.context and deepseek_settings is settings.deepseek:
        return settings
    return settings.model_copy(update={"context": context_settings, "deepseek": deepseek_settings})
