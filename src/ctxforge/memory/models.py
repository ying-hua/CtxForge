from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


MemoryScope = Literal["global", "project", "session"]
MemoryKind = Literal["preference", "fact", "decision", "summary", "working"]
MemoryStatus = Literal["ok", "empty", "disabled", "error"]


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    scope: MemoryScope
    kind: MemoryKind
    content: str
    source: str
    created_at: datetime
    updated_at: datetime
    confidence: float
    session_id: str | None = None
    project_dir: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    embedding: list[float] | None = None


@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    score: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.record.id,
            "kind": self.record.kind,
            "scope": self.record.scope,
            "score": round(self.score, 4),
            "source": self.record.source,
            "created_at": self.record.created_at.isoformat(),
            "reason": self.reason,
            "content": self.record.content,
        }


@dataclass(frozen=True)
class WorkingMemoryItem:
    id: str
    session_id: str
    project_dir: str
    key: str
    content: str
    source: str
    priority: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    project_dir: str
    summary: str
    source: str
    turn_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MemoryReport:
    status: MemoryStatus
    db_path: str
    working_count: int
    summary_count: int
    long_term_count: int
    retrieved_count: int
    hits: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "db_path": self.db_path,
            "working_count": self.working_count,
            "summary_count": self.summary_count,
            "long_term_count": self.long_term_count,
            "retrieved_count": self.retrieved_count,
            "hits": self.hits,
        }
