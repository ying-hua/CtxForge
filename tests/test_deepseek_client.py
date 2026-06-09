from __future__ import annotations

import json

import httpx
import pytest

from ctxforge.config.settings import DeepSeekSettings
from ctxforge.llm import (
    ChatCompletionRequest,
    DeepSeekAPIError,
    DeepSeekClient,
    DeepSeekRequestError,
    MissingDeepSeekApiKey,
)


def test_deepseek_client_posts_chat_completion_and_parses_usage():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "Hello from DeepSeek."},
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                    "prompt_cache_hit_tokens": 6,
                    "prompt_cache_miss_tokens": 4,
                    "completion_tokens_details": {"reasoning_tokens": 1},
                },
            },
        )

    client = DeepSeekClient(
        DeepSeekSettings(api_key="sk-test", base_url="https://api.deepseek.com", model="deepseek-v4-flash"),
        transport=httpx.MockTransport(handler),
    )

    result = client.complete(
        ChatCompletionRequest(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=64,
        )
    )

    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer sk-test"
    assert captured["body"] == {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "max_tokens": 64,
    }
    assert result.answer == "Hello from DeepSeek."
    assert result.request_id == "chatcmpl-1"
    assert result.finish_reason == "stop"
    assert result.usage.prompt_cache_hit_tokens == 6
    assert result.usage.prompt_cache_miss_tokens == 4
    assert result.usage.reasoning_tokens == 1


def test_deepseek_client_requires_api_key():
    client = DeepSeekClient(DeepSeekSettings(api_key=None))

    with pytest.raises(MissingDeepSeekApiKey):
        client.complete(ChatCompletionRequest(model="deepseek-v4-flash", messages=[]))


def test_deepseek_client_turns_http_error_into_project_error():
    client = DeepSeekClient(
        DeepSeekSettings(api_key="sk-test", max_retries=0),
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="bad key")),
    )

    with pytest.raises(DeepSeekAPIError) as exc_info:
        client.complete(ChatCompletionRequest(model="deepseek-v4-flash", messages=[]))

    assert exc_info.value.status_code == 401
    assert "bad key" in str(exc_info.value)


def test_deepseek_client_rejects_streaming_before_http_request():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    client = DeepSeekClient(
        DeepSeekSettings(api_key="sk-test"),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(DeepSeekRequestError):
        client.complete(ChatCompletionRequest(model="deepseek-v4-flash", messages=[], stream=True))

    assert called is False
