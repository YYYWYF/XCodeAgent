# Agent Development Phase 1 Implementation Plan

> 状态：已确认并完成第一期生产实现
> 计划日期：2026-09-06
> 实施日期：2026-09-07
> 上游设计：[Agent Development Workbench](./AGENT_DEVELOPMENT_WORKBENCH.md)
> 实施范围：Agent 一等开发目标、只读详情、确定性 readiness、EntitySourceBinding continuation 与现有 Build DAG/CodeRunner 接入

## 1. 本批目标与完成定义

本批把已确认 ProductPlan/TechnicalPlan 中的业务 Agent 接入现有开发工作台。完成后，用户能够：

1. 在现有应用大纲和首次开发目标选择区看到 Agent。
2. 打开 Agent 的概览、七段 `agentSettings`、依赖和实现文件状态；这些内容全部只读。
3. 以 `{type: "agent", agentId, label}` 启动现有 `/workflow/run`。
4. 在进入 Workspace Inspection 和 Build DAG 之前获得服务端确定性 readiness 结果。
5. 仅缺 EntitySourceBinding 时进入现有实体绑定流程，并通过一次性 continuation 回到原 Agent。
6. 生成并确认只覆盖当前 Agent 完整实现闭包的 Build DAG。
7. 由现有 BuildScheduler 调用现有 Agent Runtime CodeRunner，生成 Contract 限定的三个 Python 文件，并继续使用现有 Diff、失败恢复和阶段确认卡。

本批的完成不等于 Agent 已经可运行或已验收。Phase 2 才接入 Java Gateway/Python sidecar 启动与试聊，Phase 3 才补齐 Agent 专属测试、审查与验收证据。

## 2. 范围边界

### 2.1 在范围内

- ProductPlan `agents[]` 与 TechnicalPlan `agent_contracts[]` 的 Electron 只读投影。
- Agent 详情中的概览、七段 Settings、依赖、Contract Hash、Runtime commit、预期产物路径和真实文件存在状态。
- Frontend、Workflow request、Graph State、lifecycle、continuation、资源锁、Build scope 的 `agent` 类型。
- Agent readiness 的结构化 blockers。
- Agent Build 所需 Runtime、Tool Endpoint、Java Gateway、业务 Agent 和入口页面 Unit 闭包。
- 当前 Build Plan 中既有已完成任务的保留与复用。
- 现有 Agent CodeRunner 的真实调度，不修改它的写入权限。
- 页面、Endpoint、实体、应用级 Build 的回归保护。

### 2.2 不在范围内

- Agent Settings 编辑器、假开关或保存草稿。
- 独立 Agent 设计 Markdown/JSON。
- `draft/candidate/active` 配置版本、激活、回滚和发布。
- Skills Loader、Knowledge Retriever、长期记忆、MySQL/OSS Memory、上下文压缩 Adapter。
- Java Gateway 与 Python sidecar 的真实启动编排、健康检查和试聊。
- Agent 专属 Testing、Code Review、Launch、Acceptance 证据。
- 新顶层 Workflow、新产品 Endpoint、普通 REST、手写 SSE 或第二套聊天框架。
- Agent 定向普通二次修改；本批仅把 Agent 接入正式 Build 运行，不扩大 `/conversation/run` 的页面/Endpoint 目标合同。
- 自动提交、push、模板仓库修改或依赖升级。

## 3. 已锁定的合同

### 3.1 前端启动请求

```json
{
  "selectedAgentId": "inspection_assistant",
  "detailTargetType": "agent",
  "buildExecutionScope": {
    "type": "agent",
    "targetId": "inspection_assistant"
  }
}
```

请求不得携带 Contract、Settings、Tool、Schema、文件路径或 readiness 结论。服务端以 `selectedAgentId` 和当前工作区正式产物重新解析全部事实。

### 3.2 Graph 与生命周期内部字段

```json
{
  "selected_agent_id": "inspection_assistant",
  "detail_target_type": "agent",
  "build_execution_scope": {
    "type": "agent",
    "targetId": "inspection_assistant"
  }
}
```

Agent 与 `selectedPageId`、`selected_api_contract_id`、`selected_endpoint_id`、`selected_entity_id` 互斥。任何显式 Agent 选择都必须清空其他开发目标，恢复和重试也只能从服务端已登记 scope 重建目标。

### 3.3 EntitySourceBinding continuation

```json
{
  "type": "agent",
  "agentId": "inspection_assistant",
  "label": "智能回检助手"
}
```

continuation 继续绑定原 thread、原 run、TechnicalPlan 文件 SHA-256 和一次性 token。实体绑定确认后只签发“继续开发 Agent”动作，不自动启动 Build。

### 3.4 Contract Hash

TechnicalPlan schema 不新增冗余 `contractHash` 字段。平台对当前单个完整 Agent Contract 做键排序、无多余空白的 UTF-8 JSON 序列化，再计算 SHA-256，展示和 Build Context 使用统一的 `sha256:<64 hex>` 形式。

该 Hash：

- 只用于当前 Contract 身份、Build Context 和状态新鲜度判断。
- 不替代 `source.productPlanSha256`。
- 不允许客户端在请求中回填。
- 不新增兼容算法或历史 Hash fallback。

## 4. Electron Agent 读模型

新增 `DevelopmentPlanningAgentOption`，它是只读投影，不落盘：

```ts
type DevelopmentPlanningAgentOption = {
  key: string
  agentId: string
  label: string
  purpose: string
  boundaries: string[]
  capabilities: Array<{
    capabilityId: string
    name: string
    expectedResult: string
    toolIds: string[]
  }>
  entryPageIds: string[]
  entryActions: Array<{
    pageId: string
    pageLabel: string
    actionIds: string[]
  }>
  interaction: Record<string, unknown>
  contractHash: string
  agentSettings: {
    prompt: Record<string, unknown>
    model: Record<string, unknown>
    memory: Record<string, unknown>
    tools: Record<string, unknown>
    skills: Record<string, unknown>
    knowledge: Record<string, unknown>
    context: Record<string, unknown>
  }
  dependencies: {
    gateway: Record<string, unknown>
    tools: Array<Record<string, unknown>>
    entities: Array<Record<string, unknown>>
    pages: Array<Record<string, unknown>>
    runtime: Record<string, unknown>
  }
  runtime: Record<string, unknown>
  security: Record<string, unknown>
  artifacts: Array<{
    kind: 'agent' | 'tool_adapter' | 'test'
    path: string
    exists: boolean
  }>
  requiredChecks: string[]
  taskSummary?: {
    total: number
    pending: number
    running: number
    completed: number
    failed: number
  }
}
```

投影规则：

1. ProductPlan 和 TechnicalPlan 均须是当前已确认合同。
2. `agentId` 必须在两份计划中各唯一命中一次；不匹配时把正式规划标记为 invalid，不生成残缺 Agent 节点。
3. 产品名称、用途、入口和验收来自 ProductPlan；Settings、Gateway、Tools、Runtime、安全和文件路径来自 TechnicalPlan。
4. 文件存在状态只检查 Contract 中三个平台编译路径；先验证相对路径、工作区边界和非符号链接，不开放任意路径读取。
5. Runtime commit 只读 `.xcodeagent/template-generation-manifest.json` 中已验证的 `agentRuntime` 目标；不在 Renderer 执行 Git 命令。
6. `taskSummary` 只合并 scope 为当前 Agent、Contract Hash 匹配的 Build Context；旧计划不得显示为当前进度。
7. System Prompt 可以在受控 Agent 详情中展示，但不得进入搜索索引、状态摘要、AG-UI公开 readiness、默认日志或错误信息。

## 5. Agent readiness

### 5.1 服务接口

新增独立服务 `inspect_agent_development_readiness(workspace, agent_id)`。页面和 Endpoint 继续使用原 `development_readiness()`，避免改变既有返回合同。

结果形状：

```json
{
  "ready": false,
  "target_type": "agent",
  "target_id": "inspection_assistant",
  "contract_hash": "sha256:...",
  "missing_entities": [
    {"entity_id": "inspection_task", "entity_name": "回检任务"}
  ],
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

公开结果不包含完整 System Prompt、完整 Schema、物理路径、凭据或异常栈。

### 5.2 确定性检查顺序

1. RequirementSpec、ProductPlan、UiManifest、TechnicalPlan 均为当前正式且已确认产物。
2. ProductPlan/TechnicalPlan 通过 `agentId` 各唯一命中一次。
3. 复用 `validate_technical_plan_agent_contracts()` 校验完整 Contract，包含 ProductPlan Hash、Endpoint 快照和当前 Runtime capability 约束。
4. 单独计算当前 Agent Contract Hash。
5. 复用模板生成服务检查 `agentRuntime.required=true`、下载成功、仓库、`master`、commit、关键文件、关键目录和禁止真实 `.env`。
6. `invocation.gatewayEndpointId` 唯一指向当前 Java Gateway Endpoint。
7. 每个 Tool Binding 唯一解析 API Contract/Endpoint，且 Endpoint ID、Method、Path、请求/响应 Schema 快照和 `accessMode` 一致。
8. Tool Endpoint 所涉及实体均有当前已确认 EntitySourceBinding，外部 API 操作映射完整。
9. ProductPlan 入口页面与 action 仍存在，且相应 PageImplementationContract 指向 Gateway Endpoint。
10. Agent Settings 未启用当前 Runtime 未实现的 Adapter，required checks 可由当前质量链解释。

### 5.3 阻塞分流

- blockers 全部为 `entity_source_binding`：使用 `entity_source_binding_required`，登记 Agent continuation。
- blockers 同时包含其他类别：展示全部阻塞，不签发实体 continuation；修复正式规划或模板后重新检查。
- Product/Technical/Endpoint/Page Action/Contract Hash 不一致：只允许进入正式规划修订，不做静默修复。
- Runtime 模板失败：只提供模板准备重试，不允许 Build。
- readiness 抛出的内部异常：使用现有 AG-UI 错误终态和安全摘要，不伪装为完成。

## 6. Agent Build scope

### 6.1 当前代码冲突

现有 Unit Graph 的 `depends_on` 方向是“前置 Unit → 消费 Unit”：

```text
agent:runtime -> agent:<agentId> -> Java Gateway Endpoint -> Entry Page
Tool Endpoint -----------------> agent:<agentId>
```

当前 Scheduler 对 page/endpoint 目标只取“目标 + 全部前置祖先”。若简单增加 `agent:<agentId>`，会遗漏作为下游消费者的 Java Gateway 与入口页面，因此不能只扩展 `_target_unit_ids()`。

### 6.2 解决方案

不改变全局 Unit Graph 语义，不反转现有边。Agent Build Context 先以当前 Agent 的入口页面 Unit、Gateway Unit 和业务 Agent Unit 为显式根，再通过现有图收集它们的全部前置 Unit，得到并持久化 `required_unit_ids`：

```text
agent:runtime
Tool Endpoint 所属后端 Unit
backend:bootstrap（按实际数据源需要）
agent:<agentId>
Java Agent Gateway Endpoint Unit
frontend:shell / frontend:api-client / frontend:auth-guard（按入口页需要）
ProductPlan 声明的入口页面 Unit
```

Build Context 只携带当前 Agent Contract、相关 Endpoint/Schema、实体设计摘要、入口页面实现合同和 Runtime manifest 摘要。它不携带其他 Agent 的 Settings。

Scheduler 对 `scope.type=agent` 使用已随已确认 Build Plan 持久化的 `build_context.required_unit_ids` 裁剪任务；其他 scope 继续使用现有目标祖先算法。这样确认卡、执行切片和重试使用同一服务端编译结果。

### 6.3 任务保留与 CodeRunner

- `agent:runtime` 仍是无模型任务的平台 readiness Unit。
- `agent:<agentId>` 始终是唯一 `owner=agent` 业务 Unit。
- 已完成且 Unit fingerprint 与当前输入一致的 Gateway、Tool Endpoint、公共能力和页面任务沿用当前保留规则。
- 尚无任务或已失效的 required Unit 进入同一份待确认 DAG。
- `_target_unit_id()` 对 Agent 返回 `agent:<agentId>`，因此 Agent 自身任务会被替换；不无条件重做已有依赖任务。
- Agent CodeRunner 仍只能写：

  ```text
  agent-runtime/src/app/agent/<agent_id>.py
  agent-runtime/src/app/tools/<agent_id>_tools.py
  agent-runtime/tests/test_<agent_id>.py
  ```

- Java Gateway 继续由 Backend owner 负责，入口页面继续由 Frontend owner 负责。

## 7. 生命周期、恢复与资源锁

### 7.1 新资源类型

- `WorkbenchExecution.scope` 增加 `agent`。
- `ExecutionResourceType` 增加 `AGENT`。
- `ExecutionResourceLocks` 增加 `agents` map。
- 进程内 workspace lease 接受 `agent` scope。

### 7.2 Agent 资源声明

服务端从正式 Contract 计算并锁定：

- 当前 Agent（primary）。
- Gateway 和 Tool Endpoint。
- 所属 API Contract 与数据源。
- ProductPlan 声明的入口页面。

客户端不能提交或缩小这些 claims。同一 Agent 不能有两个正式写执行；与其共享 Gateway、Tool Endpoint、数据源或入口页的运行继续遵守现有锁冲突规则。

### 7.3 恢复规则

- 冷恢复从 checkpoint 和 `application-lifecycle.json` 读取 `selected_agent_id` 与 `build_execution_scope`。
- Build DAG 确认、失败重试、测试/审查/验收阶段切换均保留 Agent scope。
- Entity continuation 必须恢复原 Agent、原 thread，并重新检查 TechnicalPlan SHA-256、Contract 与 readiness。
- 旧 token、旧 TechnicalPlan、旧 Contract、不同 thread、已消费 continuation 或客户端覆盖 target 一律拒绝。
- 前端只恢复服务端已投影的稳定 ID，不从消息文案、Agent 名称或文件路径猜测目标。

## 8. UI 行为

### 8.1 应用大纲

- 在页面、接口、实体之后增加“智能体”分组。
- 搜索覆盖 Agent 名称、`agentId`、用途和能力名称。
- Agent 行显示名称、`agentId`、综合状态和阻塞/任务摘要。
- Agent 不参与“只显示与当前页面相关”的模糊关系推断；该筛选打开时，仅在当前选择本身为 Agent 时保留当前 Agent，避免按名称猜关系。
- 空数组显示“当前计划中暂无智能体”，不影响普通应用。

### 8.2 首次目标选择

复用现有目标选择卡，但 Agent 文案必须与页面/API/实体区分：

- 页面/API/实体仍显示“开始详细设计”。
- Agent 已有正式 Contract，不生成第二份详细设计；按钮显示“开始开发智能体”。
- 选择 Agent 后直接启动 `development_readiness_gate`。

### 8.3 Agent 详情

右侧开发产物详情新增四个只读区域：

1. 概览：身份、用途、能力、入口、交互、Contract Hash。
2. Agent Settings：Prompt、Model、Memory、Tools、Skills、Knowledge、Context。
3. 依赖：Runtime commit、Gateway、Tool/Schema、实体绑定、页面 Action。
4. 实现：三个 Agent 文件、Java Gateway/页面入口关联和当前任务摘要。

所有 disabled capability 明确显示“当前版本未启用”，不渲染可点击假开关。“修改配置”只说明需进入正式 TechnicalPlan 修订，本批不新增直接编辑动作。

### 8.4 状态边界

Phase 1 只投影已有证据能支持的状态：待开发、依赖阻塞、待确认任务、开发中、待测试、待审查、待验收和失败。没有 Phase 3 的 Agent 专属测试/运行/验收证据时，不显示“已完成”。

Agent 文件内容和 Diff 继续使用现有 Source/Diff 能力；详情页只展示 Contract 允许路径与真实存在状态，不增加新的任意文件读取 IPC。

## 9. 端到端流转

```text
confirmed ProductPlan + TechnicalPlan
  -> Electron 严格投影 DevelopmentPlanningAgentOption[]
  -> WorkbenchPage
  -> LeftPanel / AiChatPanel
  -> ApplicationOutline + AgentDevelopmentDetail
  -> 用户点击“开始开发智能体”
  -> @ag-ui/client 调用现有 /workflow/run
  -> request 只接收 selectedAgentId + agent scope
  -> Graph State.selected_agent_id
  -> development_readiness_gate
     -> ready: inspect_workspace
     -> 仅缺实体: EntitySourceBinding + one-time continuation
     -> 其他阻塞: AG-UI requires_user_input/error 终态
  -> prepare_build_tasks 编译 Agent required_unit_ids
  -> 现有 Build DAG 确认
  -> BuildScheduler 按持久化 required_unit_ids 执行
  -> 现有 owner=agent CodeRunner 生成三个 Python 文件
  -> 现有 Diff / Build 结果 / 测试阶段确认
```

全链路继续保持 run started、assistant message、结构化 result/error、state snapshot/delta 和唯一 run finished，不增加事件 parser。

## 10. 逐文件修改计划

### 10.1 Electron 规划投影

| 文件 | 计划修改 |
| --- | --- |
| `Frontend/src/main/agentPlanningArtifactProjection.ts`（新增） | 集中完成 Agent 唯一对齐、只读投影、Contract Hash、安全产物路径、文件存在状态、Runtime manifest commit 和当前 Agent Build task summary；避免继续扩大 `main/index.ts`。 |
| `Frontend/src/main/index.ts` | 在 `inspectWorkspacePlanningArtifacts()` 调用新投影并返回 `agents`；普通应用返回空数组。 |
| `Frontend/src/preload/index.d.ts` | 给 `inspectPlanningArtifacts` 返回值增加严格的 `agents` 类型。 |
| `Frontend/src/renderer/src/window.d.ts` | 同步 Renderer 全局 IPC 类型。 |
| `Frontend/src/renderer/src/service/applicationStorage.ts` | `inspectWorkspacePlanningArtifacts()` 返回 `DevelopmentPlanningAgentOption[]`。 |
| `Frontend/src/renderer/src/typings/applicationDevelopmentPlanning.ts` | 定义 Agent 只读选项、Settings、依赖和实现文件类型。 |

### 10.2 Frontend 数据贯穿与 UI

| 文件 | 计划修改 |
| --- | --- |
| `Frontend/src/renderer/src/pages/WorkbenchPage.tsx` | 增加 Agent 状态、加载、刷新和异常清空，并传给 LeftPanel。 |
| `Frontend/src/renderer/src/components/LeftPanel/LeftPanel.tsx` | 透传 Agent 列表。 |
| `Frontend/src/renderer/src/components/AiChatPanel/AiChatPanel.tsx` | `ActiveDetailTarget` 增加 Agent；接入 Agent 浏览、启动、目标匹配、恢复与面板渲染；只做编排，不在此文件实现 Settings UI。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/ApplicationOutline/index.tsx` | 增加 Agent 分组、搜索、选择、选中状态与空态。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/ApplicationOutline/ApplicationOutline.less` | 增加与现有紫色 token 一致的 Agent 行和状态样式，覆盖亮/暗主题。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/SessionSidebar/index.tsx` | 透传 Agent 大纲 props。 |
| `Frontend/src/renderer/src/components/AiChatPanel/hooks/useDevelopmentArtifactDetail.ts` | 增加 Agent 浏览选择，不改变 Workflow 会话归属。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/DevelopmentArtifactsPanel/index.tsx` | 按选中类型渲染 Agent 详情；页面/API/实体占位行为保持不变。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/DevelopmentArtifactsPanel/DevelopmentArtifactsPanel.less` | 为 Agent 详情容器补充响应式布局和滚动边界。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/index.tsx`（新增） | 组合概览、Settings、依赖、实现和“开始开发智能体”入口。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentSettingsView.tsx`（新增） | 独立渲染七段只读 Settings、来源、启用状态和平台能力说明。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentDependenciesView.tsx`（新增） | 独立渲染 Gateway、Tool/Schema、EntitySourceBinding、页面 Action 与 Runtime commit。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentDevelopmentDetail.less`（新增） | 使用现有主题变量实现亮/暗、hover/focus、empty/loading/blocked/success/error 状态。 |
| `Frontend/src/renderer/src/components/DetailConfirmationPageSelector/index.tsx` | 首次目标选择增加 Agent；Agent 使用“开始开发智能体”文案并跳过详细设计。 |
| `Frontend/src/renderer/src/components/DetailConfirmationPageSelector/DetailConfirmationPageSelector.less` | 增加 Agent 选项样式并复用现有主题变量。 |

### 10.3 Frontend AG-UI、恢复与阶段类型

| 文件 | 计划修改 |
| --- | --- |
| `Frontend/src/renderer/src/service/agUiAgent.ts` | 增加 `selectedAgentId`、`detailTargetType=agent` 和 `buildExecutionScope.type=agent` 的 forwardedProps；仍使用 `@ag-ui/client/core`。 |
| `Frontend/src/renderer/src/typings/workflow.ts` | 扩展 Workflow state、Build scope、Lifecycle execution/resource locks 与 Agent continuation target。 |
| `Frontend/src/renderer/src/service/chatSessions.ts` | 扩展持久化 continuation 的 Agent target 严格校验。 |
| `Frontend/src/renderer/src/components/AiChatPanel/developmentContinuation.ts` | 从公开 Workflow 恢复 Agent continuation，只以服务端 Agent ID 为权威。 |
| `Frontend/src/renderer/src/components/AiChatPanel/hooks/useWorkflowConversation.ts` | 新增 `handleStartAgentDevelopment()`；初始、确认、重试和阶段切换持续携带服务端 Agent scope。 |
| `Frontend/src/renderer/src/components/AiChatPanel/utils.ts` | 增加 Agent detail key 与 Workflow 目标匹配，避免状态串到页面/API/实体。 |
| `Frontend/src/renderer/src/components/AiChatPanel/debugExecutionScope.ts` | 调试恢复接受 Agent scope，不降级为 application。 |
| `Frontend/src/renderer/src/components/AiChatPanel/planExecutionMode.ts` | 增加按 agentId 定位 execution 的纯函数，优先使用当前 run/thread。 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/index.tsx` | Build 执行进度把 Agent scope 显示为“智能体”，不改变其他 scope 文案。 |

### 10.4 Backend request、Graph 与 AG-UI 投影

| 文件 | 计划修改 |
| --- | --- |
| `Backend/app/graph/state.py` | 增加 `selected_agent_id`。 |
| `Backend/app/protocols/workflow/request.py` | 严格解析 `selectedAgentId`；实现目标互斥、Agent scope、恢复、重试和 continuation 重建；不接受客户端 Contract。 |
| `Backend/app/graph/workflow.py` | 让 Agent scope 进入现有 `development_readiness_gate`；不新增节点或顶层 Workflow。 |
| `Backend/app/graph/nodes/development_readiness.py` | Agent 分支调用独立 readiness；按 entity-only 与其他 blocker 分流并生成安全提示。 |
| `Backend/app/protocols/workflow/projection.py` | 在当前 AG-UI state/result/summary 投影 `selectedAgentId` 与 Agent continuation/scope。 |
| `Backend/app/protocols/workflow_visualization.py` | 同步兼容当前可视化消费者的 Agent ID/scope 投影，避免两个正式投影面不一致。 |
| `Backend/app/protocols/workflow/runtime.py` | Agent readiness 运行时节点标签与进度摘要保持目标可读，不新增事件名。 |

### 10.5 Backend readiness 与 continuation

| 文件 | 计划修改 |
| --- | --- |
| `Backend/app/services/agent_development_readiness.py`（新增） | 实现正式产物、唯一 Agent、Contract、Hash、Runtime、Gateway、Tool/Schema、实体、页面 Action 和 capability 的确定性门禁及结构化 blockers。 |
| `Backend/app/services/application_template_generation.py` | 提取可复用的 Agent Runtime 只读 readiness，不复制或放宽现有模板校验。 |
| `Backend/app/domain/application_lifecycle.py` | continuation target 增加 Agent；execution/resource type/locks 增加 Agent。 |
| `Backend/app/services/development_continuation.py` | Agent target 的登记、公开投影、完整 readiness 复检和一次性恢复。 |
| `Backend/app/protocols/workflow/lifecycle.py` | entity-only Agent blocker 登记 continuation；Agent execution scope 持久化与恢复。 |

### 10.6 Backend 资源锁与 Build

| 文件 | 计划修改 |
| --- | --- |
| `Backend/app/services/execution_resource_scope.py` | 从当前 Agent Contract 解析 Agent、Gateway、Tool Endpoint、API Contract、数据源和入口页面 claims。 |
| `Backend/app/services/application_lifecycle.py` | Agent primary claim、Agent lock map 的创建、转移、释放和 revision execution 匹配。 |
| `Backend/app/workspace/run_lease.py` | 接受并保留 Agent scope/resource key，不回退 application。 |
| `Backend/app/services/build_unit_skeleton.py` | 提供基于当前 Unit Graph 的 Agent required Unit 闭包解析；不改变既有边方向。 |
| `Backend/app/services/build_context_resolver.py` | 新增 Agent Context，只暴露当前 Contract、相关 Endpoint/Schema、实体、入口页面、Runtime 摘要和 required Unit。 |
| `Backend/app/graph/nodes/tasks.py` | Agent scope 前置复检、Build Context、当前 Contract 投影、`_target_unit_id()`、任务保留和 DAG 合并。 |
| `Backend/app/services/build_scheduler.py` | Agent scope 使用已确认 Build Context 的 required Unit 切片；其他 scope 算法不变。 |
| `Backend/app/services/test_validation.py` | 通用测试修复范围把 Agent 映射到 `agent:<agentId>`，避免失败后扩大为整应用。 |
| `Backend/app/graph/nodes/lifecycle.py` | 测试/审查/验收阶段卡片从当前 Contract 投影 Agent 名称，保持 scope 不丢失。 |

### 10.7 测试与文档

| 文件 | 计划修改 |
| --- | --- |
| `Backend/tests/test_agent_development_readiness.py`（新增） | 覆盖成功、Contract/Hash、Runtime、Gateway、Tool/Schema、实体、页面 Action 和未实现 capability blockers。 |
| `Backend/tests/test_development_readiness.py` | 保证页面/Endpoint 既有合同不变，并覆盖 gate 的 Agent 分流。 |
| `Backend/tests/test_development_continuation.py` | 覆盖 Agent continuation 的签发、消费、旧 Hash、跨 thread 和重复消费拒绝。 |
| `Backend/tests/test_development_continuation_stream.py` | 覆盖 Agent → EntitySourceBinding → Agent 的 AG-UI 完整生命周期。 |
| `Backend/tests/test_workflow_request.py` | 覆盖 Agent 请求、互斥、scope-only 恢复、确认、重试和客户端伪造字段拒绝。 |
| `Backend/tests/test_application_lifecycle.py` | 覆盖 Agent execution、locks、转移和释放。 |
| `Backend/tests/test_application_lifecycle_protocol.py` | 覆盖 Agent lifecycle/continuation 的公开形状。 |
| `Backend/tests/test_execution_resource_locks.py` | 覆盖 Agent 与共享 Gateway/Tool/页面/数据源运行的资源交集。 |
| `Backend/tests/test_agent_build_context_resolver.py`（新增） | 覆盖当前 Agent-only Contract、Endpoint/Schema、入口页和 required Unit 闭包。 |
| `Backend/tests/test_build_scheduler.py` | 覆盖 Agent scope 按持久化 required Unit 执行以及其他 scope 回归。 |
| `Backend/tests/test_prepare_build_tasks_guard.py` | 覆盖 Agent readiness 重检、DAG scope、旧 Contract/Build Plan 拒绝。 |
| `Backend/tests/test_agent_build_runner.py` | 覆盖 Agent scope 真正路由到现有 Agent CodeRunner，文件权限保持三文件。 |
| `Frontend/tests/agentDevelopmentWorkbench.test.ts`（新增） | 覆盖 Electron 投影、Contract Hash、Agent 大纲/详情合同、目标键、continuation 和状态映射纯逻辑。 |
| `Frontend/scripts/run-agent-development-workbench-tests.mjs`（新增） | 使用现有前端测试脚本模式编译并运行上述测试。 |
| `Frontend/package.json` | 增加定向测试命令，不新增依赖。 |
| `docs/AGENT_DEVELOPMENT_IMPLEMENTATION_STATUS.md` | 实施完成后按真实证据更新 Phase 1 状态与限制。 |
| `docs/CODEBASE_INDEX.md` | 实施完成后记录新 readiness 服务、Agent UI 组件、Agent scope 和测试入口。 |
| `docs/AGENT_DEVELOPMENT_WORKBENCH.md` | 仅补充实际实现入口和验证记录，不改变已确认设计。 |

## 11. 回归风险与控制

| 风险 | 控制 |
| --- | --- |
| Agent ID 从旧 checkpoint 回流并覆盖当前页面/Endpoint/实体 | request 层统一互斥清空；scope-only 恢复由服务端重建。 |
| Agent Build 错误扩大成整应用 Build | request、tasks、scheduler、test repair 四层都把 `agent` 作为合法 scope；缺失 targetId 直接拒绝。 |
| 只构建 Python Agent，漏掉 Java Gateway/页面入口 | required Unit 以 Agent、Gateway、入口页面为显式根并收集前置闭包，随 Build Plan 持久化。 |
| 为了 Agent 改动全局 Unit 图导致页面/API 回归 | 不反转、不重定义既有边；仅新增 Agent scope 的闭包解析。 |
| readiness 把敏感 Prompt/Schema/路径暴露到 AG-UI | 公开 blocker 使用稳定 ID 和安全摘要；完整内容只留在受控 IPC 详情和服务端 Build Context。 |
| 已确认旧 DAG 被新 Contract 误执行 | Contract Hash、Unit fingerprint、Build Plan digest、scope 和 TechnicalPlan SHA 多重校验。 |
| Agent 状态依赖前端本地布尔值 | 状态只读 lifecycle、Build Context、task registry 和当前文件事实。 |
| 普通应用出现空白 Agent 交互 | `agents=[]` 时只显示普通空态，不生成 Runtime、不出现 Agent 启动按钮。 |
| Agent UI 破坏主题 | 只使用现有 `--wb-*`/Ant Design v4 token，逐项检查亮/暗主题状态。 |

## 12. 验证计划

### 12.1 Backend 自动化

定向执行：

```text
cd Backend
.venv/bin/python -m unittest \
  tests.test_agent_development_readiness \
  tests.test_development_readiness \
  tests.test_development_continuation \
  tests.test_development_continuation_stream \
  tests.test_workflow_request \
  tests.test_application_lifecycle \
  tests.test_application_lifecycle_protocol \
  tests.test_execution_resource_locks \
  tests.test_agent_build_context_resolver \
  tests.test_build_scheduler \
  tests.test_prepare_build_tasks_guard \
  tests.test_agent_build_runner -v
```

对全部变更 Python 文件执行 `py_compile`，并检查 Backend `/health`。

### 12.2 Frontend 自动化

```text
cd Frontend
pnpm test:agent-development-workbench
pnpm build
```

构建必须覆盖 Node/Electron 与 Web/Renderer 两套 TypeScript。

### 12.3 Electron 实机

由用户自行启动现有 Electron 项目，助手不代为长时间运行。手工检查：

1. 含 Agent 应用展示 Agent 大纲、七段 Settings、依赖和文件状态。
2. 普通应用保持原页面/API/实体体验。
3. Agent 启动后出现 readiness、Workspace Inspection、DAG 确认和 Build。
4. EntitySourceBinding 阻塞、独立实体会话和 continuation 恢复正确。
5. Agent、页面、Endpoint、实体来回切换不会串会话、卡片或目标。
6. Build 失败、停止、重试、旧卡拒绝和重新聚焦刷新正确。
7. 亮色/暗色下文字、背景、边框、hover/focus、empty/loading/blocked/success/error 均可读。
8. 真实 Diff 只包含各 owner 授权路径；Agent Runner 只改三个 Python 文件。

### 12.4 明确不宣称的验证

本批不得把以下结果记为通过：

- Agent Runtime `/health`。
- Java Gateway → Python AG-UI 联调。
- 页面 Action 真实调用 Agent。
- Tool 调用权限、写审批、短期记忆多轮效果。
- Agent 专属集成测试、代码审查和最终验收。

这些属于 Phase 2/3。

## 13. 实施顺序

1. 先补 Backend Agent target、readiness、continuation、lifecycle/resource lock 的 RED 测试与实现。
2. 再补 Build Context、required Unit、Scheduler slice 和 CodeRunner 路由的 RED 测试与实现。
3. 再补 Electron 读模型、Frontend 类型与纯逻辑测试。
4. 最后接入大纲、Agent 详情、启动按钮和阶段恢复 UI。
5. 运行定向回归、构建和健康检查。
6. 更新状态台账与 Codebase Index；等待用户 Electron 实机结果。

任一步发现正式 Contract 不能唯一提供所需事实，必须停止，不允许由模型、文件目录或前端文案补猜。

## 14. 停止与重新确认条件

出现以下任一情况立即停止实施并重新请求确认：

- 需要新增顶层 Workflow、产品 Endpoint、事件类型、依赖或存储文件。
- 需要让 Renderer 直连 Python sidecar，或改变 Java/Python 信任边界。
- 需要修改 Agent Runtime 模板共享 Factory、Settings、Context 或 Server。
- 需要扩大 Agent CodeRunner 的写入目录。
- 需要在开发阶段直接编辑或持久化 Agent Settings。
- 现有 ProductPlan/TechnicalPlan 无法稳定解析 Gateway、Tool、页面 Action 或实体依赖。
- Agent required Unit 无法在不改变既有页面/API Unit 图语义的前提下闭合。
- 实施必须覆盖本计划未列出的公共协议或高风险功能。

## 15. 回滚边界

- 功能修改保持为一个可审阅的 Phase 1 patch，不自动提交或 push。
- 不使用 `git reset`、`git checkout --`、`git clean` 或删除用户文件。
- 失败时只撤销本批新增 Agent 分支与新文件，保留用户已有 `.workbuddy/`、`Frontend/src/main/templateRepository.ts`、本设计文档以及其他现存改动。
- 任一页面/API/实体回归无法在本批范围内修复时，停止并报告具体失败，不用兼容分支掩盖。

## 16. 用户确认语义

用户明确确认本计划后，才允许修改第 10 节列出的功能源码和测试。确认不授权 Phase 2/3/4、模板仓库修改、依赖安装、提交或 push。
