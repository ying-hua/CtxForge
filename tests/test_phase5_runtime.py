from __future__ import annotations

import pytest

from ctxforge.cache import CacheStore, normalize_project_key
from ctxforge.config.settings import CtxForgeSettings
from ctxforge.llm import ChatCompletionRequest, ChatCompletionResult, ChatUsage, DeepSeekAPIError
from ctxforge.memory import MemoryStore
from ctxforge.runtime.agent import RuntimeRequest, run_phase5


class FakeChatClient:
    def __init__(self, answer: str = "Phase 5 answer.") -> None:
        self.answer = answer
        self.requests: list[ChatCompletionRequest] = []

    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        self.requests.append(request)
        return ChatCompletionResult(
            answer=self.answer,
            model=request.model,
            request_id=f"request-{len(self.requests)}",
            finish_reason="stop",
            usage=ChatUsage(
                prompt_tokens=100,
                completion_tokens=10,
                total_tokens=110,
                prompt_cache_hit_tokens=75,
                prompt_cache_miss_tokens=25,
            ),
        )


class FailingChatClient:
    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        raise DeepSeekAPIError(status_code=500, message="server failed")


class FailingSaveCacheStore(CacheStore):
    def save(self, *args, **kwargs) -> None:
        raise OSError("cache disk unavailable")


def test_phase5_first_run_saves_snapshot_and_provider_usage(tmp_path):
    settings = CtxForgeSettings()
    result = run_phase5(
        RuntimeRequest(task="same task", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=FakeChatClient(),
    )

    store = CacheStore(settings.memory.resolved_db_path(tmp_path))
    store.initialize()

    assert result.answer == "Phase 5 answer."
    assert result.cache_report["status"] == "no_baseline"
    assert result.cache_report["estimated_cache_hit_ratio"] is None
    assert result.cache_report["actual_cache_hit_ratio"] == 0.75
    assert result.cache_report["persistence_status"] == "saved"
    assert store.count(project_key=normalize_project_key(tmp_path)) == 1
    assert result.llm_report["summary_written"] is True


def test_phase5_second_run_compares_with_same_session_baseline(tmp_path):
    settings = CtxForgeSettings()
    client = FakeChatClient()
    first = run_phase5(
        RuntimeRequest(task="same task", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=client,
    )
    second = run_phase5(
        RuntimeRequest(task="same task", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=client,
    )

    assert second.cache_report["status"] == "changed"
    assert second.cache_report["baseline_snapshot_id"] == first.cache_report["snapshot_id"]
    assert second.cache_report["baseline_scope"] == "session"
    assert second.cache_report["first_changed_section"] == "session.summary"
    assert second.cache_report["stable_prefix_changed"] is False


def test_phase5_dry_run_reads_baseline_but_does_not_persist_or_write_summary(tmp_path):
    settings = CtxForgeSettings()
    client = FakeChatClient()
    run_phase5(
        RuntimeRequest(task="first", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=client,
    )
    store = CacheStore(settings.memory.resolved_db_path(tmp_path))
    store.initialize()
    before = store.count(project_key=normalize_project_key(tmp_path))

    result = run_phase5(
        RuntimeRequest(task="second", cwd=tmp_path, session_id="dry-session"),
        settings=settings,
        execute_model=False,
    )

    memory_store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    memory_store.initialize()
    assert result.llm_report["status"] == "dry_run_no_model"
    assert result.cache_report["baseline_scope"] == "project_fallback"
    assert result.cache_report["provider_usage_status"] == "dry_run"
    assert result.cache_report["persistence_status"] == "not_saved"
    assert store.count(project_key=normalize_project_key(tmp_path)) == before
    assert memory_store.get_session_summary(project_dir=str(tmp_path), session_id="dry-session") is None


def test_phase5_model_failure_does_not_persist_snapshot_or_summary(tmp_path):
    settings = CtxForgeSettings()

    with pytest.raises(DeepSeekAPIError):
        run_phase5(
            RuntimeRequest(task="fail", cwd=tmp_path, session_id="session-1"),
            settings=settings,
            client=FailingChatClient(),
        )

    cache_store = CacheStore(settings.memory.resolved_db_path(tmp_path))
    cache_store.initialize()
    memory_store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    memory_store.initialize()
    assert cache_store.count(project_key=normalize_project_key(tmp_path)) == 0
    assert memory_store.get_session_summary(project_dir=str(tmp_path), session_id="session-1") is None


def test_phase5_cache_save_failure_keeps_model_answer_and_summary(tmp_path):
    settings = CtxForgeSettings()
    failing_store = FailingSaveCacheStore(settings.memory.resolved_db_path(tmp_path))

    result = run_phase5(
        RuntimeRequest(task="answer despite cache failure", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=FakeChatClient(),
        cache_store=failing_store,
    )

    memory_store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    memory_store.initialize()
    assert result.answer == "Phase 5 answer."
    assert result.cache_report["persistence_status"] == "failed"
    assert "cache_write_failed" in str(result.cache_report["error"])
    assert memory_store.get_session_summary(project_dir=str(tmp_path), session_id="session-1") is not None


def test_phase5_disabled_cache_still_calls_model_and_writes_summary(tmp_path):
    settings = CtxForgeSettings(
        cache={"enabled": False, "snapshot_retention": 20, "allow_project_fallback": True}
    )
    result = run_phase5(
        RuntimeRequest(task="cache disabled", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=FakeChatClient(),
    )

    assert result.cache_report["status"] == "disabled"
    assert result.cache_report["persistence_status"] == "disabled"
    assert result.cache_report["actual_cache_hit_ratio"] == 0.75
    assert result.llm_report["summary_written"] is True
