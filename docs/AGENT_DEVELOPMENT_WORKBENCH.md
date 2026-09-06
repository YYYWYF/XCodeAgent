# Agent Development Workbench

> 状态：已确认设计，第一期已实现
> 确认日期：2026-09-06
> 适用范围：业务 Agent 在开发阶段的目标选择、依赖门禁、Build、测试、试聊、审查与验收

## 1. 文档目标

本文档定义业务 Agent 如何成为现有应用开发工作台中的一等开发目标，并复用现有页面、Endpoint、实体的会话、AG-UI、Build DAG、Diff、Testing、Code Review、Launch 和 Acceptance 链路。

本设计不重新定义 Agent Contract、Agent Runtime 或 Agent CodeRunner。它补齐的是当前已有“规划与生成能力”和缺失的“工作台用户旅程”之间的产品闭环。

## 2. 当前基线

已实现：

- RequirementSpec `agent_requirements[]` 定义业务 Agent 需求。
- ProductPlan v6 `agents[]` 定义 Agent 名称、用途、能力、页面入口、交互和产品验收事实。
- TechnicalPlan `agent_contracts[]` 保存完整派生执行快照，包含七段 `agentSettings`、Gateway、Tool Endpoint、Runtime、Security、Artifacts 和 Required Checks。
- 含 Agent 的应用会在 TechnicalPlan 确认后条件式准备 `agent-runtime/` 模板。
- Build DAG 已支持平台 readiness Unit `agent:runtime`、业务 Unit `agent:<agentId>` 和 `owner=agent`。
- Agent Runtime CodeRunner 已可基于完整 Contract 生成 Agent 定义、Tool Adapter 和测试文件。

未实现：

- 开发大纲中的 Agent 分组和 Agent 目标选择。
- `development_target.type=agent` 的端到端协议、生命周期和恢复。
- Agent 专属依赖门禁和可跳转的阻塞提示。
- Agent Runtime、Java Gateway、页面入口的真实启动联调。
- 通过 Java Gateway 访问 Agent Runtime 的 Electron 试聊。
- Agent 专属的测试、审查、运行和验收证据投影。
- Agent Settings 的 draft/candidate 或 candidate候选版本。

## 3. 核心原则

1. Agent 是应用版本内的业务产物，与页面、API Endpoint 和实体并列，不是 XCodeAgent 内部执行 Agent。
2. 不新建第二套工作台、顶层 Workflow、聊天框架、测试阶段或验收阶段。
3. ProductPlan 和 TechnicalPlan Agent Contract 是规划事实来源；生成的 Python/Java/Frontend 源码不能反向成为设计事实。
4. 第一期开发工作台只读展示七段 Agent Settings；用户要求修改时进入正式 TechnicalPlan 修订，不在开发页直接改写已确认 Contract。
5. 页面调用只能通过 Java Agent Gateway 进入 Python sidecar；Renderer 和生成前端禁止直连 sidecar。
6. Agent Build 仍使用现有 `/workflow/run`、Build DAG 确认、BuildScheduler 和质量链路。
7. 当前只实现当前 Contract 与当前存储形状，不增加历史兼容分支。

## 4. 信息架构

开发大纲在现有页面、API 和实体之外增加 Agent 分组：

```text
应用开发
├── 页面
├── API
├── 实体
└── 智能体
    ├── 智能回检助手
    └── 工单处理助手
```

Agent 节点至少展示：

- Agent 名称与 `agentId`。
- 产品用途。
- 主入口页面。
- 综合状态。
- 阻塞依赖数或当前执行进度。

节点状态由现有权威事实投影，不新增一份可独立漂移的 Agent 状态文件。

## 5. 事实与所有权

| 信息 | 唯一权威来源 | 开发阶段权限 |
| --- | --- | --- |
| 名称、职责、能力、页面入口、交互、业务边界、产品验收 | ProductPlan `agents[]` | 只读；修改进入 Product/Technical 正式修订 |
| System Prompt、Model、Memory、Tools、Skills、Knowledge、Context | TechnicalPlan `agent_contracts[].agentSettings` | 第一期只读；修改进入 TechnicalPlan 正式修订 |
| Gateway、Tool Endpoint 快照、Runtime、Security、Artifacts、Checks | TechnicalPlan `agent_contracts[]` 平台派生字段 | 只读，用于依赖门禁和 Build |
| EntitySourceBinding | `.xcodeagent/plans/entities/` | 依赖门禁读取；修改使用实体设计流 |
| Build DAG 与 Build Run | `.xcodeagent/plans/build-task-plan.json` 和 Build Run 绑定副本 | 按现有确认与执行规则 |
| 源码、Diff、测试、审查和验收证据 | 当前 Workspace 与对应 Run 产物 | 只能证明实现状态，不改写上游 Contract |

### 5.1 不新增重复 Agent 设计 Artifact

当前 Agent Contract 已包含产品身份、交互、七段 Settings、工具 Endpoint 快照、运行时、安全、产物和验收信息。第一期不再生成原型中的“十段式 Agent 设计 Markdown/JSON”，避免出现两份完整设计、两个确认状态和不可判定的 Build 输入。

工作台的 Agent 详情页是对已确认 ProductPlan/TechnicalPlan 的读模型，不是新的正式产物。

## 6. Agent 开发目标合同

现有页面和 Endpoint 目标联合扩展 Agent 分支：

```json
{
  "type": "agent",
  "agentId": "inspection_assistant",
  "label": "智能回检助手"
}
```

约束：

- `agentId` 必须在已确认 ProductPlan `agents[]` 与 TechnicalPlan `agent_contracts[]` 中各唯一命中一项。
- `label` 只用于展示，服务端始终从 ProductPlan 重新投影名称。
- 请求不接受客户端提交的 Contract、Settings、允许路径、工具列表、Schema 或实体绑定快照。
- 服务端从当前工作区重读已确认 ProductPlan、TechnicalPlan 和 EntitySourceBinding。
- Agent 目标使用独立 execution thread，但消息仍保存在当前通用开发会话，不把会话固定归属给 Agent。
- 一个工作区内的正式写执行继续遵守现有资源锁和串行边界。

Agent 目标必须同步进入当前合同面：

- Workflow request 与严格前后端类型。
- Graph State 中的当前 development target。
- `application-lifecycle.json` 的 execution scope/resource key。
- 依赖阻塞 continuation 的 target。
- Build DAG scope 和 Build Run 绑定。
- AG-UI Summary/State/Result 投影。
- 会话恢复、旧卡拒绝和资源锁校验。

## 7. 工作台 Agent 详情页

点击 Agent 节点后，工作台中区保留当前对话与 Workflow 卡，右侧工作区提供以下页签：

### 7.1 概览

- 名称、职责、`agentId` 和 Contract Hash。
- 核心能力与预期结果。
- 入口页面、页面 Action 和交互模式。
- 业务边界和产品验收标准。
- Runtime 为 Python 3.12 + DeepAgents sidecar。
- 当前开发状态和阻塞项。

### 7.2 Agent Settings

完整展示七段设置：

1. System Prompt。
2. Model。
3. Memory。
4. Tools。
5. Skills。
6. Knowledge。
7. Context。

每一段必须区分：

- 当前已启用并会进入 Runtime 的配置。
- 已声明但被当前平台固定关闭的能力。
- 配置来自 ProductPlan 还是 TechnicalPlan 候选。
- 是否需要写操作审批。
- 当前 Runtime 是否已实现该 Adapter。

第一期不显示可交互但无法生效的假开关。未实现的 MySQL/OSS 短期记忆、长期记忆、Skill Loader、Knowledge Retriever 和 Summary Compression 展示为“当前版本未启用”。

### 7.3 依赖

- Java Agent Gateway Endpoint。
- Tool Endpoint 及所属 API Contract。
- 请求/响应 Schema。
- Tool 涉及的实体与 EntitySourceBinding。
- 入口页面与 Action。
- Runtime 模板和真实 commit。

每个依赖显示 `ready` / `blocked` / `not_applicable`，阻塞项提供可验证的理由和定向跳转，不使用名称模糊匹配。

### 7.4 实现与证据

复用现有右侧源码、Diff、阶段产物、测试报告和审查报告能力，展示：

- Agent 定义文件。
- Tool Adapter 文件。
- Agent 单元测试文件。
- Java Gateway 和页面入口的相关改动。
- 当前 Build Run 的任务、Diff、检查和错误。

## 8. 确定性依赖门禁

Agent 进入 `inspect_workspace` 和 Build DAG 之前执行 Agent 依赖复检。

### 8.1 门禁项

1. RequirementSpec、ProductPlan、UiManifest 和 TechnicalPlan 满足当前正式确认要求。
2. ProductPlan 与 TechnicalPlan 能通过 `agentId` 唯一对齐。
3. Agent Contract 保存的 ProductPlan Hash 与当前规范化 ProductPlan 一致。
4. 模板 manifest 声明 `agentRuntime.required=true`，且 `agent-runtime/` 的来源、分支、commit 和入口通过 readiness。
5. `gatewayEndpointId` 唯一指向当前 TechnicalPlan 的 Java Agent Gateway Endpoint。
6. 每个 Tool Binding 的 API Contract、Endpoint、Method、Path 和 Schema 快照与当前 TechnicalPlan 一致。
7. Tool Endpoint 引用的实体已完成当前 EntitySourceBinding，且当前 Endpoint 所需外部 API 操作映射已确认。
8. 页面入口和 Action 稳定引用仍存在。
9. Agent Settings 没有启用当前 Runtime 未实现的能力。
10. 所有 required checks 可被当前质量链路解释，不允许模型自行宣布豁免。

### 8.2 门禁结果

```json
{
  "ready": false,
  "target": {
    "type": "agent",
    "agentId": "inspection_assistant",
    "label": "智能回检助手"
  },
  "blockers": [
    {
      "type": "entity_source_binding",
      "targetId": "inspection_task",
      "message": "回检任务实体尚未完成数据源绑定",
      "action": "start_entity_binding"
    }
  ]
}
```

公开结果只包含稳定 ID、用户可理解摘要和可执行动作，不包含凭据、完整 Schema、System Prompt 原文或服务端物理路径。

如果只缺 EntitySourceBinding，复用现有实体设计 continuation；continuation target 扩展 Agent 稳定身份。实体确认后只提供“继续开发 Agent”动作，不自动启动 Build。

如果是 Contract Hash、Endpoint 或页面 Action 失效，不尝试确定性修复产品语义，必须进入正式规划修订。

## 9. Build DAG 与生成边界

Agent 目标复用当前 Build DAG，其最小业务闭包为：

```text
Agent Runtime template readiness
  -> Tool Endpoint 所属 Java 实现
  -> Java Agent Gateway
  -> agent:<agentId> Python 实现
  -> ProductPlan 声明的页面 Action 入口
```

已完成且与当前正式 Contract 对齐的前置 Unit 按现有任务保留/重用规则处理，不重复生成。未完成的前置 Unit 必须进入同一份待确认 Build DAG，不在 Agent 生成器内隐式补建。

### 9.1 平台 Unit

`agent:runtime` 只是模板完整性与 Runtime 基线的确定性 readiness Unit：

- 不生成模型 Build 任务。
- 不允许 Agent CodeRunner 修改共享 Factory、Context、Settings 或 Server 基线，除非未来有单独确认的 Runtime 升级批次。
- 不通过文件目录存在反向推断 Contract。

### 9.2 业务 Agent Unit

`agent:<agentId>` 继续编译为唯一 `owner=agent` 实现任务，且只能修改 Contract 确定的三个文件：

```text
agent-runtime/src/app/agent/<agent_id>.py
agent-runtime/src/app/tools/<agent_id>_tools.py
agent-runtime/tests/test_<agent_id>.py
```

Agent Runtime CodeRunner：

- 必须读取 `agent-runtime-generate` Skill。
- 只接收当前 Agent Contract 和它实际引用的 Java API Contract/Schema。
- 使用模板注入的 Model、RuntimeContext 和 Checkpointer。
- 业务模块使用 `create_deep_agent`，不自行初始化第二个模型。
- 不修改 Frontend、Java Backend、正式规划产物或 `.xcodeagent/`。

Java Gateway 仍由 Data Source/Backend owner 负责，页面入口仍由 Frontend owner 负责；不把两者写权扩大给 Agent CodeRunner。

## 10. AG-UI 执行链路

Agent 开发不增加独立产品 Endpoint，使用当前 `/workflow/run`。

```text
选择 Agent
  -> 创建当前开发阶段的通用会话/execution thread
  -> development_readiness_gate
  -> inspect_workspace
  -> prepare_build_tasks
  -> Build DAG 显式确认
  -> build
  -> unit_test / unit_test_repair
  -> 测试阶段显式确认
  -> integration_test / small_task_repair
  -> 审查阶段显式确认
  -> code_review / code_review_repair
  -> 验收阶段显式确认
  -> launch_project
  -> Agent 试聊与 acceptance_review
```

所有新增或实质修改的产品动作必须使用 `@ag-ui/client` 与 `@ag-ui/core`，并保持 run start、assistant message、结构化 result/error、state snapshot/delta 和唯一 run finish。

不接受客户端用 `project_plan`、`tasks`、`agent_contracts`、目标路径或客户端状态回填服务端事实。恢复时重读工作区与原 checkpoint，并校验 thread、run、target、Contract Hash 和 Build Plan digest。

## 11. 运行、试聊与页面联调

### 11.1 运行链路

```text
Electron 内的生成应用页面
  -> Java Agent Gateway
  -> Python Agent Runtime AG-UI
  -> DeepAgents
  -> Java Tool Gateway
  -> 业务 API / 数据源
```

Renderer 不知道 Python sidecar 地址或内部 token。Java Gateway 负责：

- 完成应用用户认证与授权。
- 绑定 user、tenant、scope、thread、run 和 trace。
- 将受限的可信 RuntimeContext 传给 sidecar。
- 把 Python AG-UI SSE 转发给生成前端。
- 隐藏 sidecar 凭据、异常栈和内部拓扑。

### 11.2 工作台试聊

工作台右侧增加“Agent 试聊”视图，但仍通过生成应用的 Java Gateway 访问候选运行时。

试聊展示：

- Agent 名称、`agentId` 和 Contract Hash。
- Runtime 与 Java Gateway 健康状态。
- 当前 thread/run 身份。
- 用户消息与 Agent 回复。
- Tool 调用名称、状态、耗时、有界证据摘要和审批结果。
- 短期记忆是否启用。
- Gateway、Model、Tool 或 Runtime 类别的失败与可重试动作。
- 当前运行的是待验收候选还是已生效版本。

试聊不展示模型隐藏思维链、完整 System Prompt、凭据、无界 Tool 结果或原始异常栈。

## 12. 测试、审查与验收

Agent 使用应用共享质量阶段，不建立平行流程。下列检查编译为当前测试/审查证据的 Agent 模块：

1. Python 语法、导入和生成文件路径检查。
2. `uv run --project agent-runtime pytest agent-runtime/tests/test_<agent_id>.py`。
3. Agent Factory 能按 `agentId` 动态加载业务模块。
4. System Prompt、Model、Memory 和 Tool Binding 与当前 Contract 一致。
5. Tool Adapter 只能访问 Contract 允许的 Endpoint，不允许用户消息覆盖可信身份和 Scope。
6. Agent Runtime `/health` 正常。
7. Java Gateway 能完成 AG-UI SSE 生命周期，并处理取消、断线和错误终态。
8. 页面 Action 通过 Java Gateway 触发正确 Agent。
9. 连续追问在启用短期记忆时保持同一 thread 上下文。
10. 越权 Tool、未授权数据和写操作审批失败能被安全拒绝。

验收结果必须绑定：

- `agentId` 和 Contract Hash。
- Build Plan digest 与 Build Run ID。
- 真实代码 Diff。
- Agent 单元测试、联调和健康检查证据。
- Code Review 结果。
- 试聊与页面联调证据。
- 用户显式验收决定。

上述证据不完整时，Agent 不投影为“已完成”。

## 13. 状态投影

工作台可使用下列用户可读状态，但它们必须从现有权威事实计算：

| 展示状态 | 投影依据 |
| --- | --- |
| 待开发 | 有已确认 Contract，无 Agent 目标的已完成 Build/验收证据 |
| 依赖阻塞 | 确定性 readiness 返回 blockers |
| 待确认任务 | 当前 Agent scope 存在 pending BuildTaskPlan |
| 开发中 | 资源锁指向运行中的 Agent execution |
| 待测试 | Build 完成且停在 test-phase confirmation |
| 待审查 | Testing 通过且停在 review-phase confirmation |
| 待验收 | Code Review 通过且停在 acceptance-phase confirmation |
| 已完成 | 当前 Contract Hash 绑定的候选实现已通过质量链并被用户验收 |
| 已失效 | 上游 ProductPlan/TechnicalPlan/Endpoint/EntitySourceBinding 发生与 Agent 相关的变化 |
| 失败 | 当前 Run 失败；保留证据与安全重试/修订动作 |

不新增 `agent-status.json`。如果现有生命周期和 Build/验收产物无法稳定投影某个状态，必须先补齐现有权威边界，不得用前端本地布尔值替代。

## 14. 配置修改与候选版本

本章为第二期设计边界，不在第一批实施。

### 14.1 第一期

- Agent Settings 只读。
- “修改配置”只发起正式 TechnicalPlan 修订。
- 修订确认后依确定性影响范围使原 Agent Build 证据失效，并返回开发阶段重新 Build。
- 不保存独立 draft/candidate/active 配置。

### 14.2 第二期候选模型

如果后续确认需要在开发工作台直接编辑 Settings，必须先重新设计当前 Agent Contract 的字段所有权，不能在其上直接叠加另一份完整 Contract。

候选快照最少绑定：

```json
{
  "agentId": "inspection_assistant",
  "basedOnContractHash": "sha256:...",
  "candidateHash": "sha256:...",
  "changedSettings": ["prompt", "tools"],
  "status": "draft | confirmed | built | tested | accepted | abandoned"
}
```

确定性派生的 Endpoint 快照、Runtime、Security、Artifacts 不作为可编辑字段重复保存。candidate 在验收前不得影响已生效代码、页面调用或试聊。

## 15. 失效、并发与恢复

- ProductPlan Agent 身份、能力或入口变更，使对应 Agent 实现和页面入口证据失效。
- Agent Settings、Gateway、Tool Binding、Runtime 或 Security 变更，使对应 Agent 与相关联调证据失效。
- Tool Endpoint/Schema 变更，仅使引用该 Endpoint 的 Agent 失效。
- EntitySourceBinding 变更，仅使通过相关 Tool Endpoint 消费该实体的 Agent 失效。
- 无关页面、Endpoint、实体或 Agent 变更不得扩大失效范围。
- 同一 Agent 不能同时存在两个正式写执行。其他会话可只读查看状态，不能以前端选中状态绕过资源锁。
- 旧卡、旧 continuation、旧 Build Plan 或旧 Contract Hash 在服务端检查失败时必须被拒绝。
- 恢复只能从原 thread/checkpoint 与最新工作区产物继续，前端不构造丢失的任务或 Contract。

## 16. 安全与隐私

1. System Prompt 仅在受权限的 Contract 阅读视图和 Build 上下文中出现，不进入无关会话、AG-UI 公开摘要或默认日志。
2. 模型 Key、Java Gateway token、sidecar 内部 token、数据源凭据和私钥不进入 Contract、任务、Diff、前端、会话或安装包。
3. RuntimeContext 的 user/tenant/scope/thread/run/trace 只由 Java Gateway 通过内部认证通道注入。
4. Tool Adapter 只能访问 Contract 声明的 Java Endpoint；客户端参数不能改写 Base URL、认证头或用户 Scope。
5. 写 Tool 使用平台托管审批，数据库写、网络、依赖安装、发布和部署仍使用既有高风险边界。
6. 试聊和预览不得将宿主文件系统、Electron preload 或 XCodeAgent 自身凭据暴露给生成应用。

## 17. 错误与用户动作

| 失败类别 | 用户视图 | 可执行动作 |
| --- | --- | --- |
| Contract/Hash 不一致 | 规划已变更，当前 Agent 无法继续 | 返回规划阶段修订/重新确认 |
| 实体未绑定 | 列出实体和受影响 Tool | 开始实体设计、重新检测 |
| Endpoint/Schema 失效 | 列出稳定 Endpoint ID | 返回规划修订 |
| Runtime 模板不就绪 | 显示 manifest 失败摘要 | 重试模板准备 |
| Build 失败 | 显示失败任务、有界日志和 Diff | 复用当前修复/重试链路 |
| Runtime 不健康 | 区分 Java Gateway 和 Python sidecar | 重启候选应用、查看启动证据 |
| Model/Tool 调用失败 | 显示安全分类和 trace ID | 重试消息、查看工具摘要或返回修订 |
| 验收拒绝 | 保留候选证据和用户意见 | 进入现有修复链路或放弃当前执行 |

## 18. 分期实施

### Phase 1：Agent 一等目标与 Build 入口

本批只实现最小可闭合范围：

- 工作台从已确认 ProductPlan/TechnicalPlan 投影 Agent 大纲。
- Agent 节点、概览、七段 Settings 只读视图和依赖视图。
- `development_target.type=agent` 进入现有 `/workflow/run`。
- Agent readiness 和 EntitySourceBinding continuation。
- Agent scope 的 Build DAG 生成、确认与现有 Agent CodeRunner 执行。
- 工作台展示真实 Agent 文件与 Diff。

不在本批实现：

- Agent Settings 直接编辑。
- draft/candidate/active 配置版本。
- Skills、Knowledge、长期记忆、MySQL/OSS Memory 或压缩 Adapter。
- 独立 Agent 设计 Markdown/JSON。
- 平行 Workflow 或新的普通 REST/SSE 协议。

### Phase 2：运行启动与试聊

- 生成应用启动时编排 Java Backend、Agent Runtime 和 Frontend。
- 将 Agent Runtime 健康检查加入 Launch readiness。
- 联调 Java Agent Gateway 与 Python AG-UI SSE。
- 在工作台中通过 Java Gateway 提供候选 Agent 试聊。
- 补齐页面 Action 真实调用与错误恢复。

### Phase 3：Agent 质量与验收证据

- 把 Agent required checks 编译进入 Unit/Integration Testing。
- Code Review 增加 Agent Runtime、Tool 授权、Prompt/配置一致性检查。
- Acceptance 绑定 Contract Hash、Diff、测试、Launch 和试聊证据。
- Agent 大纲投影当前可验证的完成/失效状态。

### Phase 4：配置候选版本

- 确认 Agent Settings 字段所有权变更。
- 实现 draft/candidate/active 及 CAS/hash。
- 配置确认后进入现有 Build、Testing、Review、Launch 和 Acceptance。
- 验收前保留 active，放弃或失败不覆盖当前生效版本。

## 19. Phase 1 实施前确认清单

正式修改源码前，需要以本文档为依据确认一份逐文件实施计划，至少覆盖：

1. Agent 大纲的 Electron 读取投影与 Frontend 类型。
2. Agent 目标选择、标头、空/错/加载态和亮/暗主题。
3. Workflow request、Graph State、lifecycle execution scope 和 continuation 中的 Agent 目标。
4. Agent readiness 的确定性规则与结构化 blockers。
5. Build Unit scope、保留任务、DAG 验证和 Agent CodeRunner 绑定。
6. 会话恢复、重放、资源锁、旧卡与失效拒绝。
7. 后端单元/协议测试、前端投影/交互测试、Electron 真实流程、明暗主题和页面/API/实体回归。

发现需要改变新顶层 Workflow、另建 Agent 事实源、开放更大写权、改变 Java/Python 信任边界或引入新生产依赖时，必须停止实施并重新确认。

## 20. 与旧原型/文档的冲突裁决

1. `AGENT_PROTOTYPE_INTEGRATION.md` 的十段式 Agent 设计 Artifact 仅保留内容展示意图，不复制为当前正式 Artifact；七段 Settings 与完整派生 Contract 已取代其事实职责。
2. 原型的 Agent 独立状态不复制到生产；当前状态由 lifecycle、Build、Testing、Review 和 Acceptance 产物投影。
3. 原型的逐文件人工接受不覆盖当前 Build DAG 确认、真实 Diff、测试和验收规则。
4. `APPLICATION_DEVELOPMENT_PLANNING.md` 末尾的独立 `/application-development-planning/run` 描述不适用于 Agent Build；Agent 使用当前主 `/workflow/run`。
5. 原型把模型作为单独大纲类别；当前实现的 Model 是 Agent Settings 中的项目默认策略，本批不新建独立模型开发目标。
