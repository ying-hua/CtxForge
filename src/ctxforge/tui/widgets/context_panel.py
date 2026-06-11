from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static


class ContextPanel(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Context", classes="panel-title")
        yield Static("No context prepared.", id="context-summary", classes="panel-summary")
        yield DataTable(id="context-sections")

    def on_mount(self) -> None:
        table = self.query_one("#context-sections", DataTable)
        table.add_columns("Section", "Stability", "Tokens", "Source", "Flags")
        table.cursor_type = "row"
        table.zebra_stripes = True

    def render_report(
        self,
        context_report: dict[str, object],
        skill_report: dict[str, object],
    ) -> None:
        selected = _selected_skill_names(skill_report)
        summary = (
            f"status={context_report.get('status', 'n/a')}  "
            f"tokens={context_report.get('total_estimated_tokens', 0)}/"
            f"{context_report.get('input_budget', 0)}  "
            f"stable={context_report.get('stable_prefix_tokens', 0)}  "
            f"semi={context_report.get('semi_stable_tokens', 0)}  "
            f"dynamic={context_report.get('dynamic_tokens', 0)}\n"
            f"stable_bytes={context_report.get('stable_prefix_bytes', 0)}  "
            f"overflow={context_report.get('overflow', False)}  "
            f"skills={', '.join(selected) or 'none'}"
        )
        self.query_one("#context-summary", Static).update(summary)

        table = self.query_one("#context-sections", DataTable)
        table.clear()
        for section in _section_rows(context_report.get("included_sections"), included=True):
            table.add_row(*section)
        for section in _section_rows(context_report.get("dropped_sections"), included=False):
            table.add_row(*section)


def _section_rows(value: object, *, included: bool) -> list[tuple[str, str, str, str, str]]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        flags = []
        if item.get("required"):
            flags.append("required")
        if item.get("truncated"):
            flags.append("truncated")
        if not included:
            flags.append("dropped")
        rows.append(
            (
                str(item.get("name", "")),
                str(item.get("stability", "")),
                str(item.get("token_estimate", "")),
                str(item.get("source", "")),
                ", ".join(flags) or "-",
            )
        )
    return rows


def _selected_skill_names(skill_report: dict[str, object]) -> list[str]:
    selected = skill_report.get("selected")
    if not isinstance(selected, list):
        return []
    names = []
    for item in selected:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
        elif isinstance(item, str):
            names.append(item)
    return names
