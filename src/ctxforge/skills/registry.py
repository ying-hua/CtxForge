from __future__ import annotations

import shutil
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only used on Python < 3.11
    import tomli as tomllib

from pydantic import ValidationError

from ctxforge.skills.models import SkillDefinition, SkillDiscovery, SkillLoadError, SkillManifest


class SkillRegistry:
    def __init__(self, skills_dir: Path) -> None:
        self.skills_dir = skills_dir

    def discover(self) -> SkillDiscovery:
        if not self.skills_dir.exists():
            return SkillDiscovery(skills=[])
        if not self.skills_dir.is_dir():
            return SkillDiscovery(
                skills=[],
                errors=[
                    SkillLoadError(
                        path=str(self.skills_dir),
                        message="skills path is not a directory",
                    )
                ],
            )

        skills: list[SkillDefinition] = []
        errors: list[SkillLoadError] = []
        seen: set[str] = set()
        for directory in sorted((path for path in self.skills_dir.iterdir() if path.is_dir()), key=lambda path: path.name):
            loaded, error = self.load_directory(directory)
            if error is not None:
                errors.append(error)
                continue
            if loaded is None:
                continue
            if loaded.name in seen:
                errors.append(
                    SkillLoadError(
                        path=str(directory),
                        name=loaded.name,
                        message="duplicate skill name",
                    )
                )
                continue
            seen.add(loaded.name)
            skills.append(loaded)

        return SkillDiscovery(skills=sorted(skills, key=lambda skill: skill.name), errors=errors)

    def load_directory(self, directory: Path) -> tuple[SkillDefinition | None, SkillLoadError | None]:
        manifest_path = directory / "skill.toml"
        instructions_path = directory / "SKILL.md"
        if not manifest_path.exists():
            return None, SkillLoadError(path=str(directory), message="missing skill.toml")
        if not instructions_path.exists():
            return None, SkillLoadError(path=str(directory), message="missing SKILL.md")

        try:
            with manifest_path.open("rb") as handle:
                data = tomllib.load(handle)
            manifest = SkillManifest.model_validate(data)
            instructions = instructions_path.read_text(encoding="utf-8").strip()
            if not instructions:
                return None, SkillLoadError(
                    path=str(instructions_path),
                    name=manifest.name,
                    message="SKILL.md is empty",
                )
        except (OSError, tomllib.TOMLDecodeError, ValidationError, ValueError) as exc:
            return None, SkillLoadError(path=str(directory), message=str(exc))

        return SkillDefinition(manifest=manifest, directory=directory, instructions=instructions), None

    def install(self, source_dir: Path, *, force: bool = False) -> SkillDefinition:
        loaded, error = self.load_directory(source_dir)
        if error is not None or loaded is None:
            message = error.message if error is not None else "invalid skill directory"
            raise ValueError(message)

        target = self.skills_dir / loaded.name
        if target.exists():
            if not force:
                raise FileExistsError(f"skill already exists: {loaded.name}")
            shutil.rmtree(target)

        self.skills_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target)
        installed, installed_error = self.load_directory(target)
        if installed_error is not None or installed is None:
            message = installed_error.message if installed_error is not None else "installed skill is invalid"
            raise ValueError(message)
        return installed
