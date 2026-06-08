from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ctxforge.context import ContextSection
from ctxforge.skills.models import SkillActivation, SkillDefinition, SkillReport
from ctxforge.skills.registry import SkillRegistry
from ctxforge.skills.render import activation_reason, render_skill_instructions, render_skill_manifest


@dataclass(frozen=True)
class SkillContext:
    selected_skills: list[SkillDefinition]
    manifest_content: str
    sections: list[ContextSection]
    report: SkillReport


class SkillManager:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def select_for_context(
        self,
        *,
        task: str,
        cwd: Path,
        explicit_names: list[str] | None = None,
    ) -> SkillContext:
        discovery = self._registry.discover()
        skills_by_name = {skill.name: skill for skill in discovery.skills}
        explicit = sorted({name.strip() for name in explicit_names or [] if name.strip()})
        selected: dict[str, SkillDefinition] = {}
        activations: dict[str, SkillActivation] = {}
        missing: list[str] = []

        for name in explicit:
            skill = skills_by_name.get(name)
            if skill is None:
                missing.append(name)
                continue
            selected[skill.name] = skill
            activations[skill.name] = activation_reason(skill, explicit=True, matched_terms=[])

        task_text = task.lower()
        task_tokens = _task_tokens(task)
        for skill in discovery.skills:
            if skill.name in selected:
                continue
            matched_terms = _matched_activation_terms(skill, task_text=task_text, task_tokens=task_tokens)
            if not matched_terms:
                continue
            selected[skill.name] = skill
            activations[skill.name] = activation_reason(skill, explicit=False, matched_terms=matched_terms)

        selected_skills = [selected[name] for name in sorted(selected)]
        sections = [render_skill_instructions(skill) for skill in selected_skills]
        errors = [error.to_dict() for error in discovery.errors]
        status = _status(
            discovered_count=len(discovery.skills),
            selected_count=len(selected_skills),
            errors=errors,
            missing=missing,
        )
        report = SkillReport(
            status=status,
            skills_dir=str(self._registry.skills_dir),
            discovered_count=len(discovery.skills),
            selected_count=len(selected_skills),
            selected=[activations[skill.name].to_dict() for skill in selected_skills],
            missing=missing,
            errors=errors,
        )
        return SkillContext(
            selected_skills=selected_skills,
            manifest_content=render_skill_manifest(selected_skills),
            sections=sections,
            report=report,
        )


def _matched_activation_terms(skill: SkillDefinition, *, task_text: str, task_tokens: set[str]) -> list[str]:
    matched: list[str] = []
    for term in skill.manifest.activation:
        normalized = term.lower().strip()
        if not normalized:
            continue
        if " " in normalized:
            if normalized in task_text:
                matched.append(term)
            continue
        if normalized in task_tokens or normalized in task_text:
            matched.append(term)
    return sorted(set(matched))


def _task_tokens(task: str) -> set[str]:
    lowered = task.lower()
    ascii_tokens = {token for token in re.split(r"[^a-z0-9._-]+", lowered) if token}
    cjk_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}", lowered))
    return ascii_tokens | cjk_tokens


def _status(
    *,
    discovered_count: int,
    selected_count: int,
    errors: list[dict[str, object]],
    missing: list[str],
) -> str:
    if errors or missing:
        return "error"
    if discovered_count == 0 or selected_count == 0:
        return "empty"
    return "ok"
