from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


TuiRunPhase = Literal[
    "idle",
    "preparing",
    "streaming",
    "finalizing",
    "completed",
    "failed",
    "cancelled",
]


@dataclass
class TuiSessionState:
    session_id: str
    model: str
    active_run_id: str | None = None
    last_sequence: int = -1
    phase: TuiRunPhase = "idle"
    current_task: str = ""
    answer: str = ""
    context_report: dict[str, object] = field(default_factory=dict)
    memory_report: dict[str, object] = field(default_factory=dict)
    skill_report: dict[str, object] = field(default_factory=dict)
    cache_report: dict[str, object] = field(default_factory=dict)
    llm_report: dict[str, object] = field(default_factory=dict)
    warning: str | None = None
    error: str | None = None
