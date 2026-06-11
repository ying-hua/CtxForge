from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Protocol
from uuid import uuid4

from ctxforge.cache import CacheStore
from ctxforge.config.settings import CtxForgeSettings
from ctxforge.llm import (
    ChatCompletionRequest,
    ChatCompletionResult,
    ChatStreamChunk,
    ChatUsage,
    DeepSeekAPIError,
    DeepSeekClient,
    DeepSeekRequestError,
    DeepSeekResponseError,
    MissingDeepSeekApiKey,
)
from ctxforge.runtime.agent import (
    RuntimeRequest,
    build_dry_run_result,
    finalize_runtime_success,
    prepare_runtime_run,
)
from ctxforge.runtime.events import (
    ResponseDelta,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    RuntimePrepared,
)


logger = logging.getLogger(__name__)


class StreamingChatClient(Protocol):
    def stream(self, request: ChatCompletionRequest) -> AsyncIterator[ChatStreamChunk]:
        ...


async def stream_phase6(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    client: StreamingChatClient | None = None,
    execute_model: bool = True,
    cache_store: CacheStore | None = None,
) -> AsyncIterator[RuntimeEvent]:
    session_id = request.session_id or f"session-{uuid4().hex[:12]}"
    active_request = replace(request, session_id=session_id)
    model = active_request.model or settings.deepseek.model
    run_id = f"run-{uuid4().hex[:12]}"
    sequence = 0

    yield RunStarted(
        run_id=run_id,
        session_id=session_id,
        sequence=sequence,
        task=active_request.task,
        model=model,
    )
    sequence += 1

    try:
        prepared_run = await asyncio.to_thread(
            prepare_runtime_run,
            active_request,
            settings,
            cache_store=cache_store,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Phase 6 runtime preparation failed")
        yield RunFailed(
            run_id=run_id,
            session_id=session_id,
            sequence=sequence,
            error_code="local_prepare_error",
            message=str(exc),
            retryable=False,
            partial_answer="",
        )
        return

    prepared = prepared_run.prepared_runtime
    yield RuntimePrepared(
        run_id=run_id,
        session_id=session_id,
        sequence=sequence,
        context_report={
            **prepared.context.report.to_dict(),
            "selected_skills": prepared.selected_skill_names,
        },
        memory_report=prepared.memory_report,
        skill_report=prepared.skill_report,
        cache_report=prepared_run.cache_report.to_dict(),
    )
    sequence += 1

    if not execute_model:
        result = build_dry_run_result(prepared_run)
        yield RunCompleted(
            run_id=run_id,
            session_id=session_id,
            sequence=sequence,
            result=result,
        )
        return

    chat_client = client or DeepSeekClient(prepared_run.settings.deepseek)
    answer_parts: list[str] = []
    completion_model = prepared_run.model
    request_id: str | None = None
    finish_reason: str | None = None
    usage = ChatUsage()

    try:
        async for chunk in chat_client.stream(
            ChatCompletionRequest(
                model=prepared_run.model,
                messages=prepared.context.messages,
                max_tokens=(
                    active_request.max_output_tokens
                    or prepared_run.settings.context.reserved_output_tokens
                ),
                stream=True,
            )
        ):
            if chunk.model:
                completion_model = chunk.model
            if chunk.request_id:
                request_id = chunk.request_id
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.usage is not None:
                usage = chunk.usage
            if chunk.content_delta:
                answer_parts.append(chunk.content_delta)
                yield ResponseDelta(
                    run_id=run_id,
                    session_id=session_id,
                    sequence=sequence,
                    text=chunk.content_delta,
                )
                sequence += 1
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error_code, retryable = _classify_stream_error(exc)
        logger.warning("Phase 6 model stream failed (%s): %s", error_code, exc)
        yield RunFailed(
            run_id=run_id,
            session_id=session_id,
            sequence=sequence,
            error_code=error_code,
            message=str(exc),
            retryable=retryable,
            partial_answer="".join(answer_parts),
        )
        return

    completion = ChatCompletionResult(
        answer="".join(answer_parts),
        model=completion_model,
        request_id=request_id,
        finish_reason=finish_reason,
        usage=usage,
        raw_usage=usage.to_dict(),
    )
    try:
        result = await asyncio.to_thread(
            finalize_runtime_success,
            prepared_run,
            completion,
            request=active_request,
            summary_source="runtime.phase6.local_summary",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Phase 6 runtime finalization failed")
        yield RunFailed(
            run_id=run_id,
            session_id=session_id,
            sequence=sequence,
            error_code="local_finalize_error",
            message=str(exc),
            retryable=False,
            partial_answer=completion.answer,
        )
        return

    yield RunCompleted(
        run_id=run_id,
        session_id=session_id,
        sequence=sequence,
        result=result,
    )


def _classify_stream_error(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, MissingDeepSeekApiKey):
        return "missing_api_key", False
    if isinstance(exc, DeepSeekResponseError):
        return "invalid_response", False
    if isinstance(exc, DeepSeekRequestError):
        return "provider_error", False
    if isinstance(exc, DeepSeekAPIError):
        if exc.status_code in {401, 403}:
            return "authentication_error", False
        if exc.status_code == 429:
            return "rate_limited", True
        if exc.status_code is None:
            return "network_error", True
        return "provider_error", exc.status_code >= 500
    return "provider_error", False
