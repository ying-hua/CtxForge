# Phase 6: Textual TUI Demo 方案设计

## 1. 目标

Phase 6 的目标是为 Phase 1-5 已经完成的 Context、Memory、Skill、DeepSeek Runtime 和
Prefix Cache Observatory 增加一个长生命周期、可交互、可流式展示的终端界面。

本阶段完成：

- 新增 `ctxforge tui` 入口。
- 在同一 TUI session 中连续提交多个任务。
- 使用 Textual 展示：
  - 上下文组成面板。
  - memory 命中面板。
  - cache report 面板。
  - streaming response 面板。
- 接入 DeepSeek SSE streaming，让回答在请求完成前持续显示。
- 在 runtime 层建立与 UI 框架无关的事件协议。
- 支持运行中取消，并明确 partial response、cache snapshot 和 session summary 的处理规则。
- 保持现有 `ctxforge run` 和 `ctxforge inspect ...` CLI 行为兼容。
- 使用 Textual headless test 和 fake streaming client 完成自动化验证。

Phase 6 仍然是 Context Engineering Runtime 的可视化 Demo，不升级为完整 coding agent。

## 2. 当前实现基础与缺口

### 2.1 已有能力

当前 Phase 5 已经提供：

- `RuntimeRequest` 和 `RuntimeResult`。
- `_prepare_runtime(...)`，统一准备：
  - session summary。
  - memory retrieval。
  - skill discovery/activation。
  - deterministic context。
- `run_phase5(...)`，统一完成：
  - cache snapshot 构造。
  - baseline 查询和本地 cache 分析。
  - DeepSeek 调用。
  - provider cache usage 合并。
  - cache snapshot/report 持久化。
  - session summary 写入。
- 四类可直接消费的结构化报告：
  - `context_report`
  - `memory_report`
  - `skill_report`
  - `cache_report`
- 同步 `DeepSeekClient.complete(...)`。
- `ctxforge inspect context` 和 `ctxforge inspect cache`。

这意味着 Phase 6 不需要重写 Context Builder、MemoryStore、SkillManager 或 CacheAnalyzer。

### 2.2 当前接口不能直接支持 TUI streaming

`run_phase5(...)` 是一次性同步函数：

```text
prepare -> call model -> persist -> return RuntimeResult
```

调用者只能在全部工作完成后拿到结果，无法在以下时点更新 UI：

- context、memory、cache 本地分析已经完成。
- HTTP 连接已经建立。
- 收到一个 response delta。
- provider usage 已返回。
- cache snapshot 或 summary 正在持久化。
- 用户取消了请求。

Phase 6 的核心缺口不是“缺少四个 Widget”，而是 runtime 没有中间状态和事件边界。

### 2.3 TUI 不能直接编排底层模块

根据主架构约束：

- TUI 不直接拼 prompt。
- TUI 不直接查询或写入 memory。
- TUI 不直接构造 cache snapshot。
- TUI 不直接解析 DeepSeek SSE。

否则 CLI 和 TUI 会形成两套编排逻辑，后续 cache、summary 和错误处理行为必然漂移。

## 3. 技术选型

### 3.1 TUI 框架：Textual

选择 Textual，原因：

- Python 原生，与当前 Typer、Rich 和 dataclass/Pydantic 模型兼容。
- 原生支持 async event loop、worker、message、reactive state 和 CSS layout。
- 提供 `DataTable`、`Input`、`Static`、`Header`、`Footer`、滚动容器等组件。
- 可通过 `App.run_test()` 和 `Pilot` 做无真实终端的交互测试。
- 不需要引入 Node.js、React 或 Ink。

截至 2026-06-11，PyPI 上 Textual 最新版本为 `8.2.7`。Phase 6 建议约束：

```toml
textual>=8.2,<9
```

限制主版本上界，避免未来 Textual 9 的潜在破坏性变化直接影响 TUI。

### 3.2 依赖保持可选

Textual 只服务于 `ctxforge tui`，不应让普通 CLI 命令强制加载 TUI 依赖。

建议：

```toml
[project.optional-dependencies]
tui = [
  "textual>=8.2,<9",
]

dev = [
  "pytest>=8.2",
  "pytest-asyncio>=1.4,<2",
  "textual>=8.2,<9",
]
```

行为：

- `ctxforge run`、`ctxforge memory ...`、`ctxforge inspect ...` 不 import Textual。
- `ctxforge tui` 在命令函数内部 lazy import `ctxforge.tui.app`。
- 未安装 TUI extra 时，输出明确提示：

```text
Textual is required for `ctxforge tui`.
Install it with: python -m pip install -e ".[tui]"
```

不默认引入 `textual-dev` 或视觉快照插件。只有实际需要开发控制台或 screenshot
regression 时再增加。

### 3.3 异步策略

选择：

- 保留现有同步 `DeepSeekClient.complete(...)`，保证 Phase 4/5 CLI 兼容。
- 新增 async streaming 接口，使用 `httpx.AsyncClient.stream(...)`。
- 新增 `pytest-asyncio`，因为 Phase 6 首次真正需要测试 async generator 和 TUI worker。
- 不引入 `aiosqlite`。

SQLite、skill discovery 和 context build 仍使用现有同步实现。Phase 6 runtime 在必要时通过
`asyncio.to_thread(...)` 执行短时本地 I/O，避免阻塞 Textual event loop。

## 4. 设计原则

### 4.1 Runtime event 是主接口，Textual message 只是适配层

Phase 6 应先定义与 Textual 无关的 runtime event：

```text
RuntimeEvent
  -> Textual Message
      -> App state
          -> Widget render
```

这样未来 headless API、WebSocket UI 或 JSONL trace 也可以消费同一事件流。

### 4.2 CLI 和 TUI 共用准备与成功收尾逻辑

不能复制一份 `run_phase5(...)` 到 TUI。

应抽取并复用以下阶段：

```text
prepare
  -> memory
  -> skills
  -> context
  -> cache local analysis

finalize success
  -> provider usage
  -> cache persistence
  -> session summary
  -> RuntimeResult
```

同步 CLI 和异步 TUI 的差异只存在于“模型响应如何产生”：

- CLI：一次性 `complete(...)`。
- TUI：持续 `stream(...)`。

### 4.3 UI 是单向数据流

Widget 不相互读写状态。

```text
User input
  -> App starts worker
  -> worker consumes runtime events
  -> App handles event
  -> App updates session state
  -> affected widgets render
```

所有状态更新由 App/controller 单点处理，避免 ContextPanel、MemoryPanel、ResponsePanel
之间形成隐式依赖。

### 4.4 每个 TUI session 同时只允许一个 active run

Phase 6 不做并发多任务。

提交任务后：

- 禁用 Submit。
- 保留 Cancel。
- active run 完成、失败或取消后再允许下一次提交。

这样 session summary、cache baseline 和 response ordering 都保持确定性。

### 4.5 不按 token 频率重绘整棵 UI

模型 delta 可能非常碎。

ResponsePanel 应：

- 立即缓冲 delta。
- 以约 30-50ms 的节流周期更新可见文本。
- 只刷新 response widget，不重绘 context/memory/cache table。
- 完成后再执行最终 Markdown 渲染；流式阶段优先稳定的纯文本显示。

### 4.6 可观测性失败不丢回答

沿用 Phase 5 的 cache 降级规则，并补齐 summary 写入失败的展示语义：

- cache baseline 读取失败：继续模型调用。
- cache snapshot 保存失败：保留完整回答和 summary，显示 warning。
- memory summary 写入失败：回答仍显示，但 final status 明确标记失败。

UI 不得把“模型成功、观测数据保存失败”渲染成整个请求失败。

## 5. 总体架构

```text
ctxforge tui
  -> CtxForgeTuiApp
      -> TuiSessionState
      -> Textual worker
          -> stream_phase6(RuntimeRequest)
              -> prepare_runtime_run
                  -> MemoryManager
                  -> SkillManager
                  -> ContextBuilder
                  -> CacheAnalyzer
              -> AsyncDeepSeek streaming
              -> finalize_runtime_success
                  -> attach provider usage
                  -> CacheStore
                  -> SessionSummarizer / MemoryStore
              -> RuntimeEvent stream
          -> App event adapter
              -> ContextPanel
              -> MemoryPanel
              -> CachePanel
              -> ResponsePanel
```

模块依赖方向：

```text
tui -> runtime -> context/memory/skills/cache/llm
```

禁止：

```text
runtime -> tui
context/memory/cache -> textual
```

## 6. 建议目录结构

新增：

```text
src/ctxforge/
  runtime/
    events.py
    stream.py
  tui/
    __init__.py
    app.py
    state.py
    messages.py
    widgets/
      __init__.py
      context_panel.py
      memory_panel.py
      cache_panel.py
      response_panel.py
    styles.tcss
```

调整：

```text
src/ctxforge/
  llm/
    models.py
    deepseek.py
  runtime/
    agent.py
  cli/
    app.py
  config/
    settings.py
```

新增测试：

```text
tests/
  test_deepseek_stream.py
  test_phase6_runtime.py
  test_tui.py
```

不建议现在增加独立 Redux 风格 store、DI container 或完整 screen router。Phase 6 只有一个主
screen，Textual App 加一个小型 `TuiSessionState` 足够。

## 7. LLM Streaming 接口

### 7.1 数据模型

在 `llm/models.py` 增加：

```python
@dataclass(frozen=True)
class ChatStreamChunk:
    content_delta: str = ""
    model: str | None = None
    request_id: str | None = None
    finish_reason: str | None = None
    usage: ChatUsage | None = None
```

Phase 6 只把 assistant `content` 作为可见回答流。

不新增“隐藏推理面板”，也不把 provider 的 reasoning 字段写入日志或 session summary。

### 7.2 Client 协议

```python
class StreamingChatClient(Protocol):
    def stream(
        self,
        request: ChatCompletionRequest,
    ) -> AsyncIterator[ChatStreamChunk]:
        ...
```

`DeepSeekClient` 增加 async `stream(...)`，现有 `complete(...)` 保持不变。

公共逻辑应抽取为私有 helper：

- request URL。
- headers。
- request payload。
- usage parsing。
- error text 截断。
- response metadata。

避免同步和异步实现对 model、max_tokens、Authorization 或 usage 字段产生不同语义。

### 7.3 SSE 解析

请求必须使用：

```json
{
  "stream": true,
  "stream_options": {
    "include_usage": true
  }
}
```

解析规则：

1. 使用 `httpx.AsyncClient.stream("POST", ...)`。
2. 通过 `response.aiter_lines()` 消费 SSE。
3. 忽略空行和 `:` 开头的 comment/keepalive。
4. 只解析 `data:` payload。
5. 遇到 `[DONE]` 正常结束。
6. `choices` 非空时，从 `choices[0].delta.content` 产生 `content_delta`。
7. `choices` 非空时，从终止 chunk 读取 `finish_reason`。
8. 允许 `choices=[]` 的 usage-only chunk；这是 `include_usage=true` 的正常协议。
9. usage 存在时复用当前 `_parse_usage(...)`。
10. 除 usage-only chunk 外，JSON 或结构损坏时抛 `DeepSeekResponseError`。

### 7.4 Streaming 重试边界

Streaming 不能简单复用当前“失败就整次重试”的策略。

规则：

- 在收到第一个有效 chunk 之前发生连接错误：可按 `max_retries` 重试。
- 已经向调用者发出任何 delta 后发生错误：不自动重试。

原因：

- 重试会重新生成开头内容，调用方无法可靠判断重复边界。
- provider 可能已经产生计费和 cache side effect。
- 自动拼接两次 stream 容易生成错误答案。

部分回答由 runtime 保留，最终事件标记为失败，用户可以手动重新提交。

## 8. Runtime Event 协议

### 8.1 Event 类型

在 `runtime/events.py` 定义窄事件集合：

```python
@dataclass(frozen=True)
class RuntimeEvent:
    run_id: str
    session_id: str
    sequence: int


@dataclass(frozen=True)
class RunStarted(RuntimeEvent):
    task: str
    model: str


@dataclass(frozen=True)
class RuntimePrepared(RuntimeEvent):
    context_report: dict[str, object]
    memory_report: dict[str, object]
    skill_report: dict[str, object]
    cache_report: dict[str, object]


@dataclass(frozen=True)
class ResponseDelta(RuntimeEvent):
    text: str


@dataclass(frozen=True)
class RunCompleted(RuntimeEvent):
    result: RuntimeResult


@dataclass(frozen=True)
class RunFailed(RuntimeEvent):
    error_code: str
    message: str
    retryable: bool
    partial_answer: str


@dataclass(frozen=True)
class RunCancelled(RuntimeEvent):
    partial_answer: str
```

### 8.2 Event 顺序

成功路径：

```text
RunStarted
RuntimePrepared
ResponseDelta*
RunCompleted
```

失败路径：

```text
RunStarted
RuntimePrepared?
ResponseDelta*
RunFailed
```

取消路径：

```text
RunStarted
RuntimePrepared?
ResponseDelta*
RunCancelled
```

约束：

- `sequence` 在单次 run 内从 0 单调递增。
- 终止事件只能出现一次。
- 终止事件后不再产生 delta。
- `RunCompleted.result.answer` 必须等于所有 `ResponseDelta.text` 的拼接结果。

### 8.3 为什么 Prepared 单独发出

Context、memory 和本地 cache analysis 在模型第一个 token 前已经完成。

先发 `RuntimePrepared` 可以让用户在等待网络时立即看到：

- 实际注入了哪些 section。
- memory 命中了什么。
- cache 预计从哪里失效。
- 哪些 skill 被激活。

这正是 CtxForge TUI 与普通聊天 TUI 的差异。

## 9. Phase 6 Runtime

### 9.1 新入口

```python
async def stream_phase6(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    client: StreamingChatClient | None = None,
    execute_model: bool = True,
    cache_store: CacheStore | None = None,
) -> AsyncIterator[RuntimeEvent]:
    ...
```

### 9.2 准备阶段

建议把 Phase 5 逻辑继续拆为：

```python
@dataclass(frozen=True)
class PreparedRun:
    prepared_runtime: PreparedRuntime
    cache_snapshot: CacheSnapshot
    cache_report: CacheReport
    model: str


def prepare_runtime_run(...) -> PreparedRun:
    ...
```

`run_phase5(...)` 和 `stream_phase6(...)` 共用它。

准备完成后立即产生 `RuntimePrepared`，其中不包含：

- 原始 API key。
- 完整 rendered prompt。
- cache snapshot 的 `prompt_bytes`。

### 9.3 Streaming 阶段

Runtime：

1. 构造 `ChatCompletionRequest(stream=True)`。
2. 消费 `client.stream(...)`。
3. 按顺序累积 answer。
4. 每个非空 content delta 产生 `ResponseDelta`。
5. 保存最后观察到的：
   - model
   - request id
   - finish reason
   - usage

### 9.4 成功收尾

只有正常收到 stream 终止后，才执行：

1. 构造 `ChatCompletionResult`。
2. 将 provider cache usage 合并到 cache report。
3. 保存 cache snapshot/report。
4. 写入 session summary。
5. 构造最终 `RuntimeResult`。
6. 产生 `RunCompleted`。

成功收尾逻辑建议抽取：

```python
def finalize_runtime_success(
    prepared: PreparedRun,
    completion: ChatCompletionResult,
    *,
    request: RuntimeRequest,
    settings: CtxForgeSettings,
) -> RuntimeResult:
    ...
```

同步 `run_phase5(...)` 也调用这个函数，避免 summary source、cache error handling 和 report
字段分叉。

### 9.5 Dry-run

`execute_model=False` 时：

```text
RunStarted
RuntimePrepared
RunCompleted(dry-run RuntimeResult)
```

规则与 Phase 5 一致：

- 不调用 DeepSeek。
- 不保存 cache snapshot。
- 不写 session summary。
- response panel 显示 dry-run 说明。

这条路径用于：

- 无 API key 的 UI 演示。
- TUI layout 测试。
- 本地 context/memory/cache 检查。

## 10. 取消与错误语义

### 10.1 用户取消

用户按 `Esc` 或点击 Cancel：

1. Textual worker 被取消。
2. async generator 收到 `CancelledError`。
3. HTTP stream context 被关闭。
4. `stream_phase6(...)` 完成清理后重新抛出 `CancelledError`。
5. Textual worker adapter 根据 App 中已累积的 answer 发布 `RunCancelled`。
6. response panel 保留 partial answer，并追加 canceled 状态。

取消后不执行：

- cache snapshot 持久化。
- session summary 写入。

原因：响应不完整，provider usage 也可能缺失，不能把 partial response 当成成功基线。

### 10.2 App 退出

退出 TUI 时：

- active worker 必须被取消。
- 不等待模型继续在后台运行。
- 不启动 detached process。

Textual worker 生命周期应跟随 App，避免退出后仍持有 HTTP 连接。

### 10.3 错误分类

TUI 至少区分：

```text
missing_api_key
authentication_error
rate_limited
provider_error
network_error
invalid_response
local_prepare_error
local_persistence_warning
cancelled
```

Runtime event 只携带稳定的 `error_code` 和适合用户展示的短消息，不把完整 traceback 或响应
body 放进 UI。

详细异常写入标准日志。

## 11. TUI Session State

在 `tui/state.py` 定义：

```python
TuiRunPhase = Literal[
    "idle",
    "preparing",
    "streaming",
    "finalizing",
    "completed",
    "failed",
    "cancelled",
]


@dataclass
class TuiSessionState:
    session_id: str
    model: str
    active_run_id: str | None = None
    phase: TuiRunPhase = "idle"
    current_task: str = ""
    answer: str = ""
    context_report: dict[str, object] = field(default_factory=dict)
    memory_report: dict[str, object] = field(default_factory=dict)
    skill_report: dict[str, object] = field(default_factory=dict)
    cache_report: dict[str, object] = field(default_factory=dict)
    llm_report: dict[str, object] = field(default_factory=dict)
    warning: str | None = None
    error: str | None = None
```

同一个 App 生命周期默认复用同一个 `session_id`。

因此连续两轮任务会自然展示：

- 上一轮 session summary 进入下一轮 memory。
- 同 session cache snapshot 优先成为下一轮 baseline。

ResponsePanel 可以在内存中保留本次 App 的可见 turn history，但 Phase 6 不新增 transcript
数据库。UI 历史只是展示，不等同于发送给模型的完整多轮对话。

## 12. 界面布局

### 12.1 宽屏布局

建议第一屏：

```text
┌ CtxForge | session | model | phase | elapsed ───────────────────────┐
│ ┌ Context ─────────────────┐ ┌ Streaming Response ────────────────┐ │
│ │ token budget             │ │ user task                          │ │
│ │ stable/semi/dynamic      │ │                                   │ │
│ │ section table            │ │ streamed assistant response       │ │
│ ├ Memory ──────────────────┤ │                                   │ │
│ │ counts + hit table       │ │                                   │ │
│ ├ Cache ───────────────────┤ │                                   │ │
│ │ estimate/actual/change   │ │                                   │ │
│ └──────────────────────────┘ └────────────────────────────────────┘ │
│ [ Task input ........................................ ] [Run][Stop] │
└─────────────────────────────────────────────────────────────────────┘
```

建议比例：

- 左侧 observability：40%。
- 右侧 response：60%。
- 左侧三个面板垂直排列并各自支持滚动。

### 12.2 窄屏布局

当终端宽度不足时：

- observability 区域切为 Context / Memory / Cache tabs。
- Response 保持主要区域。
- 输入栏始终可见。

第一版至少保证：

- `120x40` 完整布局可用。
- `80x24` 不崩溃、输入可达、面板可切换、内容可滚动。

### 12.3 Header 和状态栏

显示：

- project name。
- session id 短形式。
- model。
- current phase。
- elapsed time。
- cache persistence warning 或 provider error。

不显示：

- API key。
- 完整 base URL query。
- 原始 prompt。

## 13. 面板设计

### 13.1 ContextPanel

数据源：`context_report` 和 `skill_report`。

摘要字段：

```text
status
total_estimated_tokens / input_budget
stable_prefix_tokens
semi_stable_tokens
dynamic_tokens
stable_prefix_bytes
overflow
selected_skills
```

section table：

```text
name | stability | tokens | source | flags
```

flags：

```text
required
truncated
dropped
```

默认不显示完整 section 内容和 rendered prompt，防止 TUI 第一屏暴露大段 memory/skill 内容。

### 13.2 MemoryPanel

数据源：`memory_report`。

摘要字段：

```text
status
retrieved_count
working_count
summary_count
long_term_count
```

hit table：

```text
score | scope | kind | source | reason | content preview
```

展示层对 content 做宽度截断，但不修改 runtime report。

### 13.3 CachePanel

数据源：准备阶段和完成阶段的 `cache_report`。

准备阶段显示：

```text
status
baseline_scope
common_prefix_bytes
first_changed_section
stable_prefix_changed
estimated_cache_hit_ratio
direct_changes
invalidated_sections
```

完成阶段追加：

```text
actual_cache_hit_ratio
prompt_cache_hit_tokens
prompt_cache_miss_tokens
provider_usage_status
persistence_status
```

estimated 和 actual 必须保持并列，不能合并成单一 cache ratio。

### 13.4 ResponsePanel

显示：

- 当前 user task。
- assistant response delta。
- final model/request id/finish reason/usage。
- canceled、failed 或 persistence warning。

实现建议：

- 流式阶段使用自定义 `StreamingResponse` + `Static`/scroll container。
- delta 先进入 buffer，再节流刷新。
- 完成后可切换为 Markdown 渲染。
- 不使用每个 delta 一行的 `RichLog`，避免把 token fragment 错误渲染成大量独立行。

## 14. Textual Worker 与 Message 适配

### 14.1 Worker

App 中使用 exclusive async worker：

```python
@work(exclusive=True, exit_on_error=False)
async def run_task(self, request: RuntimeRequest) -> None:
    async for event in stream_phase6(request, self.settings):
        self.post_message(RuntimeEventMessage(event))
```

`exclusive=True` 是最后一道并发保护；UI 仍应在 active run 期间禁用再次提交。

### 14.2 Message adapter

`tui/messages.py` 只定义一个轻量包装：

```python
class RuntimeEventMessage(Message):
    def __init__(self, event: RuntimeEvent) -> None:
        self.event = event
        super().__init__()
```

Textual-specific Message 不进入 runtime 模块。

### 14.3 App handler

App 根据事件类型：

- 更新 `TuiSessionState`。
- 只调用受影响 panel 的 `render_report(...)`。
- `ResponseDelta` 只追加 response buffer。
- 终止事件恢复输入焦点和 Run 按钮。

## 15. CLI 入口

新增：

```powershell
ctxforge tui
ctxforge tui -C E:\MyProgram\CtxForge
ctxforge tui --session-id session-123
ctxforge tui --model deepseek-v4-flash
ctxforge tui --skill code-review
ctxforge tui --no-model
```

建议选项：

```text
--project-dir / -C
--session-id
--model
--max-tokens
--max-output-tokens
--skill
--no-model
```

第一版不增加：

- `--resume-transcript`
- 多窗口。
- remote session。
- theme marketplace。

`ctxforge run` 继续调用同步 Phase 5 路径。Phase 6 不强制把普通 CLI 改成 streaming，以控制回归范围。

## 16. 配置

新增：

```python
class TuiSettings(BaseModel):
    response_refresh_ms: int = Field(default=40, ge=16, le=500)
    max_visible_turns: int = Field(default=20, ge=1, le=200)
    show_full_memory_content: bool = False


class CtxForgeSettings(BaseModel):
    ...
    tui: TuiSettings = Field(default_factory=TuiSettings)
```

项目配置：

```toml
[tui]
response_refresh_ms = 40
max_visible_turns = 20
show_full_memory_content = false
```

Phase 6 不持久化布局尺寸、选中行或 theme 偏好。这些是后续 UI 产品化能力。

## 17. 日志、隐私与持久化

### 17.1 日志

记录：

- run id/session id。
- phase transition。
- provider request id。
- error type。
- persistence warning。

不记录：

- API key。
- 完整 prompt。
- 每个 response delta。
- 完整 memory hit content。

### 17.2 TUI 新增的持久化

Phase 6 不新增 transcript 表。

仍然只使用已有：

- session summary。
- cache snapshot/report。
- long-term/working memory。

TUI 关闭后，可见 turn history 消失；长期状态由现有 summary 和 memory 机制承担。

### 17.3 Cache 数据边界

Phase 5 已经把完整 prompt bytes 保存到 SQLite 并执行 retention。

TUI：

- 不复制 prompt 到另一份 state file。
- 不在 panel 中显示原始 prompt。
- 不把 prompt 写入 Textual debug log。

## 18. 测试方案

### 18.1 DeepSeek SSE 单元测试

使用 `httpx.MockTransport` 和自定义 async byte stream 覆盖：

- 多个 content delta 正确拼接。
- `[DONE]` 正常结束。
- finish reason 解析。
- final usage 和 cache usage 解析。
- keepalive/comment 被忽略。
- malformed JSON 抛 `DeepSeekResponseError`。
- 首 chunk 前连接失败可重试。
- 首 chunk 后失败不自动重试。
- HTTP 4xx/5xx 错误映射保持现有语义。
- stream context 在取消时关闭。

### 18.2 Runtime event 测试

使用 fake streaming client 覆盖：

- 成功事件顺序固定。
- `RuntimePrepared` 在第一个 delta 前出现。
- completed answer 等于 delta 拼接。
- 成功后写 summary 和 cache snapshot。
- provider usage 进入最终 cache report。
- cache 保存失败仍产生 `RunCompleted`，并带 warning。
- 模型失败不写 summary 和 cache snapshot。
- 取消后保留 partial answer，但不写 summary/cache。
- dry-run 不调用 client。
- 两轮相同 session 使用 session baseline 和上一轮 summary。

### 18.3 Textual headless test

使用：

```python
async with app.run_test(size=(120, 40)) as pilot:
    ...
```

覆盖：

- App 可启动并定位 task input。
- 输入任务并提交后进入 preparing/streaming。
- Prepared event 更新 Context/Memory/Cache。
- Delta 在 ResponsePanel 可见。
- 完成后 Run 恢复可用。
- Esc/Cancel 会取消 worker。
- 错误消息可见且 App 不退出。
- `80x24` 窄屏下 panel 可访问。
- 连续两次提交复用同一 session id。

不依赖真实 API key，不在默认测试中访问网络。

### 18.4 回归测试

现有以下行为必须继续通过：

- `ctxforge run --no-model`
- `ctxforge inspect cache`
- memory/skill/config CLI。
- Phase 1-5 全部单元测试。

验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
```

可选真实 smoke test：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
.\.venv\Scripts\python.exe -m ctxforge tui -C E:\MyProgram\CtxForge
```

真实 smoke test 只验证：

- 首个 delta 能显示。
- 完成后 usage/cache report 更新。
- 第二轮复用 session。
- Cancel 能关闭活动 stream。

不对 chunk 数量、首 token 延迟或具体 cache ratio 做固定断言。

## 19. 实现顺序

建议按以下顺序编码：

1. `llm/models.py` 增加 `ChatStreamChunk`。
2. `llm/deepseek.py` 抽取同步/异步共用 request 和 usage helper。
3. 实现并测试 `DeepSeekClient.stream(...)`。
4. `runtime/events.py` 定义事件协议。
5. 从 `run_phase5(...)` 抽取 `prepare_runtime_run(...)`。
6. 从 `run_phase5(...)` 抽取 `finalize_runtime_success(...)`。
7. 确认 Phase 5 测试仍全部通过。
8. `runtime/stream.py` 实现 `stream_phase6(...)`。
9. `tests/test_phase6_runtime.py` 锁定事件顺序、取消和持久化规则。
10. `config/settings.py` 增加 `TuiSettings`。
11. `tui/state.py` 和 `tui/messages.py`。
12. 实现四个 panel widget。
13. `tui/app.py` 接入 worker、输入、取消和 layout。
14. `cli/app.py` 增加 lazy-loaded `ctxforge tui`。
15. `tests/test_tui.py` 增加 headless interaction test。
16. 更新 `pyproject.toml`、README 和 docs index。
17. 执行完整 pytest 和可选真实 API smoke test。

这个顺序先稳定 provider/runtime 协议，再写 UI，避免在 Textual event handler 中调试 SSE 和
持久化逻辑。

## 20. 非目标

Phase 6 不做：

- shell、文件编辑或任意工具执行。
- MCP client/server。
- 权限弹窗和 policy engine。
- 并发多任务或 subagent。
- 多 provider UI。
- 完整 transcript 数据库和跨进程 replay。
- 服务端 session。
- Web UI 或远程控制。
- prompt 编辑器。
- memory 的新增、删除、编辑界面。
- skill 安装界面。
- 隐藏推理过程展示。
- 精确 token streaming latency tracing。
- Textual theme/plugin marketplace。

这些能力会显著改变项目定位，不应借 Phase 6 TUI 一并引入。

## 21. 验收标准

Phase 6 完成时应满足：

- `ctxforge tui` 可以启动并提交任务。
- TUI 中同时存在 Context、Memory、Cache 和 Response 四类视图。
- Context、Memory 和本地 Cache 报告在模型首个 token 前可见。
- DeepSeek 回答以 delta 形式持续显示，而不是完成后一次性出现。
- 同一 App 中连续任务复用 session id。
- 下一轮能够看到上一轮 session summary，并优先使用同 session cache baseline。
- estimated cache ratio 和 actual cache ratio 分开显示。
- 用户可以取消 active stream。
- 取消和 stream 失败不会保存 cache snapshot 或 session summary。
- cache 持久化失败不会丢失成功回答。
- TUI 不直接访问 MemoryStore、CacheStore 或 DeepSeek HTTP 协议。
- 非 TUI CLI 不会因 Textual 未安装而失败。
- `120x40` 和 `80x24` headless TUI 测试通过。
- Phase 1-5 回归测试全部通过。

## 22. 当前实现结果

Phase 6 已按本方案落地：

```text
src/ctxforge/runtime/events.py
src/ctxforge/runtime/stream.py
src/ctxforge/tui/app.py
src/ctxforge/tui/state.py
src/ctxforge/tui/messages.py
src/ctxforge/tui/widgets/*
src/ctxforge/tui/styles.tcss
tests/test_deepseek_stream.py
tests/test_phase6_runtime.py
tests/test_tui.py
```

同时完成：

- `DeepSeekClient.stream(...)` 使用 `httpx.AsyncClient.stream(...)` 消费 SSE。
- 支持 content delta、finish reason、usage-only chunk 和 cache usage。
- stream 只在首个 chunk 前自动重试。
- Phase 5 已抽取共用 prepare、dry-run 和 finalize 逻辑。
- `stream_phase6(...)` 输出与 Textual 无关的 runtime events。
- 正常完成才保存 cache snapshot 和 session summary。
- 取消和 stream 失败保留 partial answer，但不写成功状态。
- `ctxforge tui` 使用 lazy import，普通 CLI 不直接加载 Textual。
- TUI 提供 Context、Memory、Cache、Response 四类视图。
- `120x40` 和 `80x24` headless 测试均通过。

当前验证结果：

```text
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
71 passed
```

`ctxforge tui --help` 和真实 runtime dry-run 的 headless 交互链路已通过。真实 DeepSeek
streaming smoke test 仍需有效 `DEEPSEEK_API_KEY`、网络和账户额度，因此不进入默认测试套件。

## 23. 官方参考

- Textual Workers:
  https://textual.textualize.io/guide/workers/
- Textual Testing:
  https://textual.textualize.io/guide/testing/
- Textual PyPI:
  https://pypi.org/project/textual/
- pytest-asyncio PyPI:
  https://pypi.org/project/pytest-asyncio/
- DeepSeek Chat Completions API:
  https://api-docs.deepseek.com/api/create-chat-completion
