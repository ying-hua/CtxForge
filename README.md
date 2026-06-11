# CtxForge

CtxForge is a DeepSeek-native Memory and Context Engineering runtime.

It is not another full coding agent. The goal is to provide reusable building
blocks for agents that need stable context construction, layered memory, skill
loading, and prefix-cache observability.

Detailed design documents live in [docs/README.md](./docs/README.md).

## Phase 6

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
- local skill manifest validation and discovery
- `SKILL.md` loading and semi-stable context injection
- explicit `--skill` selection and simple activation matching
- `ctxforge skill list`, `ctxforge skill inspect`, and `ctxforge skill install`
- DeepSeek Chat Completions client with mockable HTTP transport
- real `ctxforge run` model calls when `DEEPSEEK_API_KEY` is configured
- `--no-model` dry-run mode for offline context/memory/skill inspection
- session summary persistence after successful model calls
- model usage and prompt-cache usage fields in runtime reports
- full request snapshots with UTF-8 byte spans and ordered section fingerprints
- local common-prefix analysis with estimated cache reuse ratios
- DeepSeek cache hit/miss usage merged as a separate actual ratio
- SQLite-backed cache snapshot history with configurable retention
- same-session baseline selection with optional project fallback
- `ctxforge inspect cache` history, JSON, and section-change inspection
- cache observability failures isolated from successful model responses
- optional Textual TUI installed through the `tui` or `dev` extra
- `ctxforge tui` with reusable session id and model/skill/budget options
- runtime events for prepared reports, response deltas, completion, failure, and cancellation
- DeepSeek SSE streaming with usage-only chunk support
- streaming retry only before the first emitted chunk
- context, memory, cache, and response panels in one terminal interface
- throttled response rendering and explicit Run/Stop controls
- cancellation that preserves the visible partial answer without writing cache or summary state
- headless Textual coverage for wide and narrow terminal sizes

## Quick Start

```powershell
python -m pip install -e ".[dev]"
ctxforge config show
$env:DEEPSEEK_API_KEY = "sk-..."
ctxforge memory add "Use sqlite3 for early memory phases." --kind decision --source manual
ctxforge memory search "sqlite memory"
ctxforge skill list
ctxforge run "Summarize the current project direction."
ctxforge run "Summarize the current project direction." --no-model
ctxforge run "Review the current project direction." --skill code-review
ctxforge tui
ctxforge tui --no-model
ctxforge inspect context "Summarize the current project direction."
ctxforge inspect cache
pytest -p no:cacheprovider
```

## Configuration

CtxForge loads configuration in this order:

```text
defaults < user config < project config < project .env < environment < CLI flags
```

User config:

```text
~/.ctxforge/config.toml
```

Project config:

```text
./ctxforge.toml
```

Project `.env`:

```text
./.env
```

Environment variables:

```text
DEEPSEEK_API_KEY
CTXFORGE_DEEPSEEK_MODEL
CTXFORGE_DEEPSEEK_BASE_URL
CTXFORGE_DEEPSEEK_MAX_RETRIES
CTXFORGE_LOG_LEVEL
```

Optional project cache configuration:

```toml
[cache]
enabled = true
snapshot_retention = 20
allow_project_fallback = true

[tui]
response_refresh_ms = 40
max_visible_turns = 20
show_full_memory_content = false
```
