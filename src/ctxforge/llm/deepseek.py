from __future__ import annotations

from typing import Any

import httpx

from ctxforge.config.settings import DeepSeekSettings
from ctxforge.llm.errors import DeepSeekAPIError, DeepSeekRequestError, DeepSeekResponseError, MissingDeepSeekApiKey
from ctxforge.llm.models import ChatCompletionRequest, ChatCompletionResult, ChatUsage


class DeepSeekClient:
    def __init__(self, settings: DeepSeekSettings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        if not self._settings.api_key:
            raise MissingDeepSeekApiKey()
        if request.stream:
            raise DeepSeekRequestError("Streaming chat completions are not supported in Phase 4.")

        payload: dict[str, object] = {
            "model": request.model,
            "messages": request.messages,
            "stream": request.stream,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self._settings.base_url.rstrip('/')}/chat/completions"
        response = self._post_with_retries(url=url, headers=headers, payload=payload)
        return _parse_completion(response)

    def _post_with_retries(self, *, url: str, headers: dict[str, str], payload: dict[str, object]) -> httpx.Response:
        attempts = self._settings.max_retries + 1
        last_error: DeepSeekAPIError | None = None
        with httpx.Client(timeout=self._settings.timeout_seconds, transport=self._transport) as client:
            for attempt in range(attempts):
                try:
                    response = client.post(url, headers=headers, json=payload)
                except httpx.RequestError as exc:
                    last_error = DeepSeekAPIError(status_code=None, message=str(exc))
                    if attempt < attempts - 1:
                        continue
                    raise last_error from exc

                if response.status_code < 400:
                    return response

                last_error = DeepSeekAPIError(
                    status_code=response.status_code,
                    message=_short_error_text(response),
                )
                if response.status_code < 500 or attempt >= attempts - 1:
                    raise last_error

        if last_error is not None:
            raise last_error
        raise DeepSeekAPIError(status_code=None, message="request failed")


def _parse_completion(response: httpx.Response) -> ChatCompletionResult:
    try:
        data = response.json()
    except ValueError as exc:
        raise DeepSeekResponseError("DeepSeek response was not valid JSON") from exc

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DeepSeekResponseError("DeepSeek response did not include choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise DeepSeekResponseError("DeepSeek response choice was malformed")
    message = first_choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise DeepSeekResponseError("DeepSeek response did not include assistant content")

    raw_usage = data.get("usage")
    if not isinstance(raw_usage, dict):
        raw_usage = {}

    model = data.get("model")
    request_id = data.get("id") or response.headers.get("x-request-id")
    finish_reason = first_choice.get("finish_reason")
    return ChatCompletionResult(
        answer=message["content"],
        model=model if isinstance(model, str) else "",
        request_id=request_id if isinstance(request_id, str) else None,
        finish_reason=finish_reason if isinstance(finish_reason, str) else None,
        usage=_parse_usage(raw_usage),
        raw_usage=raw_usage,
    )


def _parse_usage(raw_usage: dict[str, object]) -> ChatUsage:
    details = raw_usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        details = {}
    return ChatUsage(
        prompt_tokens=_int_or_none(raw_usage.get("prompt_tokens")),
        completion_tokens=_int_or_none(raw_usage.get("completion_tokens")),
        total_tokens=_int_or_none(raw_usage.get("total_tokens")),
        prompt_cache_hit_tokens=_int_or_none(raw_usage.get("prompt_cache_hit_tokens")),
        prompt_cache_miss_tokens=_int_or_none(raw_usage.get("prompt_cache_miss_tokens")),
        reasoning_tokens=_int_or_none(details.get("reasoning_tokens")),
    )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _short_error_text(response: httpx.Response) -> str:
    text = response.text.strip()
    if not text:
        return response.reason_phrase
    return text[:500]
