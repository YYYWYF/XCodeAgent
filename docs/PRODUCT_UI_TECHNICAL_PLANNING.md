# 产品规划、UI 设计与技术规划分层

## 目标

应用创建流程把产品设计、技术规划和开发执行拆成三个独立阶段，避免 UI 设计稿与页面详细设计重复定义布局、组件和交互，也避免 UI 确认或跳过后自动越过阶段入口生成 TechnicalPlan。

四个正式产物均采用 LangGraph 原生人工审阅门：需求澄清期间只更新 checkpoint 中的未完成事实，不生成或写入 RequirementSpec 草稿；模型判定没有重要缺口后才写入待确认 Markdown/内部 JSON，再进入独立 review 节点调用 `interrupt()`。用户操作通过同一 thread 的 `Command(resume=...)` 恢复。创建规划的 SQLite checkpoint 是上下文权威，前端只提交包含 `gateId`、产物摘要和显式 action 的 `applicationPlanningInteraction`，不回传 Workflow 状态来推断恢复节点。`confirm`、`revise`、需求澄清 `answer`、UI `ui_action` 和底部 `design_change` 具有不同类型；前端按按钮/表单的明确意图产生 action，产物节点直接消费 action，用户文本只作为回答或修订内容。服务端按 thread 串行完成中断读取、版本校验和恢复，旧卡片、摘要不匹配或并发重复提交必须在节点执行前拒绝。

```text
RequirementSpec
  -> 产品确认
ProductPlan
  -> 产品确认
UiDesign（可选的真实 React 页面稿 + UiManifest）
  -> 产品确认或明确跳过
等待进入规划阶段
  -> 用户显式确认 enter_planning
TechnicalPlan
  -> 开发确认
Workbench
  -> development_readiness_gate（校验关联实体绑定）
  -> EntitySourceBinding（仅由用户手动选择实体进入，独立结束）
  -> Build DAG
  -> Build / Test / Acceptance
```

产品确认“做什么、有哪些页面、用户如何操作、页面长什么样”；开发确认“API、数据、权限和工程如何实现”。同一页面不再经过第二次视觉详设确认。

## 正式产物

### RequirementSpec

草稿路径：`.xcodeagent/drafts/specs/requirement-spec.md|json`；确认后正式路径：`.xcodeagent/specs/requirement-spec.md|json`

负责人：产品。

包含产品目标、范围、用户角色、功能模块、业务流程、初步页面清单和业务信息需求。它不生成产品验收标准，不包含模型生成的产品假设或产品风险，也不决定数据源、存储方式、页面布局、API、数据库表和代码任务。产品事实不明确时必须在需求阶段向用户澄清；用户未要求的可选细节直接省略。内部技术配置可以随工作流保存，但不得出现在 RequirementSpec 产品确认文档、概览或编辑器中，也不得成为需求澄清问题。

### ProductPlan

草稿路径：`.xcodeagent/drafts/plans/product-plan.md|json`；确认后正式路径：`.xcodeagent/plans/product-plan.md|json`

负责人：产品。

包含：

- 拍平页面清单和稳定 `pageId`；
- 页面目标、业务信息项和稳定 `actionId`；
- 每个 action 的产品行为类型、预期业务/可见结果和组合步骤；
- 页面跳转关系；
- loading、empty、error、success 等产品状态要求；
- 页面级产品验收标准。

ProductPlan 使用 `business`、`navigation`、`interface`、`external`、`sequence` 表达产品可见行为。`business` 只说明查询、提交、变更、导出等业务结果，不选择 endpoint；`interface` 只说明需要本地界面变化，具体控件效果由 UiDesign 实现；组合行为以稳定 `stepId` 和产品结果表达。ProductPlan 不包含 HTTP method、endpointId、API Schema、数据库操作或代码文件。

ProductPlan 中面向产品角色展示的验收标准，只描述生成应用的目标用户能够观察或完成的产品结果。XCodeAgent 自身的本地预览、代码生成、编译、构建、lint、typecheck、自动化/集成测试、质量门禁、工作流节点和“何时进入用户验收”等交付条件属于独立工程运行状态，不得写入应用产品验收标准；确定性归一化会剔除这类越界文案。

正式 JSON 使用 `product-plan.v6`，页面事实只保留拍平的 `pages`，不生成、不存储也不兼容读取 `frontend_pages`。根级 `agents` 只承接 RequirementSpec 已确认的业务智能体，并定义用户可确认的业务能力、入口页面与操作、交互方式、状态要求、业务边界和验收标准；普通应用必须使用 `agents: []`。ProductPlan 不选择模型、Prompt、API、工具、Skill、知识库、运行时、存储或代码路径，也不保存运行态角色、角色关系、`allowed_roles`、资源键、策略键或固定 `/roles` 页面。模型原始输出必须先通过精确 JSON 字段校验，再进入产品语义归一化和一致性校验。核心字段固定为：

权限开启时，RequirementSpec 的每条 `restrictedPages` 都以已确认的 `targetPageId` 引用业务页面；服务端在模型输出校验后仅根据该稳定绑定确定性生成内部 `authorizationTargets.pageRules[{ruleId,pageId}]`，不得按页面展示名称匹配或猜测。操作规则必须为 `{ruleId,pageId,actionId}`。`actionId` 只在所属页面内唯一，不能脱离 `pageId` 作为权限目标；`stepId` 仅用于产品组合行为，绝不进入权限目标。ProductPlan 不保存资源键或角色授权；联合确认前只校验受控页面 `pageId`、受控操作 `<pageId>_<actionId>` 与固定 `system_authorization_management` 的全局候选是否碰撞，实际资源目录仍由 TechnicalPlan 编译。

```json
{
  "schema_version": "product-plan.v6",
  "app": {"name": "...", "summary": "..."},
  "agents": [
    {
      "agentId": "order_assistant",
      "name": "订单助手",
      "purpose": "帮助用户理解订单状态并完成下一步操作",
      "capabilities": [
        {
          "capabilityId": "explain_order_status",
          "name": "解释订单状态",
          "expectedResult": "用户理解当前状态及后续可执行操作"
        }
      ],
      "entryPageIds": ["orders"],
      "pageActionBindings": [
        {"pageId": "orders", "actionIds": ["ask_order_assistant"]}
      ],
      "interaction": {
        "mode": "conversation",
        "supportsMultiTurn": true,
        "inputDescription": "用户输入订单相关问题",
        "outputDescription": "返回状态解释和业务建议",
        "stateRequirements": {
          "loading": "...",
          "empty": "...",
          "error": "...",
          "success": "...",
          "validation": "..."
        }
      },
      "boundaries": ["不得绕过订单审批或直接修改受限状态"],
      "acceptanceCriteria": ["能够根据当前订单上下文给出明确答复"]
    }
  ],
  "business_flows": [],
  "pages": [
    {
      "pageId": "orders",
      "name": "订单列表",
      "path": "/orders",
      "module_id": "order_management",
      "description": "...",
      "goal": "...",
      "information_items": [
        {"itemId": "order-number", "label": "订单编号", "description": "..."}
      ],
      "actions": [
        {
          "actionId": "open-order-detail",
          "name": "查看订单详情",
          "description": "点击订单进入详情页",
          "requiresConfirmation": false,
          "behavior": {
            "type": "navigation",
            "targetPageId": "order-detail",
            "expectedResult": "进入所选订单的详情页"
          }
        }
      ],
      "navigation_targets": ["order-detail"],
      "state_requirements": {
        "loading": "...",
        "empty": "...",
        "error": "...",
        "success": "...",
        "validation": "..."
      },
      "acceptance_criteria": []
    }
  ],
  "product_acceptance_criteria": []
}
```

模型提示直接提供包含全部 RequirementSpec 页面身份的完整 JSON 响应示例；根对象只能包含 `app`、`agents`、`business_flows`、`pages`、`product_acceptance_criteria`。`agents` 必须与 RequirementSpec 的 `agent_requirements` 按稳定 `agentId` 一一对应并保持顺序，能力名称、入口页面、交互模式和业务边界不得漂移；每个入口页面必须通过 `pageActionBindings` 引用该页面真实存在的 action。页面、智能体、能力、绑定、交互、信息项、action、behavior、sequence step 和状态对象均拒绝未声明字段。`information_items` 必须是 JSON 对象，禁止把 Python/JSON 字典序列化成字符串。action 只表示用户主动触发且会改变可见状态、结果集、页面位置、业务信息或外部效果的产品意图。阅读、浏览、滚动或看见内容不是 action，应写入 `information_items` 或 `acceptance_criteria`；纯展示页面允许 `actions: []`。导航 action 必须声明 `targetPageId`，并同步进入 `navigation_targets`。

### UiDesign

路径：

- `.xcodeagent/ui-design/pages/<PageKey>/index.tsx`
- `.xcodeagent/specs/ui-designs.json`

负责人：产品。

生成 React 页面稿时，它是页面视觉设计的唯一权威来源，负责布局、区域、组件、弹窗、操作入口、视觉层级、响应式策略、明暗主题和页面状态的视觉呈现。设计稿使用 Mock 值和本地状态表达搜索、筛选、弹窗、表单与确认交互，不接入真实 API。Mock 只能给 ProductPlan 已声明的信息项填充示例值，不能新增业务字段、指标、筛选器、操作、跳转、角色或业务区域。用户也可以明确跳过 UI 设计；此时不生成页面 TSX，Manifest 使用 `confirmation_status: skipped` 和空 `pages`。确认或跳过都只到达 `awaiting_planning_stage_entry`，用户点击绿色入口卡后，Electron 客户端为该应用创建或聚焦唯一的规划窗口。窗口跳过欢迎页与通用工作台入场，首屏直接锁定到 Planning 阶段，只展示 TechnicalPlan 规划文档。启动上下文使用 `graphThreadId` 恢复 lifecycle 中的原初始化 Graph checkpoint，并用独立的 `conversationThreadId` 创建规划 Agent 前端会话；只有该规划窗口提交一次 `enter_planning`，避免复制或丢失已确认的 RequirementSpec、ProductPlan 与 UiDesign 状态。

`ui-designs.json` 使用 `ui-manifest.v3`，它是 React 稿的引用与校验证据，不是另一份页面详设，也不是第二份产品事实。正式落盘文件不保存 TSX 正文，不重复页面名称、正式路由、描述、角色、状态要求、业务标签或验收标准；确认界面需要这些文案时，仅从当前 ProductPlan 临时投影。核心结构为：

```json
{
  "schema_version": "ui-manifest.v3",
  "confirmation_status": "pending_user_confirmation",
  "product_plan_sha256": "...",
  "pages": [
    {
      "pageId": "orders",
      "page_key": "Orders",
      "preview_path": "/page/orders",
      "code_path": ".../.xcodeagent/ui-design/pages/Orders/index.tsx",
      "code_sha256": "...",
      "status": "confirmed",
      "bindings": {
        "actions": [
          {"actionId": "open-filter", "controlIds": ["open-filter-control"], "uiEffect": "展开筛选区域"},
          {"actionId": "open-order-detail", "controlIds": ["open-order-detail-control"]}
        ],
        "information_items": [
          {"informationItemId": "order-number", "controlIds": ["order-number-display"]}
        ]
      },
      "verification": {
        "status": "passed",
        "code_sha256": "...",
        "checks": [],
        "errors": []
      }
    }
  ]
}
```

`preview_path` 只用于隔离设计稿预览，不是产品正式路由。正式路由始终来自 ProductPlan。Graph 运行态可以临时携带 `code` 供 `DesignRenderer` 渲染，落盘时必须剔除；恢复会话时只允许从同一工作区 `.xcodeagent/ui-design/` 受控目录按 `code_path` 恢复源码。

UI 生成只能消费已确认 ProductPlan，不能发明 ProductPlan 中不存在的页面、业务字段或操作；跳过 UI 时同样不改变 ProductPlan。

生成、模板适配、用户调整和最终确认都必须执行确定性一致性检查：ProductPlan 的每个 `actionId` 和 `informationItemId` 必须在 TSX 中有静态控件映射；`interface` action 还必须通过 `data-ui-effect` 固化实际本地界面效果；未知 ID、缺失映射、无归属交互控件、无归属业务展示组件都拒绝确认。仅用于切换原型状态的控件必须显式标记 `data-preview-only="true"`，不得进入 ProductPlan 或 TechnicalPlan。模板只能作为布局和组件风格参考，必须先按 ProductPlan 重写业务语义，禁止原样携带模板字段或操作。若用户跳过 UI，则不执行这些 TSX 映射门禁，页面实现契约从 ProductPlan 的 `expectedResult` 补齐本地交互效果。

RequirementSpec、ProductPlan 和 UiDesign 的产品确认均不得要求产品角色选择或审核数据源、数据库、持久化和 API 方案；这些内容只进入 TechnicalPlan 的开发确认。

### TechnicalPlan

正式路径：`.xcodeagent/plans/technical-plan.md|json`

负责人：开发。

TechnicalPlan 只写入 `.xcodeagent/plans/technical-plan.md|json`，`technical_plan` 是唯一语义来源。

TechnicalPlan 包含：

- `architecture` 默认包含前端、Java 后端和数据三段；存在业务智能体时额外包含平台确定性生成的 `agent_runtime`，固定为独立 Python 3.12 + DeepAgents sidecar；
- `entities` 由技术规划模型根据已确认 ProductPlan 的页面、信息项、业务操作与业务流程独立生成，是实体与 API 的唯一字段事实源；技术规划不读取 RequirementSpec 的 `entities`；
- API Contract 通过 `entity_ids` 关联实体；Schema 字段可使用 `entity_field_ref` 表示实体来源，计算、聚合和传输字段可以不做实体映射；
- ProductPlan 中 `business` action/step 到 endpoint 的 `action_implementations`；
- 根级 `agent_contracts`：与 ProductPlan `agents[]` 按 `agentId` 一一对应，定义能力到工具、工具到 API Endpoint、页面入口到 Java AG-UI 网关、会话、模型选择策略、安全边界和代码产物路径；普通应用固定为 `[]`；
- ProductPlan 与 UiManifest 的上游内容哈希。

正式 JSON 使用 `artifact_type: "technical-plan"`，只持久化本阶段新增的开发事实：

```json
{
  "artifact_type": "technical-plan",
  "confirmation_status": "pending_user_confirmation",
  "product_plan_sha256": "...",
  "ui_designs_sha256": "...",
  "architecture": {
    "frontend": "PC 管理端采用 React 单页应用，通过 REST JSON API 访问后端。",
    "backend": "后端采用 Java8 和 Springboot 提供业务 REST API。",
    "data": "MySQL8 负责持久化，Redis 负责缓存和热点查询。"
  },
  "entities": [
    {
      "id": "Order",
      "name": "订单",
      "description": "订单业务实体",
      "fields": [
        {"name": "order_number", "label": "订单编号", "type": "text", "required": true}
      ]
    }
  ],
  "api_contracts": [
    {
      "id": "orders_api",
      "entity_ids": ["Order"],
      "base_path": "/api/orders",
      "schemas": {},
      "endpoints": [
        {
          "id": "orders.list",
          "method": "GET",
          "path": "/api/orders"
        }
      ]
    }
  ],
  "pages": [
    {
      "pageId": "orders",
      "references": {
        "endpoint_dependencies": [],
        "action_implementations": []
      }
    }
  ],
  "agent_contracts": []
}
```

包含业务智能体时，平台在模型给出的稳定绑定通过校验后确定性补齐运行时、安全和产物字段：

```json
{
  "architecture": {
    "agent_runtime": "独立 agent-runtime Python 3.12 + DeepAgents sidecar；客户端仅通过 Java8 + Springboot 网关使用 AG-UI SSE 调用。"
  },
  "agent_contracts": [
    {
      "agentId": "inventory_assistant",
      "runtime": {
        "language": "Python",
        "pythonVersion": "3.12",
        "framework": "DeepAgents",
        "deployment": "sidecar",
        "serviceName": "agent-runtime"
      },
      "invocation": {
        "transport": "ag-ui-sse",
        "gatewayEndpointId": "inventory_api.agent_message",
        "internalPath": "/internal/agents/inventory_assistant/run"
      },
      "model": {"selection": "project_default"},
      "capabilityBindings": [
        {"capabilityId": "explain_inventory_status", "toolIds": ["get_inventory_status"]}
      ],
      "toolBindings": [
        {
          "toolId": "get_inventory_status",
          "apiContractId": "inventory_api",
          "endpointId": "inventory_api.get_status",
          "accessMode": "read"
        }
      ],
      "knowledgeReferences": [],
      "session": {"supportsMultiTurn": true, "memory": "conversation"},
      "security": {
        "directClientAccess": false,
        "authForwarding": "scoped-user-context"
      },
      "artifacts": {
        "agentPath": "agent-runtime/agents/inventory_assistant.py",
        "toolAdapterPath": "agent-runtime/tools/inventory_assistant_tools.py",
        "testPath": "agent-runtime/tests/test_inventory_assistant.py"
      }
    }
  ]
}
```

技术规划模型返回对象固定为 `architecture`、`entities`、`api_contracts`、`pages`、`agent_contracts` 五段。模型只选择 `gatewayEndpointId`、能力/工具/API 绑定、项目默认模型策略、知识引用和会话模式；Python 版本、DeepAgents、sidecar、AG-UI SSE、禁止客户端直连、内部路径和代码路径由平台确定性生成，不能被模型改写。每个工具 Endpoint 必须存在于同一 TechnicalPlan，且不能与 Agent 网关 Endpoint 相同；Java 业务后端仍固定为 Java8 + Springboot，不因应用包含智能体而替换成 Python。

TechnicalPlan 确认摘要和右侧阅读面板必须在 `agent_contracts` 非空时按需展示“智能体契约”，覆盖 Agent Runtime、Java 网关、能力→工具、工具→API Endpoint、会话/模型/安全、代码产物和 required checks；阅读面板默认打开该章节。普通应用 `agent_contracts=[]` 时不得出现该章节、Python 运行时或智能体指标。

TechnicalPlan 不再持久化 `app`、`requirements_overview`、`project_acceptance_criteria`、
`business_flows`、`acceptance_criteria`、`risks`、`data_sources`、`permission_model`、
`frontend_pages` 或 `page_implementation_contracts`。页面字段来自 ProductPlan；实体字段来自
TechnicalPlan 顶层 `entities`；数据源身份只来自后续已确认 EntitySourceBinding。API Contract 必须通过非空
`entity_ids` 关联一个或多个实体，禁止 `data_source_id`。角色/跳转/状态来自 ProductPlan；UI 路径与控件映射来自已确认 UiManifest，跳过时不提供 UI 路径和控件映射；
运行时按当前构建范围组合这些正式上游产物。

Endpoint 是否存在请求体由业务语义决定，不由 HTTP Method 单独决定。只依赖路径参数、查询参数和登录态即可完整表达的命令型 `POST`、`PUT` 或 `PATCH` 可以使用 `request_schema_ref: null`；实际消费请求体字段的 Endpoint 必须引用同一 API Contract `schemas` 内的真实请求 Schema，禁止为了通过校验生成无业务字段的空请求对象。

TechnicalPlan 模型不再生成 `navigation`、`local`、`external` 或产品可见的 `sequence` 决策；这些事实已经分别由 ProductPlan 和 UiDesign 确认。它只为需要后端/数据实现的业务 action 或业务 step 选择 endpoint：

平台规范化 TechnicalPlan 候选以及恢复失败 checkpoint 时，会把 `action_implementations` 和
`stepBindings` 已明确选择且真实存在的 Endpoint 确定性并入同页 `endpoint_dependencies`。已有依赖的
`usage`、`trigger` 和首屏标记保持不变；未知 Endpoint 不会被自动创建或掩盖，仍由一致性校验拒绝。
Contract-only 自动修复仅适用于全部剩余错误都来自 API Contract 定义的情况，页面或混合错误必须走完整
TechnicalPlan 修订。

```json
{
  "action_implementations": [
    {"actionId": "search-orders", "endpointId": "orders.list"},
    {
      "actionId": "delete-and-refresh",
      "stepBindings": [
        {"stepId": "delete-order", "endpointId": "orders.delete"},
        {"stepId": "reload-orders", "endpointId": "orders.list"}
      ]
    }
  ]
}
```

确定性编译器合并 ProductPlan 行为、UiManifest 控件/本地效果和 TechnicalPlan endpoint 实现，按需生成 Build 使用的 `PageImplementationContract`。它是运行时投影，不写入 `technical-plan.json`，也不是 TechnicalPlan 模型重复维护的第二份动作分类：

```json
{
  "pageId": "order-list",
  "uiDesignRef": {
    "path": ".xcodeagent/ui-design/pages/OrderList/index.tsx",
    "sha256": "..."
  },
  "requiredEndpointIds": ["orders.list", "orders.delete"],
  "actionBindings": [
    {"actionId": "open-filter", "bindingType": "local", "localEffect": "展开筛选区域"},
    {"actionId": "search-orders", "bindingType": "endpoint", "endpointId": "orders.list"},
    {
      "actionId": "delete-order",
      "bindingType": "sequence",
      "steps": [
        {"type": "endpoint", "endpointId": "orders.delete"},
        {"type": "endpoint", "endpointId": "orders.list"}
      ]
    }
  ],
  "responseBindings": [],
  "permissionBindings": [],
  "navigationBindings": [],
  "engineeringAcceptance": []
}
```

编译后的每个 `actionBindings` 条目会确定性附加 `actionName` 和 `uiControlRefs[{controlId,label}]`，供 Build 直接消费。其判别类型来自三个已经分权的上游来源：

- `endpoint`：调用已确认 API Contract 中的 endpoint；
- `navigation`：跳转到 ProductPlan 已声明的目标页面；
- `local`：打开弹窗、切换 Tab、展开区域等纯前端状态变化；
- `external`：打开明确的外部目标；
- `sequence`：由以上原子步骤组成的有序组合，例如提交成功后关闭弹窗并刷新。

纯装饰控件和没有产品语义的 UI 内部控件不进入 ProductPlan actions，也不要求 TechnicalPlan 绑定。ProductPlan 决定导航、外部和组合业务结果，UiDesign 决定本地界面效果，TechnicalPlan 模型显式生成 `action_implementations`；确定性编译器合并三者且绝不根据按钮名称或 HTTP Method 猜测 endpoint。

### TechnicalPlan 上下文预算

- 128k 上下文：TechnicalPlan 只注入实体上下文，以及拆分后的 ProductPlan 目标/验收、V1 页面与操作权限目标身份、业务流程、页面信息和业务动作上下文，并在修订时注入修订上下文；数据权限不进入第一阶段模型上下文；UiManifest 仍由运行时按页面/API 范围读取，不进入规划模型提示词。

## 详设节点移除与工作台执行

当前契约不生成、不读取也不迁移页面/API详设；`.xcodeagent/plans/pages/` 和 `.xcodeagent/plans/endpoints/` 不再是运行依赖。原接口详设中的操作、基数、选择器、事务、零/多匹配、状态码、副作用和风险已经收回 TechnicalPlan Endpoint。页面事实由 ProductPlan、UiDesign、TechnicalPlan references 和运行时 `PageImplementationContract` 共同提供。

页面/API开发流程固定为：

```text
选择页面或 API
  -> development_readiness_gate
  -> 关联实体缺少绑定：返回 entity_source_binding_required，用户手动选择实体
  -> EntitySourceBinding 确认后独立结束
  -> 用户重新选择原页面或 API
  -> development_readiness_gate
  -> inspect_workspace
  -> prepare_build_tasks（二次复检实体绑定）
  -> Build DAG 用户确认
  -> Build / Test / Acceptance
```

纯静态且没有 Endpoint 的页面可直接通过门禁。EntitySourceBinding 保留 database、external API、static、字段映射、建表/补列和高危 DDL 审批；它不修改已确认的 API Contract。

正式依赖顺序为：

```text
RequirementSpec -> ProductPlan -> UiDesign（可选） -> 等待进入规划阶段 -> TechnicalPlan
TechnicalPlan + EntitySourceBinding -> development_readiness_gate -> Build DAG
```

ProductPlan 或 UiDesign 变化时重新确认受影响 TechnicalPlan/运行时页面契约；TechnicalPlan API 或 Schema 变化时使相关 Build DAG 失效；EntitySourceBinding 变化时使引用实体的页面/API Build DAG 失效。纯代码实现错误进入 SmallTask 修复，不回到规划阶段。

TechnicalPlan 确认前执行确定性一致性检查：UI 中声明的每个业务操作、显示项和跳转必须能映射到 ProductPlan；每个 ProductPlan `business` action 和组合中的每个 `business` step 必须有且只有一个 endpoint 实现；TechnicalPlan 不得为 `navigation`、`interface` 或 `external` 行为重复作产品/UI 决策；每个技术绑定必须引用已存在的 action/step、endpoint、Schema 和页面。启用权限时，`authorization-manifest.v2` 必须完整覆盖 RequirementSpec 页面/操作规则及 ProductPlan 目标，确定性生成页面、顶层 action 和唯一系统资源，以及 Endpoint `operationResourceKeys` 的 ANY-OF 绑定；V1 出现数据权限字段必须拒绝确认。资源键、系统资源及 endpoint resource binding 均由确定性编译器生成。编译后的 `endpoint`、`navigation`、`local`、`external`、`sequence` 联合契约必须完整闭合，失败时不得进入工作台。

## 上下文预算

RequirementSpec 与 ProductPlan 只持久化用户提出或确认的产品事实，不保存模型推测的 `assumptions` 或 `risks`。TechnicalPlan、EntitySourceBinding 和任务规划只接收当前目标所需的结构化输入，不复制上游全文或无关历史记录。

单次模型上下文限制为当前阶段所需内容：

- ProductPlan：RequirementSpec；
- UiDesign：单页 ProductPlan 摘要；
- TechnicalPlan：拆分的 ProductPlan 页面、信息项、行为与业务流程上下文，以及必要修订信息；不读取 RequirementSpec 的 `entities`，也不加载完整上游文档或 UI manifest JSON；
- EntitySourceBinding：单个实体定义和所选数据源的有界元数据；
- Build：当前 Unit 的 TechnicalPlan Endpoint、页面实现契约、实体绑定摘要、UI 设计文件路径和工作区快照。

ProductPlan 每次自动修复只回灌最多八条校验摘要，TechnicalPlan 最多回灌十二条页面/API/数据源契约摘要；两者都不追加历史模型全文。TechnicalPlan 首次失败后优先只向模型投射报错 API Contract、绑定实体和关联产品动作，并将修复结果确定性合并回完整候选；无法定位具体 Contract 时才回退完整计划修订。三次预算耗尽时，最后一个可解析候选和精简错误只保留在 LangGraph checkpoint 的内部修复字段中，不作为 AG-UI 公开状态，也不写入正式 Markdown/JSON；用户授权“重新生成”后从该候选继续修复。只有通过校验的计划才进入确认产物，从而保持 128k 上下文预算与 checkpoint 可检查性。

TechnicalPlan 的模型生成、JSON 解析和正式契约校验共用最多三次“生成 → 校验 → 错误反馈修复”总预算。三次仍失败时，节点停留在 `technical_planning` 并返回 `technical_plan_generation_error`，仅包含精简错误与重新生成操作，不写入 Markdown/JSON，也不发出确认产物。规划模型的 `llm.token` 原始 JSON 只用于内部生成过程，前端聊天和历史消息均不得展示；确认界面直接读取 Workflow `state/result.technical_plan` 的结构化计划。

TechnicalPlan 的实体定义严格校验继续暂停；页面行为闭合校验暂时暂停；API Contract 的 `entity_ids`、可选 `entity_field_ref`、Schema 引用和统一分页校验启用。分页响应对象同级只能有 `total`、`pageSize`、`current`、`list` 四个字段。页面路由和 Endpoint 结构校验保持不变。

任何完整 TSX、数据库工具原始输出、仓库扫描结果和历史日志都写入文件，只向主上下文返回路径、哈希和有界摘要。
