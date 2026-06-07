from __future__ import annotations

from rich.console import Console
from typer.testing import CliRunner

from ctxforge.cli import app as cli_app
from ctxforge.cli.app import app


def test_memory_cli_add_search_and_run(tmp_path):
    runner = CliRunner()
    cli_app.console = Console(width=200, color_system=None)

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

    run_result = runner.invoke(app, ["run", "How should Phase 2 memory work?", "-C", str(tmp_path)])
    assert run_result.exit_code == 0, run_result.output
    assert "Phase 2 Runtime Report" in run_result.output
    assert "memory_status" in run_result.output
