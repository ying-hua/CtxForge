# Phase 4: DeepSeek Runtime 方案设计

## 1. 目标

Phase 4 的目标是把 Phase 3 的占位 runtime 升级为真实可调用的 DeepSeek Runtime。

本阶段完成：

- 封装 DeepSeek Chat Completions API。
- 将现有 Memory、Skill、Context Builder 串成一次端到端 `ctxforge run`。
- 记录模型响应、usage、request id、finish reason 和错误信息。
- 将 session summary 写入 SQLite，供下一轮作为 dynamic context 注入。
- 保留可测试的 mock 路径，不要求单元测试依赖真实 API key。
- 为 Phase 5 的真实 cache usage 接入预留字段。

Phase 4 不负责工具调用、流式 TUI、MCP、embedding、prefix cache diff 估算或自动长期记忆判定。这些属于后续 Phase 或更高层 Agent 能力。

## 2. 外部信息和 API Key

实现 Phase 4 前需要准备：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
```

可选配置：

```powershell
$env:CTXFORGE_DEEPSEEK_MODEL = "deepseek-v4-flash"
$env:CTXFORGE_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
```

当前官方文档要点：

- OpenAI-compatible base URL 是 `https://api.deepseek.com`。
- Chat endpoint 是 `POST /chat/completions`。
- 当前模型应使用 `deepseek-v4-flash` 或 `deepseek-v4-pro`。
- `deepseek-chat` 和 `deepseek-reasoner` 将在 2026-07-24 15:59 UTC 废弃，因此 Phase 4 应把默认模型改为 `deepseek-v4-flash`。
- API response 的 `usage` 可能包含 `prompt_cache_hit_tokens` 和 `prompt_cache_miss_tokens`，Phase 4 应保存这些字段，但真正的 cache 分析仍放到 Phase 5。

官方参考：

- https://api-docs.deepseek.com/
- https://api-docs.deepseek.com/api/create-chat-completion
- https://api-docs.deepseek.com/guides/kv_cache

## 3. 设计原则

### 3.1 Runtime 编排保持同步、可测

当前项目已经依赖 `httpx`，但还没有 async runtime。Phase 4 先实现同步 client：

```text
ctxforge run
  -> run_phase4
      -> MemoryManager.retrieve_for_context
      -> SkillManager.select_for_context
      -> ContextBuilder.build
      -> DeepSeekClient.complete
      -> SessionSummarizer.summarize
      -> MemoryStore.upsert_session_summary
      -> RuntimeResult
```

这样 CLI、测试和 Windows 环境都更稳定。Streaming 可以在 Phase 6 TUI 或实际需要时再加。

### 3.2 DeepSeek Client 只负责 API 协议

`DeepSeekClient` 不读取 memory，不选择 skill，不拼 prompt。它只负责：

- 校验 API key。
- 构造 HTTP request。
- 发送请求。
- 解析 answer、usage 和 metadata。
- 把 HTTP/API 错误转换为项目内异常。

业务编排仍放在 `runtime/agent.py`。

### 3.3 测试默认不打真实 API

Phase 4 的测试应通过 fake client 或 `httpx.MockTransport` 完成：

- 单元测试不需要 `DEEPSEEK_API_KEY`。
- 真实 API smoke test 只在显式提供 key 时运行。
- CI 或本地无 key 时不失败。

### 3.4 Session Summary 写入要保守

Phase 4 可以在每轮模型调用成功后写入 session summary，但不自动写长期记忆。

建议默认 summary 内容先用确定性本地摘要，避免每轮额外消耗一次模型调用：

```text
Task: <current task>
Answer: <first N chars of assistant answer>
Memory hits: <count>
Skills: <selected names>
```

后续如果要更高质量摘要，再增加可选的 `deepseek.summary_enabled`，用第二次低温模型调用生成摘要。

### 3.5 Stable Prefix 规则不改

Phase 4 只把真实模型响应接到当前 context 后面，不改变 Phase 1-3 的 section 稳定性规则。

必须保持：

- Memory sections 仍是 dynamic。
- 当前 task 仍是 dynamic。
- `SKILL.md` 正文仍是 semi-stable。
- 只有轻量 skill manifest 进入 stable prefix。

## 4. 模块设计

新增模块：

```text
src/ctxforge/llm/__init__.py
src/ctxforge/llm/deepseek.py
src/ctxforge/llm/models.py
src/ctxforge/llm/errors.py
src/ctxforge/runtime/summary.py
```

### 4.1 llm/models.py

核心模型：

```python
@dataclass(frozen=True)
class ChatUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class ChatCompletionRequest:
    model: str
    messages: list[dict[str, str]]
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool = False


@dataclass(frozen=True)
class ChatCompletionResult:
    answer: str
    model: str
    request_id: str | None
    finish_reason: str | None
    usage: ChatUsage
    raw_usage: dict[str, object]
```

### 4.2 llm/deepseek.py

建议接口：

```python
class DeepSeekClient:
    def __init__(self, settings: DeepSeekSettings, transport: httpx.BaseTransport | None = None) -> None:
        ...

    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        ...
```

请求规则：

- URL：`{base_url.rstrip("/")}/chat/completions`
- Header：
  - `Authorization: Bearer <api_key>`
  - `Content-Type: application/json`
- Body：
  - `model`
  - `messages`
  - `stream=false`
  - `max_tokens` 使用 `settings.context.reserved_output_tokens` 或 explicit override
  - `temperature` 默认先不传，让 provider 默认值生效

错误规则：

- 未配置 key：抛 `MissingDeepSeekApiKey`。
- HTTP status 非 2xx：抛 `DeepSeekAPIError`，保留 status code 和短错误文本。
- 响应结构缺失 `choices[0].message.content`：抛 `DeepSeekResponseError`。

### 4.3 runtime/summary.py

Phase 4 先实现本地确定性 summary：

```python
class SessionSummarizer:
    def summarize(
        self,
        *,
        task: str,
        answer: str,
        selected_skills: list[str],
        memory_report: dict[str, object],
        previous_summary: str | None,
    ) -> str:
        ...
```

规则：

- summary 写入 `session_summaries`，`source="runtime.phase4.local_summary"`。
- `turn_count` 从上一条 summary 递增。
- summary 只保存短摘要，不保存完整 prompt。
- 如果模型调用失败，不更新 summary，避免把失败状态伪装成有效会话状态。

## 5. Runtime 接入

新增入口：

```python
def run_phase4(
    request: RuntimeRequest,
    settings: CtxForgeSettings,
    *,
    client: ChatClient | None = None,
) -> RuntimeResult:
    ...
```

建议定义一个小协议，便于测试：

```python
class ChatClient(Protocol):
    def complete(self, request: ChatCompletionRequest) -> ChatCompletionResult:
        ...
```

执行顺序：

1. 生成或复用 `session_id`。
2. 根据 `request.max_tokens` 合成 effective settings。
3. 初始化 `MemoryStore`。
4. 读取上一轮 session summary 和当前检索 memory。
5. 选择并渲染 skill。
6. 调用 `ContextBuilder.build(...)`。
7. 将 `BuiltContext.messages` 发送给 `DeepSeekClient.complete(...)`。
8. 写入新的 session summary。
9. 返回真实 answer 和 reports。

`RuntimeResult` 建议增加：

```python
llm_report: dict[str, object]
```

`llm_report` 字段：

```text
status
provider
model
request_id
finish_reason
usage
prompt_cache_hit_tokens
prompt_cache_miss_tokens
error
```

兼容策略：

- `run_phase1/2/3` 继续返回 placeholder `llm_report` 或不变字段。
- 如果不想一次改动所有调用方，也可以先让 `RuntimeResult.llm_report` 带默认空 dict。

## 6. CLI 接入

`ctxforge run` 默认切到 Phase 4：

```powershell
ctxforge run "帮我总结这个项目的架构"
```

新增/调整选项：

```text
--model TEXT              覆盖当前 DeepSeek model
--no-model                只构建 context，不调用 DeepSeek，用于离线调试
--show-context            打印发送给模型的 prompt
--max-output-tokens INT   覆盖模型输出 token 上限
```

最小实现可以先只做：

- `ctxforge run` 默认真实调用。
- `--no-model` 走 Phase 3 dry-run，占位 answer，但仍展示 context/memory/skill reports。
- 无 API key 时给出明确错误：

```text
DEEPSEEK_API_KEY is required for Phase 4 model calls.
Set $env:DEEPSEEK_API_KEY="sk-..." or run with --no-model.
```

CLI report 从 `Phase 3 Runtime Report` 改为 `Phase 4 Runtime Report`，增加：

- `llm_status`
- `llm_request_id`
- `finish_reason`
- `prompt_tokens`
- `completion_tokens`
- `prompt_cache_hit_tokens`
- `prompt_cache_miss_tokens`
- `summary_written`

## 7. 配置调整

当前 `DeepSeekSettings` 已有：

```python
api_key: Optional[str] = None
base_url: str = "https://api.deepseek.com"
model: str = "deepseek-chat"
timeout_seconds: float = 60.0
```

Phase 4 应调整：

```python
model: str = "deepseek-v4-flash"
max_retries: int = 2
```

可选但建议暂缓：

```python
summary_enabled: bool = False
summary_model: str | None = None
```

暂缓原因：Phase 4 的第一目标是跑通端到端调用链。session summary 先用本地确定性摘要即可。

## 8. 错误处理

错误分层：

```text
CtxForgeError
  -> MissingDeepSeekApiKey
  -> DeepSeekAPIError
  -> DeepSeekResponseError
```

Runtime 行为：

- API key 缺失：CLI 退出码 1，提示配置方式。
- API 401/403：提示 key 或账户权限问题，不写 session summary。
- API 429：提示 rate limit，不写 session summary。
- API 5xx/timeout：提示可重试，不写 session summary。
- Context overflow：仍可调用模型，但 report 必须保留 truncation/drop 信息。

测试中不应断言完整错误文本，只断言错误类型和关键字段，避免 provider 文案变化导致测试脆弱。

## 9. 测试覆盖

新增测试：

```text
tests/test_deepseek_client.py
tests/test_phase4_runtime.py
```

重点：

- client 正确拼接 `/chat/completions`。
- client 带上 `Authorization: Bearer ...`。
- client 能解析 answer、finish_reason、usage、cache usage。
- 未配置 API key 时抛明确异常。
- 4xx/5xx 转成项目内异常。
- `run_phase4` 会调用 memory、skill、context、client，并返回真实 answer。
- `run_phase4` 成功后写入 session summary。
- 模型调用失败不写 summary。
- memory/summary 的动态变化不改变 stable prefix hash。
- CLI 无 key 时给出可执行提示。
- CLI `--no-model` 不需要 key。

验证命令：

```powershell
.\.venv\Scripts\python -m pytest -p no:cacheprovider
```

可选真实 API smoke test：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
.\.venv\Scripts\python -m ctxforge run "Say hello in one sentence."
```

这类测试不要放进默认 pytest，因为它依赖网络、额度和账户状态。

## 10. 实现顺序

建议按以下顺序编码：

1. `llm/models.py`：定义 request/result/usage 数据结构。
2. `llm/errors.py`：定义缺 key、HTTP、响应结构错误。
3. `llm/deepseek.py`：实现同步 `DeepSeekClient.complete`。
4. `runtime/summary.py`：实现本地 session summary。
5. `runtime/agent.py`：新增 `run_phase4`，保留 `run_phase3`。
6. `cli/app.py`：`ctxforge run` 切到 Phase 4，增加 `--no-model` 和模型错误提示。
7. `config/settings.py`：默认模型改为 `deepseek-v4-flash`，必要时增加 retry 配置。
8. `tests/test_deepseek_client.py`：用 mock transport 覆盖 HTTP 协议。
9. `tests/test_runtime.py` 或 `tests/test_phase4_runtime.py`：用 fake client 覆盖端到端 runtime。
10. `README.md`：补充 Phase 4 使用方式和 API key 配置。

## 11. 非目标

Phase 4 不做：

- Tool calls。
- Streaming CLI/TUI。
- MCP。
- DeepSeek embedding。
- sqlite-vec。
- 自动长期记忆提取和去重。
- Prefix cache diff analyzer。
- 真实 cache hit ratio 可视化。
- 多轮完整 transcript 存储。
- 任意 shell 或文件编辑工具执行。

这些能力会分别进入 Phase 5、Phase 6 或后续 Agent Layer。

## 12. 验收标准

Phase 4 完成时应满足：

- 无 `DEEPSEEK_API_KEY` 时，默认测试仍通过。
- 有 `DEEPSEEK_API_KEY` 时，`ctxforge run "..."` 能返回真实模型回答。
- CLI report 能显示 context、memory、skill、llm 四类信息。
- 模型调用成功后，下一轮同 session 能看到上一轮 session summary。
- 模型 usage 被记录到 `llm_report`。
- DeepSeek cache usage 字段如果存在，会进入 `llm_report`，为 Phase 5 使用。
- 当前任务和 memory 变化不会破坏 stable prefix hash。
