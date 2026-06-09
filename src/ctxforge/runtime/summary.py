from __future__ import annotations


class SessionSummarizer:
    def summarize(
        self,
        *,
        task: str,
        answer: str,
        selected_skills: list[str],
        memory_report: dict[str, object],
        previous_summary: str | None,
    ) -> str:
        parts = [
            f"Task: {_compact(task, 240)}",
            f"Answer: {_compact(answer, 500)}",
            f"Memory hits: {memory_report.get('retrieved_count', 0)}",
            f"Skills: {', '.join(selected_skills) if selected_skills else 'none'}",
        ]
        if previous_summary:
            parts.append(f"Previous summary: {_compact(previous_summary, 300)}")
        return "\n".join(parts)


def _compact(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 24)].rstrip()} [ctxforge: truncated]"
