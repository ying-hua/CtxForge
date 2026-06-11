from __future__ import annotations

from dataclasses import replace

from ctxforge.cache.models import (
    CacheReport,
    CacheSectionSnapshot,
    CacheSnapshot,
    ProviderCacheUsage,
    SectionChange,
)
from ctxforge.context.budget import estimate_tokens


def analyze_cache(
    current: CacheSnapshot,
    baseline: CacheSnapshot | None,
    *,
    baseline_scope: str | None = None,
) -> CacheReport:
    if baseline is None:
        return CacheReport(
            status="no_baseline",
            snapshot_id=current.id,
            baseline_snapshot_id=None,
            baseline_scope=None,
            same_session=None,
            prompt_bytes=len(current.prompt_bytes),
            common_prefix_bytes=None,
            changed_after_byte=None,
            common_prefix_estimated_tokens=None,
            total_estimated_tokens=current.total_estimated_tokens,
            estimated_cache_hit_ratio=None,
            actual_cache_hit_ratio=None,
            prompt_cache_hit_tokens=None,
            prompt_cache_miss_tokens=None,
            provider_usage_status="not_returned",
            first_changed_section=None,
            stable_prefix_changed=None,
        )

    if not _compatible(current, baseline):
        return _incomparable_report(
            current,
            baseline,
            baseline_scope=baseline_scope,
            error="snapshot_scope_or_format_changed",
        )

    if current.prompt_bytes == baseline.prompt_bytes and current.messages_sha256 != baseline.messages_sha256:
        return _incomparable_report(
            current,
            baseline,
            baseline_scope=baseline_scope,
            error="message_envelope_changed",
        )

    common_bytes = common_prefix_length(current.prompt_bytes, baseline.prompt_bytes)
    identical = current.prompt_bytes == baseline.prompt_bytes
    common_tokens = (
        current.total_estimated_tokens
        if identical
        else estimate_tokens(current.prompt_bytes[:common_bytes].decode("utf-8", errors="ignore"))
    )
    estimated_ratio = (
        1.0
        if identical
        else _ratio(common_tokens, current.total_estimated_tokens)
    )
    changes = _section_changes(current, baseline)
    first_changed = None if identical else _first_changed_section(current, baseline, common_bytes, changes)
    invalidated = [] if identical else _invalidated_sections(current, first_changed, common_bytes)

    return CacheReport(
        status="identical" if identical else "changed",
        snapshot_id=current.id,
        baseline_snapshot_id=baseline.id,
        baseline_scope=baseline_scope,
        same_session=current.session_id == baseline.session_id,
        prompt_bytes=len(current.prompt_bytes),
        common_prefix_bytes=common_bytes,
        changed_after_byte=None if identical else common_bytes,
        common_prefix_estimated_tokens=common_tokens,
        total_estimated_tokens=current.total_estimated_tokens,
        estimated_cache_hit_ratio=estimated_ratio,
        actual_cache_hit_ratio=None,
        prompt_cache_hit_tokens=None,
        prompt_cache_miss_tokens=None,
        provider_usage_status="not_returned",
        first_changed_section=first_changed,
        direct_changes=changes,
        invalidated_sections=invalidated,
        stable_prefix_changed=current.stable_prefix_sha256 != baseline.stable_prefix_sha256,
    )


def attach_provider_usage(report: CacheReport, usage: ProviderCacheUsage) -> CacheReport:
    status = "not_returned"
    actual_ratio = None
    if usage.hit_tokens is not None and usage.miss_tokens is not None:
        denominator = usage.hit_tokens + usage.miss_tokens
        actual_ratio = _ratio(usage.hit_tokens, denominator)
        status = "observed"
        if usage.prompt_tokens is not None and usage.prompt_tokens != denominator:
            status = "inconsistent"
    return replace(
        report,
        actual_cache_hit_ratio=actual_ratio,
        prompt_cache_hit_tokens=usage.hit_tokens,
        prompt_cache_miss_tokens=usage.miss_tokens,
        provider_usage_status=status,
    )


def mark_persistence(report: CacheReport, status: str, error: str | None = None) -> CacheReport:
    return replace(report, persistence_status=status, error=error or report.error)


def mark_dry_run(report: CacheReport) -> CacheReport:
    return replace(report, provider_usage_status="dry_run", persistence_status="not_saved")


def disabled_cache_report(current: CacheSnapshot) -> CacheReport:
    return CacheReport(
        status="disabled",
        snapshot_id=current.id,
        baseline_snapshot_id=None,
        baseline_scope=None,
        same_session=None,
        prompt_bytes=len(current.prompt_bytes),
        common_prefix_bytes=None,
        changed_after_byte=None,
        common_prefix_estimated_tokens=None,
        total_estimated_tokens=current.total_estimated_tokens,
        estimated_cache_hit_ratio=None,
        actual_cache_hit_ratio=None,
        prompt_cache_hit_tokens=None,
        prompt_cache_miss_tokens=None,
        provider_usage_status="not_returned",
        first_changed_section=None,
        stable_prefix_changed=None,
        persistence_status="disabled",
    )


def common_prefix_length(left: bytes, right: bytes) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index


def _compatible(current: CacheSnapshot, baseline: CacheSnapshot) -> bool:
    return (
        current.project_key == baseline.project_key
        and current.provider == baseline.provider
        and current.base_url == baseline.base_url
        and current.model == baseline.model
        and current.format_version == baseline.format_version
    )


def _section_changes(current: CacheSnapshot, baseline: CacheSnapshot) -> list[SectionChange]:
    changes: list[SectionChange] = []
    unmatched_current = set(range(len(current.sections)))
    unmatched_baseline = set(range(len(baseline.sections)))

    baseline_by_key = {section.key: index for index, section in enumerate(baseline.sections)}
    for current_index, current_section in enumerate(current.sections):
        baseline_index = baseline_by_key.get(current_section.key)
        if baseline_index is None:
            continue
        baseline_section = baseline.sections[baseline_index]
        if current_section.rendered_sha256 == baseline_section.rendered_sha256:
            unmatched_current.discard(current_index)
            unmatched_baseline.discard(baseline_index)

    for current_index in list(sorted(unmatched_current)):
        current_section = current.sections[current_index]
        baseline_index = _nearest_section(
            current_section,
            baseline,
            unmatched_baseline,
            require_same_content=True,
        )
        if baseline_index is None:
            continue
        baseline_section = baseline.sections[baseline_index]
        unmatched_current.remove(current_index)
        unmatched_baseline.remove(baseline_index)
        changes.append(
            SectionChange(
                key=current_section.key,
                name=current_section.name,
                change_type="reordered",
                stability=current_section.stability,
                previous_sha256=baseline_section.rendered_sha256,
                current_sha256=current_section.rendered_sha256,
            )
        )

    for current_index in list(sorted(unmatched_current)):
        current_section = current.sections[current_index]
        baseline_index = baseline_by_key.get(current_section.key)
        if baseline_index not in unmatched_baseline:
            baseline_index = _nearest_section(
                current_section,
                baseline,
                unmatched_baseline,
                require_same_content=False,
            )
        if baseline_index is None:
            continue
        baseline_section = baseline.sections[baseline_index]
        unmatched_current.remove(current_index)
        unmatched_baseline.remove(baseline_index)
        changes.append(
            SectionChange(
                key=current_section.key,
                name=current_section.name,
                change_type="changed",
                stability=current_section.stability,
                previous_sha256=baseline_section.rendered_sha256,
                current_sha256=current_section.rendered_sha256,
            )
        )

    for current_index in sorted(unmatched_current):
        section = current.sections[current_index]
        changes.append(
            SectionChange(
                key=section.key,
                name=section.name,
                change_type="added",
                stability=section.stability,
                previous_sha256=None,
                current_sha256=section.rendered_sha256,
            )
        )
    for baseline_index in sorted(unmatched_baseline):
        section = baseline.sections[baseline_index]
        changes.append(
            SectionChange(
                key=section.key,
                name=section.name,
                change_type="removed",
                stability=section.stability,
                previous_sha256=section.rendered_sha256,
                current_sha256=None,
            )
        )

    order = {section.key: section.ordinal for section in current.sections}
    baseline_order = {section.key: section.ordinal for section in baseline.sections}
    return sorted(
        changes,
        key=lambda change: (
            order.get(change.key, baseline_order.get(change.key, 10**9)),
            change.name,
            change.change_type,
        ),
    )


def _nearest_section(
    current_section: CacheSectionSnapshot,
    baseline: CacheSnapshot,
    unmatched_baseline: set[int],
    *,
    require_same_content: bool,
) -> int | None:
    candidates = [
        index
        for index in unmatched_baseline
        if baseline.sections[index].name == current_section.name
        and baseline.sections[index].source == current_section.source
        and (
            not require_same_content
            or baseline.sections[index].content_sha256 == current_section.content_sha256
        )
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda index: abs(baseline.sections[index].ordinal - current_section.ordinal))


def _incomparable_report(
    current: CacheSnapshot,
    baseline: CacheSnapshot,
    *,
    baseline_scope: str | None,
    error: str,
) -> CacheReport:
    return CacheReport(
        status="incomparable",
        snapshot_id=current.id,
        baseline_snapshot_id=baseline.id,
        baseline_scope=baseline_scope,
        same_session=current.session_id == baseline.session_id,
        prompt_bytes=len(current.prompt_bytes),
        common_prefix_bytes=None,
        changed_after_byte=None,
        common_prefix_estimated_tokens=None,
        total_estimated_tokens=current.total_estimated_tokens,
        estimated_cache_hit_ratio=None,
        actual_cache_hit_ratio=None,
        prompt_cache_hit_tokens=None,
        prompt_cache_miss_tokens=None,
        provider_usage_status="not_returned",
        first_changed_section=None,
        stable_prefix_changed=None,
        error=error,
    )


def _first_changed_section(
    current: CacheSnapshot,
    baseline: CacheSnapshot,
    offset: int,
    changes: list[SectionChange],
) -> str | None:
    for section in current.sections:
        if section.start_byte <= offset < section.end_byte:
            return section.name
        if offset < section.start_byte:
            return section.name

    removed = next((change.name for change in changes if change.change_type == "removed"), None)
    if removed is not None:
        return removed
    if len(current.prompt_bytes) < len(baseline.prompt_bytes) and baseline.sections:
        return baseline.sections[-1].name
    return None


def _invalidated_sections(
    current: CacheSnapshot,
    first_changed_section: str | None,
    offset: int,
) -> list[str]:
    if first_changed_section is None:
        return []
    start = next(
        (
            section.ordinal
            for section in current.sections
            if section.name == first_changed_section
            and (section.start_byte <= offset < section.end_byte or offset <= section.start_byte)
        ),
        None,
    )
    if start is None:
        return []
    return [section.name for section in current.sections[start:]]


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return min(1.0, max(0.0, numerator / denominator))
