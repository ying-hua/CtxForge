from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class CachePanel(Vertical):
    def compose(self) -> ComposeResult:
        yield Static("Cache", classes="panel-title")
        yield Static("No cache report.", id="cache-summary", classes="panel-summary")
        yield Static("", id="cache-changes")

    def render_report(self, report: dict[str, object]) -> None:
        summary = (
            f"status={report.get('status', 'n/a')}  "
            f"baseline={report.get('baseline_scope') or 'none'}  "
            f"prefix_bytes={report.get('common_prefix_bytes')}  "
            f"first_change={report.get('first_changed_section') or 'none'}\n"
            f"stable_changed={report.get('stable_prefix_changed')}  "
            f"estimated={_ratio(report.get('estimated_cache_hit_ratio'))}  "
            f"actual={_ratio(report.get('actual_cache_hit_ratio'))}\n"
            f"hit/miss={report.get('prompt_cache_hit_tokens')}/"
            f"{report.get('prompt_cache_miss_tokens')}  "
            f"usage={report.get('provider_usage_status', 'n/a')}  "
            f"persistence={report.get('persistence_status', 'n/a')}"
        )
        self.query_one("#cache-summary", Static).update(summary)

        direct = report.get("direct_changes")
        direct_names = []
        if isinstance(direct, list):
            for item in direct:
                if isinstance(item, dict):
                    direct_names.append(
                        f"{item.get('change_type', 'changed')}:{item.get('name', '')}"
                    )
        invalidated = report.get("invalidated_sections")
        invalidated_names = (
            [str(item) for item in invalidated] if isinstance(invalidated, list) else []
        )
        self.query_one("#cache-changes", Static).update(
            f"direct={', '.join(direct_names) or 'none'}\n"
            f"invalidated={', '.join(invalidated_names) or 'none'}"
        )


def _ratio(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.1%}"
