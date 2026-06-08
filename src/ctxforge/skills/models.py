from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SkillStatus = Literal["ok", "empty", "error"]


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str
    description: str
    activation: list[str] = Field(default_factory=list)
    allowed_runtime_tools: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("skill name is required")
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
        if any(char not in allowed for char in normalized):
            raise ValueError("skill name may only contain letters, numbers, dot, underscore, and dash")
        return normalized

    @field_validator("version", "description")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field is required")
        return normalized

    @field_validator("activation", "allowed_runtime_tools")
    @classmethod
    def normalize_string_list(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip() for item in value if item.strip()})
        return normalized

    def stable_summary(self) -> str:
        activation = ", ".join(self.activation) if self.activation else "none"
        tools = ", ".join(self.allowed_runtime_tools) if self.allowed_runtime_tools else "none"
        return (
            f"- {self.name} v{self.version}: {self.description} "
            f"(activation: {activation}; tools: {tools})"
        )


@dataclass(frozen=True)
class SkillDefinition:
    manifest: SkillManifest
    directory: Path
    instructions: str

    @property
    def name(self) -> str:
        return self.manifest.name


@dataclass(frozen=True)
class SkillLoadError:
    path: str
    message: str
    name: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "path": self.path,
            "message": self.message,
        }
        if self.name is not None:
            data["name"] = self.name
        return data


@dataclass(frozen=True)
class SkillDiscovery:
    skills: list[SkillDefinition]
    errors: list[SkillLoadError] = field(default_factory=list)


@dataclass(frozen=True)
class SkillActivation:
    name: str
    reason: str
    explicit: bool
    matched_terms: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "reason": self.reason,
            "explicit": self.explicit,
            "matched_terms": self.matched_terms,
        }


@dataclass(frozen=True)
class SkillReport:
    status: SkillStatus
    skills_dir: str
    discovered_count: int
    selected_count: int
    selected: list[dict[str, object]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "skills_dir": self.skills_dir,
            "discovered_count": self.discovered_count,
            "selected_count": self.selected_count,
            "selected": self.selected,
            "missing": self.missing,
            "errors": self.errors,
        }
