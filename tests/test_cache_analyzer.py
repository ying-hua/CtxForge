from __future__ import annotations

from dataclasses import replace

from ctxforge.cache import (
    ProviderCacheUsage,
    analyze_cache,
    attach_provider_usage,
    create_cache_snapshot,
)
from ctxforge.config.settings import CtxForgeSettings
from ctxforge.context import ContextBuilder, ContextSection


def test_identical_context_reports_full_local_reuse(tmp_path):
    built = ContextBuilder(CtxForgeSettings()).build(task="same", cwd=tmp_path)
    baseline = _snapshot(built, tmp_path, snapshot_id="cache-1")
    current = _snapshot(built, tmp_path, snapshot_id="cache-2")

    report = analyze_cache(current, baseline, baseline_scope="session")

    assert report.status == "identical"
    assert report.common_prefix_bytes == len(current.prompt_bytes)
    assert report.changed_after_byte is None
    assert report.estimated_cache_hit_ratio == 1.0
    assert report.direct_changes == []
    assert report.invalidated_sections == []
    assert report.stable_prefix_changed is False


def test_dynamic_task_change_preserves_stable_prefix_and_invalidates_suffix(tmp_path):
    builder = ContextBuilder(CtxForgeSettings())
    baseline = _snapshot(builder.build(task="first", cwd=tmp_path), tmp_path, snapshot_id="cache-1")
    current = _snapshot(builder.build(task="second", cwd=tmp_path), tmp_path, snapshot_id="cache-2")

    report = analyze_cache(current, baseline, baseline_scope="session")

    assert report.status == "changed"
    assert report.first_changed_section == "request.task"
    assert report.stable_prefix_changed is False
    assert report.invalidated_sections[0] == "request.task"
    assert any(change.name == "request.task" for change in report.direct_changes)
    assert 0.0 < report.estimated_cache_hit_ratio < 1.0


def test_skill_manifest_change_is_reported_as_stable_prefix_change(tmp_path):
    builder = ContextBuilder(CtxForgeSettings())
    baseline = _snapshot(
        builder.build(task="review", cwd=tmp_path, skill_names=["alpha"]),
        tmp_path,
        snapshot_id="cache-1",
    )
    current = _snapshot(
        builder.build(task="review", cwd=tmp_path, skill_names=["beta"]),
        tmp_path,
        snapshot_id="cache-2",
    )

    report = analyze_cache(current, baseline, baseline_scope="session")

    assert report.first_changed_section == "runtime.skill_manifest"
    assert report.stable_prefix_changed is True


def test_reordered_duplicate_named_sections_keep_distinct_fingerprints(tmp_path):
    builder = ContextBuilder(CtxForgeSettings())
    first_sections = [
        ContextSection("duplicate", "dynamic", 20, "first", "one"),
        ContextSection("duplicate", "dynamic", 10, "second", "two"),
    ]
    second_sections = [
        ContextSection("duplicate", "dynamic", 20, "second", "two"),
        ContextSection("duplicate", "dynamic", 10, "first", "one"),
    ]
    baseline = _snapshot(
        builder.build(task="same", cwd=tmp_path, extra_sections=first_sections),
        tmp_path,
        snapshot_id="cache-1",
    )
    current = _snapshot(
        builder.build(task="same", cwd=tmp_path, extra_sections=second_sections),
        tmp_path,
        snapshot_id="cache-2",
    )

    report = analyze_cache(current, baseline, baseline_scope="session")
    duplicate_keys = [section.key for section in current.sections if section.name == "duplicate"]

    assert len(duplicate_keys) == 2
    assert len(set(duplicate_keys)) == 2
    assert report.status == "changed"
    duplicate_changes = [change for change in report.direct_changes if change.name == "duplicate"]
    assert len(duplicate_changes) == 2
    assert {change.change_type for change in duplicate_changes} == {"reordered"}


def test_multibyte_diff_and_provider_usage_are_handled(tmp_path):
    builder = ContextBuilder(CtxForgeSettings())
    baseline = _snapshot(builder.build(task="缓存甲", cwd=tmp_path), tmp_path, snapshot_id="cache-1")
    current = _snapshot(builder.build(task="缓存乙", cwd=tmp_path), tmp_path, snapshot_id="cache-2")

    local_report = analyze_cache(current, baseline, baseline_scope="session")
    report = attach_provider_usage(
        local_report,
        ProviderCacheUsage(prompt_tokens=100, hit_tokens=75, miss_tokens=25),
    )

    assert local_report.common_prefix_estimated_tokens is not None
    assert report.actual_cache_hit_ratio == 0.75
    assert report.provider_usage_status == "observed"


def test_provider_usage_inconsistency_is_exposed(tmp_path):
    built = ContextBuilder(CtxForgeSettings()).build(task="same", cwd=tmp_path)
    current = _snapshot(built, tmp_path, snapshot_id="cache-1")
    report = attach_provider_usage(
        analyze_cache(current, None),
        ProviderCacheUsage(prompt_tokens=100, hit_tokens=20, miss_tokens=30),
    )

    assert report.actual_cache_hit_ratio == 0.4
    assert report.provider_usage_status == "inconsistent"


def test_incompatible_snapshot_is_not_compared(tmp_path):
    built = ContextBuilder(CtxForgeSettings()).build(task="same", cwd=tmp_path)
    baseline = _snapshot(built, tmp_path, snapshot_id="cache-1")
    current = replace(_snapshot(built, tmp_path, snapshot_id="cache-2"), model="other-model")

    report = analyze_cache(current, baseline, baseline_scope="session")

    assert report.status == "incomparable"
    assert report.estimated_cache_hit_ratio is None


def test_message_envelope_change_is_not_reported_as_identical(tmp_path):
    built = ContextBuilder(CtxForgeSettings()).build(task="same", cwd=tmp_path)
    baseline = _snapshot(built, tmp_path, snapshot_id="cache-1")
    current = replace(
        _snapshot(built, tmp_path, snapshot_id="cache-2"),
        messages_sha256="f" * 64,
    )

    report = analyze_cache(current, baseline, baseline_scope="session")

    assert report.status == "incomparable"
    assert report.estimated_cache_hit_ratio is None
    assert report.error == "message_envelope_changed"


def _snapshot(built, tmp_path, *, snapshot_id):
    return create_cache_snapshot(
        built,
        cwd=tmp_path,
        session_id="session-1",
        provider="deepseek",
        base_url="https://api.deepseek.com/",
        model="deepseek-v4-flash",
        snapshot_id=snapshot_id,
    )
