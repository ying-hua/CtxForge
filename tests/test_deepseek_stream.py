from __future__ import annotations

import json

import httpx
import pytest

from ctxforge.config.settings import DeepSeekSettings
from ctxforge.llm import ChatCompletionRequest, DeepSeekAPIError, DeepSeekClient, DeepSeekResponseError


def _sse(data: object) -> bytes:
    return f"data: {json.dumps(data)}\n\n".encode("utf-8")


@pytest.mark.asyncio
async def test_deepseek_stream_parses_deltas_finish_reason_and_usage():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        content = b"".join(
            [
                b": keepalive\n\n",
                _sse(
                    {
                        "id": "chatcmpl-stream-1",
                        "model": "deepseek-v4-flash",
                        "choices": [{"delta": {"content": "Hello "}, "finish_reason": None}],
                    }
                ),
                _sse(
                    {
                        "id": "chatcmpl-stream-1",
                        "model": "deepseek-v4-flash",
                        "choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}],
                    }
                ),
                _sse(
                    {
                        "id": "chatcmpl-stream-1",
                        "model": "deepseek-v4-flash",
                        "choices": [],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 2,
                            "total_tokens": 12,
                            "prompt_cache_hit_tokens": 8,
                            "prompt_cache_miss_tokens": 2,
                        },
                    }
                ),
                b"data: [DONE]\n\n",
            ]
        )
        return httpx.Response(200, content=content)

    client = DeepSeekClient(
        DeepSeekSettings(api_key="sk-test"),
        transport=httpx.MockTransport(handler),
    )

    chunks = [
        chunk
        async for chunk in client.stream(
            ChatCompletionRequest(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=64,
                stream=True,
            )
        )
    ]

    assert captured["body"] == {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 64,
    }
    assert "".join(chunk.content_delta for chunk in chunks) == "Hello world"
    assert chunks[1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.prompt_cache_hit_tokens == 8
    assert chunks[-1].usage.prompt_cache_miss_tokens == 2


@pytest.mark.asyncio
async def test_deepseek_stream_retries_connection_error_before_first_chunk():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary connect failure", request=request)
        return httpx.Response(
            200,
            content=_sse(
                {
                    "id": "chatcmpl-stream-2",
                    "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
                }
            )
            + b"data: [DONE]\n\n",
        )

    client = DeepSeekClient(
        DeepSeekSettings(api_key="sk-test", max_retries=1),
        transport=httpx.MockTransport(handler),
    )

    chunks = [
        chunk
        async for chunk in client.stream(
            ChatCompletionRequest(model="deepseek-v4-flash", messages=[], stream=True)
        )
    ]

    assert attempts == 2
    assert "".join(chunk.content_delta for chunk in chunks) == "ok"


class _FailAfterFirstChunk(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield _sse(
            {
                "id": "chatcmpl-stream-3",
                "choices": [{"delta": {"content": "partial"}, "finish_reason": None}],
            }
        )
        raise httpx.ReadError("stream interrupted")


@pytest.mark.asyncio
async def test_deepseek_stream_does_not_retry_after_emitting_chunk():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, stream=_FailAfterFirstChunk())

    client = DeepSeekClient(
        DeepSeekSettings(api_key="sk-test", max_retries=2),
        transport=httpx.MockTransport(handler),
    )
    received = []

    with pytest.raises(DeepSeekAPIError):
        async for chunk in client.stream(
            ChatCompletionRequest(model="deepseek-v4-flash", messages=[], stream=True)
        ):
            received.append(chunk.content_delta)

    assert received == ["partial"]
    assert attempts == 1


@pytest.mark.asyncio
async def test_deepseek_stream_rejects_malformed_json():
    client = DeepSeekClient(
        DeepSeekSettings(api_key="sk-test", max_retries=0),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"data: {not-json}\n\n")
        ),
    )

    with pytest.raises(DeepSeekResponseError):
        async for _ in client.stream(
            ChatCompletionRequest(model="deepseek-v4-flash", messages=[], stream=True)
        ):
            pass


@pytest.mark.asyncio
async def test_deepseek_stream_rejects_connection_close_without_done_marker():
    client = DeepSeekClient(
        DeepSeekSettings(api_key="sk-test", max_retries=0),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=_sse(
                    {
                        "id": "chatcmpl-stream-incomplete",
                        "choices": [
                            {"delta": {"content": "partial"}, "finish_reason": None}
                        ],
                    }
                ),
            )
        ),
    )
    received = []

    with pytest.raises(DeepSeekResponseError, match=r"\[DONE\]"):
        async for chunk in client.stream(
            ChatCompletionRequest(model="deepseek-v4-flash", messages=[], stream=True)
        ):
            received.append(chunk.content_delta)

    assert received == ["partial"]
