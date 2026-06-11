from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import uuid4

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Static
from textual.worker import Worker

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.runtime.agent import RuntimeRequest
from ctxforge.runtime.events import (
    ResponseDelta,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RuntimeEvent,
    RuntimePrepared,
)
from ctxforge.runtime.stream import stream_phase6
from ctxforge.tui.messages import RuntimeEventMessage
from ctxforge.tui.state import TuiSessionState
from ctxforge.tui.widgets import CachePanel, ContextPanel, MemoryPanel, ResponsePanel


StreamFactory = Callable[..., AsyncIterator[RuntimeEvent]]


class CtxForgeTuiApp(App[None]):
    CSS_PATH = "styles.tcss"
    TITLE = "CtxForge"
    SUB_TITLE = "Context Engineering Runtime"
    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("escape", "cancel_run", "Cancel"),
        ("ctrl+l", "clear_response", "Clear"),
    ]

    def __init__(
        self,
        *,
        settings: CtxForgeSettings,
        project_dir: Path,
        session_id: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        max_output_tokens: int | None = None,
        skill_names: list[str] | None = None,
        execute_model: bool = True,
        stream_factory: StreamFactory = stream_phase6,
    ) -> None:
        super().__init__()
        active_session_id = session_id or f"session-{uuid4().hex[:12]}"
        active_model = model or settings.deepseek.model
        self.settings = settings
        self.project_dir = project_dir
        self.max_tokens = max_tokens
        self.max_output_tokens = max_output_tokens
        self.skill_names = skill_names or []
        self.execute_model = execute_model
        self.stream_factory = stream_factory
        self.session = TuiSessionState(
            session_id=active_session_id,
            model=active_model,
        )
        self._active_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="status-bar")
        with Horizontal(id="main-body"):
            with Vertical(id="observability"):
                yield ContextPanel(id="context-panel", classes="observability-panel")
                yield MemoryPanel(
                    id="memory-panel",
                    classes="observability-panel",
                    show_full_content=self.settings.tui.show_full_memory_content,
                )
                yield CachePanel(id="cache-panel", classes="observability-panel")
            yield ResponsePanel(
                id="response-panel",
                refresh_ms=self.settings.tui.response_refresh_ms,
            )
        with Horizontal(id="input-bar"):
            yield Input(placeholder="Enter a task for CtxForge...", id="task-input")
            yield Button("Run", id="run-button", variant="primary")
            yield Button("Stop", id="stop-button", variant="error", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.screen.set_class(self.size.width <= 90, "narrow")
        self._render_status()
        self.query_one("#task-input", Input).focus()

    def on_resize(self, event: events.Resize) -> None:
        self.screen.set_class(event.size.width <= 90, "narrow")

    @on(Input.Submitted, "#task-input")
    def submit_input(self, event: Input.Submitted) -> None:
        self._start_task(event.value)

    @on(Button.Pressed, "#run-button")
    def press_run(self) -> None:
        self._start_task(self.query_one("#task-input", Input).value)

    @on(Button.Pressed, "#stop-button")
    def press_stop(self) -> None:
        self.action_cancel_run()

    def _start_task(self, raw_task: str) -> None:
        task = raw_task.strip()
        if not task or self._active_worker is not None:
            return
        self.session.current_task = task
        self.session.answer = ""
        self.session.error = None
        self.session.warning = None
        self.session.phase = "preparing"
        self.session.last_sequence = -1
        self.query_one("#response-panel", ResponsePanel).start(task)
        self._set_running(True)
        self._render_status()
        request = RuntimeRequest(
            task=task,
            cwd=self.project_dir,
            session_id=self.session.session_id,
            skill_names=self.skill_names,
            max_tokens=self.max_tokens,
            model=self.session.model,
            max_output_tokens=self.max_output_tokens,
        )
        self._active_worker = self._run_task(request)

    @work(exclusive=True, exit_on_error=False)
    async def _run_task(self, request: RuntimeRequest) -> None:
        try:
            async for event in self.stream_factory(
                request,
                self.settings,
                execute_model=self.execute_model,
            ):
                self.post_message(RuntimeEventMessage(event))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.post_message(
                RuntimeEventMessage(
                    RunFailed(
                        run_id=self.session.active_run_id or "run-tui",
                        session_id=self.session.session_id,
                        sequence=self.session.last_sequence + 1,
                        error_code="tui_worker_error",
                        message=str(exc),
                        retryable=False,
                        partial_answer=self.query_one("#response-panel", ResponsePanel).answer,
                    )
                )
            )

    @on(RuntimeEventMessage)
    def handle_runtime_event(self, message: RuntimeEventMessage) -> None:
        event = message.event
        if event.sequence <= self.session.last_sequence:
            return
        self.session.last_sequence = event.sequence
        self.session.active_run_id = event.run_id

        if isinstance(event, RunStarted):
            self.session.phase = "preparing"
        elif isinstance(event, RuntimePrepared):
            self.session.phase = "streaming"
            self.session.context_report = event.context_report
            self.session.memory_report = event.memory_report
            self.session.skill_report = event.skill_report
            self.session.cache_report = event.cache_report
            self.query_one("#context-panel", ContextPanel).render_report(
                event.context_report,
                event.skill_report,
            )
            self.query_one("#memory-panel", MemoryPanel).render_report(event.memory_report)
            self.query_one("#cache-panel", CachePanel).render_report(event.cache_report)
        elif isinstance(event, ResponseDelta):
            self.session.phase = "streaming"
            self.session.answer += event.text
            self.query_one("#response-panel", ResponsePanel).append_delta(event.text)
        elif isinstance(event, RunCompleted):
            self.session.phase = "completed"
            self.session.answer = event.result.answer
            self.session.context_report = event.result.context_report
            self.session.memory_report = event.result.memory_report
            self.session.skill_report = event.result.skill_report
            self.session.cache_report = event.result.cache_report
            self.session.llm_report = event.result.llm_report
            warning = _result_warning(event.result.cache_report, event.result.llm_report)
            self.session.warning = warning
            self.query_one("#cache-panel", CachePanel).render_report(event.result.cache_report)
            self.query_one("#response-panel", ResponsePanel).complete(
                event.result.llm_report,
                answer=event.result.answer,
                warning=warning,
            )
            self._finish_run()
        elif isinstance(event, RunFailed):
            self.session.phase = "failed"
            self.session.answer = event.partial_answer
            self.session.error = event.message
            self.query_one("#response-panel", ResponsePanel).fail(event.message)
            self._finish_run()
        elif isinstance(event, RunCancelled):
            self.session.phase = "cancelled"
            self.session.answer = event.partial_answer
            self.query_one("#response-panel", ResponsePanel).fail(
                "Request cancelled by user.",
                cancelled=True,
            )
            self._finish_run()

        self._render_status()

    def action_cancel_run(self) -> None:
        if self._active_worker is None:
            return
        response_panel = self.query_one("#response-panel", ResponsePanel)
        self.post_message(
            RuntimeEventMessage(
                RunCancelled(
                    run_id=self.session.active_run_id or "run-tui",
                    session_id=self.session.session_id,
                    sequence=self.session.last_sequence + 1,
                    partial_answer=response_panel.answer,
                )
            )
        )
        self._active_worker.cancel()

    def action_clear_response(self) -> None:
        if self._active_worker is not None:
            return
        self.query_one("#response-panel", ResponsePanel).clear()

    def _finish_run(self) -> None:
        self._active_worker = None
        self._set_running(False)
        task_input = self.query_one("#task-input", Input)
        task_input.value = ""
        task_input.focus()

    def _set_running(self, running: bool) -> None:
        self.query_one("#run-button", Button).disabled = running
        self.query_one("#stop-button", Button).disabled = not running
        self.query_one("#task-input", Input).disabled = running

    def _render_status(self) -> None:
        status = (
            f"project={self.project_dir.name}  "
            f"session={self.session.session_id}  "
            f"model={self.session.model}  "
            f"phase={self.session.phase}"
        )
        if self.session.warning:
            status = f"{status}  warning={self.session.warning}"
        if self.session.error:
            status = f"{status}  error={self.session.error}"
        self.query_one("#status-bar", Static).update(status)


def _result_warning(
    cache_report: dict[str, object],
    llm_report: dict[str, object],
) -> str | None:
    warnings = []
    if cache_report.get("persistence_status") == "failed":
        warnings.append(str(cache_report.get("error") or "cache persistence failed"))
    if llm_report.get("summary_error"):
        warnings.append(str(llm_report["summary_error"]))
    return "; ".join(warnings) or None
