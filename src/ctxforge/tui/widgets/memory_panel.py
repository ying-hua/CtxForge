from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static


class MemoryPanel(Vertical):
    def __init__(self, *, show_full_content: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._show_full_content = show_full_content

    def compose(self) -> ComposeResult:
        yield Static("Memory", classes="panel-title")
        yield Static("No memory report.", id="memory-summary", classes="panel-summary")
        yield DataTable(id="memory-hits")

    def on_mount(self) -> None:
        table = self.query_one("#memory-hits", DataTable)
        table.add_columns("Score", "Scope", "Kind", "Source", "Reason", "Content")
        table.cursor_type = "row"
        table.zebra_stripes = True

    def render_report(self, report: dict[str, object]) -> None:
        summary = (
            f"status={report.get('status', 'n/a')}  "
            f"retrieved={report.get('retrieved_count', 0)}  "
            f"working={report.get('working_count', 0)}  "
            f"summaries={report.get('summary_count', 0)}  "
            f"long_term={report.get('long_term_count', 0)}"
        )
        self.query_one("#memory-summary", Static).update(summary)

        table = self.query_one("#memory-hits", DataTable)
        table.clear()
        hits = report.get("hits")
        if not isinstance(hits, list):
            return
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            content = str(hit.get("content", ""))
            if not self._show_full_content and len(content) > 80:
                content = f"{content[:77]}..."
            score = hit.get("score")
            table.add_row(
                f"{score:.3f}" if isinstance(score, (int, float)) else "n/a",
                str(hit.get("scope", "")),
                str(hit.get("kind", "")),
                str(hit.get("source", "")),
                str(hit.get("reason", "")),
                content,
            )
