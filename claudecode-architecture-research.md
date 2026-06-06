# ClaudeCode CLI Agent 架构调研

> 调研对象：当前恢复版源码树。核心入口包括 `package.json`、`src/dev-entry.ts`、`src/entrypoints/cli.tsx`、`src/main.tsx`、`src/query.ts`、`src/QueryEngine.ts`、`src/Tool.ts`、`src/commands.ts`。

## 1. 项目性质

这是一个用 Bun + TypeScript + React/Ink 构建的 CLI coding agent。`package.json` 标记版本为 `999.0.0-restored`，并说明该源码树来自 source map 还原。因此它不是干净的上游源码，而是带有恢复层、兼容 shim、source map 噪声和部分缺失模块兜底的工程。

技术栈主线：

- 运行时：Bun，ESM，TypeScript，React JSX。
- CLI 框架：Commander。
- TUI 框架：Ink + React。
- 模型 API：Anthropic SDK beta messages API。
- 扩展协议：Model Context Protocol。
- 配置与权限：全局配置、settings.json 级联、permission rules、hooks、远程策略。
- 代码恢复支撑：`vendor/` 本地实现、`shims/` 本地包、`dev-entry.ts` 缺失 import 扫描。

整体目标不是单次补全，而是一个长生命周期 agent runtime：它要管理会话、上下文、工具权限、MCP 连接、插件、技能、子 agent、TUI 状态、压缩、遥测和恢复。

## 2. 总体执行链路

简化链路如下：

```text
bun run dev
  -> src/dev-entry.ts
      设置恢复版宏变量，扫描缺失相对 import
      -> src/entrypoints/cli.tsx
          处理快速路径、daemon、remote、background、模板等入口
          -> src/main.tsx
              Commander 参数解析
              初始化配置、权限、插件、MCP、工具、命令
              -> interactive: launchRepl()
                    React/Ink App + REPL
                    用户输入 -> processUserInput -> query()
              -> headless/SDK: QueryEngine.ask()/submitMessage()
                    输入处理 -> query()
              -> query.ts
                    构建 system prompt、压缩上下文、调用模型、执行工具
                    -> services/api/claude.ts
                    -> services/tools/*
```

这个设计把“入口路由”“交互 UI”“agent loop”“工具执行”“模型 API”拆成相对独立层。`main.tsx` 仍然很大，但关键行为已经被抽到 `query.ts`、`QueryEngine.ts`、`Tool.ts`、`commands.ts`、MCP 和权限服务里。

## 3. 启动与入口方案

### 3.1 恢复版入口保护

`src/dev-entry.ts` 是恢复版特有启动层：

- 从 `package.json` 填充 `globalThis.MACRO`，避免恢复后的宏缺失。
- 扫描 `src/` 和 `vendor/` 下相对 import 是否实际存在。
- `--version` 快速输出版本，并在存在缺失 import 时附加提示。
- 普通启动如果发现缺失相对 import，会提前退出，避免进入运行期才爆炸。

这层非常适合恢复工程：先验证源码闭包，再交给真实 CLI。

### 3.2 多入口快速路径

`src/entrypoints/cli.tsx` 是真正的入口分发器。它大量使用动态 import，把启动路径分成：

- `--version`、`--dump-system-prompt` 等轻量快速路径。
- Chrome MCP、computer-use MCP、daemon worker 等专用入口。
- remote-control、bridge、daemon、background session、template、environment runner 等子系统入口。
- 默认路径才启动 early input capture，再 import `main.tsx`。

这个方案的价值是减少冷启动成本，也降低某些 feature gate 或平台相关模块对普通路径的影响。

### 3.3 Commander 主程序

`src/main.tsx` 负责 CLI 参数、模式选择和核心启动编排：

- 先处理深链、direct connect、SSH remote、assistant viewer 等特殊参数。
- 判断 interactive/headless：`-p/--print`、SDK URL、非 TTY、init-only 等都会走非交互模式。
- 通过 `preAction` 做初始化：MDM、keychain、`init()`、sinks、插件目录、迁移、远程策略和 settings sync。
- 主 action 中初始化 permission context、MCP 配置、base tools、setup、bundled plugins/skills、commands、agents。
- 交互模式进入 `launchRepl()`，非交互模式走 headless store 和 query pipeline。

值得注意的是，Windows 下入口会设置 `NoDefaultCurrentDirectoryInExePath=1`，这是防止当前目录命令劫持的安全措施。

## 4. 命令体系

命令定义集中在 `src/types/command.ts` 和 `src/commands.ts`。

命令分三类：

- `PromptCommand`：把 slash command 展开成模型上下文，可带 allowedTools、model、hooks、skillRoot、fork context、agent、effort 等。
- `LocalCommand`：本地执行命令，返回文本/结果，不一定调用模型。
- `LocalJSXCommand`：本地 React/Ink UI 命令，例如配置、菜单、诊断类交互。

命令来源包括：

- 内置命令。
- 本地 `.claude/commands` 和 skills。
- bundled skills。
- plugin commands / plugin skills。
- MCP prompts / MCP skills。
- workflow commands。
- 内部/实验命令。

`getCommands(cwd)` 会统一加载、过滤 availability、应用 feature/setting gate，并把动态 skill 放在内置命令前。远程和 bridge 模式还有专门 allowlist，避免远程端触发危险或不适合的本地命令。

这个方案的关键点是：slash command 不是单一实现，而是“模型提示扩展、本地逻辑、TUI 组件、插件/技能/MCP prompt”的统一注册表。

## 5. 工具体系

### 5.1 Tool 抽象

`src/Tool.ts` 定义所有工具的统一接口。一个工具不仅有 `call()` 和 schema，还声明：

- 是否只读、是否破坏性、是否并发安全。
- 权限检查、输入校验、路径提取。
- 是否 MCP 工具、是否搜索/读取类命令。
- 是否 defer loading、是否 always load。
- prompt/schema 渲染方式。
- tool result 到 API block 的映射方式。
- TUI 渲染 hook 和进度展示。

`buildTool()` 对默认值采用保守策略，例如默认不并发安全、默认非只读、默认非破坏性、默认 allow permission。并发安全默认关闭是合理的 fail-closed 选择。

### 5.2 Base tools 与工具池

`src/tools.ts` 负责基础工具注册。核心工具包括：

- 文件和搜索：Read、Edit、Write、Glob、Grep、NotebookEdit。
- Shell：Bash、PowerShell。
- Web：WebFetch、WebSearch、WebBrowser。
- Agent：AgentTool、TaskOutput、TaskStop、TodoWrite、SkillTool。
- MCP resource tools：list/read MCP resource。
- 权限/规划：EnterPlanMode、ExitPlanMode。
- 实验或 feature-gated 工具：LSP、workflow、monitor、sleep、remote trigger、REPL tool 等。

`assembleToolPool(permissionContext, mcpTools)` 会把内置工具和 MCP 工具合并，并做 deny rule 过滤、MCP CLI 排除、排序和去重。内置工具优先于同名 MCP 工具，保证基础能力不被外部 server 意外覆盖。

## 6. Agent Loop 与模型调用

核心 agent loop 在 `src/query.ts`。

每一轮大致流程：

1. 构建 query config，读取 feature gate 快照。
2. 预取 memory、skill discovery。
3. 处理 compact boundary、tool result budget、snip、microcompact、auto compact、context collapse。
4. 构建完整 system prompt。
5. 根据权限模式、上下文长度和设置选择运行时模型。
6. 调用 `deps.callModel`，生产依赖指向 `queryModelWithStreaming()`。
7. 流式消费 assistant message、thinking、text、tool_use。
8. 如果没有 tool_use，处理 stop hook、token budget continuation、错误恢复并结束。
9. 如果有 tool_use，执行工具，生成 tool_result user message。
10. 更新工具池、MCP 状态、memory/skill attachment、tool summary，然后递归进入下一轮。

`src/query/deps.ts` 把 model call、microcompact、autocompact、uuid 做成窄依赖注入点。这对测试很友好，也说明作者在逐步把巨型 loop 拆成可替换依赖。

### 6.1 API 层

`src/services/api/claude.ts` 负责 Anthropic API 细节：

- Anthropic beta headers 和模型能力判断。
- prompt caching 和全局 cache scope。
- thinking、task budget、effort、fast mode。
- ToolSearch / deferred tools。
- MCP tool schema 转换。
- streaming 与 non-streaming fallback。
- API 错误、超时、重试、request id 链路。

它会在模型不支持 ToolSearch 时清理历史中的 tool reference 字段，避免模型切换造成 400。这个细节说明它支持中途换模型和动态工具加载。

## 7. 工具执行与权限

工具执行主要在 `src/services/tools/toolExecution.ts`、`StreamingToolExecutor.ts`、`toolOrchestration.ts` 和 `toolHooks.ts`。

### 7.1 执行流程

`runToolUse()` 会根据 tool name/alias 找到工具，然后进入 `checkPermissionsAndCallTool()`：

- 用 zod schema 校验输入。
- 执行工具自定义 `validateInput()`。
- Bash 可提前启动 speculative classifier。
- 对 hooks/permission 使用 observable input。
- 执行 PreToolUse hooks。
- 合并 hook permission decision 和 settings/rule permission。
- 调用 `tool.call()`。
- 映射、截断、持久化 tool result。
- 执行 PostToolUse 或 PostToolUseFailure hooks。
- MCP 认证错误会把 client 状态更新为 needs-auth。

hook 的 allow 不会绕过 settings deny/ask rules；这保证 hooks 不能轻易越过用户/策略级权限。

### 7.2 流式工具执行

`StreamingToolExecutor` 允许模型流中出现 tool_use 后立即开始执行，而不是等完整 assistant message 结束。它维护 queued/executing/completed/yielded 状态，并保证：

- 并发安全工具可以并行。
- 非并发安全工具独占执行。
- 输出顺序仍按模型 tool_use 顺序返回。
- Bash 失败会中止同组相关子进程。
- 工具可声明 interrupt behavior：cancel 或 block。
- 只有非并发工具可以应用 context modifier。

这个方案在编码 agent 中很重要：读取、搜索、部分 MCP 查询可以和模型流重叠，降低端到端延迟。

### 7.3 权限模式

权限初始化在 `src/utils/permissions/permissionSetup.ts`，交互授权在 `src/hooks/useCanUseTool.tsx`。

支持的主要模式包括 default、acceptEdits、plan、auto、bypass。CLI、settings 和远程策略共同决定初始模式。系统还会识别危险 Bash/PowerShell/Agent allow rule，在 auto mode 中剥离或恢复。

交互时 `useCanUseTool()` 先走静态规则；如果需要 ask，再进入 UI/bridge/channel/swarm permission 流程。Bash classifier 也可参与自动允许或拒绝。

这套方案的重点是多层权限合成：CLI 参数、settings、project rules、managed policy、hooks、classifier、远程 permission relay 都在一个决策点收口。

## 8. MCP 方案

MCP 类型在 `src/services/mcp/types.ts`。支持 transport：

- `stdio`
- `sse`
- `http`
- `ws`
- `sse-ide`
- `sdk`
- `claudeai-proxy`

配置来源和 scope 包括 local、user、project、dynamic、enterprise、claudeai、managed。

### 8.1 配置加载与策略

`src/services/mcp/config.ts` 负责：

- 加载 user/project/local/dynamic/plugin/claude.ai/enterprise MCP 配置。
- Enterprise MCP 配置存在时独占。
- Project `.mcp.json` 需要审批。
- 插件 MCP、手动 MCP、claude.ai connector 之间去重。
- 企业 allow/deny policy 过滤。
- Windows 下对 `npx` stdio 命令给出 `cmd /c` 兼容提示。

合并优先级大致是 claude.ai 低优先、plugin/user/project/local 更高，手动配置优先于插件和云端 connector。

### 8.2 连接与资源发现

`src/services/mcp/client.ts` 负责连接和调用：

- 使用 MCP SDK Client 和多种 transport。
- 连接后并行 fetch tools、prompts/commands、resources、skills。
- local server 与 remote server 使用不同连接并发限制。
- HTTP/SSE/claude.ai proxy 支持 OAuth/token cache，401 后 15 分钟内跳过重复探测。
- MCP 工具转成内部 `Tool`，并使用 `mcp__server__tool` 命名。
- MCP readOnly annotation 会影响并发安全和只读判断。
- 支持 elicitation 重试，最多 3 次。

交互模式下 `useManageMCPConnections()` 把 MCP 状态写入 AppState，支持 list changed notification、重连、插件刷新触发、批量状态更新。headless 模式则通过 `getMcpToolsCommandsAndResources()` 逐个回调接入。

## 9. 插件与技能方案

插件系统在 `src/utils/plugins/*`，内置插件脚手架在 `src/plugins/*`，skills 在 `src/skills/*`。

插件来源：

- marketplace 安装插件。
- `--plugin-dir` 或 SDK inline/session 插件。
- built-in plugin registry。

插件组件：

- `commands/` markdown slash commands。
- `skills/` 下的 `SKILL.md` 技能。
- `agents/` agent definitions。
- `hooks/` hook config。
- `.mcp.json`、manifest `mcpServers`、`.mcpb` MCP bundle。
- settings base、output styles、LSP 推荐等扩展。

`pluginLoader.ts` 使用版本化 cache：`~/.claude/plugins/cache/{marketplace}/{plugin}/{version}`，并支持 seed cache、zip cache、legacy cache fallback、policy blocklist、strict marketplace、managed plugin、dependency demotion。

`loadPluginCommands.ts` 把插件 markdown 转成 `Command`。它支持 frontmatter：

- description、allowed-tools、argument-hint、model、effort。
- shell execution。
- plugin 变量替换，例如 plugin root。
- 技能目录中的 `SKILL.md` 会按目录名生成命名空间。

这套插件体系的设计方向是“文件协议 + manifest + settings policy + cache”，比纯 JS 插件更容易审计，也更适合 CLI agent 的权限边界。

## 10. TUI 与状态管理

交互界面由 `src/replLauncher.tsx` 懒加载：

```text
launchRepl()
  -> components/App.tsx
      FpsMetricsProvider
      StatsProvider
      AppStateProvider
  -> screens/REPL.tsx
```

`AppStateProvider` 使用自定义 `createStore()` + `useSyncExternalStore()`，组件可以用 selector 订阅状态切片，避免全局状态变更导致整棵树重渲染。AppState 内容非常宽，包括：

- settings、verbose、model、permission context。
- MCP clients/tools/commands/resources。
- plugins enabled/disabled/errors/installation status。
- task/subagent 状态、todo、notifications、elicitation。
- file history、attribution、prompt suggestion、speculation。
- bridge、remote session、channel permission、swarm worker permission。

`screens/REPL.tsx` 是交互核心，负责：

- 渲染消息、输入框、权限弹窗、MCP elicitation、任务面板、插件通知。
- 管理 message state、streaming text、thinking、tool progress。
- 处理 resume/session restore、compact、rewind、export transcript。
- 调用 `handlePromptSubmit()` 处理用户输入。
- 构建 `ToolUseContext` 并触发 `query()`。
- 通过 hooks 合并 MCP tools、plugin commands、skills、agents。

这个 TUI 本质上是一个终端内 React app，而不是简单 readline。好处是复杂权限、并发任务、MCP 状态、插件菜单、消息选择、恢复会话都能以组件方式组织。

## 11. 配置系统

项目同时有旧式全局 config 和新的 settings cascade。

### 11.1 旧式 config

`src/utils/config.ts` 管理类似 `~/.claude.json` 的 GlobalConfig/ProjectConfig：

- OAuth/account、mcpServers、UI 偏好、增长实验缓存。
- project config 以 canonical git root/path 为 key。
- allowedTools、MCP approval、disabled/enabled MCP、worktree session 等。
- 写入有锁、备份、stale write/auth loss 防护，新文件权限 0600。

`enableConfigs()` 前访问 config 会抛错，这能发现过早读取配置的启动顺序 bug。

### 11.2 settings.json

`src/utils/settings/settings.ts` 管理新配置系统：

- managed settings：remote、MDM、managed-settings.json/drop-ins、HKCU 等。
- user/project/local/flag/plugin settings。
- source precedence merge，数组 concat/dedup。
- zod schema 校验。
- permission rules 单条容错，坏规则不会使整个 settings 文件失效。
- local settings 自动写入 gitignore。
- policy setting 采用 first source wins。

这说明项目经历了配置系统演进：旧 config 承载历史状态和项目级数据，新 settings 承载策略化、可合并、可托管配置。

## 12. 恢复层、vendor 与 shims

恢复版有明显兼容层：

- `dev-entry.ts` 检查缺失相对 import。
- `vendor/` 提供 native 相关源码替代，如 audio capture、image processor、modifiers、url handler。
- `shims/` 用本地 file dependency 模拟外部/native 包，例如 `modifiers-napi`、`url-handler-napi`、`@ant/computer-use-*`。
- `package.json` 用 `file:./shims/...` 指向这些本地包。

这类设计让恢复版可以在缺少原始 native 包或内部私有包时启动更多路径，但也意味着部分功能可能是 stub、降级实现或平台限定实现。

## 13. 值得借鉴的工程方案

### 13.1 冷启动分层

入口大量使用动态 import，把 `--version`、daemon、remote、MCP server、普通 REPL 分成不同加载图。CLI agent 很容易依赖爆炸，这种分层能显著改善启动时间和故障隔离。

### 13.2 命令、技能、MCP prompt 统一

把内置命令、markdown command、skill、plugin command、MCP prompt 都映射为 `Command`，让 UI、过滤、权限、模型上下文复用同一套接口。

### 13.3 工具接口携带安全元数据

Tool 不只是函数调用，而是带并发、安全、权限、schema、UI、MCP、结果映射的能力声明。这比在执行器里硬编码工具名更可维护。

### 13.4 流式工具执行

模型输出 tool_use 时即可调度工具，且仍维持最终输出顺序。对 coding agent 来说，这是降低等待感的关键优化。

### 13.5 多层权限收口

CLI、settings、managed policy、hook、classifier、bridge/channel permission 最终都回到 `CanUseToolFn` / permission decision。复杂但边界清晰。

### 13.6 配置源级联

managed/user/project/local/flag/plugin settings 有明确优先级和 schema 校验，适合企业、个人和项目三类环境共存。

### 13.7 MCP 作为一等扩展点

MCP 工具、资源、prompt、skills 都进入同一命令/工具池；连接状态进入 AppState，交互和 headless 都能消费。这比把 MCP 作为旁路功能更自然。

### 13.8 文档式插件

插件命令/技能以 markdown + frontmatter 描述，天然可读、可审计、可被模型引用，也降低第三方扩展门槛。

## 14. 主要风险与维护难点

- `main.tsx` 和 `REPL.tsx` 仍然非常大，恢复版 source map 噪声也增加阅读成本。
- feature gate 很多，部分路径只有特定构建或内部环境才可验证。
- 插件、settings、MCP、权限之间存在复杂交叉，修改任一层都可能影响启动和工具池。
- 没有统一 lint/test 脚本，回归主要依赖手工 smoke test。
- native/shim/vendor 层可能不是完整上游实现，涉及 computer use、url handler、modifiers、image processor 的功能需要逐项验证。
- MCP 和插件加载大量使用缓存，修复刷新类 bug 时必须同时考虑 memoize cache、settings cache、plugin cache、MCP fetch cache。
- 权限系统强依赖规则优先级和 hook 行为，新增工具时必须认真声明 `isReadOnly`、`isConcurrencySafe`、`isDestructive`、`checkPermissions`。

## 15. 推荐阅读路径

如果继续深入，建议按这个顺序读：

1. `package.json`：确认运行脚本、依赖和本地 shims。
2. `src/dev-entry.ts`：理解恢复版保护层。
3. `src/entrypoints/cli.tsx`：理解入口分发。
4. `src/main.tsx`：理解 CLI 参数、初始化和 interactive/headless 分叉。
5. `src/types/command.ts` + `src/commands.ts`：理解 slash command/skill/plugin command。
6. `src/Tool.ts` + `src/tools.ts`：理解工具能力模型。
7. `src/query.ts` + `src/query/deps.ts`：理解 agent loop。
8. `src/services/tools/*`：理解权限、hooks、并发工具执行。
9. `src/services/api/claude.ts`：理解模型调用、prompt cache、ToolSearch。
10. `src/services/mcp/config.ts` + `src/services/mcp/client.ts`：理解 MCP 配置和连接。
11. `src/utils/plugins/*` + `src/skills/*`：理解插件和技能。
12. `src/replLauncher.tsx` + `src/components/App.tsx` + `src/screens/REPL.tsx`：理解 TUI。
13. `src/utils/config.ts` + `src/utils/settings/settings.ts`：理解配置和策略。

## 16. 结论

这个项目的核心方案可以概括为：用 React/Ink 做长生命周期终端应用，用统一 Command/Tool 抽象承接内置能力、插件、技能和 MCP，用 query loop 编排模型、上下文压缩和工具执行，用多层权限系统控制风险，再用 settings/policy/plugin/MCP 提供企业级扩展能力。

它最值得借鉴的不是某个单点工具，而是这些模块之间的边界设计：命令负责“用户意图入口”，工具负责“可执行能力”，query loop 负责“模型与工具闭环”，权限系统负责“能不能执行”，TUI/AppState 负责“把复杂异步状态可视化”。这套分层是 CLI coding agent 从玩具走向可用产品的关键。
