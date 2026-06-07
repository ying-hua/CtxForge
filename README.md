# CtxForge

CtxForge is a DeepSeek-native Memory and Context Engineering runtime.

It is not another full coding agent. The goal is to provide reusable building
blocks for agents that need stable context construction, layered memory, skill
loading, and prefix-cache observability.

## Phase 2

The current implementation includes:

- package metadata and CLI entry point
- Typer-based command surface
- Rich-friendly output and logging hooks
- TOML configuration loading
- default, user, project, environment, and CLI override boundaries
- deterministic context section rendering
- stable prefix construction and SHA-256 snapshots
- estimated token budgeting with optional section drop/truncation
- context reports for runtime and inspection commands
- SQLite-backed memory schema initialization
- working memory and session summary storage APIs
- long-term memory metadata storage and local keyword search
- dynamic memory section injection into the context builder
- `ctxforge memory add`, `ctxforge memory list`, and `ctxforge memory search`
- placeholder skill, model, and cache diff paths for later phases

## Quick Start

```powershell
python -m pip install -e ".[dev]"
ctxforge config show
ctxforge memory add "Use sqlite3 for early memory phases." --kind decision --source manual
ctxforge memory search "sqlite memory"
ctxforge run "Summarize the current project direction."
ctxforge inspect context "Summarize the current project direction."
pytest -p no:cacheprovider
```

## Configuration

CtxForge loads configuration in this order:

```text
defaults < user config < project config < environment < CLI flags
```

User config:

```text
~/.ctxforge/config.toml
```

Project config:

```text
./ctxforge.toml
```

Environment variables:

```text
DEEPSEEK_API_KEY
CTXFORGE_DEEPSEEK_MODEL
CTXFORGE_DEEPSEEK_BASE_URL
CTXFORGE_LOG_LEVEL
```
