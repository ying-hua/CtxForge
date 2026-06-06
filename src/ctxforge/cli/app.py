from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ctxforge.config.settings import load_settings
from ctxforge.context import ContextBuilder
from ctxforge.logging import configure_logging
from ctxforge.runtime.agent import RuntimeRequest, run_phase1

app = typer.Typer(
    name="ctxforge",
    help="DeepSeek-native Memory and Context Engineering runtime.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Inspect and manage CtxForge configuration.")
inspect_app = typer.Typer(help="Inspect CtxForge runtime artifacts.")
console = Console()


@app.callback()
def _main(
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        help="Override log level, for example DEBUG, INFO, WARNING.",
    ),
) -> None:
    settings = load_settings(cli_overrides={"logging": {"level": log_level}} if log_level else None)
    configure_logging(settings.logging.level)


@app.command()
def run(
    task: str = typer.Argument(..., help="Task to run through the CtxForge runtime."),
    project_dir: Path = typer.Option(
        Path.cwd(),
        "--project-dir",
        "-C",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Project directory used for config discovery.",
    ),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Reuse an existing session id."),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="Override context token budget."),
) -> None:
    """Run a task through the Phase 1 context-aware placeholder runtime."""
    settings = load_settings(
        project_dir=project_dir,
        cli_overrides={"context": {"max_tokens": max_tokens}} if max_tokens else None,
    )
    configure_logging(settings.logging.level)

    result = run_phase1(
        RuntimeRequest(
            task=task,
            cwd=project_dir,
            session_id=session_id,
            skill_names=[],
            max_tokens=max_tokens,
        ),
        settings=settings,
    )

    console.print(Panel(result.answer, title="CtxForge", border_style="cyan"))

    table = Table(title="Phase 1 Runtime Report")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("session_id", result.session_id)
    table.add_row("project_dir", str(project_dir))
    table.add_row("model", settings.deepseek.model)
    table.add_row("max_tokens", str(settings.context.max_tokens))
    table.add_row("input_budget", str(result.context_report["input_budget"]))
    table.add_row("context_tokens", str(result.context_report["total_estimated_tokens"]))
    table.add_row("stable_prefix_sha256", str(result.context_report["stable_prefix_sha256"]))
    table.add_row("memory_db_path", str(settings.memory.resolved_db_path(project_dir)))
    console.print(table)


@inspect_app.command("context")
def inspect_context(
    task: str = typer.Argument(..., help="Task to build context for."),
    project_dir: Path = typer.Option(
        Path.cwd(),
        "--project-dir",
        "-C",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Project directory used for config discovery.",
    ),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens", help="Override context token budget."),
    show_prompt: bool = typer.Option(False, "--show-prompt", help="Print the rendered prompt after the report."),
) -> None:
    """Inspect the deterministic context built for a task."""
    settings = load_settings(
        project_dir=project_dir,
        cli_overrides={"context": {"max_tokens": max_tokens}} if max_tokens else None,
    )
    configure_logging(settings.logging.level)
    built = ContextBuilder(settings).build(task=task, cwd=project_dir)

    summary = Table(title="Context Report")
    summary.add_column("Field", style="bold")
    summary.add_column("Value")
    summary.add_row("status", built.report.status)
    summary.add_row("input_budget", str(built.report.input_budget))
    summary.add_row("total_estimated_tokens", str(built.report.total_estimated_tokens))
    summary.add_row("stable_prefix_tokens", str(built.report.stable_prefix_tokens))
    summary.add_row("stable_prefix_bytes", str(built.report.stable_prefix_bytes))
    summary.add_row("stable_prefix_sha256", built.report.stable_prefix_sha256)
    console.print(summary)

    sections = Table(title="Context Sections")
    sections.add_column("Name", style="bold")
    sections.add_column("Stability")
    sections.add_column("Priority", justify="right")
    sections.add_column("Tokens", justify="right")
    sections.add_column("Source")
    sections.add_column("Flags")
    for section in built.report.included_sections:
        flags = []
        if section.required:
            flags.append("required")
        if section.truncated:
            flags.append("truncated")
        sections.add_row(
            section.name,
            section.stability,
            str(section.priority),
            str(section.token_estimate),
            section.source,
            ", ".join(flags),
        )
    console.print(sections)

    if built.report.dropped_sections:
        dropped = Table(title="Dropped Sections")
        dropped.add_column("Name", style="bold")
        dropped.add_column("Reason")
        dropped.add_column("Tokens", justify="right")
        for section in built.report.dropped_sections:
            dropped.add_row(section.name, section.reason or "", str(section.token_estimate))
        console.print(dropped)

    if show_prompt:
        console.print(Panel(built.rendered_prompt, title="Rendered Prompt", border_style="cyan"))


@config_app.command("show")
def config_show(
    project_dir: Path = typer.Option(
        Path.cwd(),
        "--project-dir",
        "-C",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Project directory used for config discovery.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON instead of a table."),
) -> None:
    """Show the effective configuration."""
    settings = load_settings(project_dir=project_dir)

    if as_json:
        console.print(json.dumps(settings.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return

    table = Table(title="Effective CtxForge Configuration")
    table.add_column("Key", style="bold")
    table.add_column("Value")

    for key, value in _flatten(settings.model_dump(mode="json")).items():
        table.add_row(key, "" if value is None else str(value))

    console.print(table)


app.add_typer(config_app, name="config")
app.add_typer(inspect_app, name="inspect")


def _flatten(data: dict, prefix: str = "") -> dict[str, object]:
    rows: dict[str, object] = {}
    for key in sorted(data):
        value = data[key]
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            rows.update(_flatten(value, full_key))
        else:
            rows[full_key] = value
    return rows


def main() -> None:
    app()
