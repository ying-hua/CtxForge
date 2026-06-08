from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only used on Python < 3.11
    import tomli as tomllib

from pydantic import BaseModel, Field, field_validator

from ctxforge.config.paths import default_memory_db_path, default_skills_dir, project_config_path, user_config_path


class DeepSeekSettings(BaseModel):
    api_key: Optional[str] = None
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout_seconds: float = Field(default=60.0, gt=0)


class ContextSettings(BaseModel):
    max_tokens: int = Field(default=16000, gt=0)
    reserved_output_tokens: int = Field(default=2048, ge=0)


class MemorySettings(BaseModel):
    db_path: Optional[Path] = None

    def resolved_db_path(self, project_dir: Path | None = None) -> Path:
        return self.db_path or default_memory_db_path(project_dir)


class SkillsSettings(BaseModel):
    skills_dir: Optional[Path] = None

    def resolved_skills_dir(self, project_dir: Path | None = None) -> Path:
        return self.skills_dir or default_skills_dir(project_dir)


class LoggingSettings(BaseModel):
    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        normalized = value.upper()
        valid = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
        if normalized not in valid:
            raise ValueError(f"Invalid log level: {value}")
        return normalized


class CtxForgeSettings(BaseModel):
    deepseek: DeepSeekSettings = Field(default_factory=DeepSeekSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    skills: SkillsSettings = Field(default_factory=SkillsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)


def load_settings(
    project_dir: Path | None = None,
    config_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
    include_user_config: bool = True,
) -> CtxForgeSettings:
    """Load settings with default < user < project < env < cli precedence."""
    project_dir = project_dir or Path.cwd()

    merged: dict[str, Any] = {}
    if include_user_config:
        merged = _deep_merge(merged, _read_toml(user_config_path()))

    project_config = config_path or project_config_path(project_dir)
    merged = _deep_merge(merged, _read_toml(project_config))

    merged = _deep_merge(merged, _env_overrides())

    if cli_overrides:
        merged = _deep_merge(merged, _remove_none(cli_overrides))

    return CtxForgeSettings.model_validate(merged)


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        return {}
    return data


def _env_overrides() -> dict[str, Any]:
    data: dict[str, Any] = {}

    if os.getenv("DEEPSEEK_API_KEY"):
        data = _deep_merge(data, {"deepseek": {"api_key": os.environ["DEEPSEEK_API_KEY"]}})
    if os.getenv("CTXFORGE_DEEPSEEK_MODEL"):
        data = _deep_merge(data, {"deepseek": {"model": os.environ["CTXFORGE_DEEPSEEK_MODEL"]}})
    if os.getenv("CTXFORGE_DEEPSEEK_BASE_URL"):
        data = _deep_merge(data, {"deepseek": {"base_url": os.environ["CTXFORGE_DEEPSEEK_BASE_URL"]}})
    if os.getenv("CTXFORGE_LOG_LEVEL"):
        data = _deep_merge(data, {"logging": {"level": os.environ["CTXFORGE_LOG_LEVEL"]}})

    return data


def _deep_merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _remove_none(data: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            nested = _remove_none(value)
            if nested:
                cleaned[key] = nested
        elif value is not None:
            cleaned[key] = value
    return cleaned
