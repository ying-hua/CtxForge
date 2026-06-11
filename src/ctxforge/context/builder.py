from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ctxforge.config.settings import CtxForgeSettings
from ctxforge.context.budget import estimate_tokens
from ctxforge.context.models import BuiltContext, ContextReport, ContextSection, SectionReport
from ctxforge.context.render import render_prompt, render_prompt_parts, render_section, sort_sections
from ctxforge.context.snapshot import build_prefix_snapshot


class ContextBuilder:
    def __init__(self, settings: CtxForgeSettings) -> None:
        self._settings = settings

    def build(
        self,
        *,
        task: str,
        cwd: Path,
        skill_names: list[str] | None = None,
        skill_manifest_content: str | None = None,
        extra_sections: list[ContextSection] | None = None,
        include_memory_placeholders: bool = True,
    ) -> BuiltContext:
        max_tokens = self._settings.context.max_tokens
        reserved_output_tokens = self._settings.context.reserved_output_tokens
        input_budget = max(0, max_tokens - reserved_output_tokens)

        sections = self._default_sections(
            task=task,
            cwd=cwd,
            skill_names=skill_names or [],
            skill_manifest_content=skill_manifest_content,
            include_memory_placeholders=include_memory_placeholders,
        )
        sections.extend(extra_sections or [])
        prepared = [self._with_estimate(section) for section in sort_sections(sections)]
        included, included_reports, dropped_reports, truncated_reports, overflow = self._fit_budget(
            prepared,
            input_budget,
        )

        rendered_prompt, _ = render_prompt_parts(included)
        stable_sections = [section for section in included if section.stability == "stable"]
        stable_prefix = render_prompt(stable_sections)
        snapshot = build_prefix_snapshot(stable_prefix, included)
        messages = [
            {"role": "system", "content": stable_prefix},
            {
                "role": "user",
                "content": render_prompt([section for section in included if section.stability != "stable"]),
            },
        ]

        report = ContextReport(
            status="overflow" if overflow else "ok",
            max_tokens=max_tokens,
            reserved_output_tokens=reserved_output_tokens,
            input_budget=input_budget,
            total_estimated_tokens=sum(section.token_estimate for section in included),
            stable_prefix_tokens=sum(section.token_estimate for section in included if section.stability == "stable"),
            semi_stable_tokens=sum(
                section.token_estimate for section in included if section.stability == "semi_stable"
            ),
            dynamic_tokens=sum(section.token_estimate for section in included if section.stability == "dynamic"),
            section_count=len(included),
            included_sections=included_reports,
            dropped_sections=dropped_reports,
            truncated_sections=truncated_reports,
            stable_prefix_bytes=len(snapshot.stable_prefix_bytes),
            stable_prefix_sha256=snapshot.stable_prefix_sha256,
            overflow=overflow,
        )

        return BuiltContext(
            messages=messages,
            sections=included,
            rendered_prompt=rendered_prompt,
            stable_prefix=stable_prefix,
            report=report,
            snapshot=snapshot,
        )

    def _default_sections(
        self,
        *,
        task: str,
        cwd: Path,
        skill_names: list[str],
        skill_manifest_content: str | None,
        include_memory_placeholders: bool,
    ) -> list[ContextSection]:
        skill_manifest = skill_manifest_content
        if skill_manifest is None:
            skill_manifest = "\n".join(f"- {name}" for name in sorted(skill_names)) or "No selected skills."
        sections = [
            ContextSection(
                name="runtime.system_prompt",
                stability="stable",
                priority=100,
                source="builtin.runtime",
                required=True,
                content=(
                    "You are CtxForge, a DeepSeek-native Memory and Context Engineering runtime. "
                    "Build deterministic context, preserve stable prefixes, and report budget usage."
                ),
            ),
            ContextSection(
                name="runtime.context_protocol",
                stability="stable",
                priority=90,
                source="builtin.runtime",
                required=True,
                content=(
                    "Render context sections in deterministic order. Stable prefix content must not include "
                    "timestamps, random ids, session ids, temporary paths, or current tasks. Dynamic material "
                    "belongs in the suffix."
                ),
            ),
            ContextSection(
                name="runtime.skill_manifest",
                stability="stable",
                priority=70,
                source="builtin.skills",
                content=skill_manifest,
            ),
            ContextSection(
                name="project.profile",
                stability="semi_stable",
                priority=50,
                source="project.placeholder",
                content=f"Project directory name: {cwd.name}",
            ),
        ]
        if include_memory_placeholders:
            sections.extend(
                [
                    ContextSection(
                        name="memory.retrieved",
                        stability="dynamic",
                        priority=40,
                        source="memory.placeholder",
                        content="No retrieved memories in Phase 1.",
                    ),
                    ContextSection(
                        name="session.working_memory",
                        stability="dynamic",
                        priority=30,
                        source="memory.placeholder",
                        content="No working memory in Phase 1.",
                    ),
                ]
            )
        sections.append(
            ContextSection(
                name="request.task",
                stability="dynamic",
                priority=100,
                source="request",
                required=True,
                content=task,
            ),
        )
        return sections

    def _with_estimate(self, section: ContextSection) -> ContextSection:
        return replace(section, content=section.content, token_estimate=estimate_tokens(render_section(section)))

    def _fit_budget(
        self,
        sections: list[ContextSection],
        input_budget: int,
    ) -> tuple[list[ContextSection], list[SectionReport], list[SectionReport], list[SectionReport], bool]:
        included: list[ContextSection] = []
        included_reports: list[SectionReport] = []
        dropped_reports: list[SectionReport] = []
        truncated_reports: list[SectionReport] = []
        used_tokens = 0
        overflow = False

        for section in sections:
            remaining = input_budget - used_tokens
            if section.token_estimate <= remaining:
                included.append(section)
                included_reports.append(self._report(section, included=True))
                used_tokens += section.token_estimate
                continue

            if section.required:
                if remaining > 0:
                    truncated = self._truncate(section, remaining)
                    included.append(truncated)
                    report = self._report(truncated, included=True, truncated=True, reason="required_truncated")
                    included_reports.append(report)
                    truncated_reports.append(report)
                    used_tokens += truncated.token_estimate
                else:
                    included.append(section)
                    report = self._report(section, included=True, reason="required_over_budget")
                    included_reports.append(report)
                    used_tokens += section.token_estimate
                overflow = True
                continue

            if section.stability == "dynamic" and remaining > 0:
                truncated = self._truncate(section, remaining)
                if truncated.content:
                    included.append(truncated)
                    report = self._report(truncated, included=True, truncated=True, reason="budget_truncated")
                    included_reports.append(report)
                    truncated_reports.append(report)
                    used_tokens += truncated.token_estimate
                    continue

            dropped_reports.append(self._report(section, included=False, reason="budget_exceeded"))

        return included, included_reports, dropped_reports, truncated_reports, overflow

    def _truncate(self, section: ContextSection, token_budget: int) -> ContextSection:
        if token_budget <= 0:
            truncated = replace(section, content="")
            return replace(truncated, token_estimate=estimate_tokens(render_section(truncated)))

        marker = "\n[ctxforge: truncated]"
        empty = replace(section, content="")
        empty_estimate = estimate_tokens(render_section(empty))
        if empty_estimate > token_budget:
            return replace(empty, token_estimate=empty_estimate)

        best_content = ""
        low = 0
        high = len(section.content)
        while low <= high:
            midpoint = (low + high) // 2
            candidate_content = section.content[:midpoint].rstrip()
            if midpoint < len(section.content):
                candidate_content = f"{candidate_content}{marker}" if candidate_content else marker.strip()
            candidate = replace(section, content=candidate_content)
            candidate_estimate = estimate_tokens(render_section(candidate))
            if candidate_estimate <= token_budget:
                best_content = candidate_content
                low = midpoint + 1
            else:
                high = midpoint - 1

        truncated = replace(section, content=best_content)
        return replace(truncated, token_estimate=estimate_tokens(render_section(truncated)))

    def _report(
        self,
        section: ContextSection,
        *,
        included: bool,
        truncated: bool = False,
        reason: str | None = None,
    ) -> SectionReport:
        return SectionReport(
            name=section.name,
            stability=section.stability,
            priority=section.priority,
            source=section.source,
            token_estimate=section.token_estimate,
            included=included,
            required=section.required,
            truncated=truncated,
            reason=reason,
        )
