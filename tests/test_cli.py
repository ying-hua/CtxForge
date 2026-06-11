from __future__ import annotations

import json

from rich.console import Console
from typer.testing import CliRunner

from ctxforge.cache import CacheStore, analyze_cache, create_cache_snapshot, mark_persistence
from ctxforge.cli import app as cli_app
from ctxforge.cli.app import app
from ctxforge.config.settings import CtxForgeSettings
from ctxforge.context import ContextBuilder


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
    assert "Phase 5 Runtime Report" in run_result.output
    assert "memory_status" in run_result.output
    assert "skill_status" in run_result.output
    assert "cache_status" in run_result.output
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


def test_inspect_cache_table_and_json_do_not_expose_prompt(tmp_path):
    runner = CliRunner()
    cli_app.console = Console(width=240, color_system=None)
    settings = CtxForgeSettings()
    built = ContextBuilder(settings).build(task="private prompt content", cwd=tmp_path)
    snapshot = create_cache_snapshot(
        built,
        cwd=tmp_path,
        session_id="session-1",
        provider="deepseek",
        base_url=settings.deepseek.base_url,
        model=settings.deepseek.model,
        snapshot_id="cache-1",
    )
    store = CacheStore(settings.memory.resolved_db_path(tmp_path))
    store.initialize()
    store.save(
        snapshot,
        mark_persistence(analyze_cache(snapshot, None), "saved"),
        request_id="request-1",
        retention=20,
    )

    table_result = runner.invoke(app, ["inspect", "cache", "-C", str(tmp_path)])
    json_result = runner.invoke(app, ["inspect", "cache", "-C", str(tmp_path), "--json"])

    assert table_result.exit_code == 0, table_result.output
    assert "Cache Reports" in table_result.output
    assert "session-1" in table_result.output
    assert "private prompt content" not in table_result.output
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload[0]["session_id"] == "session-1"
    assert "private prompt content" not in json_result.output


def test_tui_cli_launches_textual_app_with_options(tmp_path, monkeypatch):
    runner = CliRunner()
    launched: dict[str, object] = {}

    def fake_init(self, **kwargs):
        launched.update(kwargs)

    def fake_run(self):
        launched["ran"] = True

    monkeypatch.setattr("ctxforge.tui.app.CtxForgeTuiApp.__init__", fake_init)
    monkeypatch.setattr("ctxforge.tui.app.CtxForgeTuiApp.run", fake_run)

    result = runner.invoke(
        app,
        [
            "tui",
            "-C",
            str(tmp_path),
            "--session-id",
            "session-tui",
            "--model",
            "deepseek-v4-flash",
            "--max-tokens",
            "4096",
            "--max-output-tokens",
            "512",
            "--skill",
            "review",
            "--no-model",
        ],
    )

    assert result.exit_code == 0, result.output
    assert launched["project_dir"] == tmp_path.resolve()
    assert launched["session_id"] == "session-tui"
    assert launched["model"] == "deepseek-v4-flash"
    assert launched["max_tokens"] == 4096
    assert launched["max_output_tokens"] == 512
    assert launched["skill_names"] == ["review"]
    assert launched["execute_model"] is False
    assert launched["ran"] is True


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
