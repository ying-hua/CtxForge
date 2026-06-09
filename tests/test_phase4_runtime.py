from __future__ import annotations

import pytest

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.llm import ChatCompletionRequest, ChatCompletionResult, ChatUsage, DeepSeekAPIError
from ctxforge.memory import MemoryStore
from ctxforge.runtime.agent import RuntimeRequest, run_phase4


class FakeChatClient:
    def __init__(self, answer: str = "Runtime answer.") -> None:
        self.answer = answer
        self.requests: list[ChatCompletionRequest] = []

    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        self.requests.append(request)
        return ChatCompletionResult(
            answer=self.answer,
            model=request.model,
            request_id="fake-request-1",
            finish_reason="stop",
            usage=ChatUsage(
                prompt_tokens=42,
                completion_tokens=7,
                total_tokens=49,
                prompt_cache_hit_tokens=12,
                prompt_cache_miss_tokens=30,
            ),
            raw_usage={},
        )


class FailingChatClient:
    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        raise DeepSeekAPIError(status_code=500, message="server failed")


def test_phase4_runtime_calls_client_and_writes_session_summary(tmp_path):
    settings = CtxForgeSettings()
    store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    store.initialize()
    store.add_record(
        content="Use sqlite3 for memory.",
        source="test",
        scope="project",
        kind="decision",
        project_dir=str(tmp_path),
    )
    _write_skill(tmp_path, name="code-review", activation=["review"], instructions="Review carefully.")
    client = FakeChatClient()

    result = run_phase4(
        RuntimeRequest(task="Please review memory.", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=client,
    )

    assert result.answer == "Runtime answer."
    assert result.llm_report["status"] == "ok"
    assert result.llm_report["summary_written"] is True
    assert result.llm_report["prompt_cache_hit_tokens"] == 12
    assert result.memory_report["retrieved_count"] == 1
    assert result.skill_report["selected_count"] == 1
    assert client.requests[0].model == "deepseek-v4-flash"
    assert client.requests[0].max_tokens == settings.context.reserved_output_tokens

    summary = store.get_session_summary(project_dir=str(tmp_path), session_id="session-1")
    assert summary is not None
    assert summary.turn_count == 1
    assert "Please review memory." in summary.summary
    assert "Runtime answer." in summary.summary


def test_phase4_runtime_does_not_write_summary_when_model_call_fails(tmp_path):
    settings = CtxForgeSettings()

    with pytest.raises(DeepSeekAPIError):
        run_phase4(
            RuntimeRequest(task="fail", cwd=tmp_path, session_id="session-1"),
            settings=settings,
            client=FailingChatClient(),
        )

    store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    store.initialize()
    assert store.get_session_summary(project_dir=str(tmp_path), session_id="session-1") is None


def test_phase4_session_summary_does_not_change_stable_prefix(tmp_path):
    settings = CtxForgeSettings()
    client = FakeChatClient()

    first = run_phase4(
        RuntimeRequest(task="summarize", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=client,
    )
    second = run_phase4(
        RuntimeRequest(task="summarize again", cwd=tmp_path, session_id="session-1"),
        settings=settings,
        client=client,
    )

    assert first.context_report["stable_prefix_sha256"] == second.context_report["stable_prefix_sha256"]
    assert second.memory_report["summary_count"] == 1


def _write_skill(tmp_path, *, name: str, activation: list[str], instructions: str) -> None:
    directory = tmp_path / "skills" / name
    directory.mkdir(parents=True)
    activation_lines = ", ".join(f'"{item}"' for item in activation)
    (directory / "skill.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                'version = "0.1.0"',
                f'description = "{name} skill"',
                f"activation = [{activation_lines}]",
                'allowed_runtime_tools = ["context.read"]',
            ]
        ),
        encoding="utf-8",
    )
    (directory / "SKILL.md").write_text(instructions, encoding="utf-8")
