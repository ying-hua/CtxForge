from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from ctxforge.cache import (
    CacheReport,
    CacheSnapshot,
    CacheStore,
    ProviderCacheUsage,
    analyze_cache,
    attach_provider_usage,
    create_cache_snapshot,
    disabled_cache_report,
    mark_dry_run,
    mark_persistence,
)
from ctxforge.config.settings import CtxForgeSettings
from ctxforge.context import BuiltContext, ContextBuilder
from ctxforge.llm import ChatCompletionRequest, ChatCompletionResult, DeepSeekClient
from ctxforge.memory import MemoryManager, MemoryStore, SessionSummary
from ctxforge.runtime.summary import SessionSummarizer
from ctxforge.skills import SkillManager, SkillRegistry


logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class PreparedRuntime:
    session_id: str
    context: BuiltContext
    memory_report: dict[str, object]
    skill_report: dict[str, object]
    selected_skill_names: list[str]
    previous_summary: SessionSummary | None
    memory_store: MemoryStore


@dataclass(frozen=True)
class PreparedRun:
    prepared_runtime: PreparedRuntime
    cache_snapshot: CacheSnapshot
    cache_report: CacheReport
    model: str
    settings: CtxForgeSettings
    active_cache_store: CacheStore | None


def run_phase4(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    client: ChatClient | None = None,
) -> RuntimeResult:
    """Build Phase 4 context, call DeepSeek, and persist a session summary."""
    session_id = request.session_id or f"session-{uuid4().hex[:12]}"
    effective_settings = _effective_settings(request, settings)
    prepared = _prepare_runtime(request, effective_settings, session_id=session_id)
    chat_client = client or DeepSeekClient(effective_settings.deepseek)
    completion = chat_client.complete(
        ChatCompletionRequest(
            model=request.model or effective_settings.deepseek.model,
            messages=prepared.context.messages,
            max_tokens=request.max_output_tokens or effective_settings.context.reserved_output_tokens,
            stream=False,
        )
    )

    summary = SessionSummarizer().summarize(
        task=request.task,
        answer=completion.answer,
        selected_skills=prepared.selected_skill_names,
        memory_report=prepared.memory_report,
        previous_summary=prepared.previous_summary.summary if prepared.previous_summary else None,
    )
    prepared.memory_store.upsert_session_summary(
        session_id=session_id,
        project_dir=str(request.cwd),
        summary=summary,
        source="runtime.phase4.local_summary",
        turn_count=(prepared.previous_summary.turn_count + 1) if prepared.previous_summary else 1,
    )
    llm_report = {**completion.report(), "summary_written": True}

    return RuntimeResult(
        answer=completion.answer,
        session_id=session_id,
        context_report={
            **prepared.context.report.to_dict(),
            "selected_skills": prepared.selected_skill_names,
        },
        cache_report={
            "status": "snapshot_with_api_usage_in_phase_4",
            "stable_prefix_bytes": prepared.context.report.stable_prefix_bytes,
            "stable_prefix_sha256": prepared.context.report.stable_prefix_sha256,
            "section_hashes": prepared.context.snapshot.section_hashes,
            "estimated_cache_hit_ratio": None,
            "prompt_cache_hit_tokens": completion.usage.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": completion.usage.prompt_cache_miss_tokens,
        },
        memory_report=prepared.memory_report,
        skill_report=prepared.skill_report,
        llm_report=llm_report,
    )


def run_phase5(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    client: ChatClient | None = None,
    execute_model: bool = True,
    cache_store: CacheStore | None = None,
) -> RuntimeResult:
    """Run the Phase 5 runtime with local prefix analysis and persisted cache reports."""
    prepared_run = prepare_runtime_run(request, settings, cache_store=cache_store)

    if not execute_model:
        return build_dry_run_result(prepared_run)

    chat_client = client or DeepSeekClient(prepared_run.settings.deepseek)
    completion = chat_client.complete(
        ChatCompletionRequest(
            model=prepared_run.model,
            messages=prepared_run.prepared_runtime.context.messages,
            max_tokens=(
                request.max_output_tokens
                or prepared_run.settings.context.reserved_output_tokens
            ),
            stream=False,
        )
    )
    return finalize_runtime_success(
        prepared_run,
        completion,
        request=request,
        summary_source="runtime.phase5.local_summary",
    )



def prepare_runtime_run(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    cache_store: CacheStore | None = None,
) -> PreparedRun:
    session_id = request.session_id or f"session-{uuid4().hex[:12]}"
    effective_settings = _effective_settings(request, settings)
    prepared = _prepare_runtime(request, effective_settings, session_id=session_id)
    model = request.model or effective_settings.deepseek.model
    cache_snapshot = create_cache_snapshot(
        prepared.context,
        cwd=request.cwd,
        session_id=session_id,
        provider="deepseek",
        base_url=effective_settings.deepseek.base_url,
        model=model,
    )

    active_cache_store: CacheStore | None = None
    if effective_settings.cache.enabled:
        active_cache_store = cache_store or CacheStore(
            effective_settings.memory.resolved_db_path(request.cwd)
        )
        try:
            active_cache_store.initialize()
            baseline = active_cache_store.find_baseline(
                cache_snapshot,
                allow_project_fallback=effective_settings.cache.allow_project_fallback,
            )
            cache_report = analyze_cache(
                cache_snapshot,
                baseline.snapshot if baseline else None,
                baseline_scope=baseline.scope if baseline else None,
            )
        except Exception as exc:
            logger.warning("Cache baseline lookup failed: %s", exc)
            cache_report = mark_persistence(
                analyze_cache(cache_snapshot, None),
                "failed",
                error=f"cache_read_failed: {exc}",
            )
    else:
        cache_report = disabled_cache_report(cache_snapshot)

    return PreparedRun(
        prepared_runtime=prepared,
        cache_snapshot=cache_snapshot,
        cache_report=cache_report,
        model=model,
        settings=effective_settings,
        active_cache_store=active_cache_store,
    )


def build_dry_run_result(prepared_run: PreparedRun) -> RuntimeResult:
    cache_report = prepared_run.cache_report
    if cache_report.status != "disabled":
        cache_report = mark_dry_run(cache_report)
    prepared = prepared_run.prepared_runtime
    return RuntimeResult(
        answer=(
            "CtxForge runtime dry run is ready. "
            "Context, memory, skills, and local prefix analysis were built without calling DeepSeek."
        ),
        session_id=prepared.session_id,
        context_report={
            **prepared.context.report.to_dict(),
            "selected_skills": prepared.selected_skill_names,
        },
        cache_report=cache_report.to_dict(),
        memory_report=prepared.memory_report,
        skill_report=prepared.skill_report,
        llm_report={
            "status": "dry_run_no_model",
            "provider": "deepseek",
            "model": prepared_run.model,
            "request_id": None,
            "finish_reason": None,
            "usage": {},
            "summary_written": False,
        },
    )


def finalize_runtime_success(
    prepared_run: PreparedRun,
    completion: ChatCompletionResult,
    *,
    request: RuntimeRequest,
    summary_source: str,
) -> RuntimeResult:
    prepared = prepared_run.prepared_runtime
    cache_report = attach_provider_usage(
        prepared_run.cache_report,
        ProviderCacheUsage(
            prompt_tokens=completion.usage.prompt_tokens,
            hit_tokens=completion.usage.prompt_cache_hit_tokens,
            miss_tokens=completion.usage.prompt_cache_miss_tokens,
        ),
    )

    if prepared_run.settings.cache.enabled and prepared_run.active_cache_store is not None:
        persisted_report = mark_persistence(cache_report, "saved")
        try:
            prepared_run.active_cache_store.save(
                prepared_run.cache_snapshot,
                persisted_report,
                request_id=completion.request_id,
                retention=prepared_run.settings.cache.snapshot_retention,
            )
            cache_report = persisted_report
        except Exception as exc:
            logger.warning("Cache snapshot persistence failed: %s", exc)
            cache_report = mark_persistence(
                cache_report,
                "failed",
                error=f"cache_write_failed: {exc}",
            )

    summary = SessionSummarizer().summarize(
        task=request.task,
        answer=completion.answer,
        selected_skills=prepared.selected_skill_names,
        memory_report=prepared.memory_report,
        previous_summary=prepared.previous_summary.summary if prepared.previous_summary else None,
    )
    summary_written = False
    summary_error = None
    try:
        prepared.memory_store.upsert_session_summary(
            session_id=prepared.session_id,
            project_dir=str(request.cwd),
            summary=summary,
            source=summary_source,
            turn_count=(prepared.previous_summary.turn_count + 1) if prepared.previous_summary else 1,
        )
        summary_written = True
    except Exception as exc:
        logger.warning("Session summary persistence failed: %s", exc)
        summary_error = f"summary_write_failed: {exc}"

    return RuntimeResult(
        answer=completion.answer,
        session_id=prepared.session_id,
        context_report={
            **prepared.context.report.to_dict(),
            "selected_skills": prepared.selected_skill_names,
        },
        cache_report=cache_report.to_dict(),
        memory_report=prepared.memory_report,
        skill_report=prepared.skill_report,
        llm_report={
            **completion.report(),
            "summary_written": summary_written,
            "summary_error": summary_error,
        },
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


def _prepare_runtime(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    session_id: str,
) -> PreparedRuntime:
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
    selected_names = [skill.name for skill in skill_context.selected_skills]
    context = ContextBuilder(settings).build(
        task=request.task,
        cwd=request.cwd,
        skill_names=selected_names,
        skill_manifest_content=skill_context.manifest_content,
        extra_sections=[*skill_context.sections, *memory_context.sections],
        include_memory_placeholders=False,
    )
    return PreparedRuntime(
        session_id=session_id,
        context=context,
        memory_report=memory_context.report.to_dict(),
        skill_report=skill_context.report.to_dict(),
        selected_skill_names=selected_names,
        previous_summary=previous_summary,
        memory_store=store,
    )


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
