from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


CacheStatus = Literal["no_baseline", "identical", "changed", "incomparable", "disabled"]
CacheChangeType = Literal["changed", "added", "removed", "reordered"]


@dataclass(frozen=True)
class CacheSectionSnapshot:
    key: str
    name: str
    stability: str
    source: str
    ordinal: int
    start_byte: int
    end_byte: int
    token_estimate: int
    content_sha256: str
    rendered_sha256: str
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "stability": self.stability,
            "source": self.source,
            "ordinal": self.ordinal,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "token_estimate": self.token_estimate,
            "content_sha256": self.content_sha256,
            "rendered_sha256": self.rendered_sha256,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CacheSectionSnapshot:
        return cls(
            key=str(data["key"]),
            name=str(data["name"]),
            stability=str(data["stability"]),
            source=str(data["source"]),
            ordinal=int(data["ordinal"]),
            start_byte=int(data["start_byte"]),
            end_byte=int(data["end_byte"]),
            token_estimate=int(data["token_estimate"]),
            content_sha256=str(data["content_sha256"]),
            rendered_sha256=str(data["rendered_sha256"]),
            truncated=bool(data["truncated"]),
        )


@dataclass(frozen=True)
class CacheSnapshot:
    id: str
    format_version: int
    project_key: str
    session_id: str
    provider: str
    base_url: str
    model: str
    prompt_bytes: bytes
    prompt_sha256: str
    messages_sha256: str
    stable_prefix_sha256: str
    total_estimated_tokens: int
    sections: list[CacheSectionSnapshot]
    created_at: datetime


@dataclass(frozen=True)
class SectionChange:
    key: str
    name: str
    change_type: CacheChangeType
    stability: str | None
    previous_sha256: str | None
    current_sha256: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "change_type": self.change_type,
            "stability": self.stability,
            "previous_sha256": self.previous_sha256,
            "current_sha256": self.current_sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SectionChange:
        return cls(
            key=str(data["key"]),
            name=str(data["name"]),
            change_type=str(data["change_type"]),  # type: ignore[arg-type]
            stability=str(data["stability"]) if data.get("stability") is not None else None,
            previous_sha256=(
                str(data["previous_sha256"]) if data.get("previous_sha256") is not None else None
            ),
            current_sha256=str(data["current_sha256"]) if data.get("current_sha256") is not None else None,
        )


@dataclass(frozen=True)
class ProviderCacheUsage:
    prompt_tokens: int | None
    hit_tokens: int | None
    miss_tokens: int | None


@dataclass(frozen=True)
class CacheReport:
    status: CacheStatus
    snapshot_id: str | None
    baseline_snapshot_id: str | None
    baseline_scope: str | None
    same_session: bool | None
    prompt_bytes: int
    common_prefix_bytes: int | None
    changed_after_byte: int | None
    common_prefix_estimated_tokens: int | None
    total_estimated_tokens: int
    estimated_cache_hit_ratio: float | None
    actual_cache_hit_ratio: float | None
    prompt_cache_hit_tokens: int | None
    prompt_cache_miss_tokens: int | None
    provider_usage_status: str
    first_changed_section: str | None
    direct_changes: list[SectionChange] = field(default_factory=list)
    invalidated_sections: list[str] = field(default_factory=list)
    stable_prefix_changed: bool | None = None
    persistence_status: str = "not_saved"
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "snapshot_id": self.snapshot_id,
            "baseline_snapshot_id": self.baseline_snapshot_id,
            "baseline_scope": self.baseline_scope,
            "same_session": self.same_session,
            "prompt_bytes": self.prompt_bytes,
            "common_prefix_bytes": self.common_prefix_bytes,
            "changed_after_byte": self.changed_after_byte,
            "common_prefix_estimated_tokens": self.common_prefix_estimated_tokens,
            "total_estimated_tokens": self.total_estimated_tokens,
            "estimated_cache_hit_ratio": self.estimated_cache_hit_ratio,
            "actual_cache_hit_ratio": self.actual_cache_hit_ratio,
            "prompt_cache_hit_tokens": self.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self.prompt_cache_miss_tokens,
            "provider_usage_status": self.provider_usage_status,
            "first_changed_section": self.first_changed_section,
            "direct_changes": [change.to_dict() for change in self.direct_changes],
            "invalidated_sections": self.invalidated_sections,
            "stable_prefix_changed": self.stable_prefix_changed,
            "persistence_status": self.persistence_status,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CacheReport:
        direct_changes = data.get("direct_changes")
        return cls(
            status=str(data["status"]),  # type: ignore[arg-type]
            snapshot_id=str(data["snapshot_id"]) if data.get("snapshot_id") is not None else None,
            baseline_snapshot_id=(
                str(data["baseline_snapshot_id"]) if data.get("baseline_snapshot_id") is not None else None
            ),
            baseline_scope=str(data["baseline_scope"]) if data.get("baseline_scope") is not None else None,
            same_session=bool(data["same_session"]) if data.get("same_session") is not None else None,
            prompt_bytes=int(data["prompt_bytes"]),
            common_prefix_bytes=(
                int(data["common_prefix_bytes"]) if data.get("common_prefix_bytes") is not None else None
            ),
            changed_after_byte=(
                int(data["changed_after_byte"]) if data.get("changed_after_byte") is not None else None
            ),
            common_prefix_estimated_tokens=(
                int(data["common_prefix_estimated_tokens"])
                if data.get("common_prefix_estimated_tokens") is not None
                else None
            ),
            total_estimated_tokens=int(data["total_estimated_tokens"]),
            estimated_cache_hit_ratio=(
                float(data["estimated_cache_hit_ratio"])
                if data.get("estimated_cache_hit_ratio") is not None
                else None
            ),
            actual_cache_hit_ratio=(
                float(data["actual_cache_hit_ratio"])
                if data.get("actual_cache_hit_ratio") is not None
                else None
            ),
            prompt_cache_hit_tokens=(
                int(data["prompt_cache_hit_tokens"])
                if data.get("prompt_cache_hit_tokens") is not None
                else None
            ),
            prompt_cache_miss_tokens=(
                int(data["prompt_cache_miss_tokens"])
                if data.get("prompt_cache_miss_tokens") is not None
                else None
            ),
            provider_usage_status=str(data["provider_usage_status"]),
            first_changed_section=(
                str(data["first_changed_section"]) if data.get("first_changed_section") is not None else None
            ),
            direct_changes=[
                SectionChange.from_dict(item)
                for item in direct_changes or []
                if isinstance(item, dict)
            ],
            invalidated_sections=[str(item) for item in data.get("invalidated_sections", [])],
            stable_prefix_changed=(
                bool(data["stable_prefix_changed"]) if data.get("stable_prefix_changed") is not None else None
            ),
            persistence_status=str(data.get("persistence_status", "not_saved")),
            error=str(data["error"]) if data.get("error") is not None else None,
        )


@dataclass(frozen=True)
class CacheBaseline:
    snapshot: CacheSnapshot
    scope: Literal["session", "project_fallback"]


@dataclass(frozen=True)
class CacheHistoryEntry:
    snapshot: CacheSnapshot
    report: CacheReport
    request_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "created_at": self.snapshot.created_at.isoformat(),
            "session_id": self.snapshot.session_id,
            "provider": self.snapshot.provider,
            "base_url": self.snapshot.base_url,
            "model": self.snapshot.model,
            "request_id": self.request_id,
            "report": self.report.to_dict(),
        }
