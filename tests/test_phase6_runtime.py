from __future__ import annotations

import asyncio

import pytest

from ctxforge.cache import CacheStore, normalize_project_key
from ctxforge.config.settings import CtxForgeSettings
from ctxforge.llm import ChatCompletionRequest, ChatStreamChunk, ChatUsage, DeepSeekAPIError
from ctxforge.memory import MemoryStore
from ctxforge.runtime.agent import RuntimeRequest
from ctxforge.runtime.events import ResponseDelta, RunCompleted, RunFailed, RunStarted, RuntimePrepared
from ctxforge.runtime.stream import stream_phase6


class FakeStreamingClient:
    def __init__(self, parts: list[str] | None = None) -> None:
        self.parts = parts or ["Phase ", "6 answer."]
        self.requests: list[ChatCompletionRequest] = []

    async def stream(self, request: ChatCompletionRequest):
        self.requests.append(request)
        for index, part in enumerate(self.parts):
            yield ChatStreamChunk(
                content_delta=part,
                model=request.model,
                request_id="request-stream-1",
                finish_reason="stop" if index == len(self.parts) - 1 else None,
            )
        yield ChatStreamChunk(
            model=request.model,
            request_id="request-stream-1",
            usage=ChatUsage(
                prompt_tokens=100,
                completion_tokens=10,
                total_tokens=110,
                prompt_cache_hit_tokens=80,
                prompt_cache_miss_tokens=20,
            ),
        )


class FailingStreamingClient:
    async def stream(self, request: ChatCompletionRequest):
        yield ChatStreamChunk(content_delta="partial", model=request.model)
        raise DeepSeekAPIError(status_code=500, message="stream failed")


class BlockingStreamingClient:
    def __init__(self) -> None:
        self.block = asyncio.Event()

    async def stream(self, request: ChatCompletionRequest):
        yield ChatStreamChunk(content_delta="partial", model=request.model)
        await self.block.wait()
        yield ChatStreamChunk(content_delta="never")


async def _collect(request, settings, **kwargs):
    return [event async for event in stream_phase6(request, settings, **kwargs)]


@pytest.mark.asyncio
async def test_phase6_success_emits_ordered_events_and_persists(tmp_path):
    settings = CtxForgeSettings()
    client = FakeStreamingClient()
    events = await _collect(
        RuntimeRequest(task="stream this", cwd=tmp_path, session_id="session-1"),
        settings,
        client=client,
    )

    assert isinstance(events[0], RunStarted)
    assert isinstance(events[1], RuntimePrepared)
    assert [event.sequence for event in events] == list(range(len(events)))
    deltas = [event.text for event in events if isinstance(event, ResponseDelta)]
    assert deltas == ["Phase ", "6 answer."]
    completed = events[-1]
    assert isinstance(completed, RunCompleted)
    assert completed.result.answer == "".join(deltas)
    assert completed.result.cache_report["actual_cache_hit_ratio"] == 0.8
    assert completed.result.llm_report["summary_written"] is True
    assert client.requests[0].stream is True

    cache_store = CacheStore(settings.memory.resolved_db_path(tmp_path))
    cache_store.initialize()
    assert cache_store.count(project_key=normalize_project_key(tmp_path)) == 1
    memory_store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    memory_store.initialize()
    assert memory_store.get_session_summary(
        project_dir=str(tmp_path),
        session_id="session-1",
    ) is not None


@pytest.mark.asyncio
async def test_phase6_failure_preserves_partial_answer_without_persisting(tmp_path):
    settings = CtxForgeSettings()
    events = await _collect(
        RuntimeRequest(task="fail", cwd=tmp_path, session_id="session-1"),
        settings,
        client=FailingStreamingClient(),
    )

    failed = events[-1]
    assert isinstance(failed, RunFailed)
    assert failed.error_code == "provider_error"
    assert failed.retryable is True
    assert failed.partial_answer == "partial"

    cache_store = CacheStore(settings.memory.resolved_db_path(tmp_path))
    cache_store.initialize()
    memory_store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    memory_store.initialize()
    assert cache_store.count(project_key=normalize_project_key(tmp_path)) == 0
    assert memory_store.get_session_summary(
        project_dir=str(tmp_path),
        session_id="session-1",
    ) is None


@pytest.mark.asyncio
async def test_phase6_dry_run_emits_prepared_and_completed_without_client(tmp_path):
    settings = CtxForgeSettings()
    events = await _collect(
        RuntimeRequest(task="dry run", cwd=tmp_path, session_id="session-1"),
        settings,
        execute_model=False,
    )

    assert [type(event) for event in events] == [RunStarted, RuntimePrepared, RunCompleted]
    completed = events[-1]
    assert isinstance(completed, RunCompleted)
    assert completed.result.llm_report["status"] == "dry_run_no_model"


@pytest.mark.asyncio
async def test_phase6_cancellation_does_not_persist(tmp_path):
    settings = CtxForgeSettings()
    client = BlockingStreamingClient()
    stream = stream_phase6(
        RuntimeRequest(task="cancel", cwd=tmp_path, session_id="session-1"),
        settings,
        client=client,
    )

    assert isinstance(await anext(stream), RunStarted)
    assert isinstance(await anext(stream), RuntimePrepared)
    assert isinstance(await anext(stream), ResponseDelta)
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    cache_store = CacheStore(settings.memory.resolved_db_path(tmp_path))
    cache_store.initialize()
    memory_store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    memory_store.initialize()
    assert cache_store.count(project_key=normalize_project_key(tmp_path)) == 0
    assert memory_store.get_session_summary(
        project_dir=str(tmp_path),
        session_id="session-1",
    ) is None


@pytest.mark.asyncio
async def test_phase6_second_turn_reuses_session_summary_and_cache_baseline(tmp_path):
    settings = CtxForgeSettings()
    client = FakeStreamingClient()
    request = RuntimeRequest(task="same session", cwd=tmp_path, session_id="session-1")

    await _collect(request, settings, client=client)
    second = await _collect(request, settings, client=client)

    prepared = next(event for event in second if isinstance(event, RuntimePrepared))
    assert prepared.cache_report["baseline_scope"] == "session"
    included = prepared.context_report["included_sections"]
    assert isinstance(included, list)
    assert any(
        isinstance(section, dict) and section.get("name") == "session.summary"
        for section in included
    )
