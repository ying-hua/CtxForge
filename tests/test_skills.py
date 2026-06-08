from __future__ import annotations

import textwrap

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.skills import SkillManager, SkillRegistry


def test_registry_discovers_valid_skills_and_reports_invalid_directories(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir,
        name="code-review",
        activation=["review", "diff"],
        instructions="Review code changes with project context.",
    )
    (skills_dir / "broken").mkdir()

    discovery = SkillRegistry(skills_dir).discover()

    assert [skill.name for skill in discovery.skills] == ["code-review"]
    assert len(discovery.errors) == 1
    assert discovery.errors[0].message == "missing skill.toml"


def test_manager_selects_explicit_and_activation_matched_skills(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, name="code-review", activation=["review"], instructions="Review instructions.")
    _write_skill(skills_dir, name="writer", activation=["draft"], instructions="Writing instructions.")

    context = SkillManager(SkillRegistry(skills_dir)).select_for_context(
        task="Please review this diff.",
        cwd=tmp_path,
        explicit_names=["writer"],
    )

    assert [skill.name for skill in context.selected_skills] == ["code-review", "writer"]
    assert context.report.status == "ok"
    selected = {item["name"]: item for item in context.report.selected}
    assert selected["writer"]["reason"] == "explicit"
    assert selected["code-review"]["matched_terms"] == ["review"]
    assert "code-review" in context.manifest_content
    assert [section.name for section in context.sections] == [
        "skill.code-review.instructions",
        "skill.writer.instructions",
    ]


def test_manager_reports_missing_explicit_skills(tmp_path):
    context = SkillManager(SkillRegistry(tmp_path / "skills")).select_for_context(
        task="No local skills.",
        cwd=tmp_path,
        explicit_names=["missing"],
    )

    assert context.report.status == "error"
    assert context.report.missing == ["missing"]
    assert context.selected_skills == []


def test_skill_manifest_is_deterministic_for_input_order(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, name="alpha", activation=["a"], instructions="Alpha instructions.")
    _write_skill(skills_dir, name="zeta", activation=["z"], instructions="Zeta instructions.")
    manager = SkillManager(SkillRegistry(skills_dir))

    first = manager.select_for_context(task="Task.", cwd=tmp_path, explicit_names=["zeta", "alpha"])
    second = manager.select_for_context(task="Task.", cwd=tmp_path, explicit_names=["alpha", "zeta"])

    assert first.manifest_content == second.manifest_content
    assert [skill.name for skill in first.selected_skills] == ["alpha", "zeta"]


def test_settings_resolves_project_skills_dir(tmp_path):
    settings = CtxForgeSettings()

    assert settings.skills.resolved_skills_dir(tmp_path) == tmp_path / "skills"


def _write_skill(skills_dir, *, name: str, activation: list[str], instructions: str):
    directory = skills_dir / name
    directory.mkdir(parents=True)
    activation_lines = ", ".join(f'"{item}"' for item in activation)
    (directory / "skill.toml").write_text(
        textwrap.dedent(
            f"""
            name = "{name}"
            version = "0.1.0"
            description = "{name} skill"
            activation = [{activation_lines}]
            allowed_runtime_tools = ["context.read", "memory.search"]
            """
        ).strip(),
        encoding="utf-8",
    )
    (directory / "SKILL.md").write_text(instructions, encoding="utf-8")
    return directory
