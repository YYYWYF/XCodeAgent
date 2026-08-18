# XCodeAgent 产品设计与流程设计审阅报告

> 审阅日期：2026-08-13
>
> 文档状态：评审稿
>
> 审阅范围：产品定位、初始化旅程、工作台交互、正式产物、执行与验证、权限与信任、恢复与交付、文档治理、竞品对照
>
> 当前实现基线：以 `docs/XCODEAGENT_COMPLETE_WORKFLOW.md` 和源码抽查为准；工作区内未提交的 ProductPlan / TechnicalPlan 改造按“开发中”处理，不视为已稳定发布能力。

## 1. 结论摘要

XCodeAgent 已经具备一个普通 Coding Chat 不具备的雏形：本地工作区、正式需求与计划、显式确认、确定性 Graph、专业 Agent、Build DAG、测试、预览、验收和恢复状态都已进入同一产品边界。这个方向有价值，也比单纯追求“多 Agent”更有差异化。

当前最主要的问题不是功能少，而是：**产品尚未形成唯一、稳定、可向用户解释的交付模型。** 同一概念存在多套文档和实现；用户确认的对象、系统实际执行的对象、最终声称验证通过的对象并不总是一致；运行证据和恢复能力又不足以弥补这种信任缺口。

建议将产品定位收敛为：

> **面向本地真实代码库的、可确认、可验证、可恢复的 AI 交付工作台。**

近期不应继续用新增规划层、正式产物或 Agent 数量证明完整性，而应优先完成以下五件事：

1. 冻结唯一的 Artifact DAG 和字段所有权，结束 ProductPlan、TechnicalPlan、ProjectPlan、PageDetail、EntityDesign、开发计划之间的竞争。
2. 让“用户确认的计划”成为真实执行计划的上游；Build DAG 只能由它确定性编译，不能再次生成另一套语义计划。
3. 把当前“Integration Test”拆成 Build Verification、Runtime Smoke、Independent Review 和 User Acceptance，并展示真实证据。
4. 建立硬权限边界、单工作区写隔离、可审阅 checkpoint 和分维恢复；发布前先解决已有安全审计中的阻断项。
5. 简化工作台心智：用户选择业务对象和目标，系统负责路由，不要求用户理解 endpoint、Graph、thread、resume node 等内部结构。

若面向携带真实模型密钥、数据库凭证的外部用户，当前版本仍应视为发布阻断状态。`docs/SECURITY_IMPLEMENTATION_RISK_AUDIT.md` 已给出无鉴权本地后端、自助审批、通用命令执行、敏感文件泄漏和密钥打包等完整风险链；这些不是普通技术债，而是产品信任承诺的前置条件。

## 2. 审阅方法与事实边界

本报告采用四类证据：

- 当前流程文档：`docs/XCODEAGENT_COMPLETE_WORKFLOW.md`、`docs/WORKFLOW.md`、`docs/APPLICATION_LIFECYCLE.md`、`docs/APPLICATION_DEVELOPMENT_PLANNING.md`。
- 目标设计和专项审计：`docs/PRODUCT_UI_TECHNICAL_PLANNING.md`、`docs/detail_confirmation_design.md`、`docs/MODEL_OUTPUT_COMMUNICATION_DESIGN.md`、`docs/PROJECT_ARCHITECTURE_AUDIT.md`、`docs/SECURITY_IMPLEMENTATION_RISK_AUDIT.md`。
- 实现抽查：创建规划 Graph、ProductPlan 开发中改造、工作台阶段文案、计划执行 Dock、详情选择器和 AG-UI 会话链路。
- 竞品官方资料：Cursor、Claude Code、OpenAI Codex、GitHub Copilot、Devin Desktop（原 Windsurf 文档入口）、Cline、Roo Code、OpenCode、Replit、Lovable 和 v0；检索日期为 2026-08-13。

需要特别说明：当前未提交改动已经把创建规划改为 `RequirementSpec -> ProductPlan -> UI Design -> TechnicalPlan`，方向上正在修复“UI 先于技术/产品计划”的依赖倒置，见 `Backend/app/graph/application_planning_workflow.py:L25-L54`、`L486-L513` 和 `docs/PRODUCT_UI_TECHNICAL_PLANNING.md:L7-L20`。前端阶段文案也已改为四阶段，但仍保留旧 `ProjectPlan` 兼容分支，确认版本绑定、恢复权威源和旧流程文档尚未完成收口，因此本报告将其判断为“正确方向、迁移未闭合”，而不是已稳定发布能力。

## 3. 当前产品模型

### 3.1 当前用户旅程

按现行文档，用户大致经历：

```text
创建应用
  -> 需求澄清与 RequirementSpec 确认
  -> 逐页 UI 设计与确认
  -> ProjectPlan 确认
  -> 模板克隆、页面占位和菜单写入
  -> 进入工作台并启动模板预览
  -> 选择页面或 endpoint
  -> 页面 / endpoint 详细设计与确认
  -> Workspace Inspection
  -> Build DAG
  -> 数据库 / 后端 / 前端 Agent 执行
  -> “Integration Test”与自动修复
  -> 正式预览
  -> 用户验收或回到相应调整节点
```

对应证据见 `docs/XCODEAGENT_COMPLETE_WORKFLOW.md:L127-L148` 和 `L301-L370`。

### 3.2 值得保留的设计

以下能力应继续成为产品核心，而不是在简化流程时被删除：

- 正式 Markdown 产物和显式确认；澄清答案不等于确认。
- 外层确定性 Graph 负责阶段和门禁，Agent 不自行改变生命周期。
- 大型代码、日志和工具输出落盘，Graph State 只保存有界摘要和引用。
- 页面、endpoint、数据库和测试有结构化状态，不只靠聊天文字表达。
- 工作台内可查看预览、进度、代码差异和历史会话。
- AG-UI 统一承载产品动作、进度、状态、错误和恢复，而不是继续扩散手写传输协议。

这些原则与 Deep Agents 的上下文隔离、文件系统持久化和渐进披露方向一致。官方资料也明确建议用子代理隔离高输出任务、用文件保存大结果、让主上下文只接收摘要：[Deep Agents Context Engineering](https://docs.langchain.com/oss/python/deepagents/context-engineering)、[Deep Agents Subagents](https://docs.langchain.com/oss/python/deepagents/subagents)。

## 4. 关键问题总览

| 优先级 | 问题 | 用户影响 | 建议决策 |
| --- | --- | --- | --- |
| P0 | 外部分发所需的安全信任边界尚未成立 | 密钥、数据库和本机工作区存在高风险 | 作为发布门，不与普通功能迭代混排 |
| P0 | 客户端可指定内部恢复节点并直达完成 | 可绕过验证、启动和用户验收，伪造“已完成” | 客户端只提交 typed decision；高价值节点 fail-closed 校验前置不变量 |
| P0 | Artifact DAG 和字段所有权未冻结 | 用户和开发者无法判断哪份计划才是真的 | 冻结 RequirementSpec -> ProductPlan -> UiDesign -> TechnicalPlan -> EndpointDetail |
| P0 | 确认计划与执行计划可能不是同一个对象 | 用户确认 A，系统执行 B | Unit Plan 确定性编译 Build DAG，删除平行计划 |
| P0 | 验证名称和证据强于真实能力 | “通过”可能只是编译或静态检查 | 拆分验证等级并要求 required-check manifest |
| P0 | 模型输出、工具过程和错误不能稳定重放 | 用户无法审计完成原因和失败原因 | canonical content blocks + event ledger + exhaustive reducer |
| P0 | 同一工作区允许并发写 | 多 Run 可覆盖页面、菜单、API 和报告 | 先实行一 workspace 一个写 execution |
| P0 | 模板门禁可能假成功 | 用户进入残缺工作台 | 后端复核实际文件 manifest 后才能 ready |
| P1 | 页面 / endpoint / 模式 / Graph 心智过重 | 新用户必须理解内部实现才能操作 | 单 composer + 业务对象 + 执行前范围卡 |
| P1 | 确认数量增长但 ICP 未定义 | 单人用户频繁被组织式审批打断 | 先服务 Single Builder，团队审核后置 |
| P1 | 恢复有多个事实源，缺少影响预览 | 恢复可能跳错阶段或覆盖新状态 | 服务端恢复 + 文件/对话/流程分维回退 |
| P1 | 预览是展示窗口，不是高效反馈工具 | 用户只能用文字描述 UI 问题 | 元素选择、截图、console、network 直接回填 |
| P1 | 缺少稳定 Git 交付闭环 | 验收后仍留下难追溯的脏工作区 | 基线、change set、commit/PR/export 显式交付 |
| P1 | 文档和 UI 阶段持续漂移 | 维护者和用户看到不同流程 | 单一 manifest 生成流程、能力和术语投影 |
| P2 | 成本、时间、重试和失败模式不透明 | 用户无法判断等待是否值得 | 运行预算、耗时、重试和成本面板 |
| P2 | 团队、部署、平台化边界过早扩张 | 分散对可信本地交付的投入 | 先完成单人本地闭环，再做团队与部署 |

## 5. P0：必须优先解决的问题

### 5.1 发布信任边界尚未成立

已有安全审计的结论是：不可信网页、Renderer XSS、模型生成代码或本机恶意进程，可以经无鉴权本地后端或宽 IPC，自行选择工作区、批准操作、读取敏感文件和执行主机命令。关键证据集中在 `docs/SECURITY_IMPLEMENTATION_RISK_AUDIT.md:L16-L210`。

代码抽查还发现两个不能由提示词或 UI 约束弥补的直接漏洞：

- `Backend/app/tools/execute.py:L1-L65` 明确绕过 permission middleware，并以 `shell=True` 执行命令，默认没有超时，输出也没有硬上限。这意味着权限模型尚未落实到真正产生副作用的工具边界。
- `Backend/app/protocols/workflow/request.py:L603-L706` 接受客户端 `resumeFrom` 和完整恢复状态，`Backend/app/graph/workflow.py:L22-L55` 可从 START 直达 `finalize_project`，而 `Backend/app/graph/nodes/lifecycle.py:L79-L88` 会无条件返回 `completed`。因此客户端当前可以跳过测试、启动和验收，直接制造“项目已完成”的状态。

恢复不是普通导航能力，而是流程完整性边界。生产客户端不应提交内部节点名；只应提交 `threadId + interactionId + basedOnRevision + typed decision`，由服务端依据权威 checkpoint 决定恢复位置。`launch_project`、`acceptance`、`finalize_project` 等高价值节点还必须自行校验前置不变量，不能只相信路由。

这会直接破坏“本地更安全”的产品叙事。建议发布门至少包含：

1. Electron Main 生成会话 capability，敏感路由强制验证；Renderer 不直接持有完整权限。
2. 前端只传 opaque workspace ID，后端不接受任意 `workspace_root`。
3. 沙箱决定“能不能做”，审批决定“什么时候问”；审批绑定窗口、用户手势、run、workspace、参数 hash、TTL 和单次消费。
4. 所有 Agent 和子代理使用不可绕过的统一 Tool Policy；命令必须有结构化 argv、规范化 cwd、超时、输出上限、网络/敏感路径策略，子代理只能继承授权子集。
5. 默认无网络；按域名、工具、时限授权。数据库写、push、部署、删除和迁移永远单独确认。
6. 构建产物不得携带真实 `.env`；密钥进入 OS credential vault 或服务端代理。

Claude Code 明确把权限规则与 OS 沙箱定义为互补层，[Configure permissions](https://code.claude.com/docs/en/permissions)；GitHub Copilot cloud agent 也禁止代理批准/合并自己的 PR，并让代理 PR 的 Actions 默认等待人工批准，[Risks and mitigations](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)。XCodeAgent 应采用同样的纵深防御思路，而不是把模型风险分类当作安全边界。

### 5.2 产品 Artifact 模型没有唯一答案

当前仓库同时存在以下互斥方案：

- 现行文档：RequirementSpec -> UI Design -> ProjectPlan -> PageDetail / EndpointDetail。
- 旧 `WORKFLOW.md`：Requirements -> Project Planning 两阶段。
- 新开发中方案：RequirementSpec -> ProductPlan -> UiDesign -> TechnicalPlan，并停止新 PageDetail。
- Entity 系列文档：独立 EntityDesign 可能再次拥有数据源、字段映射和确认门禁。

PageDetail 是否保留也没有结论：`docs/APPLICATION_DEVELOPMENT_PLANNING.md:L5-L12` 仍以 `plans/pages/` 判断状态；`docs/detail_confirmation_design.md:L71-L83` 继续设计 PageDetail batch；`docs/PRODUCT_UI_TECHNICAL_PLANNING.md:L111-L140` 则要求停止生成。

建议冻结为：

```text
RequirementSpec
  -> ProductPlan
  -> UiDesign
  -> TechnicalPlan
  -> EndpointDetail（仅复杂或缺失的 endpoint）
  -> Execution Unit Plan（用户可读）
  -> Build DAG（内部编译结果）
  -> ChangeSet + EvidenceSet
  -> Delivery
```

字段所有权建议如下：

| 产物 | 唯一拥有的语义 | 不应包含 |
| --- | --- | --- |
| RequirementSpec | 目标、角色、范围、业务流程、成功标准、约束 | 布局、API、表结构、代码任务 |
| ProductPlan | 页面树、业务信息、稳定 action、产品状态、导航、产品验收 | HTTP、Schema、数据库、代码路径 |
| UiDesign | 布局、组件、视觉层级、响应式、状态视觉 | API 内部逻辑、数据库操作 |
| TechnicalPlan | 架构、API Contract、Schema、全局数据模型/来源、权限实现、PageImplementationContract | 重复描述 UI |
| EndpointDetail | 单 endpoint 的查询、事务、副作用、异常和数据操作细节 | 修改已确认 API Schema 或全局数据模型 |
| Execution Unit Plan | 用户可理解的交付单元、依赖、完成标准、验证和回滚 | 低层 Agent 调度细节 |
| Build DAG | owner、文件范围、依赖、调度和机器验收 | 再次发明产品或技术语义 |

第一阶段不建议再引入独立 EntityDesign 正式产物；先让 TechnicalPlan 统一拥有全局数据模型和来源，EndpointDetail 只拥有单接口决策。若未来团队模式确实需要数据库负责人独立审核，再把 EntityDesign 提升为可选的团队治理产物。这样可以减少一个事实源和一轮强制确认。

每个下游产物必须保存直接上游 `artifactId + revision + sha256`。上游变更只失效受影响的下游，不允许按目录是否存在推断“已设计”。

### 5.3 用户确认 A，系统可能执行 B

`/application-development-planning/run` 已能生成并确认 `developmentTasks`，但当前前端没有挂载，它也不被 `prepare_build_tasks` 消费；真实执行由 Main Task Preparer 再次生成 Build DAG。现状是不可达的平行计划；一旦直接挂载，就会变成“用户确认 A、系统执行 B”。证据见 `docs/XCODEAGENT_COMPLETE_WORKFLOW.md:L263-L299`。

建议不要直接挂载现有组件。二选一：

- 推荐：把它重定义为 Execution Unit Plan，确认后由确定性 compiler 生成 Build DAG，并能逐条追溯。
- 更小方案：删除该流程和“确认后执行”文案，仅在现有 Build DAG 上提供用户可读投影。

必须建立以下追踪关系：

```text
requirementId
  -> product action / acceptance
  -> technical binding
  -> execution unit
  -> build task
  -> changed files
  -> checks and evidence
```

用户调整一个需求时，系统才能精确知道哪些确认、任务和证据失效，而不是全流程重跑或依赖模型判断。

### 5.4 “Integration Test”名实不符

现阶段主要验证依赖安装、TypeScript/build、Maven install 和静态 API contract，没有稳定覆盖后端启动、真实 HTTP smoke、页面加载、登录和关键业务路径。质量门又可能只对“已有检查”执行 `all(...)`，而不验证必需检查是否齐全。现有文档已经在 `docs/XCODEAGENT_COMPLETE_WORKFLOW.md:L930-L950` 明确指出该问题。

建议使用四级证据：

| 等级 | 用户文案 | 最低证据 |
| --- | --- | --- |
| L1 | 已完成代码生成 | 真实 diff、文件归属、无越权路径 |
| L2 | 已通过构建检查 | required manifest、命令、退出码、类型/编译/契约结果 |
| L3 | 已通过运行验证 | 后端 health、代表性 API、页面加载、console/network、关键路径 smoke |
| L4 | 已完成用户验收 | 验收版本 hash、用户决定、未验证项、交付动作 |

流程名称改为：

```text
Build Verification
  -> Launch
  -> Runtime Smoke
  -> Independent Review
  -> User Acceptance
```

每种项目类型先编译 `expected_checks`；所有 required ID 必须存在且执行，结果区分 `passed / failed / skipped_optional / missing_required / not_run`。实现 Agent 的自我总结不能成为 Review 结论。

Lovable 已把浏览器测试的点击步骤、截图、URL、console 和 network 作为可见证据，[Test your app in a browser](https://docs.lovable.dev/features/browser-testing)；Cursor 也建议把测试、类型检查、lint、自动 review 与人工 review 组合使用，[Reviewing and testing code](https://cursor.com/learn/reviewing-testing)。

### 5.5 运行结果不可稳定审计和重放

`docs/MODEL_OUTPUT_COMMUNICATION_DESIGN.md:L23-L102` 已证明：普通模型文本和 JSON 可能丢失；前端忽略 `RUN_ERROR`、`STATE_DELTA`、`MESSAGES_SNAPSHOT`、`ACTIVITY_*`、`REASONING_*` 和未知事件；完成后隐藏过程；Electron 持久化还会删除工具调用和过程步骤。

这意味着用户看到的“完成”无法稳定回答：模型说了什么、用了什么工具、测试为什么通过、失败发生在哪里、重启后证据去哪了。

建议优先落地该文档提出的核心架构：

- append-only run event ledger；事件有 `eventId + sequence + runId + source`。
- 统一 `ContentBlock`：text、JSON、tool、activity、artifact、error、unknown。
- exhaustive frontend adapter；未知类型必须成为折叠块，不能静默丢弃。
- 大内容落盘，界面保存摘要、hash、大小、截断标记和 artifact ref。
- 完成后 final answer 展开，过程、工具和 workflow 默认折叠但仍可查看。
- 结论标记证据等级：`observed / test-proven / user-confirmed / unverified`。

当前 `Backend/app/persistence/run_store.py` 虽定义了 JSONL store，但活跃主流程没有把它作为权威事件源；多数事件仍存在本轮 runtime 内存并依赖 AG-UI 输出。建议 append-only ledger 同时成为 AG-UI 投影、审计、指标、恢复和并发 fencing 的共同输入，而不是新增一份旁路日志。

不应展示私有思维链；应展示决策输入、执行动作、工具事实、验证证据和失败原因。

### 5.6 同一工作区没有真正写隔离

`resourceLocks` 目前只是显示 owner 的观察元数据；多个 Run 可以同时修改同一页面、API、菜单、报告和数据库资源，最新 writer 仅覆盖界面显示。证据见 `docs/XCODEAGENT_COMPLETE_WORKFLOW.md:L877-L884`。

代码侧风险比文档描述更深一层：`Backend/app/workspace/run_lease.py:L42-L64` 明确不按资源交集阻断跨 Run；单 Run 调度器又只做锁字符串相等比较，`frontend/src/**` 与 `frontend/src/a.tsx` 不会被视为冲突，见 `Backend/app/services/build_scheduler.py:L622-L680`。所以当前既没有跨 Run 隔离，也没有可靠的父子路径 / glob 冲突判断。

建议分两步：

1. 近期：一 workspace 只允许一个正式写 execution；只读问答、研究和预览可以并行。冲突请求进入可见队列，可选择停止当前任务或稍后执行。
2. 后续：提供 Local / Isolated Worktree 两种环境。需要并行写时每个任务使用独立 worktree/branch，合并阶段串行并展示冲突。

Codex、GitHub Copilot app、Cursor Cloud Agents 和 Devin Desktop 都把隔离环境或 worktree 作为并行代理的基础，而不是只做资源名标记：[Codex app](https://openai.com/index/introducing-the-codex-app/)、[GitHub Copilot app sessions](https://docs.github.com/en/copilot/how-tos/github-copilot-app/agent-sessions)、[Cursor Cloud Agents](https://cursor.com/docs/cloud-agent)、[Devin Desktop Cascade](https://docs.devin.ai/desktop/cascade/cascade)。

### 5.7 模板门禁可能假成功

当前 renderer 可能吞掉 clone 错误，页面写入为空也可能继续；后端完成 lifecycle 时只复核 RequirementSpec 和 ProjectPlan，而不复核真实模板目录和文件。详见 `docs/XCODEAGENT_COMPLETE_WORKFLOW.md:L236-L261` 和 `L888-L904`。

建议让后端成为模板完整性 verifier：

- 输入是 renderer 的实际写入 manifest，而不是 `succeeded=true`。
- 校验前后端目录、package/pom、入口、菜单、预期页面文件及数量。
- 校验 template version、source commit、hash 和缺失项。
- 失败时 lifecycle 保持可重试状态，并展示缺失项；不得写 `ready_for_workbench`。
- 模板完成后建立可恢复的 Git/checkpoint 基线。

## 6. P1：显著影响使用体验的问题

### 6.1 用户被迫理解内部 Graph 和数据模型

当前用户需要区分页面与 endpoint、设计修改与自由协作、主 Workflow 与 Conversation、页面会话与普通工作台会话。首次选择界面甚至要求用户决定“先设计页面还是接口”，见 `Frontend/src/renderer/src/components/DetailConfirmationPageSelector/index.tsx:L335-L450`。

这些是系统路由问题，不应成为普通用户的前置知识。建议：

- 只有一个 composer；顶部显示当前业务对象和当前模式摘要。
- 用户说“修改订单页筛选”，系统自动判定问答、局部修改或正式设计变更。
- 进入高成本或会使正式产物失效的流程前，展示范围卡：将修改哪些产品产物、代码、数据和验证项。
- 专家设置才允许显式选择 workflow、agent、resume node 或内部 target type。

Ask/Plan 与 Build/Act 的分离仍应保留，但用“只分析 / 执行修改”这类用户语言表达。Cursor、Claude Code、Cline、OpenCode 和 Replit 都把这种权限差异放在模式层，而不是要求用户理解内部 Graph：[Cursor Plan Mode](https://cursor.com/docs/agent/plan-mode)、[Claude permission modes](https://code.claude.com/docs/en/permission-modes)、[Cline Plan & Act](https://docs.cline.bot/core-workflows/plan-and-act)、[Replit Plan vs Build](https://docs.replit.com/learn/plan-vs-build-mode)。

### 6.2 确认疲劳与目标用户不清晰

开发中目标流程至少包含 RequirementSpec、ProductPlan、UiDesign、TechnicalPlan 和部分 EndpointDetail 的显式确认。若再加入 EntityDesign、页面任务计划、数据库变更和验收，单人用户会连续扮演产品、设计、架构、数据库和测试负责人。

建议先明确第一 ICP 为 **Single Builder：有一定技术判断力、需要生成或维护企业内部 Web 应用的个人开发者/产品工程师**。在这个模式下：

- 每个正式产物仍需显式确认，但同一审核面只展示变更、风险和开放问题，不要求逐页重复确认没有变化的内容。
- 低风险页面可批量审核；数据库写、权限、外部接口和范围变化单独突出。
- 确认按钮显示“确认的版本 hash、下游影响和下一步”，而不是只有“继续”。
- 允许保存草稿、退回修改和拒绝；修改后必须重新展示最终版本再确认。

开发中的 ProductPlan 实现目前仍用“继续”“无误”等自然语言子串识别确认，且没有把决定绑定到 artifact ID / revision / hash，见 `Backend/app/graph/nodes/product_planning.py:L39-L101`。JSON 与 Markdown 又是连续 `write_text`，并非注释所称的原子写入，见 `Backend/app/workspace/product_plan_documents.py:L109-L132`。这两点应在合并该改造前修正，否则新流程虽然层次更合理，确认本身仍不可信。

团队 reviewer、任务分派、审批审计和共享运行报告应放到后续 Team Mode，不要用连续弹窗假装团队协作。

### 6.3 计划编辑能力不对称

RequirementSpec 有结构化编辑和保存草稿，ProjectPlan 主要靠自然语言反馈后整体重生成，见 `docs/XCODEAGENT_COMPLETE_WORKFLOW.md:L127-L136`。这会增加模型成本，也使稳定 ID 和用户的局部修改不可靠。

建议 ProductPlan / TechnicalPlan 都提供结构化编辑：

- 页面树、角色、业务 action、API contract 和数据来源分别编辑。
- 显示 before/after diff、受影响下游和将失效的确认。
- 自然语言反馈作为生成 patch 的辅助，用户仍审阅确定性 diff。
- 禁止用户直接编辑内部 JSON；继续以 Markdown / 结构化 UI 为用户产物。

### 6.4 预览没有成为反馈和验证中枢

当前预览主要用于展示和最终验收；用户难以精确引用元素、console 错误或网络请求。建议增加：

- 元素选择后自动附带 selector、组件/源码位置、截图和当前路由。
- 一键把 console error、failed network request、accessibility issue 发送到当前任务。
- 验收模板来自 RequirementSpec / ProductPlan 的用户路径，而不是空白文本框。
- 桌面、平板、移动端视口与截图对比成为证据。
- 区分“模板骨架预览”和“可验收预览”，显示各自证据等级。

Devin Desktop Preview 已支持选取元素、console error 并将其作为代理上下文，[Previews](https://docs.devin.ai/desktop/previews)。XCodeAgent 可以进一步把这些反馈绑定到自己的需求、计划和验收版本，而不只是追加一条聊天消息。

### 6.5 恢复仍有多个事实源

正常恢复会同时涉及 lifecycle、LangGraph checkpoint、客户端 `resumeState`、磁盘 artifacts 和内部跳转。缺少 lifecycle 的旧工作区还可能绕过新门禁继续运行，见 `docs/XCODEAGENT_COMPLETE_WORKFLOW.md:L877-L884` 和 `L948-L960`。

建议：

- 客户端只提交 `threadId / executionId / interactionId / basedOnRevision / decision`。
- 服务端从 checkpoint 和已校验 artifact 恢复，不允许客户端提交完整业务状态覆盖服务器。
- 旧工作区先进入显式 migration，不静默兼容到正式 Build。
- 恢复前展示影响预览和不可恢复的外部副作用。
- 支持只恢复文件、文件+对话、文件+流程状态；数据库和部署用补偿动作，不承诺虚假的完整回滚。

Claude Code 支持分别恢复代码和对话，[Checkpointing](https://code.claude.com/docs/en/checkpointing)；Replit rollback 会说明代码、Agent memory、任务、配置和数据库的影响，[Checkpoints and Rollbacks](https://docs.replit.com/references/version-control/checkpoints-and-rollbacks)。

### 6.6 缺少版本基线和交付闭环

`docs/APPLICATION_CODE_COMMIT_REMINDER.md` 已提出模板完成、质量门通过、验收完成和快速修改完成时的 Git 提醒，但目前仍是设计文档。

建议把交付变成正式阶段：

```text
ChangeSet Review
  -> 选择文件与敏感项检查
  -> 展示 EvidenceSet
  -> 用户选择：保留本地 / checkpoint / commit / branch / PR / export
```

- 模板完成后建立初始化基线。
- 每个验收交付单元绑定需求版本、计划版本、run、真实 diff 和测试证据。
- 失败或停止时主操作是继续修复、审阅差异或撤销本轮，不建议提交半成品。
- 不自动 push、merge、amend 或覆盖用户已有 staged changes。
- commit message 或交付清单应能反查运行记录和确认版本。

GitHub Copilot 的代理 commit 会链接 session log，便于 code review 和审计，[Managing agent sessions](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/manage-and-track-agents)。这是 XCodeAgent 本地交付也应达到的可追溯水平。

### 6.7 缺少运行预算和成本反馈

当前文档详细讨论 token 上限，却缺少面向用户的预算体验。长链路可能生成多页 UI、技术计划、多个 endpoint、Build、重复安装、测试和修复；用户无法预判等待时间和成本。

建议每次执行前显示：

- 预计影响范围、预计模型调用数、最大重试、是否会安装依赖或联网。
- Local / Worktree、权限级别和可能修改的数据资源。
- 运行中显示耗时、token/成本、重试次数、当前阻塞和取消后保留内容。
- 相同失败重复出现时建议升级模型、缩小范围或请求用户决策，而不是无限修复。

Replit 已把模型档位、速度和成本放在同一运行选择中，并提供预算控制，[Agent Modes](https://docs.replit.com/replitai/assistant/)、[Managing spend](https://docs.replit.com/billing/managing-spend)。XCodeAgent 不必复制商业计费，但应让用户理解时间与资源代价。

### 6.8 开发中改造存在上下文放大和任务重复执行窗口

当前 ProductPlan 改造修复了初始化依赖顺序，但 UI 页面状态仍保存完整 TSX `code`，每次进度又投影全部已完成页面；Electron 还会在每条消息中深拷贝并持久化完整 workflow。相关路径为 `Backend/app/graph/nodes/ui_confirmation.py:L223-L256`、`Backend/app/protocols/workflow/runtime.py:L523-L548`、`Backend/app/protocols/workflow/projection.py:L195-L250`、`Frontend/src/main/sessionMessageNormalization.ts:L14-L43`。页数乘以消息数后，会同时放大 checkpoint、事件流、Electron 会话文件和恢复请求，直接违背 128k 有界上下文目标。

建议在该改造合并前改为 `codePath + sha256 + compact manifest`，源码按需读取；公共投影只包含当前页面、增量状态和有界摘要。

另外，Build 批次会先让 Agent 写入工作区，再在批次末统一结算任务结果和 Build DAG。如果进程在“文件已写、状态未 checkpoint”之间崩溃，恢复后可能重复执行副作用。每个任务应有持久 attempt：`dispatched -> side-effect evidence -> settled`，恢复时先按 workspace revision、changed-file hash 和证据对账，再决定是否重试。

## 7. 文档与产品治理问题

### 7.1 文档存在多套当前事实

同一流程至少有四种描述：`WORKFLOW.md`、`XCODEAGENT_COMPLETE_WORKFLOW.md`、`APPLICATION_LIFECYCLE.md` 和开发中的 `PRODUCT_UI_TECHNICAL_PLANNING.md`。`WORKFLOW.md:L810-L823` 的“当前不实现”仍列出多项已经实现的能力；`Backend/README.md` 也保留旧 requirement planner、development orchestrator 和任意 workspace_root 调用示例，容易误导新开发者和安全评估。

建议建立三层文档：

```text
docs/current/       由 manifest / schema / code projection 生成或校验的当前事实
docs/decisions/     有状态、日期、owner、supersedes 的 ADR / 产品决策
docs/archive/       历史审计、旧方案和被替代设计
```

每份设计文档头部必须包含：

```text
status: proposed | accepted | implementing | current | superseded
as_of:
owner:
source_of_truth:
supersedes:
superseded_by:
```

Graph 节点、lifecycle stage、前端阶段标签、resume 节点和 capabilities 应来自一个版本化 manifest；CI 校验文档和前端是否引用未知或遗漏阶段。

### 7.2 术语应从用户界面收口

用户界面建议只保留：

- 需求文档
- 产品方案
- UI 设计
- 技术方案
- 执行计划
- 构建检查
- 运行验证
- 验收与交付

`ProjectState`、`Graph`、`checkpoint`、`resume_from`、`EndpointDetail`、`Build DAG`、`AG-UI` 等只在专家诊断和开发文档出现。

## 8. 竞品对照

截至 2026-08-13，成熟产品正在收敛为五层：意图、执行环境、信任边界、验证证据和恢复。XCodeAgent 的差距和机会如下。

| 产品 / 范式 | 已成熟机制 | XCodeAgent 应借鉴 | 不应盲抄 |
| --- | --- | --- | --- |
| Cursor | Plan、checkpoint、消息队列/打断、Cloud Agent、独立 Review | 计划可编辑；完成页给 diff、测试、截图、日志；Preview 参与验证 | 本地默认直接落盘和云端规模不适合作为近期安全基线 |
| Claude Code | 权限模式、OS 沙箱、hooks、代码/对话分维 rewind、受限子代理 | 沙箱与审批分层；hooks 做确定性政策；子代理最小工具面 | 专家配置过宽；普通用户不应看到 bypass 模式 |
| OpenAI Codex | Local / Worktree / Cloud、handoff、并行线程、sandbox + approval | 把执行位置和隔离作为一等概念；安全 handoff；脱敏遥测 | 通用任务线程不能替代需求和计划产物 |
| GitHub Copilot | 临时环境、session log、签名 commit、draft PR 人工落闸 | 每个交付可反查运行和验证；高风险外部动作必须候选化 | 不把 GitHub PR 作为唯一交付形式 |
| Devin Desktop | 连续 Todo、消息队列、named checkpoint、交互 Preview | 元素/错误直接反馈；实时问题面板成为上下文 | “实时感知”必须显示采集范围；避免不可逆 rollback |
| Cline / Roo Code | Plan/Act、逐工具审批、shadow Git、角色文件权限、编排代理无写权 | 权限按能力分类；子代理任务与工具可见；编排层最小权限 | 不让模型独自判断命令风险；避免大量 Mode 配置 |
| OpenCode | 简洁 Build/Plan、只读子代理、child session、allow/ask/deny | 运行时底座保持简单；child session 可导航；大输出隔离 | 不为灵活性牺牲正式产物与验收 |
| Replit / Lovable / v0 | 可编辑计划、Done/Out of scope、全栈 Preview、浏览器测试、checkpoint、发布快照 | 计划补齐完成标准和范围外；Preview 变验收台；显式发布 | 托管环境优势不能直接套到任意本地项目 |

官方资料：

- Cursor：[Agent Overview](https://cursor.com/docs/agent/overview)、[Plan Mode](https://cursor.com/docs/agent/plan-mode)、[Cloud Agents](https://cursor.com/docs/cloud-agent)、[Agent Security](https://cursor.com/docs/agent/security)。
- Claude Code：[Permission modes](https://code.claude.com/docs/en/permission-modes)、[Permissions](https://code.claude.com/docs/en/permissions)、[Hooks](https://code.claude.com/docs/en/hooks-guide)、[Subagents](https://code.claude.com/docs/en/sub-agents)。
- OpenAI Codex：[Codex app](https://openai.com/index/introducing-the-codex-app/)、[Secure agents](https://openai.com/index/introducing-upgrades-to-codex/)。
- GitHub Copilot：[Agent sessions](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/manage-and-track-agents)、[Cloud agent risks](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)、[Copilot app sessions](https://docs.github.com/en/copilot/how-tos/github-copilot-app/agent-sessions)。
- Devin Desktop：截至本报告日期，旧 Windsurf 文档入口已迁移/重定向，应以 [Cascade](https://docs.devin.ai/desktop/cascade/cascade) 和 [Previews](https://docs.devin.ai/desktop/previews) 的当前官方边界为准。
- Cline：[Plan & Act](https://docs.cline.bot/core-workflows/plan-and-act)、[Checkpoints](https://docs.cline.bot/core-workflows/checkpoints)。
- Roo Code：[Modes](https://docs.roocode.com/features/custom-modes)、[Boomerang Tasks](https://docs.roocode.com/features/boomerang-tasks)。
- OpenCode：[Agents](https://opencode.ai/docs/agents/)、[Permissions](https://opencode.ai/v2/docs/permissions)。
- Replit：[Plan vs Build](https://docs.replit.com/learn/plan-vs-build-mode)、[Build with Agent](https://docs.replit.com/learn/build-with-agent)、[Checkpoints and Rollbacks](https://docs.replit.com/references/version-control/checkpoints-and-rollbacks)。
- Lovable：[Plan mode](https://docs.lovable.dev/features/plan-mode)、[Browser testing](https://docs.lovable.dev/features/browser-testing)、[Security](https://docs.lovable.dev/features/security)。
- v0：[Quickstart](https://api2.v0.dev/docs/quickstart)、[Sandbox](https://api2.v0.dev/docs/sandbox)、[Git and preview behavior](https://api2.v0.dev/docs/faqs)。

## 9. 推荐目标流程

### 9.1 单人本地交付主链

```text
1. 选择新项目或现有项目
   -> 展示 Git 状态、执行环境和安全边界

2. 理解目标
   -> 用户、问题、范围、约束、已有系统、Done

3. RequirementSpec
   -> 编辑、diff、确认版本

4. ProductPlan
   -> 页面、action、状态、导航、产品验收
   -> 编辑、影响分析、确认版本

5. UiDesign
   -> 按页面生成；可批量审阅变化
   -> 确认版本和上游 hash

6. TechnicalPlan
   -> API、Schema、数据模型、权限、实现绑定、风险、验证、回滚
   -> 确认版本

7. Execution Contract
   -> Local 或 Isolated Worktree
   -> Unit Plan、预计成本、权限预算、expected checks
   -> 用户确认开始执行

8. Milestone execution
   -> 每个 Unit 有输入、输出、文件范围、checkpoint、Done 和证据

9. Build Verification
   -> required checks 必须齐全

10. Runtime Smoke + Independent Review
    -> API、页面路径、console/network、未验证项

11. User Acceptance
    -> 按 ProductPlan 验收路径检查

12. Delivery
    -> 保留本地 / checkpoint / commit / branch / PR / export
```

### 9.2 小任务快速通道

不是所有请求都需要完整瀑布流程。建议按风险编译流程：

| 请求 | 默认流程 |
| --- | --- |
| 只读问答、解释、研究 | 只读模式，不创建正式产物 |
| 文档、注释、局部样式和确定性小修 | 范围卡 -> 修改 -> 聚焦验证 -> diff/撤销 |
| 页面行为、单 endpoint 实现且契约不变 | Unit Plan -> 修改 -> affected checks -> 验收 |
| 新页面、API Schema、权限、数据库或跨模块变更 | 正式 Product / Technical 流程 |
| 数据库写、外部发布、push、迁移 | 正式流程 + 单独高风险审批 |

升级条件必须由确定性规则和真实 diff 辅助判断；模型分类只能提供建议，不能绕过门禁。

## 10. 推荐信息架构

工作台建议围绕任务和证据组织，而不是围绕 Agent 角色组织：

```text
Project
├── Overview：当前版本、风险、待确认、最近交付
├── Product：需求文档、产品方案、UI 设计
├── Build：执行计划、任务、运行时间线
├── Preview：可交互预览、验收路径、错误与截图
├── Changes：diff、checkpoint、commit/PR/export
├── Evidence：build、API、browser、review、安全检查
└── Settings：模型、权限、环境、Skills、数据源
```

页面和 API 大纲仍可作为业务导航，但状态统一为：

```text
未规划 -> 待确认 -> 已确认 -> 已失效 -> 构建中 -> 验证中 -> 可验收 -> 已交付
```

状态来源是 Artifact Index 和 EvidenceSet，不再以目录是否为空或前端临时标记判断。

## 11. 路线图

### Phase 0：冻结产品契约

目标：停止继续制造平行事实源。

- 决定并记录唯一 Artifact DAG、字段 owner、PageDetail 删除、EntityDesign 暂缓、Unit Plan / Build DAG 关系。
- 给所有设计文档补 status / owner / supersedes，旧文档归档。
- 用单一 manifest 对齐 Graph、lifecycle、前端阶段和 capabilities。
- 完成正在开发的 ProductPlan / TechnicalPlan 迁移，包括前端文案、恢复和历史工作区迁移。

退出标准：任意产品字段只有一个 owner；用户确认的每个正式产物都有 revision/hash；文档和 UI 只显示一套当前流程。

### Phase 1：可信执行底座

目标：让“完成”和“可恢复”成为真实承诺。

- 修复安全审计 P0：capability、workspace registry、统一 Tool Policy、密钥、沙箱/网络。
- 单 workspace 单写 execution；只读任务并行。
- 后端模板 manifest 门禁。
- canonical event ledger、ContentBlock reducer、错误和工具过程持久化。
- required-check manifest 和四级验证证据。
- checkpoint 影响预览与文件/对话/流程分维恢复。

退出标准：不存在无证据的“测试通过”；重启后能重放一次完整运行；并发 Run 不会覆盖同一工作区；恢复操作能说明影响。

### Phase 2：简化用户旅程

目标：让用户围绕业务目标工作，而不是操纵 Graph。

- 单 composer 自动路由和执行前范围卡。
- ProductPlan / TechnicalPlan 结构化编辑、diff 和失效分析。
- Preview 元素、截图、console、network 反馈。
- Execution Unit Plan 确定性编译 Build DAG。
- Git 基线、ChangeSet Review 和显式交付。
- 时间、重试、成本和权限预算可见。

退出标准：一个新用户不需要理解 endpoint/Graph/thread 也能从需求走到可验收交付；计划条目可追踪到文件和证据。

### Phase 3：隔离并行与团队能力

目标：在可信单人闭环上扩展，而不是提前平台化。

- Isolated Worktree、Local <-> Worktree handoff、串行合并门。
- reviewer、只读运行分享、审批审计和组织权限模板。
- hooks / policy 扩展、脱敏遥测导出。
- GitHub/GitLab PR 和显式部署 adapter。
- 有明确需求时再启用独立 EntityDesign / 数据库负责人流程。

## 12. 建议指标

不要只统计 token、任务数和代码行。建议用以下指标判断产品是否真的更好：

| 维度 | 指标 |
| --- | --- |
| 首次价值 | 从创建到第一个可交互预览的中位时间 |
| 需求质量 | RequirementSpec / ProductPlan 首次确认率；平均澄清轮数 |
| 确认负担 | 每个交付单元的人工确认次数、平均审阅时长 |
| 执行可靠性 | 无人工补救完成率；自动修复后最终通过率 |
| 证据质量 | required checks 完整率；missing-required 发生率 |
| 运行真实性 | L2 通过但 L3 失败的比例，即“假通过率” |
| 纠偏成本 | 每次交付的用户 steer 次数、重复失败次数、回滚率 |
| 恢复能力 | 重启/崩溃后成功续跑率；恢复到错误 revision 的事件数 |
| 变更质量 | 越权文件修改率；用户验收时发现的非目标改动率 |
| 交付闭环 | 验收后形成 checkpoint/commit/export 的比例 |
| 成本 | 每个验收通过 Unit 的 token、时间和模型调用数 |
| 安全 | 未授权高风险动作、敏感文件访问和网络外发阻断数 |

## 13. 建议立即停止或暂缓的事项

在 Phase 0 和 Phase 1 完成前，建议：

- 不新增新的正式规划产物或确认页面。
- 不直接挂载当前独立 Application Development Planning 流程。
- 不再扩展 PageDetail；优先完成 PageImplementationContract 迁移。
- 不把多 Run 并行写包装成产品能力。
- 不继续使用“Integration Test 已通过”这类强于证据的文案。
- 不把团队分工、部署和更多 Provider 作为近期主卖点。
- 不让旧文档继续以“当前设计”身份并存。
- 不把 Graph、AG-UI、endpoint、checkpoint 等内部术语暴露为普通用户必须理解的概念。

## 14. 最终判断

XCodeAgent 的方向不是错，甚至比许多只做聊天和代码补全的产品更接近完整交付。但当前的复杂度主要被系统内部结构吸收得不够，转而暴露给了用户和维护者：多套计划、多套状态、多次确认、多种会话和多个恢复来源。

下一阶段最有价值的工作不是再增加功能，而是完成一次“产品语义收口”：

```text
一个目标
-> 一组有版本的正式产物
-> 一份用户确认的执行契约
-> 一个可追溯的任务图
-> 一套真实验证证据
-> 一个可恢复、可交付的变更集
```

做到这一点后，XCodeAgent 才会从“功能很多的 Agent 工程”变成“用户敢把真实项目交给它的产品”。
