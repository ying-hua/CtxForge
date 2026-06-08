from __future__ import annotations

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.memory import MemoryStore
from ctxforge.runtime.agent import RuntimeRequest, run_phase0, run_phase2, run_phase3


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


def test_phase2_runtime_returns_real_memory_report(tmp_path):
    settings = CtxForgeSettings()
    store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    store.initialize()
    store.add_record(
        content="Use sqlite3 for early memory phases.",
        source="test",
        scope="project",
        kind="decision",
        project_dir=str(tmp_path),
    )

    result = run_phase2(
        RuntimeRequest(task="What should memory use?", cwd=tmp_path, session_id="session-1"),
        settings=settings,
    )

    assert result.answer.startswith("Phase 2 memory layer is ready")
    assert result.session_id == "session-1"
    assert result.cache_report["status"] == "snapshot_only_in_phase_2"
    assert result.memory_report["status"] == "ok"
    assert result.memory_report["retrieved_count"] == 1
    included_names = {section["name"] for section in result.context_report["included_sections"]}
    assert "memory.retrieved" in included_names
    assert "session.working_memory" in included_names


def test_phase2_memory_changes_do_not_change_stable_prefix(tmp_path):
    settings = CtxForgeSettings()
    first = run_phase2(RuntimeRequest(task="sqlite memory", cwd=tmp_path, session_id="session-1"), settings=settings)

    store = MemoryStore(settings.memory.resolved_db_path(tmp_path))
    store.initialize()
    store.add_record(
        content="sqlite memory should stay in dynamic suffix.",
        source="test",
        scope="project",
        kind="fact",
        project_dir=str(tmp_path),
    )
    second = run_phase2(RuntimeRequest(task="sqlite memory", cwd=tmp_path, session_id="session-1"), settings=settings)

    assert first.context_report["stable_prefix_sha256"] == second.context_report["stable_prefix_sha256"]
    assert second.memory_report["retrieved_count"] == 1


def test_phase3_runtime_activates_skills_and_injects_sections(tmp_path):
    _write_skill(tmp_path, name="code-review", activation=["review"], instructions="Use project-aware review steps.")

    result = run_phase3(
        RuntimeRequest(task="Please review this diff.", cwd=tmp_path, session_id="session-1"),
        settings=CtxForgeSettings(),
    )

    assert result.answer.startswith("Phase 3 skill layer is ready")
    assert result.cache_report["status"] == "snapshot_only_in_phase_3"
    assert result.skill_report["status"] == "ok"
    assert result.context_report["selected_skills"] == ["code-review"]
    included_names = {section["name"] for section in result.context_report["included_sections"]}
    assert "skill.code-review.instructions" in included_names
    assert "memory.retrieved" in included_names


def test_phase3_dynamic_task_changes_do_not_change_stable_prefix_when_skills_match(tmp_path):
    _write_skill(tmp_path, name="code-review", activation=["review"], instructions="Use project-aware review steps.")
    settings = CtxForgeSettings()

    first = run_phase3(
        RuntimeRequest(task="Please review this diff.", cwd=tmp_path, session_id="session-1"),
        settings=settings,
    )
    second = run_phase3(
        RuntimeRequest(task="Please review another change.", cwd=tmp_path, session_id="session-1"),
        settings=settings,
    )

    assert first.context_report["stable_prefix_sha256"] == second.context_report["stable_prefix_sha256"]


def _write_skill(tmp_path, *, name: str, activation: list[str], instructions: str):
    directory = tmp_path / "skills" / name
    directory.mkdir(parents=True)
    activation_lines = ", ".join(f'"{item}"' for item in activation)
    (directory / "skill.toml").write_text(
        "\n".join(
            [
                f'name = "{name}"',
                'version = "0.1.0"',
                f'description = "{name} skill"',
                f"activation = [{activation_lines}]",
                'allowed_runtime_tools = ["context.read"]',
            ]
        ),
        encoding="utf-8",
    )
    (directory / "SKILL.md").write_text(instructions, encoding="utf-8")
