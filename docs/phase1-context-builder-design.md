# Phase 1: Context Builder 设计文档

## 1. 目标

Phase 1 的目标是实现一个确定性、可预算、可观测的 Context Builder，为后续 Memory、Skill、DeepSeek Runtime 和 Prefix Cache Analyzer 提供稳定基础。

Phase 1 不负责真实模型调用，也不负责真实 Memory/Skill 检索。它只负责把已经给定的上下文材料按稳定协议构造成 prompt，并输出可解释报告。

核心交付：

- `ContextSection`：统一描述上下文片段。
- `ContextBuilder`：构建最终 prompt/messages。
- `ContextReport`：报告 section 组成、token 估算、预算裁剪和 stable prefix 信息。
- `PrefixSnapshot`：记录 stable prefix bytes、hash 和 stable section hash。
- CLI/runtime 接入：`ctxforge run` 和 `ctxforge inspect context` 可以展示 Phase 1 context 状态。

## 2. 设计原则

### 2.1 Prefix 稳定优先

CtxForge 的核心价值之一是 prefix-cache 友好。因此 Phase 1 以字节级稳定为第一原则。

Stable Prefix 中不能包含：

- 当前 task。
- session id。
- 时间戳。
- 随机 ID。
- 临时路径。
- 最近工具结果。
- 当前轮 Memory 检索结果。

这些内容只能进入 dynamic suffix。

### 2.2 确定性渲染

相同输入必须得到相同的 `rendered_prompt`、`stable_prefix` 和 `stable_prefix_sha256`。

排序规则固定为：

```text
stability order -> priority desc -> name asc -> source asc
```

其中 stability 顺序为：

```text
stable -> semi_stable -> dynamic
```

### 2.3 预算可解释

Phase 1 不追求 provider 级别 token 精度，只做本地确定性估算。

当前估算策略：

```text
estimated_tokens = ceil(len(rendered_text) / 4)
```

报告字段明确使用 `estimated`，避免把本地估算误认为真实 API usage。

## 3. 核心模型

### 3.1 ContextSection

`ContextSection` 是 Context Builder 的最小输入单元。

字段：

```python
name: str
stability: Literal["stable", "semi_stable", "dynamic"]
priority: int
content: str
source: str
token_estimate: int = 0
required: bool = False
```

字段含义：

- `name`：section 的稳定标识，用于排序、报告和 hash。
- `stability`：决定 section 属于 stable prefix、semi-stable project context 还是 dynamic suffix。
- `priority`：同一 stability 内的排序和预算取舍依据。数值越高，越靠前，越优先保留。
- `content`：section 正文。
- `source`：来源标识，例如 `builtin.runtime`、`memory.placeholder`、`request`。
- `token_estimate`：渲染后 token 估算值。
- `required`：预算紧张时是否必须保留。required section 可能被截断或触发 overflow，但不会静默丢弃。

### 3.2 BuiltContext

`BuiltContext` 是 Context Builder 的完整输出。

字段：

```python
messages: list[dict[str, str]]
sections: list[ContextSection]
rendered_prompt: str
stable_prefix: str
report: ContextReport
snapshot: PrefixSnapshot
```

用途：

- `messages` 提供给后续 DeepSeek Client。
- `sections` 保留最终纳入上下文的 section。
- `rendered_prompt` 用于 inspect/debug。
- `stable_prefix` 用于 prefix-cache 分析。
- `report` 用于 CLI/TUI 展示。
- `snapshot` 用于 Phase 5 cache diff。

### 3.3 ContextReport

`ContextReport` 记录构建过程的可观测数据。

核心字段：

```text
status
max_tokens
reserved_output_tokens
input_budget
total_estimated_tokens
stable_prefix_tokens
semi_stable_tokens
dynamic_tokens
section_count
included_sections
dropped_sections
truncated_sections
stable_prefix_bytes
stable_prefix_sha256
overflow
```

报告目的：

- 解释上下文由哪些 section 组成。
- 说明 token 预算如何被使用。
- 标记哪些 section 被丢弃或截断。
- 暴露 stable prefix hash，方便用户判断 prefix 是否稳定。

### 3.4 PrefixSnapshot

`PrefixSnapshot` 记录 stable prefix 的快照。

字段：

```python
stable_prefix_bytes: bytes
stable_prefix_sha256: str
section_hashes: dict[str, str]
```

Phase 1 只生成快照，不做跨轮对比。跨轮 diff 和 cache hit ratio 估算留给 Phase 5。

## 4. 默认上下文分区

Phase 1 默认构建以下 section。

### 4.1 Stable Prefix

```text
runtime.system_prompt
runtime.context_protocol
runtime.skill_manifest
```

说明：

- `runtime.system_prompt` 是 CtxForge 的基础运行身份和职责。
- `runtime.context_protocol` 说明上下文构建规则。
- `runtime.skill_manifest` 当前只记录已选择 skill 名称；真实 Skill 文档注入留给 Phase 3。

### 4.2 Semi-Stable Project Context

```text
project.profile
```

说明：

- 当前只使用项目目录名作为 placeholder。
- 后续可扩展为项目配置、项目摘要、用户偏好等。

### 4.3 Dynamic Suffix

```text
request.task
memory.retrieved
session.working_memory
```

说明：

- `request.task` 是当前用户任务，必须属于 dynamic。
- Memory 相关 section 当前是 placeholder。
- 后续 Phase 2 接入真实 Memory 后，不应改变 stable prefix。

## 5. 渲染协议

每个 section 使用固定 XML-like 包裹格式：

```text
<context_section name="..." stability="..." priority="..." source="...">
...
</context_section>
```

渲染规则：

- 换行统一为 `\n`。
- section content 会进行首尾空白规整。
- section 之间使用两个换行分隔。
- section 排序不依赖调用方传入顺序。
- skill 名称等无序列表必须先排序再渲染。

## 6. 预算策略

### 6.1 输入预算

输入预算来自配置：

```text
input_budget = context.max_tokens - context.reserved_output_tokens
```

其中：

- `max_tokens` 是总上下文预算。
- `reserved_output_tokens` 是为模型输出预留的预算。

### 6.2 保留和裁剪规则

构建器按排序后的 section 顺序尝试纳入上下文。

规则：

- 如果 section 估算 token 小于等于剩余预算，直接纳入。
- `required=True` 的 section 不会被静默丢弃。
- dynamic section 在预算不足但仍有剩余空间时可以截断。
- semi-stable 和 stable 的 optional section 在预算不足时直接丢弃。
- required section 如果仍然无法放入预算，会触发 `overflow=True`。

### 6.3 截断策略

动态 section 截断时使用二分查找，找到在完整 section header/footer 渲染后仍能放进预算的最大 content 长度。

截断后追加 marker：

```text
[ctxforge: truncated]
```

这样可以避免只按正文字符数截断导致 section 渲染后仍超预算。

## 7. Runtime 和 CLI 接入

### 7.1 Runtime

Phase 1 runtime 入口为：

```python
run_phase1(request: RuntimeRequest, settings: CtxForgeSettings) -> RuntimeResult
```

行为：

- 构建真实 Phase 1 context。
- 返回真实 `context_report`。
- 返回 stable prefix snapshot 信息到 `cache_report`。
- Memory、Skill、DeepSeek 调用仍保持 placeholder。

`run_phase0` 当前作为兼容别名保留，内部调用 `run_phase1`。

### 7.2 CLI

`ctxforge run` 当前展示 Phase 1 Runtime Report，包括：

- session id。
- project dir。
- model。
- max tokens。
- input budget。
- context estimated tokens。
- stable prefix hash。
- memory db path。

新增命令：

```powershell
ctxforge inspect context "Explain this project."
```

可选参数：

```powershell
--project-dir / -C
--max-tokens
--show-prompt
```

该命令用于查看 section 组成、token 估算、stable prefix hash、被丢弃/截断的 section，以及可选的完整 rendered prompt。

## 8. 测试覆盖

Phase 1 测试重点：

- 相同输入构建结果确定性一致。
- skill_names 输入顺序不同，stable prefix hash 仍一致。
- dynamic task 改变时，stable prefix hash 不变。
- section 按 stability、priority、name、source 稳定排序。
- 动态 optional section 在预算不足时可截断。
- semi-stable optional section 在预算不足时可丢弃。
- runtime 返回 Phase 1 context report 和 cache snapshot。

当前验证命令：

```powershell
.\.venv\Scripts\python -m pytest
```

## 9. 后续 Phase 接入点

### 9.1 Phase 2 Memory

Memory Manager 接入后，应通过 dynamic section 注入：

```text
memory.retrieved
session.working_memory
session.summary
```

原则：

- Memory 检索结果不得进入 stable prefix。
- Memory section 需要带来源和优先级，方便预算裁剪。

### 9.2 Phase 3 Skill

Skill Registry 接入后，应区分：

- 稳定 manifest：可进入 stable prefix。
- skill 文档正文：通常进入 semi-stable。
- activation 结果和当前任务相关 skill notes：进入 dynamic。

### 9.3 Phase 4 DeepSeek Runtime

DeepSeek Client 可直接消费 `BuiltContext.messages`。

真实 API usage 返回后，可以与 `ContextReport.total_estimated_tokens` 对照记录，但不应替代本地确定性预算逻辑。

### 9.4 Phase 5 Prefix Cache Analyzer

Prefix Cache Analyzer 可以消费 `PrefixSnapshot`：

```text
stable_prefix_bytes
stable_prefix_sha256
section_hashes
```

用于对比当前轮和上一轮 context，找出首个变化位置、变化 section 和估算 cache hit ratio。

## 10. 当前实现文件

```text
src/ctxforge/context/models.py
src/ctxforge/context/builder.py
src/ctxforge/context/render.py
src/ctxforge/context/budget.py
src/ctxforge/context/snapshot.py
src/ctxforge/runtime/agent.py
src/ctxforge/cli/app.py
tests/test_context_builder.py
tests/test_runtime.py
```
