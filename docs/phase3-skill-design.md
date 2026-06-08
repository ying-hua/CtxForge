# Phase 3: Skill 方案设计

## 1. 目标

Phase 3 的目标是把 Phase 1/2 中的 placeholder skill 路径升级为可运行的本地 Skill Layer。

本阶段完成：

- 本地 `skills/` 目录发现。
- `skill.toml` manifest 校验。
- `SKILL.md` 加载。
- 显式 skill 选择和简单 activation。
- 将 skill manifest 与 skill 文档注入 `ContextBuilder`。
- 提供 `ctxforge skill list`、`ctxforge skill inspect`、`ctxforge skill install`。

Phase 3 不负责真实 DeepSeek 调用、MCP、远程 marketplace、脚本执行、权限弹窗或 prefix cache diff。这些分别属于后续 Phase 或更高层 Agent 能力。

## 2. 设计原则

### 2.1 Skill 是本地文件协议

Phase 3 的 skill 只是一组本地文档和 manifest：

```text
skills/
  code-review/
    skill.toml
    SKILL.md
    examples/
    scripts/
```

`examples/` 和 `scripts/` 可以存在，但 Phase 3 不读取 examples，不执行 scripts。这样可以先验证“发现 -> 激活 -> 上下文注入”链路，而不是提前进入工具权限和脚本沙箱复杂度。

### 2.2 Stable Prefix 只放轻量 manifest

选中 skill 的稳定摘要进入 `runtime.skill_manifest`，属于 stable prefix。摘要只包含排序后的 name、version、description、activation 和 allowed runtime tools。

`SKILL.md` 正文进入 semi-stable section：

```text
skill.<name>.instructions
```

原因：

- manifest 小且稳定，适合帮助模型理解当前可用 skill。
- `SKILL.md` 可能较长，也更像项目上下文，不应和 runtime 协议混在一起。
- 当前任务、匹配关键词、activation reason 只进入 report，不进入 stable prefix。

### 2.3 激活策略保持可解释

Phase 3 只做两类激活：

1. 显式选择：用户通过 `--skill NAME` 指定。
2. 简单关键词匹配：当前 task 命中 manifest 中的 `activation` 词。

每个选中 skill 都记录：

- `name`
- `reason`
- `explicit`
- `matched_terms`

这样 CLI/TUI 后续可以解释为什么某个 skill 被注入。

## 3. 文件协议

### 3.1 skill.toml

最小 manifest：

```toml
name = "code-review"
version = "0.1.0"
description = "Review code changes with project context."
activation = ["review", "diff", "bug"]
allowed_runtime_tools = ["memory.search", "context.read"]
```

校验规则：

- `name`、`version`、`description` 必填且非空。
- `name` 只允许字母、数字、`.`、`_`、`-`。
- `activation` 和 `allowed_runtime_tools` 默认为空列表。
- 未知字段报错，避免协议漂移。
- 列表字段会去重并排序，保证稳定渲染。

### 3.2 SKILL.md

`SKILL.md` 必须存在且非空。Phase 3 不解析内部标题或元数据，只把正文作为 semi-stable context section 注入。

## 4. 模块边界

新增模块：

```text
src/ctxforge/skills/__init__.py
src/ctxforge/skills/models.py
src/ctxforge/skills/registry.py
src/ctxforge/skills/manager.py
src/ctxforge/skills/render.py
```

### 4.1 SkillRegistry

职责：

- 从 `<project_dir>/skills/` 发现本地 skill。
- 加载和校验 `skill.toml`。
- 读取 `SKILL.md`。
- 报告缺失文件、manifest 错误、重复 skill name。
- 安装本地 skill 目录到项目 `skills/`。

`SkillRegistry` 不关心当前任务，也不生成 context section。

### 4.2 SkillManager

职责：

- 接收当前 task 和显式 skill names。
- 根据 registry 结果选择 skill。
- 计算 activation reason。
- 生成 `SkillReport`。
- 生成选中 skill 的 context sections。

### 4.3 Skill Renderer

职责：

- 将选中 skill 渲染成 stable manifest 文本。
- 将 `SKILL.md` 渲染成 semi-stable `ContextSection`。

section 规则：

```text
runtime.skill_manifest       stable      priority 70 source builtin.skills
skill.<name>.instructions    semi_stable priority 45 source skill:<name>
```

## 5. Runtime 接入

新增 runtime 入口：

```python
def run_phase3(request: RuntimeRequest, settings: CtxForgeSettings) -> RuntimeResult:
    ...
```

执行顺序：

1. 生成或复用 `session_id`。
2. 初始化 `MemoryStore`。
3. 调用 `MemoryManager.retrieve_for_context(...)`。
4. 调用 `SkillManager.select_for_context(...)`。
5. 将 skill sections 与 memory sections 一起传给 `ContextBuilder.build(...)`。
6. 使用 `skill_manifest_content` 替换默认 placeholder manifest。
7. 返回真实 `memory_report` 和真实 `skill_report`。

`RuntimeResult` 增加：

```python
skill_report: dict[str, object]
```

`run_phase1` 和 `run_phase2` 保留兼容，但只返回各自 Phase 的 placeholder skill report。

## 6. CLI 接入

`ctxforge run` 默认切到 Phase 3：

```powershell
ctxforge run "Please review this diff." --skill code-review
```

新增命令：

```powershell
ctxforge skill list
ctxforge skill inspect code-review
ctxforge skill install ./path/to/skill
ctxforge skill install ./path/to/skill --force
```

`skill install` 行为：

- 源目录必须包含合法 `skill.toml` 和非空 `SKILL.md`。
- 安装目标为 `<project_dir>/skills/<skill-name>`。
- 目标已存在时报错。
- `--force` 会替换同名本地 skill。

## 7. 测试覆盖

Phase 3 测试重点：

- registry 发现合法 skill，并报告缺失 `skill.toml` 或 `SKILL.md` 的目录。
- manifest 校验拒绝空字段、未知字段和非法 name。
- 显式 `--skill` 与关键词 activation 可以同时工作。
- 显式选择不存在的 skill 会进入 `skill_report.missing`。
- 选中 skill 按 name 排序，manifest 渲染不受输入顺序影响。
- `SKILL.md` 注入为 semi-stable section。
- 当前 task 变化但 skill 集合不变时，stable prefix hash 不变。
- `ctxforge skill list`、`inspect`、`install` 和 `run --skill` 可用。

验证命令：

```powershell
.\.venv\Scripts\python -m pytest -p no:cacheprovider
```

## 8. 非目标

Phase 3 不做：

- 执行 `scripts/`。
- 解析 `examples/`。
- 远程 skill marketplace。
- 自动更新 skill。
- MCP prompt/tool 映射。
- 工具权限模型。
- DeepSeek API 调用。
- Prefix cache hit ratio 估算。
- TUI skill 面板。

这些能力会在后续 Phase 或真实使用压力出现后再设计。
