from __future__ import annotations

from ctxforge.context.models import ContextSection


STABILITY_ORDER = {
    "stable": 0,
    "semi_stable": 1,
    "dynamic": 2,
}


def sort_sections(sections: list[ContextSection]) -> list[ContextSection]:
    return sorted(
        sections,
        key=lambda section: (
            STABILITY_ORDER[section.stability],
            -section.priority,
            section.name,
            section.source,
        ),
    )


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def render_section(section: ContextSection) -> str:
    content = normalize_newlines(section.content).strip()
    return (
        f'<context_section name="{section.name}" '
        f'stability="{section.stability}" '
        f'priority="{section.priority}" '
        f'source="{section.source}">\n'
        f"{content}\n"
        "</context_section>"
    )


def render_prompt(sections: list[ContextSection]) -> str:
    return "\n\n".join(render_section(section) for section in sections)
