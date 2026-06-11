from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static


class ResponsePanel(Vertical):
    def __init__(self, *, refresh_ms: int = 40, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._refresh_seconds = refresh_ms / 1000
        self._answer = ""
        self._pending = ""

    def compose(self) -> ComposeResult:
        yield Static("Streaming Response", classes="panel-title")
        yield Static("No task submitted.", id="response-task", classes="panel-summary")
        with VerticalScroll(id="response-scroll"):
            yield Static("", id="response-body")
        yield Static("", id="response-meta", classes="panel-summary")

    def on_mount(self) -> None:
        self.set_interval(self._refresh_seconds, self.flush_pending)

    def start(self, task: str) -> None:
        self._answer = ""
        self._pending = ""
        self.query_one("#response-task", Static).update(f"Task: {task}")
        self.query_one("#response-body", Static).update("")
        self.query_one("#response-meta", Static).update("Preparing runtime...")

    def clear(self) -> None:
        self._answer = ""
        self._pending = ""
        self.query_one("#response-task", Static).update("No task submitted.")
        self.query_one("#response-body", Static).update("")
        self.query_one("#response-meta", Static).update("Ready.")

    def append_delta(self, text: str) -> None:
        self._pending += text

    def flush_pending(self) -> None:
        if not self._pending:
            return
        self._answer += self._pending
        self._pending = ""
        self.query_one("#response-body", Static).update(self._answer)
        self.query_one("#response-scroll", VerticalScroll).scroll_end(animate=False)

    def complete(
        self,
        report: dict[str, object],
        *,
        answer: str,
        warning: str | None = None,
    ) -> None:
        self._answer = answer
        self._pending = ""
        self.query_one("#response-body", Static).update(answer)
        self.query_one("#response-scroll", VerticalScroll).scroll_end(animate=False)
        usage = report.get("usage")
        usage_text = ""
        if isinstance(usage, dict):
            usage_text = (
                f"prompt={usage.get('prompt_tokens')} "
                f"completion={usage.get('completion_tokens')}"
            )
        meta = (
            f"Completed | model={report.get('model') or 'n/a'} | "
            f"request={report.get('request_id') or 'n/a'} | "
            f"finish={report.get('finish_reason') or 'n/a'} | {usage_text}"
        )
        if warning:
            meta = f"{meta}\nWarning: {warning}"
        self.query_one("#response-meta", Static).update(meta)

    def fail(self, message: str, *, cancelled: bool = False) -> None:
        self.flush_pending()
        label = "Cancelled" if cancelled else "Failed"
        self.query_one("#response-meta", Static).update(f"{label}: {message}")

    @property
    def answer(self) -> str:
        return f"{self._answer}{self._pending}"
