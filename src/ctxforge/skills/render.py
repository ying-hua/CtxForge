from __future__ import annotations

from ctxforge.context import ContextSection
from ctxforge.skills.models import SkillActivation, SkillDefinition


def render_skill_manifest(skills: list[SkillDefinition]) -> str:
    if not skills:
        return "No selected skills."
    return "\n".join(skill.manifest.stable_summary() for skill in sorted(skills, key=lambda skill: skill.name))


def render_skill_instructions(skill: SkillDefinition) -> ContextSection:
    return ContextSection(
        name=f"skill.{skill.name}.instructions",
        stability="semi_stable",
        priority=45,
        source=f"skill:{skill.name}",
        content=skill.instructions,
    )


def activation_reason(skill: SkillDefinition, *, explicit: bool, matched_terms: list[str]) -> SkillActivation:
    if explicit:
        return SkillActivation(
            name=skill.name,
            reason="explicit",
            explicit=True,
            matched_terms=[],
        )
    return SkillActivation(
        name=skill.name,
        reason="activation_match",
        explicit=False,
        matched_terms=matched_terms,
    )
