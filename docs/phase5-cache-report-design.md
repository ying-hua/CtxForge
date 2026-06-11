# Phase 5: Prefix Cache Report 方案设计

## 1. 目标

Phase 5 的目标是把 Phase 1-4 已经预留的 cache snapshot 和 DeepSeek cache usage 字段，升级为可持久化、可比较、可解释的 Prefix Cache Observatory。

本阶段完成：

- 保存模型请求对应的上下文快照。
- 对比当前请求与基线请求的字节级共同前缀。
- 对所有 context section 计算有序 fingerprint。
- 找出首个变化 section、直接变化 section 和被连带失效的后续 section。
- 估算本地 prefix reuse potential。
- 合并 DeepSeek 返回的真实 cache hit/miss token。
- 在 `ctxforge run` 中展示当前 cache report。
- 提供 `ctxforge inspect cache` 查看历史报告。

Phase 5 不负责精确账单、跨供应商统一指标、底层 KV cache 控制、服务端 cache 生命周期管理或 TUI。这些超出当前 MVP 边界。

## 2. 当前实现基础与缺口

### 2.1 已有能力

当前代码已经具备：

- `BuiltContext.rendered_prompt`：完整、确定性渲染的 context。
- `BuiltContext.messages`：实际发送给 DeepSeek Client 的消息。
- `PrefixSnapshot`：
  - `stable_prefix_bytes`
  - `stable_prefix_sha256`
  - stable section hashes
- `ContextReport`：
  - 各稳定性分区 token 估算
  - stable prefix bytes/hash
  - section 纳入、丢弃和截断信息
- `ChatUsage`：
  - `prompt_cache_hit_tokens`
  - `prompt_cache_miss_tokens`
- `run_phase4`：真实模型调用、usage 解析和 session summary 写入。

### 2.2 现有快照不能直接承担 Phase 5

Phase 1 的 `PrefixSnapshot` 只描述 stable prefix，适合验证“动态 task/memory 是否破坏 stable prefix”，但不能回答：

- semi-stable project/skill 内容是否变化。
- dynamic memory、summary、task 从哪个字节开始变化。
- 未变化的 dynamic section 是否仍可形成更长共同前缀。
- 第一个变化 section 之后有哪些 section 被 prefix 规则连带失效。
- 当前请求与上一请求的完整 prefix 重合比例。

因此 Phase 5 不应改变 `PrefixSnapshot` 的既有语义，而应新增完整的 `CacheSnapshot`。

### 2.3 Phase 4 只保存了当前 usage

`run_phase4` 已把 DeepSeek 返回的 cache hit/miss token 放入 report，但没有：

- 上一请求的持久化基线。
- 当前和上一请求的 byte diff。
- section 级变化解释。
- 本地估算与真实 usage 的并列展示。
- `ctxforge inspect cache`。

Phase 5 将补齐这一链路。

## 3. DeepSeek 指标语义

DeepSeek 的 context cache 由服务端自动处理。客户端不能把本地共同前缀比例直接视为真实命中率。

Phase 5 必须区分两类指标：

### 3.1 本地估算

```text
estimated_cache_hit_ratio
```

定义：

```text
当前请求与选定基线的共同前缀估算 token
------------------------------------------------
当前请求的总输入估算 token
```

它表示本地可观察到的 prefix reuse potential，是上限性质的启发式指标，不保证服务端实际命中。

影响真实命中的因素包括：

- 服务端是否仍保留对应 cache。
- 服务端实际 tokenizer 和 cache block 粒度。
- 当前请求是否匹配了本地没有保存的其他历史请求。
- provider、model、账户隔离和服务端实现变化。

### 3.2 服务端真实指标

```text
actual_cache_hit_ratio
```

当 DeepSeek 返回以下字段时：

```text
prompt_cache_hit_tokens
prompt_cache_miss_tokens
```

计算：

```text
actual_cache_hit_ratio =
  prompt_cache_hit_tokens
  / (prompt_cache_hit_tokens + prompt_cache_miss_tokens)
```

如果字段缺失或分母为 0，值为 `None`，不能用 0 代替。

服务端 usage 是本次请求的权威观测值；本地估算只负责解释“上下文为什么可能命中或失效”。

官方参考：

- https://api-docs.deepseek.com/guides/kv_cache
- https://api-docs.deepseek.com/api/create-chat-completion

## 4. 设计原则

### 4.1 保持 Phase 1 契约

`PrefixSnapshot` 继续只负责 stable prefix：

- 不增加动态内容。
- 不改变 `stable_prefix_sha256` 计算方式。
- 现有 Phase 1-4 测试应继续通过。

Phase 5 新增的完整快照放在 `ctxforge.cache` 模块，不污染 Context Builder 的稳定性职责。

### 4.2 字节 diff 与 section diff 同时存在

字节 diff 回答：

```text
第一个不同字节在哪里？
```

section diff 回答：

```text
哪个逻辑 section 直接发生变化？
从哪个 section 开始，后续 prefix 都不再可复用？
```

只做 section hash 会丢失 section 内部变化位置；只做 byte diff 又无法给用户可理解的模块解释。两者必须同时输出。

### 4.3 不把 HTTP JSON 序列化当成 provider cache key

`httpx` 的 JSON 字段顺序和空白不是模型输入语义的一部分。Phase 5 不比较 HTTP wire bytes。

本地 byte diff 使用 `BuiltContext.rendered_prompt.encode("utf-8")`，原因是：

- 它由 CtxForge 确定性渲染。
- 它保留所有 section 的真实顺序和内容。
- 它可以稳定映射到 section byte span。
- 它不会因 HTTP client 的序列化实现变化产生伪 diff。

同时保存 `BuiltContext.messages` 的 canonical JSON SHA-256，用于检测 provider-facing message envelope 是否变化，但不对外宣称它等同于服务端 cache key。

### 4.4 只比较兼容快照

基线至少需要满足：

- 相同 project key。
- 相同 provider。
- 相同 normalized base URL。
- 相同 model。
- 相同 snapshot format version。

不同 model 或 format version 的快照不参与 ratio 估算，报告状态为 `incomparable`。

如果完整 prompt bytes 相同但 `messages_sha256` 不同，说明 provider-facing message
envelope 已变化；此时同样报告为 `incomparable`，不能误报为 100% identical。

### 4.5 本地报告不能覆盖真实 usage

即使本地估算为 1.0，服务端真实命中也可能更低。

即使本地与“上一请求”重合很低，服务端也可能命中更早的历史请求。

报告中必须并列展示两个比例和数据来源，不把两者合并成单一值。

### 4.6 Cache observability 失败不丢模型回答

模型已经成功返回时，cache snapshot 持久化失败不应让用户失去回答。

Runtime 应：

- 返回模型 answer。
- 将 `cache_report.persistence_status` 标记为 `failed`。
- 写入标准日志。
- 不伪装成已成功保存。

## 5. 总体调用链

```text
ctxforge run
  -> prepare runtime context
      -> retrieve memory
      -> select skills
      -> ContextBuilder.build
  -> CacheSnapshotFactory.create
  -> CacheStore.find_baseline
  -> CacheAnalyzer.compare
  -> DeepSeekClient.complete
  -> CacheAnalyzer.attach_provider_usage
  -> CacheStore.save_snapshot_and_report
  -> write session summary
  -> RuntimeResult(cache_report=...)
```

第一轮没有本地基线时：

```text
local status = no_baseline
estimated_cache_hit_ratio = None
actual cache fields = provider response values, if present
```

后续轮次：

```text
load compatible baseline
  -> common prefix byte diff
  -> section change analysis
  -> local estimate
  -> call provider
  -> attach actual usage
  -> persist current snapshot as future baseline
```

## 6. 模块设计

新增目录：

```text
src/ctxforge/cache/
  __init__.py
  models.py
  snapshot.py
  analyzer.py
  store.py
```

新增测试：

```text
tests/test_cache_analyzer.py
tests/test_cache_store.py
tests/test_phase5_runtime.py
```

现有文件调整：

```text
src/ctxforge/config/settings.py
src/ctxforge/runtime/agent.py
src/ctxforge/cli/app.py
tests/test_cli.py
README.md
```

### 6.1 cache/models.py

定义：

- `CacheSectionSnapshot`
- `CacheSnapshot`
- `SectionChange`
- `ProviderCacheUsage`
- `CacheReport`

### 6.2 cache/snapshot.py

职责：

- 从 `BuiltContext` 构造完整 cache snapshot。
- 计算 prompt bytes/hash。
- 计算 canonical messages hash。
- 为每个 section 计算有序 fingerprint 和 byte span。
- 生成 snapshot format version。

### 6.3 cache/analyzer.py

职责：

- 计算两个 byte string 的最长共同前缀。
- 识别首个变化 section。
- 区分直接变化和连带失效。
- 计算本地估算 token/ratio。
- 合并 provider cache usage。
- 输出纯数据 `CacheReport`。

Analyzer 不访问 SQLite，不调用模型，不读取配置。

### 6.4 cache/store.py

职责：

- 初始化 cache 表。
- 保存和读取快照。
- 选择兼容基线。
- 查询历史 report。
- 按 retention 配置清理旧快照。

CacheStore 使用与 MemoryStore 相同的 SQLite 文件，但保持独立类和独立表，不把 cache 查询逻辑塞进 MemoryStore。

## 7. 核心数据模型

### 7.1 CacheSectionSnapshot

```python
@dataclass(frozen=True)
class CacheSectionSnapshot:
    key: str
    name: str
    stability: str
    source: str
    ordinal: int
    start_byte: int
    end_byte: int
    token_estimate: int
    content_sha256: str
    rendered_sha256: str
    truncated: bool
```

说明：

- `key` 使用 `name#ordinal`，避免重复 section name 覆盖。
- `start_byte/end_byte` 对应完整 `rendered_prompt` 的 UTF-8 byte span。
- `content_sha256` 判断正文变化。
- `rendered_sha256` 同时覆盖 metadata、正文和渲染协议变化。
- `ordinal` 保留最终 section 顺序。

Phase 1 的 `section_hashes: dict[str, str]` 继续保留；Phase 5 使用有序 list，不复用会丢重复项的 dict。

### 7.2 CacheSnapshot

```python
@dataclass(frozen=True)
class CacheSnapshot:
    id: str
    format_version: int
    project_key: str
    session_id: str
    provider: str
    base_url: str
    model: str
    prompt_bytes: bytes
    prompt_sha256: str
    messages_sha256: str
    stable_prefix_sha256: str
    total_estimated_tokens: int
    sections: list[CacheSectionSnapshot]
    created_at: datetime
```

`project_key` 应使用规范化绝对路径：

```python
os.path.normcase(str(cwd.resolve()))
```

这样可以减少 Windows 路径大小写和相对路径导致的重复 scope。

### 7.3 SectionChange

```python
@dataclass(frozen=True)
class SectionChange:
    key: str
    name: str
    change_type: Literal["changed", "added", "removed", "reordered"]
    stability: str | None
    previous_sha256: str | None
    current_sha256: str | None
```

### 7.4 ProviderCacheUsage

```python
@dataclass(frozen=True)
class ProviderCacheUsage:
    prompt_tokens: int | None
    hit_tokens: int | None
    miss_tokens: int | None
```

Cache Analyzer 不直接依赖 `ctxforge.llm.ChatUsage`。Runtime 负责把 `ChatUsage` 转成该结构，避免 cache 模块反向依赖 LLM client。

### 7.5 CacheReport

```python
@dataclass(frozen=True)
class CacheReport:
    status: str
    snapshot_id: str
    baseline_snapshot_id: str | None
    baseline_scope: str | None
    same_session: bool | None
    prompt_bytes: int
    common_prefix_bytes: int | None
    changed_after_byte: int | None
    common_prefix_estimated_tokens: int | None
    total_estimated_tokens: int
    estimated_cache_hit_ratio: float | None
    actual_cache_hit_ratio: float | None
    prompt_cache_hit_tokens: int | None
    prompt_cache_miss_tokens: int | None
    provider_usage_status: str
    first_changed_section: str | None
    direct_changes: list[SectionChange]
    invalidated_sections: list[str]
    stable_prefix_changed: bool | None
    persistence_status: str
```

建议状态：

```text
no_baseline
identical
changed
incomparable
disabled
```

## 8. 快照构造

### 8.1 Prompt bytes

```python
prompt_bytes = built_context.rendered_prompt.encode("utf-8")
prompt_sha256 = sha256(prompt_bytes).hexdigest()
```

### 8.2 Canonical messages hash

```python
canonical_messages = json.dumps(
    built_context.messages,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
messages_sha256 = sha256(canonical_messages.encode("utf-8")).hexdigest()
```

它用于检测 message role、分组或 envelope 的变化。

如果未来 `ContextBuilder` 从两条 message 改成多条 message，应提高 `format_version`，避免把不同协议的历史快照当成可比基线。

### 8.3 Section span

快照构造不能手写另一套 section 分隔规则。

建议在 `context/render.py` 新增：

```python
def render_prompt_parts(
    sections: list[ContextSection],
) -> tuple[str, list[RenderedSectionSpan]]:
    ...
```

`ContextBuilder` 和 `CacheSnapshotFactory` 共用该函数，确保：

- 渲染文本只有一个实现来源。
- byte span 与最终 prompt 完全一致。
- `\n\n` 分隔符的归属规则固定。

span 使用 UTF-8 byte offset，而不是 Python character index。

### 8.4 截断状态

`BuiltContext.sections` 中的 section 已经是预算处理后的最终版本。

`truncated` 可从 `ContextReport.truncated_sections` 的 key 集合映射，报告应比较实际发送版本，而不是预算处理前的原始内容。

## 9. 对比算法

### 9.1 最长共同前缀

```python
def common_prefix_length(left: bytes, right: bytes) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    return index
```

MVP 的 context 通常远小于需要复杂算法的规模，线性扫描足够。不要为了 byte diff 引入额外依赖。

结果：

```text
common_prefix_bytes = index
changed_after_byte = index
```

完全相同时：

```text
common_prefix_bytes = len(current.prompt_bytes)
changed_after_byte = None
status = identical
```

### 9.2 UTF-8 与 token 估算

共同前缀可能停在多字节字符中间。估算 token 前需要截到最后一个合法 UTF-8 边界：

```python
common_text = current.prompt_bytes[:common_prefix_bytes].decode(
    "utf-8",
    errors="ignore",
)
```

然后复用当前 `estimate_tokens`：

```text
common_prefix_estimated_tokens = estimate_tokens(common_text)
estimated_cache_hit_ratio =
  common_prefix_estimated_tokens / current.total_estimated_tokens
```

比例 clamp 到 `[0.0, 1.0]`。

### 9.3 Section 变化

按 `key=name#ordinal` 和有序 sequence 比较：

- 相同 key、hash 不同：`changed`
- 当前新增：`added`
- 基线存在、当前缺失：`removed`
- 相同逻辑 section 出现在不同 ordinal：`reordered`

需要区分：

```text
direct_changes
```

直接 hash、存在性或顺序发生变化的 section。

```text
invalidated_sections
```

从首个变化位置开始，当前请求中的所有后续 section。即使后续 section 自身 hash 未变化，也因为 prefix 中更早内容变化而无法沿用同一连续前缀。

### 9.4 首个变化 section

优先用 byte offset 映射当前 section span：

- offset 位于当前 section 内：该 section 是首个变化 section。
- offset 位于两个 section 的分隔符：后一个 current section 是首个变化 section。
- section 被删除且 current 无对应 span：使用基线 section 名并标记 `removed`。
- prompt 完全一致：`None`。

### 9.5 Stable prefix 状态

```python
stable_prefix_changed = (
    previous.stable_prefix_sha256 != current.stable_prefix_sha256
)
```

这个字段用于快速识别严重稳定性回归。

如果只是 task、memory 或 session summary 变化，预期：

```text
stable_prefix_changed = False
first_changed_section 属于 semi_stable 或 dynamic
```

如果 skill manifest 或 runtime protocol 变化，预期：

```text
stable_prefix_changed = True
invalidated_sections 覆盖更大范围
```

## 10. 基线选择

### 10.1 兼容性 scope

CacheStore 先筛选：

```text
project_key
provider
base_url
model
format_version
```

### 10.2 选择顺序

建议：

1. 优先选择相同 `session_id` 的最新成功模型请求。
2. 如果没有，选择当前 project/cache scope 下最新成功模型请求。
3. 都没有则 `no_baseline`。

报告记录：

```text
baseline_scope = session | project_fallback | None
same_session = True | False | None
```

这样既支持 Phase 6 长会话，也让当前一次一进程的 CLI 在没有显式复用 `--session-id` 时仍能产生有意义的项目级报告。

### 10.3 基线限制

MVP 只与一个选定基线比较，不搜索全部历史快照中的“最佳共同前缀”。

因此可能出现：

- 本地 estimate 低，但 provider 命中更高，因为服务端匹配了其他历史请求。
- 本地 estimate 高，但 provider 命中更低，因为服务端 cache 已过期或未建立。

这是可解释 observability 的边界，不是 bug。

## 11. SQLite 持久化

### 11.1 独立 cache schema

使用同一个 `.ctxforge/ctxforge.sqlite3`，新增独立表：

```sql
CREATE TABLE IF NOT EXISTS cache_snapshots (
    id TEXT PRIMARY KEY,
    format_version INTEGER NOT NULL,
    project_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_bytes BLOB NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    messages_sha256 TEXT NOT NULL,
    stable_prefix_sha256 TEXT NOT NULL,
    total_estimated_tokens INTEGER NOT NULL,
    sections_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    request_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cache_scope_created
ON cache_snapshots(
    project_key,
    provider,
    base_url,
    model,
    created_at DESC
);

CREATE INDEX IF NOT EXISTS idx_cache_session_created
ON cache_snapshots(
    project_key,
    session_id,
    created_at DESC
);
```

建议使用独立的：

```text
cache_schema_migrations
```

不要复用当前 `MemoryStore` 的 `schema_migrations` version，避免两个模块争用同一整数版本空间。

### 11.2 保存内容

`sections_json` 保存 section snapshot metadata 和 hash，不保存第二份正文。

`report_json` 保存当次已经计算完成的报告，便于 `inspect cache` 直接展示历史结果，不需要用新版本 analyzer 重算旧报告。

CacheStore 在保存和读取时校验 prompt SHA-256、section span、section rendered
SHA-256 以及 report/snapshot id 对应关系。损坏记录不会作为后续 baseline。

### 11.3 保存时机

只把成功解析的模型请求保存为未来基线：

- 模型调用成功：保存。
- API error：不保存。
- timeout：不保存，因为无法确认 provider 是否完成处理。
- `--no-model`：只做临时本地比较，不保存。

这能避免 dry-run 和明确失败请求污染真实调用基线。

### 11.4 Retention

默认每个 project/provider/model scope 保留最近 20 条快照：

```toml
[cache]
snapshot_retention = 20
```

保存后在同一事务中清理超出 retention 的旧记录。

原因：

- byte diff 需要保存原始 prompt bytes。
- prompt 可能包含 task、memory 和 skill 内容。
- 限制历史条数能降低磁盘和隐私暴露面。

`ctxforge inspect cache` 不打印原始 prompt，也不把 prompt bytes 写入日志。

## 12. Runtime 接入

### 12.1 新入口

```python
def run_phase5(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    client: ChatClient | None = None,
    execute_model: bool = True,
) -> RuntimeResult:
    ...
```

保留 `run_phase4` 供历史测试和阶段演示使用。

### 12.2 避免复制 Phase 4 编排

建议从 `run_phase4` 抽出私有准备函数：

```python
@dataclass(frozen=True)
class PreparedRuntime:
    session_id: str
    context: BuiltContext
    memory_report: dict[str, object]
    skill_report: dict[str, object]
    selected_skill_names: list[str]
    previous_summary: SessionSummary | None
    memory_store: MemoryStore


def _prepare_runtime(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
) -> PreparedRuntime:
    ...
```

Phase 4 和 Phase 5 共用 memory、skill、context 构建逻辑，避免两条运行链逐渐漂移。

### 12.3 Phase 5 执行顺序

1. 生成或复用 `session_id`。
2. 合成 effective settings。
3. `_prepare_runtime(...)`。
4. 构造 current `CacheSnapshot`。
5. 从 CacheStore 读取 compatible baseline。
6. 生成 local `CacheReport`。
7. 如果 `execute_model=False`：
   - 不调用 DeepSeek。
   - 不写 session summary。
   - 不保存 cache snapshot。
   - 返回 `provider_usage_status="dry_run"`。
8. 调用 DeepSeek。
9. 将 `ChatUsage` 转换为 `ProviderCacheUsage`。
10. 合并 actual ratio。
11. 保存 snapshot/report。
12. 写入 session summary。
13. 返回 `RuntimeResult`。

### 12.4 API 调用失败

如果 `DeepSeekClient.complete` 抛错：

- 不保存 cache snapshot。
- 不写 session summary。
- 保持 Phase 4 的异常类型和 CLI 退出行为。

### 12.5 Cache 保存失败

如果模型成功但 CacheStore 保存失败：

- `cache_report.persistence_status = "failed"`
- `llm_report.status` 仍为 `ok`
- session summary 仍继续写入
- CLI 显示 cache persistence warning

## 13. CLI 设计

### 13.1 ctxforge run

`ctxforge run` 默认切换到 `run_phase5`。

现有 `--no-model` 改为：

```text
run_phase5(..., execute_model=False)
```

而不是回退到 `run_phase3`。这样离线模式仍能看到相对于最近真实基线的本地 cache diff。

Phase 5 Runtime Report 增加：

```text
cache_status
cache_baseline_scope
cache_baseline_snapshot_id
common_prefix_bytes
changed_after_byte
first_changed_section
stable_prefix_changed
estimated_cache_hit_ratio
actual_cache_hit_ratio
prompt_cache_hit_tokens
prompt_cache_miss_tokens
cache_persistence_status
```

比例统一显示为百分比，原始 dict 中保留 `0.0-1.0` float。

### 13.2 ctxforge inspect cache

命令：

```powershell
ctxforge inspect cache
ctxforge inspect cache -C E:\MyProgram\CtxForge
ctxforge inspect cache --session-id session-123
ctxforge inspect cache --limit 10
ctxforge inspect cache --json
```

默认行为：

- 查询当前 project 最近的 cache reports。
- 不重新构建 context。
- 不调用 DeepSeek。
- 不打印原始 prompt。

表格字段：

```text
created_at
session_id
model
status
baseline_scope
common_prefix_bytes
first_changed_section
estimated_hit_ratio
actual_hit_ratio
hit_tokens
miss_tokens
```

如果没有快照：

```text
No cache snapshots found for this project.
```

`--json` 输出结构化 report，便于后续脚本和 Phase 6 TUI 消费。

### 13.3 Section 详情

可增加：

```text
--show-sections
```

显示：

- direct changes。
- invalidated sections。
- change type。
- stability。

第一版可以默认只显示 summary，`--show-sections` 再展开详情，避免普通输出过长。

## 14. 配置

新增：

```python
class CacheSettings(BaseModel):
    enabled: bool = True
    snapshot_retention: int = Field(default=20, ge=1, le=1000)
    allow_project_fallback: bool = True


class CtxForgeSettings(BaseModel):
    ...
    cache: CacheSettings = Field(default_factory=CacheSettings)
```

项目配置示例：

```toml
[cache]
enabled = true
snapshot_retention = 20
allow_project_fallback = true
```

第一版不需要新增第三方依赖。

当 `cache.enabled=false`：

- 不初始化 cache 表。
- 不读取或保存 snapshot。
- `cache_report.status="disabled"`。
- DeepSeek 调用和 session summary 不受影响。

## 15. 报告示例

### 15.1 本地变化且 provider 返回 usage

```json
{
  "status": "changed",
  "snapshot_id": "cache-abc123",
  "baseline_snapshot_id": "cache-def456",
  "baseline_scope": "session",
  "same_session": true,
  "prompt_bytes": 18240,
  "common_prefix_bytes": 14608,
  "changed_after_byte": 14608,
  "common_prefix_estimated_tokens": 3652,
  "total_estimated_tokens": 4560,
  "estimated_cache_hit_ratio": 0.8009,
  "actual_cache_hit_ratio": 0.7421,
  "prompt_cache_hit_tokens": 3384,
  "prompt_cache_miss_tokens": 1176,
  "provider_usage_status": "observed",
  "first_changed_section": "session.summary",
  "direct_changes": [
    {
      "name": "session.summary",
      "change_type": "changed"
    },
    {
      "name": "request.task",
      "change_type": "changed"
    }
  ],
  "invalidated_sections": [
    "session.summary",
    "request.task"
  ],
  "stable_prefix_changed": false,
  "persistence_status": "saved"
}
```

### 15.2 第一轮

```json
{
  "status": "no_baseline",
  "baseline_snapshot_id": null,
  "estimated_cache_hit_ratio": null,
  "actual_cache_hit_ratio": 0.0,
  "provider_usage_status": "observed",
  "first_changed_section": null,
  "stable_prefix_changed": null
}
```

第一轮本地 estimate 为 `None`。如果 provider 返回 0 hit，则 actual ratio 才是 `0.0`。

## 16. 错误与降级

### 16.1 Provider 未返回 cache usage

```text
provider_usage_status = not_returned
actual_cache_hit_ratio = None
```

本地 diff 和 snapshot 仍然有效。

### 16.2 Usage 不一致

如果三个字段都存在，但：

```text
prompt_tokens != hit_tokens + miss_tokens
```

报告：

```text
provider_usage_status = inconsistent
```

actual ratio 仍按 `hit / (hit + miss)` 计算，同时保留原始值，不能静默修正 provider 数据。

### 16.3 Snapshot format 不兼容

旧 snapshot 的 `format_version` 不匹配：

```text
status = incomparable
reason = snapshot_format_changed
```

当前成功请求仍保存为新格式基线。

### 16.4 数据库损坏或 JSON 解析失败

- 单条坏 snapshot 不应阻止查找更早的可用基线。
- 无法解析的记录记 warning 并跳过。
- 当前模型调用不依赖 cache DB 可用性。

## 17. 测试覆盖

### 17.1 Analyzer 单元测试

- 完全相同 prompt：
  - `status=identical`
  - ratio 为 1.0
  - 无 invalidated sections
- task 改变：
  - stable prefix 不变
  - 首个变化 section 是 `request.task`
- session summary 改变：
  - 首个变化 section 是 `session.summary`
  - 后续 task 被连带 invalidated
- skill manifest 改变：
  - `stable_prefix_changed=True`
- skill instructions 改变：
  - stable prefix 不变
  - 首个变化 section 属于 semi-stable
- section 新增、删除、重排。
- 重复 section name 不丢 fingerprint。
- 中文多字节字符附近的 byte diff。
- 变化发生在 section 分隔符。
- 无 baseline 时 estimate 为 `None`。
- provider usage 缺失、正常和不一致。

### 17.2 Store 测试

- schema 可重复初始化。
- 保存后可读取完整 snapshot。
- 优先选择同 session 基线。
- 无同 session 时按配置 project fallback。
- model/base URL/format 不同不混用。
- retention 只保留最新 N 条。
- history 查询按时间倒序。
- 损坏 JSON 记录被跳过并继续查询。
- prompt bytes、section span/hash 或 report id 损坏的记录被拒绝或跳过。

### 17.3 Runtime 测试

- Phase 5 第一轮返回 `no_baseline` 并保存 snapshot。
- 第二轮返回 byte diff 和 section changes。
- DeepSeek usage 进入 actual ratio。
- 模型失败不保存 snapshot、不写 summary。
- `execute_model=False` 不保存 snapshot、不写 summary。
- cache 保存失败仍返回模型 answer。
- memory/summary 变化不改变 stable prefix hash。
- model override 使用独立 baseline。

### 17.4 CLI 测试

- `ctxforge run --no-model` 展示 local cache report。
- `ctxforge run` 展示 estimated 和 actual ratio。
- `ctxforge inspect cache` 展示历史。
- `--session-id` 正确过滤。
- `--json` 可被 `json.loads`。
- 无 snapshot 时提示清晰且退出码为 0。

验证命令：

```powershell
.\.venv\Scripts\python -m pytest -p no:cacheprovider
```

可选真实 API smoke test：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
.\.venv\Scripts\python -m ctxforge run "Summarize the project." --session-id cache-smoke
.\.venv\Scripts\python -m ctxforge run "Summarize the project briefly." --session-id cache-smoke
.\.venv\Scripts\python -m ctxforge inspect cache --session-id cache-smoke
```

真实 smoke test 只验证字段存在和命令链路，不断言具体 hit ratio，因为服务端 cache 状态不确定。

## 18. 实现顺序

建议按以下顺序编码：

1. `cache/models.py`：定义 snapshot、section fingerprint、usage 和 report。
2. `context/render.py`：增加共用的 rendered span 输出。
3. `cache/snapshot.py`：从 `BuiltContext` 构造完整快照。
4. `cache/analyzer.py`：实现 byte diff、section diff 和 ratio。
5. `tests/test_cache_analyzer.py`：先锁定纯算法行为。
6. `cache/store.py`：实现 SQLite schema、baseline 查询和 retention。
7. `tests/test_cache_store.py`：覆盖持久化和筛选规则。
8. `config/settings.py`：增加 `CacheSettings`。
9. `runtime/agent.py`：
   - 抽取 `_prepare_runtime`
   - 新增 `run_phase5`
   - 合并 provider usage
10. `tests/test_phase5_runtime.py`：覆盖成功、失败、dry-run 和降级。
11. `cli/app.py`：
   - `ctxforge run` 切到 Phase 5
   - 新增 `ctxforge inspect cache`
12. `tests/test_cli.py`：覆盖表格和 JSON 输出。
13. 更新根 `README.md` 的当前 phase 和命令示例。
14. 执行完整 pytest 和可选真实 API smoke test。

## 19. 非目标

Phase 5 不做：

- 控制 DeepSeek 服务端 KV cache。
- 读取服务端 cache key 或 cache 生命周期。
- 精确费用和账单计算。
- 在不同 provider 之间统一 cache 指标。
- 为所有历史请求搜索最佳匹配前缀。
- 保存完整 response transcript。
- Streaming token 级实时 cache 面板。
- Textual TUI。
- 分布式 tracing 或远程 observability backend。

## 20. 验收标准

Phase 5 完成时应满足：

- `ctxforge run` 每次都返回结构化 `cache_report`。
- 第一轮无基线时不会伪造 0% 本地命中。
- 后续请求能报告共同前缀 byte、首个变化 section 和 invalidated sections。
- task、memory、summary 变化不会误报 stable prefix 变化。
- skill manifest 或 runtime protocol 变化能被识别为 stable prefix 变化。
- DeepSeek 返回 cache usage 时，报告能计算 actual ratio。
- DeepSeek 不返回 cache usage 时，本地 analyzer 仍可工作。
- `ctxforge inspect cache` 能读取历史报告且不显示原始 prompt。
- model/API 失败不会污染未来基线。
- cache 持久化失败不会丢失已经成功生成的模型回答。
- 快照 retention 生效，SQLite 不会无限保存完整 prompt。
- 不新增第三方依赖。
- 完整测试通过。

## 21. 当前实现结果

Phase 5 已按本方案落地：

```text
src/ctxforge/cache/__init__.py
src/ctxforge/cache/models.py
src/ctxforge/cache/snapshot.py
src/ctxforge/cache/analyzer.py
src/ctxforge/cache/store.py
tests/test_cache_analyzer.py
tests/test_cache_store.py
tests/test_phase5_runtime.py
```

同时完成：

- `context/render.py` 提供统一 prompt 渲染和 UTF-8 byte span。
- `config/settings.py` 增加 `CacheSettings`。
- `runtime/agent.py` 增加 `run_phase5` 并抽取 Phase 4/5 共用准备流程。
- `ctxforge run` 默认进入 Phase 5。
- `ctxforge inspect cache` 支持表格、JSON 和 section 变化详情。
- `--no-model` 执行本地 cache diff，但不保存 snapshot 或 session summary。
- cache 保存失败不会丢失成功的模型回答。
- README 已同步到 Phase 5。

当前验证结果：

```text
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
53 passed
```

CLI dry-run 和 `inspect cache` 冒烟链路已通过。真实 DeepSeek cache usage smoke test 仍需有效
`DEEPSEEK_API_KEY`、网络和账户额度，因此不进入默认测试套件。
