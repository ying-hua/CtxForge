from __future__ import annotations

from rich.console import Console
from typer.testing import CliRunner

from ctxforge.cli import app as cli_app
from ctxforge.cli.app import app


def test_memory_cli_add_search_and_run(tmp_path):
    runner = CliRunner()
    cli_app.console = Console(width=200, color_system=None)
    _write_skill(tmp_path, name="code-review", activation=["review"], instructions="Use project-aware review steps.")

    add_result = runner.invoke(
        app,
        [
            "memory",
            "add",
            "Use sqlite3 for Phase 2 memory.",
            "-C",
            str(tmp_path),
            "--kind",
            "decision",
            "--scope",
            "project",
            "--source",
            "test",
        ],
    )
    assert add_result.exit_code == 0, add_result.output
    assert "Added memory" in add_result.output

    search_result = runner.invoke(app, ["memory", "search", "sqlite memory", "-C", str(tmp_path)])
    assert search_result.exit_code == 0, search_result.output
    assert "Use sqlite3 for Phase 2 memory." in search_result.output

    run_result = runner.invoke(app, ["run", "Please review Phase 2 memory work.", "-C", str(tmp_path), "--no-model"])
    assert run_result.exit_code == 0, run_result.output
    assert "Phase 4 Runtime Report" in run_result.output
    assert "memory_status" in run_result.output
    assert "skill_status" in run_result.output
    assert "dry_run_no_model" in run_result.output
    assert "code-review" in run_result.output


def test_skill_cli_list_inspect_and_install(tmp_path):
    runner = CliRunner()
    cli_app.console = Console(width=200, color_system=None)
    source_dir = _write_external_skill(tmp_path, name="writer", activation=["draft"], instructions="Draft clearly.")

    install_result = runner.invoke(app, ["skill", "install", str(source_dir), "-C", str(tmp_path)])
    assert install_result.exit_code == 0, install_result.output
    assert "Installed skill writer" in install_result.output

    list_result = runner.invoke(app, ["skill", "list", "-C", str(tmp_path)])
    assert list_result.exit_code == 0, list_result.output
    assert "writer" in list_result.output

    inspect_result = runner.invoke(app, ["skill", "inspect", "writer", "-C", str(tmp_path)])
    assert inspect_result.exit_code == 0, inspect_result.output
    assert "Draft clearly." in inspect_result.output


def _write_skill(tmp_path, *, name: str, activation: list[str], instructions: str):
    return _write_skill_directory(tmp_path / "skills", name=name, activation=activation, instructions=instructions)


def _write_external_skill(tmp_path, *, name: str, activation: list[str], instructions: str):
    return _write_skill_directory(tmp_path / "external-skills", name=name, activation=activation, instructions=instructions)


def _write_skill_directory(parent, *, name: str, activation: list[str], instructions: str):
    directory = parent / name
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
    return directory
