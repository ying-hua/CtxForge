from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from ctxforge.cache.models import CacheSectionSnapshot, CacheSnapshot
from ctxforge.context import BuiltContext
from ctxforge.context.render import normalize_newlines, render_prompt_parts, render_section


CACHE_SNAPSHOT_FORMAT_VERSION = 1


def create_cache_snapshot(
    built_context: BuiltContext,
    *,
    cwd: Path,
    session_id: str,
    provider: str,
    base_url: str,
    model: str,
    snapshot_id: str | None = None,
    created_at: datetime | None = None,
) -> CacheSnapshot:
    rendered_prompt, spans = render_prompt_parts(built_context.sections)
    if rendered_prompt != built_context.rendered_prompt:
        raise ValueError("cache snapshot rendering does not match built context")

    truncated = {
        (report.name, report.source, report.priority, report.stability)
        for report in built_context.report.truncated_sections
    }
    section_snapshots: list[CacheSectionSnapshot] = []
    for section, span in zip(built_context.sections, spans):
        content = normalize_newlines(section.content).strip()
        rendered = render_section(section)
        section_snapshots.append(
            CacheSectionSnapshot(
                key=f"{section.name}#{span.ordinal}",
                name=section.name,
                stability=section.stability,
                source=section.source,
                ordinal=span.ordinal,
                start_byte=span.start_byte,
                end_byte=span.end_byte,
                token_estimate=section.token_estimate,
                content_sha256=_hash_bytes(content.encode("utf-8")),
                rendered_sha256=_hash_bytes(rendered.encode("utf-8")),
                truncated=(
                    section.name,
                    section.source,
                    section.priority,
                    section.stability,
                )
                in truncated,
            )
        )

    prompt_bytes = rendered_prompt.encode("utf-8")
    canonical_messages = json.dumps(
        built_context.messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CacheSnapshot(
        id=snapshot_id or f"cache-{uuid4().hex[:16]}",
        format_version=CACHE_SNAPSHOT_FORMAT_VERSION,
        project_key=normalize_project_key(cwd),
        session_id=session_id,
        provider=provider.strip().lower(),
        base_url=normalize_base_url(base_url),
        model=model,
        prompt_bytes=prompt_bytes,
        prompt_sha256=_hash_bytes(prompt_bytes),
        messages_sha256=_hash_bytes(canonical_messages),
        stable_prefix_sha256=built_context.report.stable_prefix_sha256,
        total_estimated_tokens=built_context.report.total_estimated_tokens,
        sections=section_snapshots,
        created_at=created_at or datetime.now(timezone.utc),
    )


def normalize_project_key(cwd: Path) -> str:
    return os.path.normcase(str(cwd.resolve()))


def normalize_base_url(base_url: str) -> str:
    raw = base_url.strip().rstrip("/")
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        return raw
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def _hash_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()
