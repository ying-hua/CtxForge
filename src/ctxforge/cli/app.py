from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ctxforge.config.settings import load_settings
from ctxforge.context import ContextBuilder
from ctxforge.llm import DeepSeekAPIError, DeepSeekResponseError, MissingDeepSeekApiKey
from ctxforge.logging import configure_logging
from ctxforge.memory import MemoryRecord, MemoryStore
from ctxforge.runtime.agent import RuntimeRequest, run_phase3, run_phase4
from ctxforge.skills import SkillRegistry

app = typer.Typer(
    name="ctxforge",
    help="DeepSeek-native Memory and Context Engineering runtime.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Inspect and manage CtxForge configuration.")
inspect_app = typer.Typer(help="Inspect CtxForge runtime artifacts.")
memory_app = typer.Typer(help="Inspect and manage CtxForge memory.")
skill_app = typer.Typer(help="Inspect and manage CtxForge skills.")
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
    max_output_tokens: Optional[int] = typer.Option(
        None,
        "--max-output-tokens",
        help="Override model output token budget.",
    ),
    model: Optional[str] = typer.Option(None, "--model", help="Override the DeepSeek model for this run."),
    no_model: bool = typer.Option(False, "--no-model", help="Build context without calling DeepSeek."),
    skill_names: Optional[list[str]] = typer.Option(
        None,
        "--skill",
        help="Explicitly activate a local skill by name. May be provided multiple times.",
    ),
) -> None:
    """Run a task through the Phase 4 DeepSeek runtime."""
    cli_overrides: dict[str, object] = {}
    if max_tokens or max_output_tokens:
        cli_overrides["context"] = {
            "max_tokens": max_tokens,
            "reserved_output_tokens": max_output_tokens,
        }
    if model:
        cli_overrides["deepseek"] = {"model": model}
    settings = load_settings(
        project_dir=project_dir,
        cli_overrides=cli_overrides or None,
    )
    configure_logging(settings.logging.level)

    request = RuntimeRequest(
        task=task,
        cwd=project_dir,
        session_id=session_id,
        skill_names=skill_names or [],
        max_tokens=max_tokens,
        model=model,
        max_output_tokens=max_output_tokens,
    )
    try:
        result = run_phase3(request, settings=settings) if no_model else run_phase4(request, settings=settings)
    except (MissingDeepSeekApiKey, DeepSeekAPIError, DeepSeekResponseError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    console.print(Panel(result.answer, title="CtxForge", border_style="cyan"))

    table = Table(title="Phase 4 Runtime Report")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("session_id", result.session_id)
    table.add_row("project_dir", str(project_dir))
    table.add_row("model", model or settings.deepseek.model)
    table.add_row("max_tokens", str(settings.context.max_tokens))
    table.add_row("max_output_tokens", str(max_output_tokens or settings.context.reserved_output_tokens))
    table.add_row("input_budget", str(result.context_report["input_budget"]))
    table.add_row("context_tokens", str(result.context_report["total_estimated_tokens"]))
    table.add_row("stable_prefix_sha256", str(result.context_report["stable_prefix_sha256"]))
    table.add_row("memory_db_path", str(settings.memory.resolved_db_path(project_dir)))
    table.add_row("memory_status", str(result.memory_report["status"]))
    table.add_row("retrieved_memories", str(result.memory_report["retrieved_count"]))
    table.add_row("working_memory_items", str(result.memory_report["working_count"]))
    table.add_row("session_summary_present", str(bool(result.memory_report["summary_count"])))
    table.add_row("skill_status", str(result.skill_report["status"]))
    table.add_row("selected_skills", ", ".join(_selected_skill_names(result.skill_report)) or "none")
    llm_report = result.llm_report or {"status": "dry_run_no_model", "request_id": None, "finish_reason": None}
    usage = llm_report.get("usage") if isinstance(llm_report.get("usage"), dict) else {}
    table.add_row("llm_status", str(llm_report.get("status")))
    table.add_row("llm_request_id", str(llm_report.get("request_id") or "none"))
    table.add_row("finish_reason", str(llm_report.get("finish_reason") or "none"))
    table.add_row("prompt_tokens", str(usage.get("prompt_tokens") if isinstance(usage, dict) else None))
    table.add_row("completion_tokens", str(usage.get("completion_tokens") if isinstance(usage, dict) else None))
    table.add_row(
        "prompt_cache_hit_tokens",
        str(usage.get("prompt_cache_hit_tokens") if isinstance(usage, dict) else None),
    )
    table.add_row(
        "prompt_cache_miss_tokens",
        str(usage.get("prompt_cache_miss_tokens") if isinstance(usage, dict) else None),
    )
    table.add_row("summary_written", str(llm_report.get("summary_written", False)))
    console.print(table)


@skill_app.command("list")
def skill_list(
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
) -> None:
    """List local project skills."""
    settings = load_settings(project_dir=project_dir)
    discovery = SkillRegistry(settings.skills.resolved_skills_dir(project_dir)).discover()
    if not discovery.skills:
        console.print("No skills found.")
    else:
        table = Table(title="Skills")
        table.add_column("Name", style="bold")
        table.add_column("Version")
        table.add_column("Description")
        table.add_column("Activation")
        table.add_column("Tools")
        for skill in discovery.skills:
            table.add_row(
                skill.name,
                skill.manifest.version,
                skill.manifest.description,
                ", ".join(skill.manifest.activation) or "none",
                ", ".join(skill.manifest.allowed_runtime_tools) or "none",
            )
        console.print(table)
    _print_skill_errors(discovery.errors)


@skill_app.command("inspect")
def skill_inspect(
    name: str = typer.Argument(..., help="Skill name to inspect."),
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
) -> None:
    """Inspect a local project skill."""
    settings = load_settings(project_dir=project_dir)
    discovery = SkillRegistry(settings.skills.resolved_skills_dir(project_dir)).discover()
    skill = next((candidate for candidate in discovery.skills if candidate.name == name), None)
    if skill is None:
        _print_skill_errors(discovery.errors)
        console.print(f"Skill not found: {name}")
        raise typer.Exit(code=1)

    table = Table(title=f"Skill: {skill.name}")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("name", skill.name)
    table.add_row("version", skill.manifest.version)
    table.add_row("description", skill.manifest.description)
    table.add_row("activation", ", ".join(skill.manifest.activation) or "none")
    table.add_row("allowed_runtime_tools", ", ".join(skill.manifest.allowed_runtime_tools) or "none")
    table.add_row("directory", str(skill.directory))
    console.print(table)
    console.print(Panel(skill.instructions, title="SKILL.md", border_style="cyan"))


@skill_app.command("install")
def skill_install(
    source_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
        help="Local skill directory containing skill.toml and SKILL.md.",
    ),
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
    force: bool = typer.Option(False, "--force", help="Replace an existing installed skill with the same name."),
) -> None:
    """Install a local skill into the project skills directory."""
    settings = load_settings(project_dir=project_dir)
    registry = SkillRegistry(settings.skills.resolved_skills_dir(project_dir))
    try:
        installed = registry.install(source_dir, force=force)
    except (FileExistsError, OSError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"Installed skill {installed.name} to {installed.directory}")


@memory_app.command("add")
def memory_add(
    content: str = typer.Argument(..., help="Memory content to persist."),
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
    scope: str = typer.Option("project", "--scope", help="Memory scope: global, project, or session."),
    kind: str = typer.Option("fact", "--kind", help="Memory kind: preference, fact, decision, summary, or working."),
    source: str = typer.Option("manual", "--source", help="Source label for this memory."),
    confidence: float = typer.Option(0.8, "--confidence", min=0.0, max=1.0, help="Confidence from 0 to 1."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Session id for session-scope memory."),
) -> None:
    """Persist a long-term memory record."""
    settings = load_settings(project_dir=project_dir)
    store = MemoryStore(settings.memory.resolved_db_path(project_dir))
    store.initialize()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    record = store.upsert_record(
        MemoryRecord(
            id=f"mem-{uuid4().hex[:16]}",
            scope=scope,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            content=content,
            source=source,
            confidence=confidence,
            session_id=session_id,
            project_dir=str(project_dir) if scope in {"project", "session"} else None,
            created_at=now,
            updated_at=now,
        )
    )
    console.print(f"Added memory {record.id}")


@memory_app.command("list")
def memory_list(
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
    scope: Optional[str] = typer.Option(None, "--scope", help="Filter by scope."),
    kind: Optional[str] = typer.Option(None, "--kind", help="Filter by kind."),
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Filter by session id."),
    limit: int = typer.Option(50, "--limit", min=1, help="Maximum rows to show."),
) -> None:
    """List memory records."""
    settings = load_settings(project_dir=project_dir)
    store = MemoryStore(settings.memory.resolved_db_path(project_dir))
    store.initialize()
    records = store.list_records(
        scope=scope,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        project_dir=str(project_dir) if scope == "project" else None,
        session_id=session_id,
        limit=limit,
    )
    table = Table(title="Memory Records")
    table.add_column("ID", style="bold")
    table.add_column("Scope")
    table.add_column("Kind")
    table.add_column("Confidence", justify="right")
    table.add_column("Source")
    table.add_column("Content")
    for record in records:
        table.add_row(
            record.id,
            record.scope,
            record.kind,
            f"{record.confidence:.2f}",
            record.source,
            record.content,
        )
    console.print(table)


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search query."),
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
    session_id: Optional[str] = typer.Option(None, "--session-id", help="Session id for session-scope search."),
    limit: int = typer.Option(5, "--limit", min=1, help="Maximum hits to show."),
) -> None:
    """Search memory records with the Phase 2 local scorer."""
    settings = load_settings(project_dir=project_dir)
    store = MemoryStore(settings.memory.resolved_db_path(project_dir))
    store.initialize()
    hits = store.search_records(query=query, project_dir=str(project_dir), session_id=session_id, limit=limit)
    table = Table(title="Memory Search")
    table.add_column("ID", style="bold")
    table.add_column("Scope")
    table.add_column("Kind")
    table.add_column("Score", justify="right")
    table.add_column("Reason")
    table.add_column("Content")
    for hit in hits:
        table.add_row(
            hit.record.id,
            hit.record.scope,
            hit.record.kind,
            f"{hit.score:.2f}",
            hit.reason,
            hit.record.content,
        )
    if not hits:
        console.print("No memory hits.")
        return
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
app.add_typer(memory_app, name="memory")
app.add_typer(skill_app, name="skill")


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


def _selected_skill_names(skill_report: dict[str, object]) -> list[str]:
    selected = skill_report.get("selected", [])
    if not isinstance(selected, list):
        return []
    names = []
    for item in selected:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
        elif isinstance(item, str):
            names.append(item)
    return names


def _print_skill_errors(errors: object) -> None:
    if not errors:
        return
    table = Table(title="Skill Errors")
    table.add_column("Path", style="bold")
    table.add_column("Message")
    for error in errors:
        if hasattr(error, "path") and hasattr(error, "message"):
            table.add_row(str(error.path), str(error.message))
        elif isinstance(error, dict):
            table.add_row(str(error.get("path", "")), str(error.get("message", "")))
    console.print(table)


def main() -> None:
    app()
