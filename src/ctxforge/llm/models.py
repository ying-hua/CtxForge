from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    reasoning_tokens: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "prompt_cache_hit_tokens": self.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self.prompt_cache_miss_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


@dataclass(frozen=True)
class ChatCompletionRequest:
    model: str
    messages: list[dict[str, str]]
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False


@dataclass(frozen=True)
class ChatCompletionResult:
    answer: str
    model: str
    request_id: str | None
    finish_reason: str | None
    usage: ChatUsage = field(default_factory=ChatUsage)
    raw_usage: dict[str, object] = field(default_factory=dict)

    def report(self) -> dict[str, object]:
        usage = self.usage.to_dict()
        return {
            "status": "ok",
            "provider": "deepseek",
            "model": self.model,
            "request_id": self.request_id,
            "finish_reason": self.finish_reason,
            "usage": usage,
            "prompt_cache_hit_tokens": usage["prompt_cache_hit_tokens"],
            "prompt_cache_miss_tokens": usage["prompt_cache_miss_tokens"],
            "error": None,
        }
