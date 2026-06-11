from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, Static

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.runtime.agent import RuntimeRequest, RuntimeResult
from ctxforge.runtime.events import (
    ResponseDelta,
    RunCompleted,
    RunStarted,
    RuntimePrepared,
)
from ctxforge.tui.app import CtxForgeTuiApp


def _prepared(run_id: str, session_id: str, sequence: int = 1) -> RuntimePrepared:
    return RuntimePrepared(
        run_id=run_id,
        session_id=session_id,
        sequence=sequence,
        context_report={
            "status": "ok",
            "total_estimated_tokens": 10,
            "input_budget": 100,
            "stable_prefix_tokens": 4,
            "semi_stable_tokens": 2,
            "dynamic_tokens": 4,
            "stable_prefix_bytes": 20,
            "overflow": False,
            "included_sections": [
                {
                    "name": "request.task",
                    "stability": "dynamic",
                    "token_estimate": 4,
                    "source": "request",
                    "required": True,
                    "truncated": False,
                }
            ],
            "dropped_sections": [],
        },
        memory_report={
            "status": "ok",
            "retrieved_count": 1,
            "working_count": 0,
            "summary_count": 0,
            "long_term_count": 1,
            "hits": [
                {
                    "score": 0.9,
                    "scope": "project",
                    "kind": "decision",
                    "source": "test",
                    "reason": "keyword",
                    "content": "Use sqlite3.",
                }
            ],
        },
        skill_report={
            "status": "ok",
            "selected": [{"name": "review", "reason": "explicit"}],
        },
        cache_report={
            "status": "changed",
            "baseline_scope": "session",
            "common_prefix_bytes": 20,
            "first_changed_section": "request.task",
            "stable_prefix_changed": False,
            "estimated_cache_hit_ratio": 0.5,
            "actual_cache_hit_ratio": None,
            "provider_usage_status": "not_returned",
            "persistence_status": "not_saved",
            "direct_changes": [],
            "invalidated_sections": ["request.task"],
        },
    )


def _result(session_id: str, answer: str) -> RuntimeResult:
    return RuntimeResult(
        answer=answer,
        session_id=session_id,
        context_report={},
        memory_report={},
        skill_report={},
        cache_report={
            "status": "changed",
            "baseline_scope": "session",
            "common_prefix_bytes": 20,
            "first_changed_section": "request.task",
            "stable_prefix_changed": False,
            "estimated_cache_hit_ratio": 0.5,
            "actual_cache_hit_ratio": 0.8,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
            "provider_usage_status": "observed",
            "persistence_status": "saved",
            "direct_changes": [],
            "invalidated_sections": ["request.task"],
        },
        llm_report={
            "status": "ok",
            "model": "deepseek-v4-flash",
            "request_id": "request-1",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            "summary_written": True,
        },
    )


@pytest.mark.asyncio
async def test_tui_streams_reports_and_response(tmp_path):
    async def fake_stream(request: RuntimeRequest, settings, **kwargs):
        run_id = "run-1"
        session_id = request.session_id or ""
        yield RunStarted(run_id, session_id, 0, request.task, request.model or settings.deepseek.model)
        yield _prepared(run_id, session_id)
        yield ResponseDelta(run_id, session_id, 2, "Hello ")
        await asyncio.sleep(0)
        yield ResponseDelta(run_id, session_id, 3, "TUI")
        yield RunCompleted(run_id, session_id, 4, _result(session_id, "Hello TUI"))

    app = CtxForgeTuiApp(
        settings=CtxForgeSettings(),
        project_dir=tmp_path,
        session_id="session-test",
        stream_factory=fake_stream,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#task-input")
        await pilot.press(*list("review"))
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert app.session.phase == "completed"
        assert "Hello TUI" in str(app.query_one("#response-body", Static).render())
        assert "tokens=10/100" in str(app.query_one("#context-summary", Static).render())
        assert "retrieved=1" in str(app.query_one("#memory-summary", Static).render())
        assert "actual=80.0%" in str(app.query_one("#cache-summary", Static).render())
        assert app.query_one("#run-button", Button).disabled is False


@pytest.mark.asyncio
async def test_tui_dry_run_uses_real_runtime_and_shows_answer(tmp_path):
    app = CtxForgeTuiApp(
        settings=CtxForgeSettings(),
        project_dir=tmp_path,
        session_id="session-dry",
        execute_model=False,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#task-input")
        await pilot.press(*list("inspect"))
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert app.session.phase == "completed"
        body = str(app.query_one("#response-body", Static).render())
        assert "dry run is ready" in body
        assert "status=ok" in str(app.query_one("#context-summary", Static).render())


@pytest.mark.asyncio
async def test_tui_cancel_keeps_partial_response(tmp_path):
    blocker = asyncio.Event()

    async def blocking_stream(request: RuntimeRequest, settings, **kwargs):
        run_id = "run-cancel"
        session_id = request.session_id or ""
        yield RunStarted(run_id, session_id, 0, request.task, request.model or settings.deepseek.model)
        yield _prepared(run_id, session_id)
        yield ResponseDelta(run_id, session_id, 2, "partial")
        await blocker.wait()

    app = CtxForgeTuiApp(
        settings=CtxForgeSettings(),
        project_dir=tmp_path,
        session_id="session-cancel",
        stream_factory=blocking_stream,
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.click("#task-input")
        await pilot.press(*list("cancel"))
        await pilot.press("enter")
        for _ in range(20):
            await pilot.pause(0.02)
            if app.session.answer == "partial":
                break
        await pilot.press("escape")
        await pilot.pause(0.1)

        assert app.session.phase == "cancelled"
        assert app.session.answer == "partial"
        assert "Cancelled" in str(app.query_one("#response-meta", Static).render())
        assert app.query_one("#run-button", Button).disabled is False


@pytest.mark.asyncio
async def test_tui_narrow_layout_and_session_reuse(tmp_path):
    seen_sessions: list[str | None] = []

    async def fast_stream(request: RuntimeRequest, settings, **kwargs):
        seen_sessions.append(request.session_id)
        run_id = f"run-{len(seen_sessions)}"
        session_id = request.session_id or ""
        yield RunStarted(run_id, session_id, 0, request.task, request.model or settings.deepseek.model)
        yield _prepared(run_id, session_id)
        yield RunCompleted(run_id, session_id, 2, _result(session_id, "done"))

    app = CtxForgeTuiApp(
        settings=CtxForgeSettings(),
        project_dir=Path(tmp_path),
        session_id="session-reused",
        stream_factory=fast_stream,
    )
    async with app.run_test(size=(80, 24)) as pilot:
        assert app.screen.has_class("narrow")
        for task in ("first", "second"):
            await pilot.click("#task-input")
            await pilot.press(*list(task))
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.session.phase == "completed"

    assert seen_sessions == ["session-reused", "session-reused"]
