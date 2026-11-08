# 产品规划、UI 设计与技术规划分层

## 目标

阶段回看只改变浏览位置。进入计划后需求阶段只读；进入开发及后续任何阶段后，需求和计划都只读，新增或调整需求统一从开发阶段提出。系统通过正式修订返回需求/计划时，当前业务阶段的权威会话重新允许对话、修改和确认，旧会话仍只读；规划会话只能由流程创建。

应用创建流程把产品设计、技术规划和开发执行拆成三个独立阶段，避免 UI 设计稿与页面详细设计重复定义布局、组件和交互，也避免 UI 确认后自动越过阶段入口生成 TechnicalPlan。

四个正式产物均采用 LangGraph 原生人工审阅门：需求澄清期间只更新 checkpoint 中的未完成事实，不生成或写入 RequirementSpec 草稿；模型判定没有重要缺口后才写入待确认 Markdown/内部 JSON，再进入独立 review 节点调用 `interrupt()`。阶段内用户操作通过同一 thread 的 `Command(resume=...)` 恢复；DESIGN 与 PLAN 使用独立 thread，通过受校验的服务端交接承接当前产物和未消费确认门。创建规划的 SQLite checkpoint 是上下文权威，前端只提交包含 `gateId`、产物摘要和显式 action 的 `applicationPlanningInteraction`，不回传 Workflow 状态来推断恢复节点。`confirm`、`revise`、需求澄清 `answer`、UI `ui_action` 和底部 `design_change` 具有不同类型；前端按按钮/表单的明确意图产生 action，产物节点直接消费 action，用户文本只作为回答或修订内容。服务端按 thread 串行完成中断读取、版本校验和恢复，旧卡片、摘要不匹配或并发重复提交必须在节点执行前拒绝。

```text
RequirementSpec
  -> 产品确认
ProductPlan
  -> 产品确认
UiDesign（真实 React 页面稿 + UiManifest）
  -> 产品明确确认
等待进入计划阶段
  -> 用户显式确认 enter_planning
TechnicalPlan
  -> 开发确认
Workbench
  -> api_design（按 Endpoint 设计字段来源，确认后独立结束）
  -> api_design_readiness_gate（页面/API 开发前校验关联 Endpoint）
  -> Build DAG
  -> Build / Test / Acceptance
```

产品确认“做什么、有哪些页面、用户如何操作、页面长什么样”；开发确认“API、数据、权限和工程如何实现”。同一页面不再经过第二次视觉详设确认。

## 正式产物

### RequirementSpec

草稿路径：`.devagentstudio/drafts/specs/requirement-spec.md|json`；确认后正式路径：`.devagentstudio/specs/requirement-spec.md|json`

负责人：产品。

包含产品目标、范围、用户角色、功能模块、业务流程、初步页面清单和业务信息需求。它不生成产品验收标准，不包含模型生成的产品假设或产品风险，也不决定数据源、存储方式、页面布局、API、数据库表和代码任务。产品事实不明确时必须在需求阶段向用户澄清；用户未要求的可选细节直接省略。内部技术配置可以随工作流保存，但不得出现在 RequirementSpec 产品确认文档、概览或编辑器中，也不得成为需求澄清问题。

### ProductPlan

草稿路径：`.devagentstudio/drafts/plans/product-plan.md|json`；确认后正式路径：`.devagentstudio/plans/product-plan.md|json`

负责人：产品。

包含：

- 拍平页面清单和稳定 `pageId`；
- 页面目标、业务信息项和稳定 `actionId`；
- 每个 action 的产品行为类型、预期业务/可见结果和组合步骤；
- 页面跳转关系；
- loading、empty、error、success 等产品状态要求；
- 页面级产品验收标准。

ProductPlan 使用 `business`、`navigation`、`interface`、`external`、`sequence` 表达产品可见行为。`business` 只说明查询、提交、变更、导出等业务结果，不选择 endpoint；`interface` 只说明需要本地界面变化，具体控件效果由 UiDesign 实现；组合行为以稳定 `stepId` 和产品结果表达。ProductPlan 不包含 HTTP method、endpointId、API Schema、数据库操作或代码文件。

ProductPlan 中面向产品角色展示的验收标准，只描述生成应用的目标用户能够观察或完成的产品结果。DevAgent Studio 自身的本地预览、代码生成、编译、构建、lint、typecheck、自动化/集成测试、质量门禁、工作流节点和“何时进入用户验收”等交付条件属于独立工程运行状态，不得写入应用产品验收标准；确定性归一化会剔除这类越界文案。

正式 JSON 使用 `product-plan.v5`，页面事实只保留拍平的 `pages`，不生成、不存储也不兼容读取 `frontend_pages`。ProductPlan 不保存运行态角色、角色关系、`allowed_roles`、资源键、策略键或固定 `/roles` 页面。模型原始输出必须先通过精确 JSON 字段校验，再进入产品语义归一化和一致性校验。核心字段固定为：

权限开启时，RequirementSpec 的每条 `restrictedPages` 都以已确认的 `targetPageId` 引用业务页面；服务端在模型输出校验后仅根据该稳定绑定确定性生成内部 `authorizationTargets.pageRules[{ruleId,pageId}]`，不得按页面展示名称匹配或猜测。操作规则必须为 `{ruleId,pageId,actionId}`。`actionId` 只在所属页面内唯一，不能脱离 `pageId` 作为权限目标；`stepId` 仅用于产品组合行为，绝不进入权限目标。自然语言中已经验证的受控页面或操作会自动启用需求层权限能力；若当前应用尚未启用权限，只补问真实初始管理员 `subjectId`，不要求用户重复选择开关。ProductPlan 不另存资源键或角色授权。需求文档的“角色与权限”章节通过平台共享编译器展示业务角色、页面/操作资源及默认授权；联合确认前复验角色、目标映射和全局资源键碰撞。资源目录从当前 RequirementSpec、ProductPlan 与 application.json 派生，不保存第二份可编辑授权数据。技术规划仅追加操作到 Endpoint 的权限绑定，并以“接口权限绑定”展示本阶段结果。修改角色或默认授权必须返回需求修订并重新联合确认；页面权限不会自动继承为接口权限。

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

- `.devagentstudio/ui-design/pages/<PageKey>/index.tsx`
- `.devagentstudio/specs/ui-designs.json`

负责人：产品。

生成 React 页面稿时，它是页面视觉设计的唯一权威来源，负责布局、区域、组件、弹窗、操作入口、视觉层级、响应式策略、明暗主题和页面状态的视觉呈现。设计稿使用 Mock 值和本地状态表达搜索、筛选、弹窗、表单与确认交互，不接入真实 API。Mock 只能给 ProductPlan 已声明的信息项填充示例值，不能新增业务字段、指标、筛选器、操作、跳转、角色或业务区域。当前合同必须完成并确认 UI 设计，不支持跳过。确认只到达 `awaiting_planning_stage_entry`，用户点击绿色入口卡后，当前工作台为该应用创建或恢复独立的 PLAN StageSession，并原地切换到 Planning 阶段。先以 `forwardedProps.planningStageHandoff` 校验当前来源 Run/lifecycle revision 并在 PLAN 会话的独立线程重建原审阅门，再在该线程提交 `enter_planning`。交接不运行产物模型、不消费用户确认；旧 DESIGN 线程不再有权推进。RequirementSpec、ProductPlan 与 UiDesign 从服务端当前 checkpoint 传递，不使用前端快照。`initialization.iterationId` 保持不变，`initialization.threadId` 指向新阶段线程。

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
      "code_path": ".../.devagentstudio/ui-design/pages/Orders/index.tsx",
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

`preview_path` 只用于隔离设计稿预览，不是产品正式路由。正式路由始终来自 ProductPlan。Graph 运行态可以临时携带 `code` 供 `DesignRenderer` 渲染，落盘时必须剔除；恢复会话时只允许从同一工作区 `.devagentstudio/ui-design/` 受控目录按 `code_path` 恢复源码。

UI 生成只能消费已确认 ProductPlan，不能发明 ProductPlan 中不存在的页面、业务字段或操作。

生成、模板适配、用户调整和最终确认都必须执行确定性一致性检查：ProductPlan 的每个 `actionId` 和 `informationItemId` 必须在 TSX 中有静态控件映射；`interface` action 还必须通过 `data-ui-effect` 固化实际本地界面效果；未知 ID、缺失映射、无归属交互控件、无归属业务展示组件都拒绝确认。仅用于切换原型状态的控件必须显式标记 `data-preview-only="true"`，不得进入 ProductPlan 或 TechnicalPlan。模板只能作为布局和组件风格参考，必须先按 ProductPlan 重写业务语义，禁止原样携带模板字段或操作。

RequirementSpec、ProductPlan 和 UiDesign 的产品确认均不得要求产品角色选择或审核数据源、数据库、持久化和 API 方案；这些内容只进入 TechnicalPlan 的开发确认。

### TechnicalPlan

正式路径：`.devagentstudio/plans/technical-plan.json`

负责人：开发。

TechnicalPlan 只写入 `.devagentstudio/plans/technical-plan.json`，`technical_plan` 是唯一语义来源。

TechnicalPlan 通过结构化面板审阅，完整展示所有 Path/Query/Header 参数、请求体、响应体及嵌套类型、必填和已声明约束。正式产物与正式修订草稿均只保存 JSON，不生成或读取技术规划 Markdown，也不把 JSON 作为用户可编辑文档。修改通过原 AG-UI 修改意见提交，新版本仍需显式确认；修订继续保留基线、上游哈希与生命周期绑定。技术确认回执只包含 JSON，模板交接和 Electron 工作区检查不再要求 Markdown。多余的 `plans/technical-plan.md` 或技术修订草稿 `artifact.md` 直接忽略，也不参与停止后继续的工作区指纹；正式目录的多余文件不主动删除，修订草稿仍按原确认/放弃流程整体清理。其他产物的 Markdown 合同保持原用途。

TechnicalPlan 包含：

- `architecture` 三段技术架构、业务实体、API Contract、请求/响应 Schema；
- `entities` 由技术规划模型根据已确认 ProductPlan 的页面、信息项、业务操作与业务流程独立生成，是实体与 API 的唯一字段事实源；技术规划不读取 RequirementSpec 的 `entities`；
- API Contract 通过 `entity_ids` 关联实体；Schema 字段可使用 `entity_field_ref` 表示实体来源，计算、聚合和传输字段可以不做实体映射；
- ProductPlan 中 `business` action/step 到 endpoint 的 `action_implementations`；
- ProductPlan 与 UiManifest 的上游内容哈希。

首版技术规划在同一个生成节点内分段完成：

1. 全应用先统一实体及业务 Contract 的轻量身份与页面归属，再逐实体展开字段、逐 Contract 生成精简接口目录、逐页面生成业务 action/step 的 `action_bindings`；程序合并并验证完整目录，不让模型一次输出全应用字段、接口和动作映射。
2. 按业务能力划分 Contract；小 Contract 一次生成参数、请求/响应 Schema 和 Endpoint，大 Contract 在同一身份内分批生成后拼接，只读取关联实体与页面需求，各 Contract 的 Schema 引用在自身内部闭合。
3. 按页面分别生成 `endpoint_dependencies` 和 `action_implementations`，读取精简全局接口目录，支持跨页面及跨模块复用同一接口，不重复生成实体或 API。
4. 程序组装现有 TechnicalPlan，补齐固定架构、权限 Manifest、上游哈希并执行原完整校验，保存完整内部 JSON 后停在原技术规划确认门。

全局目录的模型响应边界固定为四类：`catalog_registry` 只返回 `{entities:[{id,name,description}],contracts:[{id,name,entity_ids,base_path,page_ids}]}`；`catalog_entity` 只返回当前实体的 `{fields:[...]}`；`catalog_endpoints` 只返回当前 Contract 的 `{endpoints:[{id,name,method,path,summary}]}`；`catalog_bindings` 只返回当前页面的 `{action_bindings:[...]}` 或严格的 `catalog_issue`。字段和接口身份不能在其他片段重写。每个接口目录提交前检查能力归属：路径必须位于当前 `base_path` 的完整路径段范围内，不得占用其他已登记 Contract 的 ID 命名空间或更具体的路由范围；不同能力可以共享同一个 `base_path`，但仍不得重复声明相同接口。跨 Contract 的 Endpoint ID 与规范化 method/path 冲突反馈双方接口及归属。接口模型只额外读取其他能力的轻量 ID、名称和路径范围，不携带完整业务正文。恢复时重新校验当前候选接口缓存，原子撤下无效片段后定向重生成，保留有效身份、实体和其他接口；后续目录、映射依赖键包含已完成接口内容，来源改变后不会复用原错误依赖。各生成片段独立最多尝试三次，沿现有恢复入口执行。每页映射提交前检查真实业务槽位完整覆盖。静态页面不额外调用业务映射模型。

目录子片段以 `catalog-part:` 加修复输入和内容摘要标识，校验后先落盘，连接中断或进程退出可从未完成片段继续。完整目录校验成功后，一次原子发布 `catalog` 与下游失效，同时撤下目录子片段引用；完整目录发布前不生成完整 Contract。页面映射阶段就可以报告目录缺口，不必等到页面技术引用阶段。修复只重做受影响实体、业务能力目录和页面映射，其他部分在重新验证后复用；若修复中再次报告缺口，保留最初已发布目录作为失效基线，避免遗漏前一轮修改影响的缓存。原样无效修复会撤下本轮目录子候选，下一次目录修复必须实际重新请求；同一页面的目录修复链最多三轮，不能通过修改内容摘要重置。

轻量身份目录、单个实体字段和单个 Contract 的精简接口目录仍各为一个独立重试单位；本轮不增加它们内部的递归分批。整个应用的完整目录由代码组装，全局引用校验和业务确认规则不变。

接口生成按业务要求明确记录操作参数的真实来源：可以使用内部 id、业务编号或上游已提供的身份。需要从列表选择记录时，响应应提供对应身份；模型通过页面需求和关联 GET 的响应字段摘要核对来源。平台不按路径字面量 `/{id}` 或固定响应字段名推断业务用途，也不据此拒绝候选或强制重建 Schema。原有的结构、引用和参数一致性校验保留。实体引用只能选择真实存在的字段；最终物化不替模型按属性同名补引用，尤其不能把备件、供应商等选项 id 猜成采购申请 id。

共用生成规则与 `entity_definitions.py::entity_json_schema` 一致：隐式实体 id 在 API JSON 中为 string，关联字段的实体类型为 text，接口响应、请求和路径参数保持同一身份类型，不能因为数据库主键为 BIGINT 就输出 JSON number。整数实体字段使用语义类型 number，整数/非负约束写入说明；API 数值规则仅用于完整或分批 Contract 的 JSON Schema：正整数业务输入使用 integer 和 minimum=1，非负数值包含 minimum=0，不能只在说明中写约束。实体字段阶段不接受 integer、minimum 等 Schema 属性。

逐页映射提示列出从已确认 ProductPlan 提取的全部合法动作/步骤槽位。普通 business 动作始终只有 stepId=null，不能因为刷新多个数据结果就在技术规划中虚构步骤。动作绑定主要业务接口；主体和关联列表已有独立读取接口时，在页面 endpoint_dependencies 中完整声明读取，并通过 trigger 明确初始加载/刷新时触发。不为此重复创建业务接口，也不能声称主要接口返回其他接口的数据。真实业务操作或所需读取缺失时，才以原动作身份报告 catalog_issue，沿既有目录修复补齐。片段独立尝试、依赖修复有界，保留原确认门，不修改已确认产品动作。这些生成与结构规则不能代替业务语义和跨模块输入来源的独立审阅。

分批 Contract 提示将冻结目录、Schema 和引用标记为只读输入，并在输入之后重申当前响应的唯一根结构；接口批次仅返回 endpoints，禁止回传 schemas、endpoint_refs 或根级 authentication。Schema 目录给出 base 与 fields/required/references 同级的示例，字段批次仅返回 properties。意外回传只读上下文仍严格拒绝，不通过丢弃多余字段掩盖 Schema 变化；已通过片段保留，每个失败批次独立最多尝试三次。

references 必须声明未来字段内部的裸引用，包含分页 list.items 等嵌套位置。字段若引用本 Contract 已注册、却未在当前 Schema.references 登记的对象，错误属于冻结目录决策，必须先原子撤下该 Contract 的目录和子片段，再由独立有界的依赖修复重建；全局目录与其他 Contract 保留。反馈包含具体 Schema 和遗漏名称，不让无权修改目录的字段阶段重复空转。未注册名称仍是字段输出错误，只重试该字段；不自动补引用或内联共享对象绕过冻结规则。

字段提示的 schema_ref_examples 使用当前真实注册名给出完整 JSON 引用例子。OpenAPI 的 `#/components/schemas/...`、`#/definitions/...` 等带前缀写法仍按未注册引用拒绝，不作别名归一化；明确反馈当前裸名称协议后再由原模型修正。

已解决诊断按阶段收口：当前首次未完成的完整目录、Contract 或页面通过后，后续模块不再继承这一校验错误；原始用户要求继续保留。页面提示也列出唯一合法业务动作/步骤清单，导航、界面和外部动作由上游决定，不因为跳转目标有读取接口就重新绑定 Endpoint。

读取 Schema 与页面引用还须核对 information_items 的实际数据来源。关联 id 不等于编号/名称等业务展示字段；展示投影可作为只读传输字段，不伪造成当前实体字段。依赖其他查询补齐数据时，必须声明真实初始加载/刷新依赖，弹窗打开时的选项查询不能冒充初始列表数据来源。当前结构校验不能普遍证明自然语言展示语义，复杂应用仍需独立内容核验。

首次生成与正式修订停止后共用原“继续生成”机制：真实 writer 退出后，保存原阶段、可选正式修订和工作区内容指纹，再取消执行并释放占用。首次生成没有正式修订，不能因此转为开发就绪。继续动作由 Backend ActionPlan 签发，经原 lifecycle CAS 与业务节点重试消费结束凭据；生成片段按同输入摘要复用，不改变显式确认要求。内置失败重试保留原业务要求，只有用户明确提交新的修改要求才改变该输入。

Contract 内部分批由代码根据当前模型的**输出预算**决定，和模型输入上下文窗口无关。接口数超过 12，或输出体量估算超过 `min(6000, default_max_tokens × 40%)` 时分批；十二个轻量接口可以一次生成。估算采用目录、关联实体字段和请求/响应重复成本的保守权重，不声称等同供应商真实 token 数。批次目标为 `min(3500, default_max_tokens × 25%)`，接口每批最多 6 个；相同请求/响应引用的接口优先相邻，最终顺序仍由目录恢复。这些是初始策略值，实际响应大小仍由长度终止信号兜底。

大 Contract 先冻结唯一 Schema 名称目录、根属性、对象字段及必填清单、跨 Schema 引用与接口请求/响应引用。每个 Schema 只有一个定义位置，公共和私有定义均不重复生成；复杂嵌套对象通过具名 Schema 引用。对象按字段组展开（最多 24 个且受批次目标约束），接口批次没有 Schema 写权限。接口提示只携带当前批次关联动作与 Schema 引用闭包，并在前后明确限定目标 ID，避免模型把整个页面的接口重新展开。代码合并完整 Schema、按目录顺序合并 Endpoint，再执行原 Contract 完整校验，不增加最后一次模型重写。

仅当 SDK 明确报告 `finish_reason=length` 或 `stop_reason=max_tokens` 时，将失败接口/字段组记录为二分任务，交给依赖调整后的下一次生成执行；普通 JSON、身份或语义错误只重试当前片段，每片段最多三次。模型调用边界异常交回现有失败流程，不叠加片段重放。小 Contract 若明确截断，下一次进入分批；单个接口/字段或 Schema 目录仍截断则显式失败，不提交半成品。批次使用 Contract 归属、冻结目录与内容摘要作为持久化键，完成邻居不重做；目录修复须一并撤销受影响 Contract 的 Schema 目录、字段、接口和缩批标记，保留未变化模块。

片段属于内部候选，不是额外的正式文档或确认节点。当前按模块串行调用模型，不增加调度器或新的 AG-UI 接口。各片段独立最多尝试三次（首次加两次重试），只重试解析或局部校验失败的当前片段；最新诊断仅注入当前提示，成功模块不消耗其他模块的机会。片段耗尽后立即返回原失败审阅，不再乘以外层重试次数。计数仅属于本次执行，不写入恢复存储；手动重试或进程重启后显式继续时，未完成模块获得新预算，同输入成功片段仍复用。用户停止与保存失败不触发模型重放。每个片段校验通过后，必须先同步写入现有内容寻址资料库，再原子更新 `.devagentstudio/runtime/technical-plan-generation.json` 的当前输入摘要与已完成引用，随后才能请求下一模块。模型连接异常或进程退出后，通过现有业务节点重试读取同输入目录继续；ProductPlan、UI 摘要、application.json 或原始修改要求变化则清空当前引用，拒绝迟到旧输入写入。Graph 只保留内部摘要 `technical_plan_generation_input_sha256`，不重复保存片段正文。完整正式文档提交成功后清理当前目录；失败片段不会写入正式计划，也不因恢复而消费用户确认。存储损坏显式失败，不扫描历史文件或静默降级。

已有完整候选后，二次修改、跨页面权限冲突等全局修复继续使用原局部文件编辑器，不重新分段生成。最终正式产物仍只有一份 TechnicalPlan JSON，结构化审阅和显式确认规则保持不变。

页面生成先独立核对接口真实能力，不把目录覆盖声明当作正确答案。目录不能满足当前业务动作/步骤或首次加载时，只返回内部 `catalog_issue: {pageId, kind, actionId, stepId, reason}`；kind 为 `business_action` 或 `initial_load`，直接业务动作的 stepId 为 null，首次加载的 actionId/stepId 均为 null。未知页面/动作、非业务 step 或混合输出不能触发上游失效。有效缺口撤下旧目录并持久化待修复上下文，在独立有界的目录修复链下一轮先修目录；新目录与下游依赖失效一次原子提交，未变化的契约和页面保留，实体变化也会使消费它的契约失效。原样返回且未改变报告页面依赖的目录不能清除缺口。修复通过后清掉已解决的系统诊断，避免后续页面沿用过期缺口；原始业务要求始终保留。这些均为节点内部候选，不新增确认门、公开接口或历史兼容格式。

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
      "name": "订单管理",
      "entity_ids": ["Order"],
      "base_path": "/api/orders",
      "schemas": {},
      "endpoints": [
        {
          "id": "orders.list",
          "name": "查询订单列表",
          "method": "GET",
          "path": "/api/orders",
          "summary": "分页查询订单并返回列表展示所需字段。"
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
TechnicalPlan 顶层 `entities`；实体不做全局数据源绑定，数据源身份只存在于后续已确认的 Endpoint API 设计。API 设计场景 Entity 只能从当前 Contract 关联的 TechnicalPlan 实体复制，复制后保持只读且不回写全局实体；模板实体或字段变化会因 TechnicalPlan 契约指纹变化使 Endpoint 设计失效。API Contract 必须通过非空
`entity_ids` 关联一个或多个实体，禁止 `data_source_id`。角色/跳转/状态来自 ProductPlan；UI 路径与控件映射来自已确认 UiManifest；
运行时按当前构建范围组合这些正式上游产物。

每个 API Contract 和 Endpoint 都必须持久化非空且至少包含一个中文字符的 `name`，允许混合英文缩写。Contract `name` 表示接口分组名称，Endpoint `name` 表示操作名称；Endpoint `summary` 继续保存较完整的接口说明。两层 `name` 不参与稳定身份或引用，关联关系仍只使用 Contract/Endpoint `id`。名称保存在结构化候选中；新增 Contract 或 Endpoint 仍必须提供合法名称。

技术规划阶段的右侧结构化文档面板展示这两层名称：Contract 卡片显示分组名称和 `base_path`，Endpoint 列表显示方法、接口名称、路径和 `summary`，选中接口的详情标题也显示接口名称。进入工作台后的接口快捷任务入口、开发产物接口树和字段映射目录均突出显示 Endpoint `name`；字段映射目录同时显示 Contract `name` 与 Endpoint `name`，并沿用开发产物 API 树的紧凑层级、缩进连接线、紫色接口图标和悬停高亮，悬停接口一秒后显示方法与完整路径。选中接口的编辑区标题继续显示 Contract 名称、请求方法和完整路径；开发产物接口树的请求方法和完整路径继续通过悬停查看。Contract 分组只突出显示 Contract `name`，不增加分组悬停信息，并统一使用“接口”作为界面文案。页面绑定、Endpoint 设计及 Markdown 展示仍保持各自现有格式。

Endpoint 是否存在请求体由业务语义决定，不由 HTTP Method 单独决定。只依赖路径参数、查询参数和登录态即可完整表达的命令型 `POST`、`PUT` 或 `PATCH` 可以使用 `request_schema_ref: null`；实际消费请求体字段的 Endpoint 必须引用同一 API Contract `schemas` 内的真实请求 Schema，禁止为了通过校验生成无业务字段的空请求对象。

TechnicalPlan 模型不再生成 `navigation`、`local`、`external` 或产品可见的 `sequence` 决策；这些事实已经分别由 ProductPlan 和 UiDesign 确认。它只为需要后端/数据实现的业务 action 或业务 step 选择 endpoint：

平台规范化 TechnicalPlan 候选以及恢复失败 checkpoint 时，会把 `action_implementations` 和
`stepBindings` 已明确选择且真实存在的 Endpoint 确定性并入同页 `endpoint_dependencies`。已有依赖的
`usage`、`trigger` 和首屏标记保持不变；未知 Endpoint 不会被自动创建或掩盖，仍由一致性校验拒绝。
存在完整候选后，缺失的业务 `action_implementations`、Contract、Schema、页面引用和混合错误统一通过
`grep/read_file/edit_file` 局部修复。模型按需查阅已确认 ProductPlan，并编辑绑定及必要依赖；平台仍严格
校验业务 action/step、Endpoint 身份和全量引用闭合。首版修复与二次修改使用同一入口，不再按错误类别
要求重写整个 Contract 或回落整份规划；修复预算耗尽后返回原错误审阅门。

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
    "path": ".devagentstudio/ui-design/pages/OrderList/index.tsx",
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

TechnicalPlan 的二次修改和首版完整候选的自动修复复用 DeepAgents `grep`、`read_file`、`edit_file`：模型按需搜索和读取片段，再以唯一匹配的 `old_string/new_string` 修改内存候选，不再输出整个 Contract 或规划。候选只包含 architecture、entities、api_contracts、pages，已确认 ProductPlan 和脱敏应用约束只读；工具不能访问工作区、改上游或整文件写入。单次读取最多 120 行、搜索最多 40 项；不额外设置模型调用次数上限，图执行沿用框架默认配置。模型回复不代替文件修改；最终 JSON、根字段、中文说明和完整技术合同仍须通过平台校验，编译或校验失败按原始意见与当前错误继续局部修复，原三轮预算保持不变。全部通过后沿原事务保存完整内部 JSON 并等待明确确认。首版尚无候选时使用上述分模块生成器；片段结构错误使用各自三次预算，明确截断走有限缩批任务，已完成模块保留。拿到完整候选后不因编辑失败回退整份重写。内部工具文件不会作为可编辑用户产物或新的配置源。

用户原始修改要求固定保留在内部生成输入 `planning_adjustment_request`，每轮系统校验反馈单独放入 `technical_plan_validation_feedback`，只替换本轮诊断，不覆盖原始要求，也不累积历史修复全文。无候选时的完整生成、有候选时的局部编辑和确认复验均携带原始要求；确认时的校验修复沿用技术事务已冻结的业务要求。中文说明错误进入同一完整校验与编辑流程，不另起首版翻译模型。系统诊断不能撤销用户意见，修复仍须遵守已确认上游与应用配置，新版本继续等待正式确认。这两个输入仅用于模型上下文，不新增正式产物字段或公开 AG-UI 合同。

- 128k 上下文：TechnicalPlan 只注入实体上下文，以及拆分后的 ProductPlan 目标/验收、V1 页面与操作权限目标身份、业务流程、页面信息和业务动作上下文。确认卡直接修订和 `workbench_plan_revision` 都以 checkpoint 中当前 TechnicalPlan 为 authoritative baseline，叠加本轮修改请求并返回完整新版本，同时保留未受影响事实及必要依赖闭合；RequirementSpec、ProductPlan 或 UiDesign 先变化时旧 baseline 失效并按新上游重建。数据权限不进入第一阶段模型上下文；UiManifest 仍由运行时按页面/API 范围读取，不进入规划模型提示词。

## Endpoint API 动态映射与工作台执行

页面没有独立详设文件；页面事实由 ProductPlan、UiDesign、TechnicalPlan references 和运行时 `PageImplementationContract` 共同提供。API 则有独立 `api_design` 节点，但它不是第二份 API 契约：TechnicalPlan 唯一负责 Endpoint 的 method、path、参数和 Schema，API 设计只能给这些既有 API 叶子字段补充数据库/外部 API 字段来源或自然语言业务处理。业务处理不保存表达式或可执行规则，默认由 ApplicationService 按用户说明实现。当前实现不读取、不迁移历史 Endpoint 设计结构。

Endpoint 设计和页面/API开发流程固定为：

```text
待设计 Endpoint -> 独立映射弹窗 -> 保存 Endpoint JSON/Markdown -> 返回原界面
页面或 API 开发
  -> api_design_readiness_gate
  -> 目标包含 Endpoint：一次性返回全部关联 Endpoint 列表并等待统一检测
  -> 缺少/过期：列表标识 pending/stale，用户逐项补齐或修改
  -> 全部就绪：仍可查看或修改完整映射集合
  -> refresh 且确认的 revisions 仍一致：inspect_workspace
  -> prepare_build_tasks（二次复检 Endpoint 设计）
  -> Build DAG 用户确认
  -> Build / Test / Acceptance
```

当前正式契约为 `endpoint-field-mapping.v7`，详见 [单数据源字段取值规则](FIELD_VALUE_RULES.md)。工作台沿用原有数据来源选择和更换入口，不新增数据表 / 外部 API 场景 Tab。`sourceBinding` 保存单表或单 Operation 身份；`databaseWrites`、`databaseQuery` 和 `externalApiBindings` 按目标保存统一 `right` 取值规则，支持接口参数、固定值、内置上下文和业务生成。业务加工显式保存依赖、自然语言规则、缺值策略和默认值；同一请求参数可以复用。响应的多字段加工保留 `source_mapping`，无数据源字段的返回取值使用 `value_mapping`，纯业务入参用途使用 `business_description`。规则在行内应用，正式保存仍须显式确认；草稿不推进开发。查询树保留顶层及一层子组 AND/OR，空值运算符不带右值，update/delete 不允许无条件执行。

每次确认写入 `.devagentstudio/plans/endpoints/endpoint--<contractId>--<endpointId>.json/.md`。JSON 当前版为 `endpoint-field-mapping.v7`，保存 `databaseOperation`、`databaseWrites`、`externalApiBindings`、可选 `databaseQuery: { join, items }`、正式 `fieldMappings`、TechnicalPlan 指纹、脱敏来源快照及修订号；Markdown 展示写入字段、外部 API 请求取值规则、值来源、条件分组和括号语义。生成与验收消费结构化写入值、外部请求取值规则和完整查询树，并使用参数化值。双文件、版本、确认状态、指纹和修订号均须通过校验；旧正式产物需重新配置，旧草稿不加载，不迁移或双写。

开发门禁只投影目标范围内的 Endpoint 状态，不保存聚合 `apiDesignResult` 快照。右侧开发产物通过独立 `/endpoint-designs/run` AG-UI 读取当前正式产物；该接口按当前工作区、API Contract 与 Endpoint 标识提供 `get/prepare/save`，返回 pending、confirmed 或 stale 状态以及结构化设计和 Markdown。只要目标包含 Endpoint，门禁首次进入就展示完整状态集合；保存只更新映射，不启动主工作流，用户可从已完成条目的“查看映射”进入右侧工作台修改。用户回到门禁点击“确认并检测”后统一复检，全部有效便直接继续开发，不再展示 API 映射确认卡片；无 Endpoint 的纯静态页面直接通过。

旧 EntitySourceBinding 代码、设计页面和独立入口保留，但不再处于正常旅程，不影响 API 设计状态、开发门禁或 Build 上下文。

正式依赖顺序为：

```text
RequirementSpec -> ProductPlan -> UiDesign -> 等待进入规划阶段 -> TechnicalPlan
TechnicalPlan -> independent Endpoint mapping -> api_design_readiness_gate confirmation -> Build DAG
```

ProductPlan 或 UiDesign 变化时重新确认受影响 TechnicalPlan/运行时页面契约；TechnicalPlan API 或 Schema 变化时使相关 Endpoint 设计和 Build DAG 失效。纯代码实现错误进入 SmallTask 修复，不回到规划阶段。

TechnicalPlan 确认前执行确定性一致性检查：UI 中声明的每个业务操作、显示项和跳转必须能映射到 ProductPlan；每个 ProductPlan `business` action 和组合中的每个 `business` step 必须有且只有一个 endpoint 实现；TechnicalPlan 不得为 `navigation`、`interface` 或 `external` 行为重复作产品/UI 决策；每个技术绑定必须引用已存在的 action/step、endpoint、Schema 和页面。启用权限时，`authorization-manifest.v3` 必须完整覆盖 RequirementSpec 页面/操作规则及 ProductPlan 目标，确定性生成页面、顶层 action 和唯一系统资源，以及 Endpoint `operationResourceKeys` 的 ANY-OF 绑定；V1 出现数据权限字段必须拒绝确认。资源键、系统资源及 endpoint resource binding 均由确定性编译器生成。编译后的 `endpoint`、`navigation`、`local`、`external`、`sequence` 联合契约必须完整闭合，失败时不得进入工作台。

## 上下文预算

RequirementSpec 与 ProductPlan 只持久化用户提出或确认的产品事实，不保存模型推测的 `assumptions` 或 `risks`。TechnicalPlan、Endpoint API Design 和任务规划只接收当前目标所需的结构化输入，不复制上游全文或无关历史记录。

单次模型上下文限制为当前阶段所需内容：

- ProductPlan：RequirementSpec；
- UiDesign：单页 ProductPlan 摘要；
- TechnicalPlan：拆分的 ProductPlan 页面、信息项、行为与业务流程上下文，以及必要修订信息；不读取 RequirementSpec 的 `entities`，也不加载完整上游文档或 UI manifest JSON；
- Endpoint API Design：单个 Endpoint 契约、场景 Entity 模板和按需加载的数据源有界元数据；
- Build：当前 Unit 的 TechnicalPlan Endpoint、Endpoint 自包含字段映射/业务说明/来源快照、派生数据流表达式、页面实现契约、UI 设计文件路径和工作区快照。

ProductPlan 每次自动修复只回灌最多八条校验摘要，TechnicalPlan 最多回灌十二条页面/API/数据源契约摘要；两者都不追加历史模型全文。TechnicalPlan 拿到完整候选后统一让模型按需搜索、读取和编辑报错片段及必要依赖，不因混合错误回退整份计划生成。三次预算耗尽时，最后一个可解析候选和精简错误只保留在 LangGraph checkpoint 的内部修复字段中，不作为 AG-UI 公开状态，也不写入正式 TechnicalPlan JSON；用户授权“重新生成”后从该候选继续局部修复。只有通过校验的计划才进入确认产物，从而保持 128k 上下文预算与 checkpoint 可检查性。

TechnicalPlan 分模块生成时，每个身份目录、实体字段、接口目录、页面绑定、完整小 Contract、Schema 目录、字段组、接口批次和页面技术引用各自拥有最多三次尝试。片段耗尽后不再进入外层重试；同一目录或 Contract Schema 决策的依赖修复链单独最多三轮，成功片段继续复用。完整候选取得后，正式契约的“校验 → 局部编辑修复”仍保留最多三轮预算，与片段次数分离。任一预算耗尽时，节点停留在 `technical_planning` 并返回 `technical_plan_generation_error`，仅包含精简错误与重新生成操作，不写入 TechnicalPlan JSON，也不发出确认产物。失败后的重新生成仍标记为 `repair`：当前 TechnicalPlan 缺失或为空且事务未声明基线摘要时，技术入口与统一兜底执行器允许重入，由节点使用内部候选和错误续修，候选缺失时重新生成；已有计划或已声明摘要必须严格匹配，`revise` 始终要求有效基线。修复或重新生成通过校验后仍必须等待用户确认。规划模型的 `llm.token` 原始 JSON 只用于内部生成过程，前端聊天和历史消息均不得展示；确认卡和右侧文档通过唯一 Workflow `state.artifactRefs` 按需读取结构化计划。服务端在正文外置前投影 `state.technicalPlanConfirmed`，前端只有在当前技术阶段 completed、该值为 true、lifecycle 已进入模板阶段且正式 TechnicalPlan JSON 的确认回执完整时才能触发模板交接；正文引用本身与旧确认回执都不代表新版本已确认。

TechnicalPlan 的实体定义严格校验继续暂停；页面行为闭合校验暂时暂停；API Contract 的 `entity_ids`、可选 `entity_field_ref`、Schema 引用和统一分页校验启用。分页响应对象同级只能有 `total`、`pageSize`、`current`、`list` 四个字段。页面路由和 Endpoint 结构校验保持不变。

任何完整 TSX、数据库工具原始输出、仓库扫描结果和历史日志都写入文件，只向主上下文返回路径、哈希和有界摘要。
