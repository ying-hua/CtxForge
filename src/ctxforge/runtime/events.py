from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ctxforge.runtime.agent import RuntimeResult


@dataclass(frozen=True)
class RuntimeEvent:
    run_id: str
    session_id: str
    sequence: int


@dataclass(frozen=True)
class RunStarted(RuntimeEvent):
    task: str
    model: str


@dataclass(frozen=True)
class RuntimePrepared(RuntimeEvent):
    context_report: dict[str, object]
    memory_report: dict[str, object]
    skill_report: dict[str, object]
    cache_report: dict[str, object]


@dataclass(frozen=True)
class ResponseDelta(RuntimeEvent):
    text: str


@dataclass(frozen=True)
class RunCompleted(RuntimeEvent):
    result: RuntimeResult


@dataclass(frozen=True)
class RunFailed(RuntimeEvent):
    error_code: str
    message: str
    retryable: bool
    partial_answer: str


@dataclass(frozen=True)
class RunCancelled(RuntimeEvent):
    partial_answer: str
