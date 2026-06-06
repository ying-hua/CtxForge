from __future__ import annotations

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.context import ContextBuilder, ContextSection


def test_context_build_is_deterministic(tmp_path):
    builder = ContextBuilder(CtxForgeSettings())

    first = builder.build(task="Explain Phase 1.", cwd=tmp_path, skill_names=["zeta", "alpha"])
    second = builder.build(task="Explain Phase 1.", cwd=tmp_path, skill_names=["alpha", "zeta"])

    assert first.rendered_prompt == second.rendered_prompt
    assert first.report.stable_prefix_sha256 == second.report.stable_prefix_sha256
    assert first.snapshot.section_hashes == second.snapshot.section_hashes


def test_dynamic_task_does_not_change_stable_prefix(tmp_path):
    builder = ContextBuilder(CtxForgeSettings())

    first = builder.build(task="Task one.", cwd=tmp_path)
    second = builder.build(task="Task two with different content.", cwd=tmp_path)

    assert first.stable_prefix == second.stable_prefix
    assert first.report.stable_prefix_sha256 == second.report.stable_prefix_sha256
    assert first.rendered_prompt != second.rendered_prompt


def test_sections_sort_by_stability_priority_and_name(tmp_path):
    sections = [
        ContextSection("dynamic.low", "dynamic", 1, "d", "test"),
        ContextSection("stable.low", "stable", 1, "s", "test"),
        ContextSection("stable.high.b", "stable", 10, "b", "test"),
        ContextSection("stable.high.a", "stable", 10, "a", "test"),
    ]

    built = ContextBuilder(CtxForgeSettings()).build(
        task="hello",
        cwd=tmp_path,
        extra_sections=sections,
    )

    names = [section.name for section in built.sections]
    assert names.index("stable.high.a") < names.index("stable.high.b")
    assert names.index("stable.high.b") < names.index("stable.low")
    assert names.index("stable.low") < names.index("dynamic.low")


def test_budget_truncates_low_priority_dynamic_sections(tmp_path):
    settings = CtxForgeSettings()
    settings = settings.model_copy(
        update={"context": settings.context.model_copy(update={"max_tokens": 500, "reserved_output_tokens": 100})}
    )
    low_priority = ContextSection(
        "dynamic.large_optional",
        "dynamic",
        1,
        "x" * 4000,
        "test",
    )

    built = ContextBuilder(settings).build(
        task="short task",
        cwd=tmp_path,
        extra_sections=[low_priority],
    )

    truncated_names = {section.name for section in built.report.truncated_sections}
    assert "dynamic.large_optional" in truncated_names
    assert built.report.total_estimated_tokens <= built.report.input_budget


def test_budget_drops_low_priority_semi_stable_sections(tmp_path):
    settings = CtxForgeSettings()
    settings = settings.model_copy(
        update={"context": settings.context.model_copy(update={"max_tokens": 440, "reserved_output_tokens": 100})}
    )
    low_priority = ContextSection(
        "project.large_optional",
        "semi_stable",
        1,
        "x" * 4000,
        "test",
    )

    built = ContextBuilder(settings).build(
        task="short task",
        cwd=tmp_path,
        extra_sections=[low_priority],
    )

    dropped_names = {section.name for section in built.report.dropped_sections}
    assert "project.large_optional" in dropped_names
