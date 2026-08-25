# 产品规划、UI 设计与技术规划分层

## 目标

应用创建流程把产品决策和开发决策拆成两个独立确认边界，避免 UI 设计稿与页面详细设计重复定义布局、组件和交互。

四个正式产物均采用 LangGraph 原生人工审阅门：需求澄清期间只更新 checkpoint 中的未完成事实，不生成或写入 RequirementSpec 草稿；模型判定没有重要缺口后才写入待确认 Markdown/内部 JSON，再进入独立 review 节点调用 `interrupt()`。用户操作通过同一 thread 的 `Command(resume=...)` 恢复。创建规划的 SQLite checkpoint 是上下文权威，前端只提交包含 `gateId`、产物摘要和显式 action 的 `applicationPlanningInteraction`，不回传 Workflow 状态来推断恢复节点。`confirm`、`revise`、需求澄清 `answer`、UI `ui_action` 和底部 `design_change` 具有不同类型；前端按按钮/表单的明确意图产生 action，产物节点直接消费 action，用户文本只作为回答或修订内容。服务端按 thread 串行完成中断读取、版本校验和恢复，旧卡片、摘要不匹配或并发重复提交必须在节点执行前拒绝。

```text
RequirementSpec
  -> 产品确认
ProductPlan
  -> 产品确认
UiDesign（可选的真实 React 页面稿 + UiManifest）
  -> 产品确认或明确跳过
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

正式 JSON 使用 `product-plan.v5`，页面事实只保留拍平的 `pages`，不生成、不存储也不兼容读取 `frontend_pages`。ProductPlan 不保存运行态角色、角色关系、`allowed_roles`、资源键、策略键或固定 `/roles` 页面。模型原始输出必须先通过精确 JSON 字段校验，再进入产品语义归一化和一致性校验。核心字段固定为：

```json
{
  "schema_version": "product-plan.v5",
  "app": {"name": "...", "summary": "..."},
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

模型提示直接提供包含全部 RequirementSpec 页面身份的完整 JSON 响应示例；根对象只能包含 `app`、`business_flows`、`pages`、`product_acceptance_criteria`。页面、信息项、action、behavior、sequence step 和状态对象均拒绝未声明字段。`information_items` 必须是 JSON 对象，禁止把 Python/JSON 字典序列化成字符串。action 只表示用户主动触发且会改变可见状态、结果集、页面位置、业务信息或外部效果的产品意图。阅读、浏览、滚动或看见内容不是 action，应写入 `information_items` 或 `acceptance_criteria`；纯展示页面允许 `actions: []`。导航 action 必须声明 `targetPageId`，并同步进入 `navigation_targets`。

### UiDesign

路径：

- `.xcodeagent/ui-design/pages/<PageKey>/index.tsx`
- `.xcodeagent/specs/ui-designs.json`

负责人：产品。

生成 React 页面稿时，它是页面视觉设计的唯一权威来源，负责布局、区域、组件、弹窗、操作入口、视觉层级、响应式策略、明暗主题和页面状态的视觉呈现。设计稿使用 Mock 值和本地状态表达搜索、筛选、弹窗、表单与确认交互，不接入真实 API。Mock 只能给 ProductPlan 已声明的信息项填充示例值，不能新增业务字段、指标、筛选器、操作、跳转、角色或业务区域。用户也可以明确跳过 UI 设计；此时不生成页面 TSX，Manifest 使用 `confirmation_status: skipped` 和空 `pages`，TechnicalPlan 与页面代码生成直接依据 ProductPlan、TechnicalPlan 和模板技能继续。

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

- `architecture` 三段技术架构、业务实体、API Contract、请求/响应 Schema；
- `entities` 根据 RequirementSpec 实体骨架补齐规范字段，是实体与 API 的唯一字段事实源；
- API Contract 通过 `entity_ids` 关联实体；Schema 字段可使用 `entity_field_ref` 表示实体来源，计算、聚合和传输字段可以不做实体映射；
- ProductPlan 中 `business` action/step 到 endpoint 的 `action_implementations`；
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
  ]
}
```

TechnicalPlan 不再持久化 `app`、`requirements_overview`、`project_acceptance_criteria`、
`business_flows`、`acceptance_criteria`、`risks`、`data_sources`、`permission_model`、
`frontend_pages` 或 `page_implementation_contracts`。页面字段来自 ProductPlan；实体字段来自
TechnicalPlan 顶层 `entities`；数据源身份只来自后续已确认 EntitySourceBinding。API Contract 必须通过非空
`entity_ids` 关联一个或多个实体，禁止 `data_source_id`。角色/跳转/状态来自 ProductPlan；UI 路径与控件映射来自已确认 UiManifest，跳过时不提供 UI 路径和控件映射；
运行时按当前构建范围组合这些正式上游产物。

TechnicalPlan 模型不再生成 `navigation`、`local`、`external` 或产品可见的 `sequence` 决策；这些事实已经分别由 ProductPlan 和 UiDesign 确认。它只为需要后端/数据实现的业务 action 或业务 step 选择 endpoint：

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

- 128k 上下文：TechnicalPlan 只注入实体上下文，以及拆分后的 ProductPlan 目标/验收、已确认数据规则与目标身份、业务流程、页面信息和业务动作上下文，并在修订时注入修订上下文；UiManifest 仍由运行时按页面/API 范围读取，不进入规划模型提示词。

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
RequirementSpec -> ProductPlan -> UiDesign（可选） -> TechnicalPlan
TechnicalPlan + EntitySourceBinding -> development_readiness_gate -> Build DAG
```

ProductPlan 或 UiDesign 变化时重新确认受影响 TechnicalPlan/运行时页面契约；TechnicalPlan API 或 Schema 变化时使相关 Build DAG 失效；EntitySourceBinding 变化时使引用实体的页面/API Build DAG 失效。纯代码实现错误进入 SmallTask 修复，不回到规划阶段。

TechnicalPlan 确认前执行确定性一致性检查：UI 中声明的每个业务操作、显示项和跳转必须能映射到 ProductPlan；每个 ProductPlan `business` action 和组合中的每个 `business` step 必须有且只有一个 endpoint 实现；TechnicalPlan 不得为 `navigation`、`interface` 或 `external` 行为重复作产品/UI 决策；每个技术绑定必须引用已存在的 action/step、endpoint、Schema 和页面。启用权限时，`authorization-manifest.v1` 必须完整覆盖 RequirementSpec 规则、ProductPlan 目标和数据规则的实体/API 绑定；资源键、系统资源及 endpoint resource binding 均由确定性编译器生成。编译后的 `endpoint`、`navigation`、`local`、`external`、`sequence` 联合契约必须完整闭合，失败时不得进入工作台。

## 上下文预算

RequirementSpec 与 ProductPlan 只持久化用户提出或确认的产品事实，不保存模型推测的 `assumptions` 或 `risks`。TechnicalPlan、EntitySourceBinding 和任务规划只接收当前目标所需的结构化输入，不复制上游全文或无关历史记录。

单次模型上下文限制为当前阶段所需内容：

- ProductPlan：RequirementSpec；
- UiDesign：单页 ProductPlan 摘要；
- TechnicalPlan：RequirementSpec 实体上下文、拆分的 ProductPlan 行为上下文和必要修订信息，不加载完整上游文档或 UI manifest JSON；
- EntitySourceBinding：单个实体定义和所选数据源的有界元数据；
- Build：当前 Unit 的 TechnicalPlan Endpoint、页面实现契约、实体绑定摘要、UI 设计文件路径和工作区快照。

ProductPlan 每次自动修复只回灌最多八条校验摘要，TechnicalPlan 最多回灌十二条页面/API/数据源契约摘要；两者都不追加历史模型全文。重试候选保留在当前节点局部变量中，TechnicalPlan 只注入精简实体和 ProductPlan 行为上下文，只有通过校验的计划才进入确认产物，从而保持 128k 上下文预算与 checkpoint 可检查性。

TechnicalPlan 的模型生成、JSON 解析和正式契约校验共用最多三次“生成 → 校验 → 错误反馈修复”总预算。三次仍失败时，节点停留在 `technical_planning` 并返回 `technical_plan_generation_error`，仅包含精简错误与重新生成操作，不写入 Markdown/JSON，也不发出确认产物。规划模型的 `llm.token` 原始 JSON 只用于内部生成过程，前端聊天和历史消息均不得展示；确认界面直接读取 Workflow `state/result.technical_plan` 的结构化计划。

TechnicalPlan 的实体定义严格校验继续暂停；页面行为闭合校验暂时暂停；API Contract 的 `entity_ids`、可选 `entity_field_ref`、Schema 引用和统一分页校验启用。分页响应对象同级只能有 `total`、`pageSize`、`current`、`list` 四个字段。页面路由和 Endpoint 结构校验保持不变。

任何完整 TSX、数据库工具原始输出、仓库扫描结果和历史日志都写入文件，只向主上下文返回路径、哈希和有界摘要。
