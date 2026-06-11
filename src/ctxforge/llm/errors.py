from __future__ import annotations


class DeepSeekError(RuntimeError):
    """Base class for DeepSeek client errors."""


class MissingDeepSeekApiKey(DeepSeekError):
    def __init__(self) -> None:
        super().__init__(
            'DEEPSEEK_API_KEY is required for model calls. '
            'Set $env:DEEPSEEK_API_KEY="sk-..." or run with --no-model.'
        )


class DeepSeekAPIError(DeepSeekError):
    def __init__(self, *, status_code: int | None, message: str) -> None:
        self.status_code = status_code
        self.message = message
        label = f"DeepSeek API error {status_code}" if status_code is not None else "DeepSeek API error"
        super().__init__(f"{label}: {message}")


class DeepSeekRequestError(DeepSeekError):
    pass


class DeepSeekResponseError(DeepSeekError):
    pass
