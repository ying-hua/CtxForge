from __future__ import annotations

from pathlib import Path


def default_data_dir() -> Path:
    return Path.home() / ".ctxforge"


def user_config_path() -> Path:
    return default_data_dir() / "config.toml"


def project_config_path(project_dir: Path | None = None) -> Path:
    return (project_dir or Path.cwd()) / "ctxforge.toml"


def default_memory_db_path(project_dir: Path | None = None) -> Path:
    return (project_dir or Path.cwd()) / ".ctxforge" / "ctxforge.sqlite3"
