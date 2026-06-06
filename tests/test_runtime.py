from __future__ import annotations

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.runtime.agent import RuntimeRequest, run_phase0


def test_phase0_runtime_returns_reports(tmp_path):
    result = run_phase0(
        RuntimeRequest(task="hello", cwd=tmp_path, skill_names=["example"]),
        settings=CtxForgeSettings(),
    )

    assert result.answer.startswith("Phase 1 context builder is ready")
    assert result.session_id.startswith("session-")
    assert result.context_report["selected_skills"] == ["example"]
    assert result.context_report["status"] == "ok"
    assert result.context_report["stable_prefix_sha256"]
    assert result.cache_report["status"] == "snapshot_only_in_phase_1"
    assert result.memory_report["db_path"].endswith("ctxforge.sqlite3")
