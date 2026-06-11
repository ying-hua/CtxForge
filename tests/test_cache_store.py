from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ctxforge.cache import CacheStore, analyze_cache, create_cache_snapshot, mark_persistence
from ctxforge.config.settings import CtxForgeSettings
from ctxforge.context import ContextBuilder


def test_store_persists_snapshot_report_and_history(tmp_path):
    store = CacheStore(tmp_path / ".ctxforge" / "ctxforge.sqlite3")
    store.initialize()
    store.initialize()
    snapshot = _snapshot(tmp_path, "session-1", "cache-1", datetime.now(timezone.utc))
    report = mark_persistence(analyze_cache(snapshot, None), "saved")

    store.save(snapshot, report, request_id="request-1", retention=20)
    history = store.list_history(project_key=snapshot.project_key)

    assert store.count(project_key=snapshot.project_key) == 1
    assert history[0].snapshot.prompt_bytes == snapshot.prompt_bytes
    assert history[0].report.status == "no_baseline"
    assert history[0].request_id == "request-1"
    assert "prompt_bytes" not in history[0].to_dict()


def test_store_prefers_same_session_then_project_fallback(tmp_path):
    store = CacheStore(tmp_path / "cache.sqlite3")
    store.initialize()
    now = datetime.now(timezone.utc)
    same_session = _snapshot(tmp_path, "session-1", "cache-1", now)
    other_session = _snapshot(tmp_path, "session-2", "cache-2", now + timedelta(seconds=1))
    store.save(same_session, analyze_cache(same_session, None), request_id=None, retention=20)
    store.save(other_session, analyze_cache(other_session, None), request_id=None, retention=20)

    current_same = _snapshot(tmp_path, "session-1", "cache-3", now + timedelta(seconds=2))
    selected_same = store.find_baseline(current_same, allow_project_fallback=True)
    current_new = _snapshot(tmp_path, "session-3", "cache-4", now + timedelta(seconds=3))
    selected_fallback = store.find_baseline(current_new, allow_project_fallback=True)

    assert selected_same is not None
    assert selected_same.scope == "session"
    assert selected_same.snapshot.id == "cache-1"
    assert selected_fallback is not None
    assert selected_fallback.scope == "project_fallback"
    assert selected_fallback.snapshot.id == "cache-2"


def test_store_does_not_mix_models_and_respects_retention(tmp_path):
    store = CacheStore(tmp_path / "cache.sqlite3")
    store.initialize()
    now = datetime.now(timezone.utc)
    snapshots = [
        _snapshot(tmp_path, "session-1", f"cache-{index}", now + timedelta(seconds=index))
        for index in range(3)
    ]
    for snapshot in snapshots:
        store.save(snapshot, analyze_cache(snapshot, None), request_id=None, retention=2)

    different_model = replace(
        _snapshot(tmp_path, "session-1", "cache-other", now + timedelta(seconds=4)),
        model="different-model",
    )
    selected = store.find_baseline(different_model, allow_project_fallback=True)

    assert store.count(project_key=snapshots[0].project_key) == 2
    assert [entry.snapshot.id for entry in store.list_history(project_key=snapshots[0].project_key)] == [
        "cache-2",
        "cache-1",
    ]
    assert selected is None


def test_store_skips_corrupt_snapshot_and_uses_older_baseline(tmp_path):
    db_path = tmp_path / "cache.sqlite3"
    store = CacheStore(db_path)
    store.initialize()
    now = datetime.now(timezone.utc)
    valid = _snapshot(tmp_path, "session-1", "cache-valid", now)
    store.save(valid, analyze_cache(valid, None), request_id=None, retention=20)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO cache_snapshots(
                id, format_version, project_key, session_id, provider, base_url, model,
                prompt_bytes, prompt_sha256, messages_sha256, stable_prefix_sha256,
                total_estimated_tokens, sections_json, report_json, request_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "cache-corrupt",
                valid.format_version,
                valid.project_key,
                valid.session_id,
                valid.provider,
                valid.base_url,
                valid.model,
                b"broken",
                "broken",
                "broken",
                "broken",
                1,
                "{not-json",
                "{}",
                None,
                (now + timedelta(seconds=1)).isoformat(),
            ),
        )

    current = _snapshot(tmp_path, "session-1", "cache-current", now + timedelta(seconds=2))
    selected = store.find_baseline(current, allow_project_fallback=True)

    assert selected is not None
    assert selected.snapshot.id == "cache-valid"


def test_store_skips_snapshot_with_tampered_prompt_bytes(tmp_path):
    db_path = tmp_path / "cache.sqlite3"
    store = CacheStore(db_path)
    store.initialize()
    now = datetime.now(timezone.utc)
    valid = _snapshot(tmp_path, "session-1", "cache-valid", now)
    newer = _snapshot(tmp_path, "session-1", "cache-newer", now + timedelta(seconds=1))
    store.save(valid, analyze_cache(valid, None), request_id=None, retention=20)
    store.save(newer, analyze_cache(newer, valid), request_id=None, retention=20)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE cache_snapshots SET prompt_bytes = ? WHERE id = ?",
            (b"tampered", newer.id),
        )

    current = _snapshot(tmp_path, "session-1", "cache-current", now + timedelta(seconds=2))
    selected = store.find_baseline(current, allow_project_fallback=True)

    assert selected is not None
    assert selected.snapshot.id == valid.id


def test_store_rejects_report_for_different_snapshot(tmp_path):
    store = CacheStore(tmp_path / "cache.sqlite3")
    store.initialize()
    snapshot = _snapshot(tmp_path, "session-1", "cache-1", datetime.now(timezone.utc))
    other = replace(snapshot, id="cache-2")

    with pytest.raises(ValueError, match="snapshot id"):
        store.save(snapshot, analyze_cache(other, None), request_id=None, retention=20)


def _snapshot(tmp_path, session_id: str, snapshot_id: str, created_at: datetime):
    built = ContextBuilder(CtxForgeSettings()).build(task=snapshot_id, cwd=tmp_path)
    return create_cache_snapshot(
        built,
        cwd=tmp_path,
        session_id=session_id,
        provider="deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        snapshot_id=snapshot_id,
        created_at=created_at,
    )
