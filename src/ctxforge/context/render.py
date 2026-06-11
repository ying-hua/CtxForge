from __future__ import annotations

from dataclasses import dataclass

from ctxforge.context.models import ContextSection


STABILITY_ORDER = {
    "stable": 0,
    "semi_stable": 1,
    "dynamic": 2,
}


@dataclass(frozen=True)
class RenderedSectionSpan:
    ordinal: int
    name: str
    start_byte: int
    end_byte: int


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
    rendered, _ = render_prompt_parts(sections)
    return rendered


def render_prompt_parts(sections: list[ContextSection]) -> tuple[str, list[RenderedSectionSpan]]:
    parts: list[str] = []
    spans: list[RenderedSectionSpan] = []
    byte_offset = 0
    separator = "\n\n"
    separator_bytes = len(separator.encode("utf-8"))

    for ordinal, section in enumerate(sections):
        if ordinal:
            parts.append(separator)
            byte_offset += separator_bytes

        rendered = render_section(section)
        start_byte = byte_offset
        byte_offset += len(rendered.encode("utf-8"))
        parts.append(rendered)
        spans.append(
            RenderedSectionSpan(
                ordinal=ordinal,
                name=section.name,
                start_byte=start_byte,
                end_byte=byte_offset,
            )
        )

    return "".join(parts), spans
