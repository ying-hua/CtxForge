# Phase 2: Memory 方案设计

## 1. 目标

Phase 2 的目标是把 Phase 1 中的 Memory placeholder 替换为可运行、可检索、可观测的 SQLite-backed Memory Layer。

Phase 2 只负责 Memory 本地链路，不负责真实 DeepSeek 调用、Skill 激活、prefix cache diff 或 TUI 展示。它需要完成：

- SQLite schema 初始化与迁移入口。
- Working Memory 的读写与清理。
- Session Summary 的保存与读取。
- Long-term Memory metadata 的保存与简单检索。
- 将检索结果以 dynamic section 注入 `ContextBuilder`。
- 提供 `ctxforge memory list` 与 `ctxforge memory search` 等最小 CLI。

Phase 2 的核心交付不是“聪明的记忆系统”，而是一个边界清楚、后续可以接 embedding/vector store 的 Memory Manager。

## 2. 设计原则

### 2.1 Memory 永远不进入 Stable Prefix

Phase 1 已经建立稳定 prefix 的规则。Phase 2 接入后，所有 Memory 结果只能进入 dynamic suffix：

```text
memory.retrieved
session.working_memory
session.summary
```

原因：

- Memory 检索结果随当前 task、session 和数据库内容变化。
- 把 Memory 放入 stable prefix 会破坏 prefix-cache 稳定性。
- Phase 5 的 cache report 应该能清楚解释变化来自 dynamic suffix，而不是 stable prefix。

### 2.2 SQLite 优先，向量能力后置

Phase 2 使用 Python 内置 `sqlite3`。不引入 SQLAlchemy、sqlite-vec、外部 embedding 包或异步 DB 层。

长期记忆表预留 embedding 字段和 metadata 字段，但 Phase 2 默认检索策略是本地确定性检索：

```text
kind/scope/session filter + keyword overlap + recency + confidence
```

这样可以先把 Memory 读写、注入和报告链路跑通，避免被向量扩展安装和 DeepSeek embedding 可用性阻塞。

### 2.3 为什么不是直接文件

直接把记忆写成 JSON、JSONL 或普通文本文件也能工作，但它更适合极小规模、append-only 的记录，不适合 Phase 2 的目标。

主要问题是：

- 查询成本高。Memory 需要按 `scope / kind / session_id / project_dir / 关键词 / 时间` 过滤和排序，文件方案很快会退化成全量扫描加手写索引。
- 一致性弱。working memory、session summary、long-term metadata 往往会一起更新，文件方案很难提供原子事务，容易出现半写入状态。
- 扩展性差。后面加去重、embedding、来源追踪、统计字段时，文件格式会越来越像一个手工实现的数据库。
- 检索可解释性差。Phase 2 需要返回命中原因和可排序结果，SQLite 天然适合保存结构化字段和评分依据。
- 并发和维护更麻烦。即使当前是单用户本地工具，未来出现多进程或多轮运行时，文件锁和格式兼容都会变成额外负担。

文件存储的优势主要是直观、易调试、适合一次性导出；但对 Phase 2 这种“可检索、可更新、可追踪、可扩展”的 Memory 层，SQLite 是更稳的中间层。

### 2.4 写入要保守

默认不把完整对话写入长期记忆。Phase 2 只保存调用方明确给出的事实、决策、摘要、偏好和工作状态。

长期记忆写入必须包含：

- `scope`
- `kind`
- `content`
- `source`
- `confidence`
- `created_at`

没有来源的长期记忆不应静默写入。

### 2.5 检索结果必须可解释

Memory 检索不仅返回 content，还要返回命中原因，供 CLI、runtime report 和后续 TUI 展示。

每条命中至少包含：

- `id`
- `kind`
- `scope`
- `score`
- `source`
- `created_at`
- `reason`

## 3. 核心模型

### 3.1 MemoryRecord

```python
@dataclass(frozen=True)
class MemoryRecord:
    id: str
    scope: Literal["global", "project", "session"]
    kind: Literal["preference", "fact", "decision", "summary", "working"]
    content: str
    source: str
    created_at: datetime
    updated_at: datetime
    confidence: float
    session_id: str | None = None
    project_dir: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    embedding: list[float] | None = None
```

说明：

- `scope=global`：跨项目用户偏好或通用经验。
- `scope=project`：当前项目事实、架构决策、实现约定。
- `scope=session`：当前或历史 session 摘要。
- `kind=working`：只用于 Working Memory，不进入长期可复用记忆。
- `embedding` 在 Phase 2 只做 schema 预留，默认不写入。

### 3.2 MemoryHit

```python
@dataclass(frozen=True)
class MemoryHit:
    record: MemoryRecord
    score: float
    reason: str
```

`reason` 示例：

```text
scope_match + keyword_overlap(3) + confidence(0.80)
```

### 3.3 MemoryReport

```python
@dataclass(frozen=True)
class MemoryReport:
    status: Literal["ok", "empty", "disabled", "error"]
    db_path: str
    working_count: int
    summary_count: int
    long_term_count: int
    retrieved_count: int
    hits: list[dict[str, object]]
```

Runtime 中的 `memory_report` 应从 Phase 1 的 placeholder 字段升级为真实报告。

## 4. SQLite Schema

Phase 2 使用单文件 SQLite，默认路径沿用当前配置：

```text
.ctxforge/ctxforge.sqlite3
```

### 4.1 schema_migrations

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

Phase 2 初始 schema 版本为 `1`。

### 4.2 memory_records

```sql
CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL NOT NULL,
    session_id TEXT,
    project_dir TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    embedding_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_scope_kind
ON memory_records(scope, kind);

CREATE INDEX IF NOT EXISTS idx_memory_project_session
ON memory_records(project_dir, session_id);

CREATE INDEX IF NOT EXISTS idx_memory_created_at
ON memory_records(created_at);
```

约束由 Python 层校验：

- `scope in {"global", "project", "session"}`
- `kind in {"preference", "fact", "decision", "summary", "working"}`
- `0.0 <= confidence <= 1.0`
- `content.strip()` 非空

### 4.3 working_memory

Working Memory 可以放进 `memory_records(kind="working")`，但单独建表更利于按 session 清理和排序。

```sql
CREATE TABLE IF NOT EXISTS working_memory (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project_dir TEXT NOT NULL,
    key TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_working_memory_session
ON working_memory(project_dir, session_id, priority DESC, updated_at DESC);
```

同一 session 下允许多个 working memory item。Phase 2 不做复杂 todo 状态机，只保存可注入上下文的短文本。

### 4.4 session_summaries

```sql
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    project_dir TEXT NOT NULL,
    summary TEXT NOT NULL,
    source TEXT NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_summaries_project
ON session_summaries(project_dir, updated_at DESC);
```

Phase 2 只提供写入和读取能力。真实“每轮会话压缩摘要”由 Phase 4 DeepSeek Runtime 接入模型后再自动生成。

## 5. 模块边界

新增模块建议：

```text
src/ctxforge/memory/__init__.py
src/ctxforge/memory/models.py
src/ctxforge/memory/store.py
src/ctxforge/memory/manager.py
src/ctxforge/memory/render.py
```

### 5.1 MemoryStore

`MemoryStore` 是 SQLite 访问层，只负责持久化，不关心 context section。

```python
class MemoryStore:
    def __init__(self, db_path: Path) -> None: ...
    def initialize(self) -> None: ...
    def upsert_record(self, record: MemoryRecord) -> MemoryRecord: ...
    def list_records(...) -> list[MemoryRecord]: ...
    def search_records(...) -> list[MemoryHit]: ...
    def upsert_working_item(...) -> WorkingMemoryItem: ...
    def list_working_items(...) -> list[WorkingMemoryItem]: ...
    def clear_working_items(...) -> int: ...
    def upsert_session_summary(...) -> SessionSummary: ...
    def get_session_summary(...) -> SessionSummary | None: ...
```

规则：

- `initialize()` 创建父目录和 schema。
- SQLite connection 使用 context manager，避免长连接状态污染测试。
- 时间字段统一存 ISO 8601 UTC 字符串。
- JSON 字段通过 `json.dumps(..., sort_keys=True)` 保持确定性。

### 5.2 MemoryManager

`MemoryManager` 是 runtime-facing 层，负责把 store 结果转成 context section 和 report。

```python
class MemoryManager:
    def retrieve_for_context(
        self,
        *,
        task: str,
        cwd: Path,
        session_id: str,
        limit: int = 5,
    ) -> MemoryContext:
        ...
```

`MemoryContext` 包含：

```python
@dataclass(frozen=True)
class MemoryContext:
    sections: list[ContextSection]
    report: MemoryReport
```

### 5.3 Memory Renderer

Memory 注入 context 前需要稳定渲染，避免同一检索结果在不同运行中顺序漂移。

排序规则：

```text
score desc -> kind asc -> created_at desc -> id asc
```

输出 section：

```text
memory.retrieved       dynamic priority 40 source memory.search
session.working_memory dynamic priority 35 source memory.working
session.summary        dynamic priority 32 source memory.summary
```

示例内容：

```text
- [decision/project score=0.82 source=docs/arch] Use sqlite3 in early phases.
- [preference/global score=0.64 source=memory] Prefer durable repo docs.
```

## 6. 检索策略

### 6.1 Query Normalization

Phase 2 不引入中文分词库。默认做轻量 normalization：

- lower-case。
- 按非字母数字字符切分英文 token。
- 对 CJK 文本保留连续片段，并允许 `content LIKE '%query%'`。
- 丢弃长度为 1 的纯英文 token。

### 6.2 Scope Filter

默认检索范围：

```text
global memories
project memories where project_dir == cwd
session memories where session_id == current session_id
recent session summaries for current project
```

不跨项目检索 project-scope 记忆。

### 6.3 Score

Phase 2 使用可解释的本地分数：

```text
score =
  keyword_overlap * 1.0
  + exact_phrase_match * 2.0
  + confidence * 0.5
  + recency_boost
  + scope_boost
```

建议 boost：

```text
scope_boost:
  session: 0.30
  project: 0.20
  global: 0.10

recency_boost:
  created within 7 days: 0.20
  created within 30 days: 0.10
  else: 0
```

如果没有任何关键词命中，但存在当前 session summary，可以返回 summary，reason 标记为 `fallback_session_summary`。

## 7. Runtime 接入

新增 runtime 入口：

```python
def run_phase2(request: RuntimeRequest, settings: CtxForgeSettings) -> RuntimeResult:
    ...
```

行为：

1. 生成或复用 `session_id`。
2. 初始化 `MemoryStore`。
3. 调用 `MemoryManager.retrieve_for_context(...)`。
4. 将 `MemoryContext.sections` 作为 `extra_sections` 传给 `ContextBuilder`。
5. 返回真实 `memory_report`。
6. `answer` 仍是 placeholder，说明 DeepSeek Runtime 将在 Phase 4 接入。

Phase 2 暂不自动把每次 `ctxforge run` 的完整 task 写入长期记忆。可以选择把当前 task 作为 working memory 写入，但必须来源明确：

```text
source = "runtime.current_task"
```

如果这样做，需要保证它进入下一轮 dynamic section，而不是当前轮 stable prefix。

`run_phase1` 可保留兼容别名；CLI 默认调用 `run_phase2` 后，测试中需要明确覆盖 Phase 2 memory report。

## 8. CLI 接入

新增 Typer 子命令：

```powershell
ctxforge memory list
ctxforge memory search "query"
ctxforge memory add "content" --kind decision --scope project --source manual
ctxforge memory working set "key" "content" --session-id session-123
ctxforge memory working clear --session-id session-123
```

Phase 2 最小必须实现：

- `memory list`
- `memory search`
- `memory add`

`working set/clear` 可以作为同 phase 的第二批任务，或者只先保留 store API 和测试。

CLI 输出用 Rich table，不引入 TUI。

`ctxforge run` 的报告标题从 `Phase 1 Runtime Report` 调整为 `Phase 2 Runtime Report`，并增加：

- `memory_status`
- `retrieved_memories`
- `working_memory_items`
- `session_summary_present`

## 9. Context Builder 接入点

Phase 1 的 `ContextBuilder.build(..., extra_sections=...)` 已经满足 Phase 2 接入需求。Phase 2 不需要改 stable prefix 规则。

需要注意：

- 默认 placeholder `memory.retrieved` 和 `session.working_memory` 会与真实 Memory section 重名。
- 实现时应避免重复 section。

建议做法：

1. 在 `ContextBuilder.build()` 增加参数：

```python
include_memory_placeholders: bool = True
```

2. `run_phase2` 调用时设置为 `False`。
3. CLI `inspect context` 仍可默认展示 placeholder，除非后续增加 `--with-memory`。

这样 Phase 1 测试不受影响，Phase 2 runtime 也不会同时出现 placeholder 和真实 Memory。

## 10. 测试覆盖

Phase 2 测试重点：

- 初始化 store 时自动创建 `.ctxforge/ctxforge.sqlite3` 和 schema。
- `upsert_record` 后可以按 scope/kind/source/list 查询。
- project-scope 记忆不会跨 project_dir 泄漏。
- session working memory 只按当前 session 注入。
- session summary 可以 upsert 并在下一轮检索。
- search 结果排序确定：score desc -> kind asc -> created_at desc -> id asc。
- `MemoryManager.retrieve_for_context` 返回 dynamic sections，且 source/priority 正确。
- `run_phase2` 返回真实 `memory_report`，并把 memory sections 注入 context report。
- dynamic memory 改变不影响 `stable_prefix_sha256`。
- `ctxforge memory search` 对空库返回清晰的 empty 状态。

验证命令沿用：

```powershell
.\.venv\Scripts\python -m pytest -p no:cacheprovider
```

`-p no:cacheprovider` 用于规避当前 Windows 环境里 pytest cache 目录的噪声，不代表代码必须依赖该参数。

## 11. 实现顺序

建议按以下顺序编码：

1. `memory/models.py`：定义 `MemoryRecord`、`MemoryHit`、`MemoryReport`、`WorkingMemoryItem`、`SessionSummary`。
2. `memory/store.py`：实现 schema 初始化、record CRUD、working memory、session summary。
3. `memory/manager.py`：实现 context 检索、score、report。
4. `memory/render.py`：实现 Memory section 渲染与稳定排序。
5. `runtime/agent.py`：新增 `run_phase2`，保留 `run_phase1`。
6. `context/builder.py`：支持关闭 memory placeholder。
7. `cli/app.py`：新增 `memory` 子命令，`run` 切到 Phase 2。
8. `tests/test_memory_store.py`、`tests/test_memory_manager.py`、`tests/test_runtime.py`：补验收。

## 12. 非目标

Phase 2 不做：

- DeepSeek API 调用。
- DeepSeek embedding 调用。
- sqlite-vec 接入。
- SQLAlchemy 或迁移框架。
- 多用户权限模型。
- 自动总结完整对话。
- 长期记忆自动判定和去重。
- TUI memory 面板。
- prefix cache hit ratio 估算。

这些分别属于 Phase 4、Phase 5 和 Phase 6，或者等 Memory 数据规模和真实使用压力出现后再做。

## 13. 当前应修改文件

预计实现 Phase 2 时会触及：

```text
src/ctxforge/context/builder.py
src/ctxforge/runtime/agent.py
src/ctxforge/cli/app.py
src/ctxforge/memory/__init__.py
src/ctxforge/memory/models.py
src/ctxforge/memory/store.py
src/ctxforge/memory/manager.py
src/ctxforge/memory/render.py
tests/test_context_builder.py
tests/test_memory_store.py
tests/test_memory_manager.py
tests/test_runtime.py
```

文档和 README 可在实现完成后同步更新，而不是在方案阶段提前声明已完成能力。
