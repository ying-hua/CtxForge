from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ContextStability = Literal["stable", "semi_stable", "dynamic"]


@dataclass(frozen=True)
class ContextSection:
    name: str
    stability: ContextStability
    priority: int
    content: str
    source: str
    token_estimate: int = 0
    required: bool = False


@dataclass(frozen=True)
class SectionReport:
    name: str
    stability: ContextStability
    priority: int
    source: str
    token_estimate: int
    included: bool
    required: bool
    truncated: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class ContextReport:
    status: str
    max_tokens: int
    reserved_output_tokens: int
    input_budget: int
    total_estimated_tokens: int
    stable_prefix_tokens: int
    semi_stable_tokens: int
    dynamic_tokens: int
    section_count: int
    included_sections: list[SectionReport] = field(default_factory=list)
    dropped_sections: list[SectionReport] = field(default_factory=list)
    truncated_sections: list[SectionReport] = field(default_factory=list)
    stable_prefix_bytes: int = 0
    stable_prefix_sha256: str = ""
    overflow: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "max_tokens": self.max_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "input_budget": self.input_budget,
            "total_estimated_tokens": self.total_estimated_tokens,
            "stable_prefix_tokens": self.stable_prefix_tokens,
            "semi_stable_tokens": self.semi_stable_tokens,
            "dynamic_tokens": self.dynamic_tokens,
            "section_count": self.section_count,
            "included_sections": [section.__dict__ for section in self.included_sections],
            "dropped_sections": [section.__dict__ for section in self.dropped_sections],
            "truncated_sections": [section.__dict__ for section in self.truncated_sections],
            "stable_prefix_bytes": self.stable_prefix_bytes,
            "stable_prefix_sha256": self.stable_prefix_sha256,
            "overflow": self.overflow,
        }


@dataclass(frozen=True)
class PrefixSnapshot:
    stable_prefix_bytes: bytes
    stable_prefix_sha256: str
    section_hashes: dict[str, str]


@dataclass(frozen=True)
class BuiltContext:
    messages: list[dict[str, str]]
    sections: list[ContextSection]
    rendered_prompt: str
    stable_prefix: str
    report: ContextReport
    snapshot: PrefixSnapshot
