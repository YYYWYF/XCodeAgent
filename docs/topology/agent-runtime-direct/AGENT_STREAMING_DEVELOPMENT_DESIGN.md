# Agent Runtime 流式接口与开发流程详细设计

日期：2026-10-29。状态：待评审的目标设计，尚未实施。本文中的新增字段、版本、任务职责和 Runtime 能力均为提案，不表示当前模板或代码已经支持。

## 1. 要解决的问题与结论

当前开发链以实体、请求/响应 Schema 和 Endpoint 字段映射为主要输入。业务 Agent 已经拥有独立七模块设计、开发入口和 `/agents/{agentId}/run` 运行入口，但该入口尚未作为统一接口设计中的完整能力被描述。页面直接关联 Agent，与页面通过 API Contract 关联业务接口并存，造成设计、生成、门禁、失效和验收使用不同事实。

目标是让以下链路闭合：

> 产品业务目标 → 智能体能力设计 → 正式智能体运行接口 → 页面交互接入 → 已确认 DAG → 实现与装配 → 真实业务验收 → 修改后的定向更新。

推荐方案：接口工作台增加“智能体”来源；技术规划提前声明智能体运行接口；智能体提供明确的输入、输出和交互能力合同；接口设计绑定该合同；页面通过接口消费消息、状态、待交互和业务结果。协议、会话隔离和运行入口由平台模板提供。

核心决策：

1. 智能体运行接口是一种正式 Endpoint，不要求绑定业务实体，也不沿用普通响应叶子字段映射。
2. 保留 `/agents/{agentId}/run`，将现有入口纳入接口目录；不再生成一次 HTTP 转发或第二份 Agent 执行逻辑。
3. Agent 能力定义继续保存在 `TechnicalPlan.agent_contracts`，不增加另一份可独立修改的 Agent 正式文件。
4. 页面在技术实现上引用 Endpoint；产品阶段的 Agent 页面入口继续表达产品意图，不作为第二套技术调用绑定。
5. 设计阶段消费已确认契约，不要求前置代码已完成；真实开发和联调按 DAG 的实现依赖执行。
6. 生命周期、路由、输入校验和结果投射采用平台实现；自然语言不能替代结构化输入、业务成功和写入授权判断。
7. 新产品交互全程使用 AG-UI 和官方客户端；保留既有基础设施及既有业务接口合同，不在本任务中统一重写普通业务接口传输。

## 2. 当前事实与证据边界

| 已读取的当前入口 | 当前事实 | 设计影响 |
| --- | --- | --- |
| `Backend/app/services/runtime_agent_contracts.py` | Agent 合同严格限定身份、非空 pageIds 和七模块；Tools 精确引用 Endpoint | 增加能力合同、移除技术侧 pageIds 权威；更新全部严格字段校验 |
| `Backend/app/services/runtime_agent_settings.py` | Agent 集合及身份与已确认 ProductPlan 对齐；设置修订只允许 Prompt/Temperature | 保留产品身份权威；公开合同修订不能混入局部设置入口 |
| `Backend/app/domain/api_design.py` | v7 来源为数据库、外部 API、静态数据；正式设计以字段映射为主体 | 引入判别联合，避免给流式接口伪造 response_body 字段 |
| `Backend/app/services/api_contracts.py` | 规范化采用字段白名单，页面依赖部分按实体或 HTTP Method 处理 | 新字段必须贯通规范化；智能体动作不能按 POST 推断成 create |
| `Backend/app/services/page_implementation_contract.py` | 页面行为由产品、UI、技术绑定确定性编译 | 扩展运行时消费投影，不新增第二份可编辑页面详设 |
| `Backend/app/agents/frontend/runtime_instruction.py` | 页面按 Agent.pageIds 直接获取固定运行路径；已有交互续接与业务结果约束 | 替换技术调用事实来源，不能让旧直连路径继续独立生效 |
| `Backend/app/services/build_context_resolver.py` | 页面和独立 Agent 纳入 Tool；单 Endpoint 尚无 Agent 来源依赖 | 三种开发目标必须使用同一依赖闭包 |
| `Backend/app/services/runtime_agent_dag.py` | Tool Endpoint → Agent → Page；七模块复用原 Unit/DAG | 新运行 Endpoint 不套用 CRUD 五阶段，不复制调度器 |
| `Backend/app/services/runtime_agent_module_policy.py` | 模块事实包含页面及全 Agent 集合的部分信息 | 拆分业务模块事实和共享注册事实，控制失效粒度 |
| `Backend/app/services/endpoint_design_detail.py` | 保存复核 TechnicalPlan 哈希和 baseRevision | 保留并发保护，并增加 Agent 能力摘要复核 |
| `Backend/app/services/execution_resource_scope.py` | 页面、接口、Agent 和共享 Runtime 工厂存在资源锁 | 新依赖闭包必须贯通锁与目标状态 |
| `Backend/app/services/runtime_business_acceptance.py` 及 `business_acceptance_verifiers/python_agent*.py` | 当前静态验收依赖确定的工厂、模板和调用形状 | 新装配必须同步验收，不能只修改生成提示词 |

本文没有扫描整个项目，没有读取外部模板源码，也没有执行测试、构建、health 或语法检查。已有文档中各批次“待完成”描述可能滞后；能力结论以当前生产者/消费者及后续单次实际验证为准，不从索引摘要推断运行通过。

## 3. 范围与首版限制

本次改造覆盖显式 `agent_runtime_direct` 应用中的 Agent 运行接口及其设计、门禁、DAG、页面接入和验收。Java 应用不增加 Agent 实现；普通接口映射保持原有业务语义。

首版明确限制：

- 一个 Agent 对应一个标准运行 Endpoint，一个 Endpoint 绑定一个 Agent；多个页面复用该 Endpoint。
- 不支持 Agent-as-Tool、多个 Agent 路由或 Agent 编排；Agent Tool 只引用非 Agent 运行接口背后的本地业务服务。
- 不开放自定义运行 URL、任意请求/事件脚本、用户自定义 SSE 或任意输出转换代码。
- 首版允许文本能力和结构化结果；结构化结果必须有明确生产者和运行时校验。写操作只有通过写入控制与动态验收才开放。
- 当前 Runtime 的匿名会话边界保持不变；`auth.enable`、`authorization.enabled` 的权威仍为 application.json。未支持的登录/权限组合继续明确拒绝，不因本设计放行。
- 不支持历史合同迁移、旧字段别名、双写、旧检查点恢复或协议协商回退。当前生产者和消费者作为一个批次切换。
- 不增加可独立修改的 AgentContract、页面详设或运行接口路由配置文件；生成描述符是派生产物。

无页面的可复用 Agent：技术合同不再强制拥有页面，但是否允许用户创建该产品能力仍由 ProductPlan 决定。首版不自动扩大产品 Agent 集合；若当前产品协议只支持页面入口，则保留此产品约束，并明确“技术实现不依赖页面”不等于“本期新增无页面产品入口”。

## 4. 分层、权威与设计顺序

| 层次 | 唯一权威 | 可以编辑的事实 | 不能在此编辑 |
| --- | --- | --- | --- |
| 产品 | RequirementSpec、ProductPlan | Agent 身份与职责、产品入口、业务目标、用户确认要求 | 路由、凭据、技术协议细节 |
| 视觉与交互 | 已确认 UiDesign/UiManifest | 控件、布局、产品已允许的交互展示 | 猜测业务成功条件、改变后端能力 |
| 技术基础 | TechnicalPlan.entities | 业务数据结构和可选实体关联 | 自动创建聊天记录或助手实体以凑接口绑定 |
| 智能体能力 | TechnicalPlan.agent_contracts | 七模块、能力输入、公开状态、结果、待交互定义 | 页面的控件路径、第二份产品职责 |
| 接口声明 | TechnicalPlan.api_contracts | 稳定 Endpoint、kind、运行路径、Agent 引用 | 另一份独立的 Agent 输入/结果定义 |
| 接口详细设计 | Endpoint Markdown/内部 JSON | 业务输入绑定、会话使用策略、已确认合同快照 | 改 Agent、改公开 Schema、改应用配置 |
| 页面接入 | TechnicalPlan.pages.references，加运行时编译 | 动作到 Endpoint、输入来源、展示消费和成功后动作 | 第二份 Agent 配置、直接调用内部 Tool 模拟 Agent |
| 平台运行 | Runtime 模板及确定性编译器 | AG-UI 生命周期、会话/执行控制、校验与路由装配 | 模型自报身份、模型自报写入授权 |

工作台组织顺序可保持“实体 / 智能体 → 接口 → 页面接入”。这不改变现有 RequirementSpec/ProductPlan 联合确认、UI 确认、TechnicalPlan 确认顺序。前期视觉设计照常进行，接口之后完善的是页面技术接入。

TechnicalPlan 可以在一个候选事务中共同设计业务 Endpoint、Agent 和运行 Endpoint。草稿内相互引用在确认前统一验证；不要求先把所有对象分别确认为独立产物。确认后的接口详细设计消费该正式版本。

## 5. 技术合同

以下为拟采用的当前合同字段；实施时必须同步正式 Pydantic、模型提示、规范化、Markdown 同步、类型与验收，禁止仅在 dict 中透传。

### 5.1 Agent 能力定义

Agent 增加 `interactionContract`，移除技术权威字段 `pageIds`。核心形状如下：

```json
{
  "agentId": "order_assistant",
  "name": "订单助手",
  "purpose": "分析订单异常，并按确认结果创建工单",
  "agentSettings": {"prompt": {}, "model": {}, "memory": {}, "tools": {}, "skills": {}, "knowledge": {}, "context": {}},
  "interactionContract": {
    "inputSchema": {
      "type": "object",
      "properties": {"orderId": {"type": "string"}},
      "required": ["orderId"],
      "additionalProperties": false
    },
    "textOutput": true,
    "publicStateSchema": {
      "type": "object",
      "properties": {"phase": {"type": "string"}},
      "additionalProperties": false
    },
    "outcomes": [
      {"code": "analysis_completed", "effect": "read", "resultSchema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": false}},
      {"code": "ticket_created", "effect": "write", "resultSchema": {"type": "object", "properties": {"ticketId": {"type": "string"}}, "required": ["ticketId"], "additionalProperties": false}}
    ],
    "interactionKinds": ["clarification", "confirmation"]
  }
}
```

示例为章节缩写，七模块空对象不是合法可执行配置。能力公开合同是唯一可编辑定义，其他位置只保存引用和平台生成的只读快照。

约束：

- `inputSchema` 只描述业务上下文。用户对话通过 AG-UI messages 传入，不再同时定义一个重复的 `question` 业务参数。
- 输入、公开状态和结果仅支持当前已实现的有限 JSON Schema 子集：对象、数组、基本类型、required、enum、对象内局部引用；禁止远程引用和任意表达式。子集及大小/深度上限统一由平台定义，超出能力明确报错。
- 仅文本 Agent 允许 `outcomes=[]`，界面只能表达回答结束，不能据此触发写入成功行为。
- 结果 code 在当前 Agent 内唯一、稳定；结果结构不要求引用实体。实体引用存在时必须指向真实字段。
- `publicStateSchema` 是对外投影，不是内部 LangGraph state；checkpoint、完整工具参数、模型配置和凭据不进入公开状态。
- `effect=write` 不能由模型一句“已创建”满足，必须有真实业务服务提交证据。
- 技术侧页面使用者从正式 Page → Endpoint → Agent 关系反向推导。工作台可展示只读 `consumerPageIds`，不回写 Agent 核心定义。

### 5.2 Endpoint 声明

为所有当前 Endpoint 显式增加 `kind`，取值 `business` 或 `agent_runtime`；不通过 method、路径或是否存在 Agent 猜测类型。

智能体运行 Endpoint 示例：

```json
{
  "id": "order_assistant.run",
  "name": "运行订单助手",
  "kind": "agent_runtime",
  "method": "POST",
  "path": "/agents/order_assistant/run",
  "agent_id": "order_assistant",
  "summary": "在订单上下文中持续分析并处理用户请求"
}
```

规则：

- `agent_id` 是接口指向 Agent 的权威；路径由平台从该引用确定性生成并校验唯一性。
- 运行 Endpoint 的业务输入、状态和结果从 Agent 能力合同编译；不维护第二份可编辑 request/response Schema，不引用普通 response_schema_ref。
- AG-UI 协议请求形状由模板统一提供；业务输入由公开能力合同校验，二者不能混成一个“聊天响应 DTO”。
- Contract.entity_ids 允许空数组；如存在引用，必须有效。首版智能体 Contract 仅包含该 Agent 的标准运行 Endpoint，不与 business Endpoint 混组。
- 普通 business Endpoint 保留当前 Schema 和映射逻辑；计算型接口也无需为了非空实体要求创建虚假实体。
- 每个正式 Agent 必须有且只有一个运行 Endpoint；运行 Endpoint 引用必须覆盖且仅覆盖当前正式 Agent。
- 所有技术引用使用 `apiContractId + endpointId` 复合身份。前端不能只凭 endpointId 查找后取第一项；页面、Tool、DAG、冻结目录和验收使用同一键。

### 5.3 Endpoint 详细设计与版本切换

统一正式封装拟切换为 `schemaVersion=endpoint-design.v8`、`artifactType=endpoint-design`。物理 Endpoint JSON/Markdown 路径保持既有稳定路径；不增加历史格式读取或迁移。

共享字段：`artifactRevision`、`apiContractId`、`endpointId`、`implementationKind`、确认状态、直接依赖摘要和确认时间。

- `implementationKind=business`：使用现有来源、查询、写入和映射语义。
- `implementationKind=agent_runtime`：仅允许 `sourceBinding={sourceType: agent, agentId}`、`inputBindings`、`sessionPolicy` 和平台生成的公开合同快照。
- 两种实现用严格判别联合表示。Agent 分支禁止 databaseWrites/databaseQuery/externalApiBindings/fieldMappings；业务分支禁止 Agent 运行配置。
- `sourceBinding.agentId` 必须与当前 Endpoint.agent_id 一致，不能作为第二个可自由修改的引用。
- 快照记录 Agent 能力摘要及当前 Endpoint 声明摘要，供冻结和验收使用；不是独立权威。保存、DAG 确认和执行前重新计算。
- ordinary v7 的所有当前生产者、读取者、测试 fixture、菜单状态和冻结消费者一起更新。旧文件不迁移，遇到非当前格式提示重新设计；不能保留 v7 专用门禁而只让新面板认识 v8。

### 5.4 输入绑定

首版输入来源限定为：

| 来源 | 用途 | 校验 |
| --- | --- | --- |
| `client_input` | 页面显式提交的业务输入，如 orderId | 页面引用和接口目标类型一致，Runtime 视为不可信参数 |
| `constant` | 已确认且非敏感的固定业务值 | 符合目标 Schema；禁止保存凭据 |
| `server_context` | 平台允许的可信上下文项 | 由服务端注入，客户端不得覆盖；不开放任意 Principal/namespace 字段 |

每个必填能力输入必须有且只有一个来源。输入绑定只做确定性取值，不执行自然语言转换，不把模型文本解析为 orderId。页面可给接口的公开 client_input 字段赋值，不能提供 sourceType、Agent 身份或服务端上下文来改变装配。

首版不同页面对同一 Agent 的输入模式保持一致；同一会话的业务上下文在首次调用时冻结。切换订单等上下文必须新建会话，不能在等待确认期间静默修改 orderId。

## 6. 设计工作台与确认事务

### 6.1 用户旅程

1. 技术规划根据已确认产品事实，生成或修订 Agent 能力、运行 Endpoint 和页面 action 引用。
2. 用户确认 TechnicalPlan；Markdown 编辑先同步回内部 JSON，再执行整体一致性校验。
3. 打开接口设计，“数据来源”为智能体时，展示 Agent、输入绑定、会话策略及只读交互/结果合同，不展示数据库映射表。
4. 用户确认当前 Endpoint 详细设计；草稿保存不代表设计就绪，也不触发开发。
5. 页面接入展示消息区、状态区、待交互区、结果和后续动作的绑定；编译为 PageImplementationContract。
6. 进入现有开发门禁，检查目标完整依赖闭包，生成 Pending DAG；用户确认精确 Pending 身份后执行。

### 6.2 来源切换不能绕过技术规划

“增加智能体来源并选择 Agent”的入口保留，但有两种确定行为：

- 当前 Endpoint 已为 agent_runtime 且 Agent 引用一致：直接配置详细设计。
- business 切到 agent_runtime，或更换 Agent：展示“将修改技术规划”的具体影响，创建现有 Formal Revision 候选；确认技术规划后再继续接口配置。

更换来源不是单个草稿内清空 response 字段后直接保存。它可能使 ProductPlan action 接入、原 Schema、页面消费和 DAG 失效，必须经过原正式边界。确认前保留原正式设计，取消则放弃当前候选。

公开能力修改沿 TechnicalPlan Formal Revision；现有 Prompt/Temperature 设置入口继续只改允许字段。不得借 Markdown 同步或配置补丁修改 outcomes/inputSchema。

### 6.3 并发与落盘

- 复用应用修订互斥、Endpoint 保存锁和 baseRevision；保存额外核对 Agent 能力摘要和 Endpoint 摘要。
- TechnicalPlan 全文件哈希用于编辑并发保护；它变化时要求重新加载，不自动合并别人的修订。
- 设计有效性和任务复用使用直接语义摘要，不把无关说明文字或确认时间作为所有下游任务依赖。
- Markdown/JSON 成对保存，任一失败回滚成对产物；同版本验证失败时不标记 confirmed。
- 索引和侧栏投影从正式状态重建，不以聊天卡片、旧菜单或前端局部状态决定确认权威。

## 7. Runtime 运行合同

### 7.1 接入与协议归属

唯一公开执行入口保持 `POST /agents/{agentId}/run`，前端以 `@ag-ui/client` 和 `@ag-ui/core` 调用。Studio 的设计动作继续走 `/endpoint-designs/run`；二者属于不同服务和会话，不能混用 Studio 工作流 runId 或调试凭据。

Agent 运行的 AG-UI messages、threadId、runId 使用标准协议。拟增加的业务字段放入标准 forwardedProps：

- `agentInput`：当前 Endpoint 允许的 client_input 字段。
- `interactionResponse`：保留当前交互入口，提交精确 interactionId 和用户值。
- `runtimeControl`：拟由同一入口承载只读状态/取消控制；是有限判别动作，不新建 JSON REST 产品端点。

初次业务输入和消息由 Runtime Pydantic 边界校验。输入绑定、固定值及 server_context 在服务端编译，客户端不能提交完整配置覆盖它们。未支持字段和冲突动作明确拒绝。

新增业务输入、状态查询和取消能力必须先在真实模板核实并实现；本设计不假定当前模板已经消费这些 proposed 字段。

### 7.2 身份与状态分离

| 身份或状态 | 含义 |
| --- | --- |
| 可信匿名身份 + 服务端 namespace | 数据、会话与检查点归属；来自服务端 Cookie/Profile |
| `threadId` | 一次持续对话；客户端可选择 ID，但必须由服务端核验归属 |
| AG-UI `runId` | 一次协议调用；澄清回答或确认续接使用新的 runId |
| `executionId` | 一次逻辑业务执行；等待交互和继续执行保持同一个 ID |
| `interactionId` | 当前待处理交互的单次身份 |
| `operationId` | 一次实际业务写入身份，与重连或新 runId 分离 |
| 连接状态 | connected/disconnected；不等同业务执行状态 |

逻辑执行状态为 `running / waiting_input / succeeded / failed / cancelled / unknown`。`idle` 表示当前没有活跃逻辑执行，不作为已成功结果。一个 thread 同时最多一个活跃 execution；只读状态控制不占用新业务执行，交互续接只能消费该 execution 当前交互。

`unknown` 表示服务端尚不能确认实际执行/提交状态，不是可自动重试的失败。查询当前证据后才能显示确定状态；仍不可确定时要求人工处理。

### 7.3 事件和结果

标准消息、工具事件、快照和 delta 继续交给官方 SDK。平台复用当前 Agent runtime 业务事件概念，拟整体升级业务载荷为 v2：`agent_runtime_result.v2`、`agent_runtime_error.v2`，状态键仍为 `agentRuntime`，载荷显式 `schemaVersion=2`。当前端、模板、调试器和验收同步切换，不识别旧事件别名。

v2 公开状态至少包括：`agentId`、Endpoint 复合身份、`threadId`、`executionId`、当前逻辑状态、`stateRevision`、公开业务 state、当前 pendingInteraction 和已验证 outcomes。协议 runId 记录本次调用，不代替 executionId。

- 公开状态更新由模板统一编译；结果事件和快照使用同一服务端事实，不能各自维护结果。
- stateRevision 在同一 execution 内单调递增；晚到回执、不同 execution 的旧回执不能覆盖当前状态。
- 初始状态先通过完整快照建立；只有基线已知时应用官方 delta。无法建立基线时重新读取当前快照，不自行猜测合并。
- 待交互载荷保留当前 schemaVersion/interactionId/kind/message/payload 概念，并绑定 executionId、能力摘要和操作参数摘要。确认严格使用 true/false，澄清使用真实答案。
- CUSTOM 是标准 AG-UI 扩展事件，不建立第二套事件流。处理预期业务载荷允许严格校验，但不得手写 SSE 或标准帧解析。
- 可处理业务失败按现有动作流规范交付完整生命周期和明确失败结果；意外传输/执行异常交给标准错误机制，不能伪造业务成功。

### 7.4 业务结果的可信来源

| 输出 | 生产方式 | 页面可否据此触发业务成功动作 |
| --- | --- | --- |
| 文本增量 | 模型/图产生的标准消息 | 否，只供展示 |
| 只读结构化分析 | 受约束结构化生成或确定性节点，Runtime 校验 Schema | 可触发对应只读结果展示，不能证明外部写入 |
| 写入结果 | 已确认参数对应的 ApplicationService 成功提交结果，由受信包装层生成 | 可以，且必须有 operationId 和真实提交证据 |

不解析“工单创建成功”字样作为写入结果，不把模型自填 ticketId 当作已提交。拒绝、不执行或回答结束不生成 ticket_created。

结构化输出校验失败交付明确结果契约错误；保留已发生操作证据，不自动重新运行模型或写操作。后续是否重试由用户决定。

### 7.5 等待、续接、断线与取消

| 触发 | 确定行为 |
| --- | --- |
| 请求澄清/确认 | 保存当前 pendingInteraction，逻辑状态 waiting_input；本次协议流可正常结束 |
| 用户回答 | 服务端核验身份、thread、execution、interaction、能力摘要及参数摘要，原子消费当前交互后继续 |
| 双击确认/跨标签重复提交 | 同一 interaction 只允许一次消费；重复响应返回当前事实，不重复执行业务写入 |
| 旧确认或参数已变化 | 明确拒绝，不能自动重新生成交互并当成已确认 |
| 网络断开/页面卸载 | 停止本地订阅；不据此宣称服务端已取消或回滚，不自动重发原消息 |
| 用户返回页面 | 通过同入口的 AG-UI 只读状态动作读取当前事实，不启动模型或恢复执行 |
| 显式取消 | 按 executionId 提交，模板请求取消并返回已确认状态；等待确认可作废，已提交写入不回滚 |
| 取消发生在写入提交附近 | 以服务端操作证据为准，必要时保持 unknown；不能仅因 UI 显示取消就隐藏已创建的工单 |
| Cookie 失效/Profile 重启 | 重新建立匿名会话，不把旧 thread 自动归属给新身份，不自动恢复有副作用执行 |
| 正式合同改变 | 活跃执行保持原摘要身份；首版不运行旧合同副本，不能续接不匹配执行。先终止/处理当前执行，再由用户显式开始新会话 |

不提供历史协议迁移或断点事件自动重放。首版恢复的是当前权威状态，不保证重现断线期间每一条文本增量；完整历史消息能力不在本次新增范围。

### 7.6 写操作控制与幂等边界

当前“interrupt → 严格确认 → 服务调用”的静态控制流约束继续保留，新状态机制需要同步更新 verifier，不能通过放宽验证绕过。

写路径至少满足：

1. 执行前形成稳定 operationId、具体工具身份、确认参数和参数摘要。
2. 用户明确确认具体参数；服务端再次检查当前身份、资源归属和权限，不信任消息或客户端提供的 Principal。
3. 待交互消费和执行归属受并发控制；批准不能被另一次执行复用。
4. 业务服务按 operationId 提供重复提交抑制，记录提交结果；本地数据库操作的幂等记录和业务写入应在同一事务完成。
5. 外部写入没有幂等键或查询证据时，不宣称 exactly-once；发生不确定提交返回 unknown，禁止自动重试。
6. 图节点重入、服务重启及客户端重试不能依赖进程内 set 去重。持久操作记录属于当前运行状态基础设施，不等于历史格式兼容功能。

以上依赖若真实模板/业务服务尚不具备，则写能力不开放；只读闭环可以先交付，但必须明确标记为只读交付，不能报告本设计全部完成。

## 8. 页面接入合同

沿用现有 ProductPlan action、UiManifest 控件和 TechnicalPlan action_implementations。`actionBindings.type=endpoint` 可以继续使用，增加由 Endpoint.kind 确定的运行消费投影；不新增一个绕过 Endpoint 的 agent 动作分支。

PageImplementationContract 增加只读编译区 `runtimeBindings`，每项关联稳定 actionId、Endpoint 复合身份、Agent 引用和：

- `inputBindings`：页面字段到公开 client_input 字段；禁止跨类型或缺少必填输入。
- `messageBindings`：标准助手消息的展示区域。
- `stateBindings`：公开 state 字段到已有页面控件。
- `interactionBindings`：澄清、确认、拒绝入口。
- `outcomeBindings`：明确 outcome code 和 result 字段到结果区域或后续动作。
- `sessionBinding`：页面复用同一对话的可见身份；切换业务上下文新建会话。

完整性规则：

1. 公开输入、状态和结果路径必须存在；必填结构无法绑定时不能靠自然语言说明放行。
2. 支持 confirmation 的能力必须有可见确认与拒绝入口；支持 clarification 必须有回答入口。未提供专属样式时可以使用模板通用交互组件，但不得自动回答。
3. `RUN_FINISHED`、文本出现特定词语、HTTP 200 或连接关闭都不能成为业务成功触发条件。
4. 原 sequence 的后续步骤需声明等待哪个业务 outcome；例如 ticket_created 才刷新列表。单纯分析完成不能执行创建成功后的步骤。
5. 页面处理状态、交互和结果事件重复投递；副作用型 UI 动作以 executionId + outcome code + operationId（如有）及结果身份去重。
6. 同一 run 可能包含多种业务结果；序列推进只消费当前动作等待的结果，不把任意 outcome 当作整条业务流程成功。
7. 成功后页面再次发起写请求属于新的产品动作，必须已有产品定义和正式绑定，不能隐式重复 Agent 已完成的写入。
8. 明暗主题覆盖输入、流式消息、等待、确认、拒绝、失败、断线、unknown 和空态；沿用紫色主题 token。

现有 ProductPlan 中 Agent.pageActionBindings 继续用来证明产品入口；技术编译器校验该入口最终到达同一个 Agent，不能把产品意图和实际运行目标分离。

## 9. 门禁、DAG 与执行范围

### 9.1 统一依赖闭包

平台建立一个有界、确定性的目标依赖解析结果，供 readiness、BuildContext、锁、冻结目录、DAG、执行切片和失效消费。禁止每个模块分别猜测 Agent 依赖。

```text
页面 → 直接调用 Endpoint
智能体运行 Endpoint → 目标 Agent + 自身详细设计
Agent → Tools 引用的业务 Endpoint + 当前技能/知识资源
业务 Endpoint → 自身来源与正式设计
```

箭头表示“依赖”。Tool 若引用 agent_runtime Endpoint，首版直接拒绝，并提供引用链。其他闭环、悬空引用、重复身份也在生成任务前拒绝。

- 单页面：只纳入该页面直接调用接口及其传递依赖，不因 Tool 引用扩大前端公开接口列表。
- 单运行接口：纳入目标 Agent、必要 Tool、Runtime 基础能力及运行接口装配，不扩展到全部页面。
- 单 Agent：纳入自身七模块、必要 Tool 和其标准运行入口的装配验收，不拉入消费页面。
- 普通业务接口：保持已有目标范围，不反向拉入使用它的所有 Agent。

锁集合与“需重建对象”不同：修改共享工厂可能需要共享锁，但不代表必须重建全部 Agent。反向消费者用于提示和失效，不一律成为开发任务。

### 9.2 门禁

现有 api_design_readiness_gate 按 kind 分派：

- business：检查当前来源与完整映射。
- agent_runtime：检查当前正式 Agent、能力合同、输入绑定、会话策略、模板支持和直接摘要；不要求 fieldMappings 或数据库 CRUD。
- 页面：在上述基础上检查页面运行绑定闭合。

门禁同时投影所有目标相关接口的 pending/confirmed/stale 及原因，保留用户显式确认检测行为。保存设计不推进开发；设计 ready 不等于代码已实现。

### 9.3 Unit 和任务归属

保持现有 `agent:<agentId>` 和业务 `backend:endpoint:<contractId>:<endpointId>` Unit 身份。运行 Endpoint 复用 Endpoint Unit 身份，但职责按 kind 分派：

| 对象 | 职责 |
| --- | --- |
| business Endpoint | 原业务实现职责，不新增 Agent 分支代码 |
| Agent Unit | 七模块及能力输入/结构化输出在真实图中的接线；上下文/工具等受影响模块消费能力摘要 |
| agent_runtime Endpoint | 一项确定性的 `runtime_binding` 职责：编译并校验路由、输入绑定、公开投影及目标注册关系 |
| 页面 Unit | 按确认的页面消费合同实现 UI 和交互 |

`runtime_binding` 是需正式登记的平台注册能力，经原 PendingPlan 来源、确认、Diff 和执行器机制处理；不由模型伪造平台资格，不套用 repository/application_service/controller 五阶段。固定任务身份拟为 `backend:endpoint:<contractId>:<endpointId>::runtime_binding`。

模板统一提供执行路由与校验器；确定性任务生成/更新专用描述符，拟定路径 `agent-runtime/src/app/server/agent_surfaces.py`。该路径是新增的模板平台所有权路径，必须显式登记，不能通过扩大业务 Agent allowed_paths 写 server 基础设施。共享描述符按当前已确认合同和已实现 Agent 事实合并，不覆盖其他有效条目。

实现依赖为 Tool 业务任务 → Agent 所需模块 → runtime_binding。页面源代码可按已确认合同并行生成，保留当前前后端并行策略；页面真实联调/验收必须等待 Runtime 相关任务成功。

使用同一 Frozen Store、Local/Global Validation、PendingPlan、精确 DraftIdentity、Build 和 Recovery。共享锁串行化工厂/描述符写入；不新建第二套调度器或自动确认流程。

## 10. 模板与 Studio 的交付边界

模板必须同时具备：读取运行描述符、校验业务输入、可信上下文绑定、公开状态投影、结构化结果校验、当前状态读取、交互原子续接、取消控制、写入证据和同源代理。

Studio 负责：产物模型、工作台、正式修订、依赖闭包、规划/冻结/确认、确定性描述符任务、生成提示和验收编译。

外部模板不是本仓库可静默替代的实现。实施前只读核实其明确入口和能力；缺少必须能力时列出精确模板补丁范围。若不能在当前授权工作区完成模板修改，则保持对应功能阻断，交付 Studio 改动时明确“模板未配套，功能未完成”。不得添加 fallback 路由、loopback 转发或把静态合同文件当作运行能力。

模板能力通过当前 TemplateState/显式能力元数据声明；只接受当前目标合同，不增加旧版本探测与兼容分支。当前能力声明还需真实模板检查和执行证据支持。

## 11. 增量失效、运行修订与完成状态

| 变化 | 需要失效的直接消费者 | 不应自动发生 |
| --- | --- | --- |
| Prompt/Temperature 变化，公开能力合同不变 | 相关 Agent 模块、行为验收及运行实例缓存 | 所有页面重新做输入/结果绑定 |
| Agent 输入/公开状态/结果/交互合同变化 | 运行 Endpoint 设计、相关页面绑定、受影响模块、Pending DAG 和相关验收 | 自动确认新接口或页面 |
| Tool 业务合同/确认参数变化 | Agent tools 及相关能力验收、相关任务 | 所有无关 Agent 模块重建 |
| 页面视觉变化 | 该页面实现与 UI 验收 | Agent 核心定义变化 |
| 添加一个 Agent | 新 Agent 及共享工厂/运行描述符 | 把全部 Agent 的 Prompt 标记失效 |
| 运行入口 kind/Agent 引用改变 | 当前接口、消费页面和依赖切片 | 直接修改正式接口来源跳过技术修订 |

共享工厂集合摘要单独记录在平台注册职责中，业务模块只消费直接事实。编辑并发全文件哈希与语义失效摘要分别使用；发现已被其他事务更新时不自动覆盖。

设计状态、实现状态和验收证据分别投影：

- 设计：pending / confirmed / stale。
- 实现：沿用当前开发生命周期，完成必须与当前直接输入匹配。
- 验收：分别记录结构合同、工程可运行、受控业务行为及真实模型会话证据，不合并为一个“已通过”。

活跃业务执行持有能力摘要；公开合同修订前需确认并处理活跃执行。首版拒绝不匹配的旧交互续接，不加载旧合同实现。工具已提交的业务结果不因正式设计被修改而抹除。

## 12. 风险登记与处理

优先级：P0 为上线或完整交付阻断；P1 为对应场景开放前必须完成；P2 为可在明确限制下后续完善。以下是设计风险，不等于全部已经发生的缺陷。

| ID | 优先级 | 触发条件与后果 | 处理与阻断点 | 验收证据 |
| --- | --- | --- | --- | --- |
| R01 | P0 | 技术规划仍生成普通响应，详细设计却选择 Agent，合同前后冲突 | 技术规划先分 kind；跨类型/换 Agent 走 Formal Revision | 来源切换不直接写正式 Endpoint；取消保留原版本 |
| R02 | P0 | 原页面直连与新接口引用并存，调用错误 Agent 或重复执行 | 统一 Page → Endpoint → Agent；移除技术直连生成路径 | 页面仅有一个正式运行调用入口 |
| R03 | P0 | 规范化白名单、Markdown 同步或 TS 投影丢弃新增字段 | 更新全部生产消费链；缺字段严格失败 | 从生成、编辑、确认到冻结往返保留 kind/能力/引用 |
| R04 | P0 | Runtime 模板未同步，Studio 显示 ready 但实际不识别输入/交互 | 模板能力与真实源码/运行检查共同门禁 | 从运行入口收到实际输入与可继续交互 |
| R05 | P0 | 运行接口被当 Tool，或出现间接环，规划死锁/运行递归 | 禁止 Agent Endpoint 作为 Tool；依赖闭包报告完整引用链 | 自引用/间接引用在任务生成前拒绝 |
| R06 | P0 | 线程、匿名身份或 namespace 混用，串会话/越权 | 服务端核验归属；不信任客户端 Principal；保持 Profile 隔离 | A 身份不能读/继续 B 的执行 |
| R07 | P0 | 双击确认、图节点重入、重连导致重复写入 | 原子交互消费、稳定 operationId、业务幂等与提交证据 | 同一批准重复提交仅产生一次可证明写入 |
| R08 | P0 | 参数变化后仍使用旧确认，批准 A 实际执行 B | 绑定 execution、interaction、参数摘要和当前权限 | 改参/过期/跨 execution 确认明确拒绝 |
| R09 | P0 | 模型文本/伪造 ID 被当业务成功，页面刷新或提示错误 | 写结果只由可信提交链生产；运行时校验结果 | 没有真实提交不能得到 write outcome |
| R10 | P0 | 单接口或单 Agent 开发漏掉依赖，只有全量开发能跑 | 同一依赖闭包贯通门禁、冻结、切片、锁和验收 | 三种目标包含必要依赖且不扩成 application |
| R11 | P0 | 新运行接口套用 CRUD 阶段，生成重复 controller/HTTP 转发 | 确定性 runtime_binding 及专属 verifier | 路由唯一、真实装配、无 loopback 转发 |
| R12 | P0 | v7/v8 或 runtime 载荷混读，局部切换后静默误判 | 当前生产消费者同批切换，旧格式明确拒绝 | 旧文件不放行；无兼容别名或双写 |
| R13 | P1 | 将 RUN_FINISHED/HTTP 200 当成功，等待确认时关闭页面 | 协议/逻辑/业务结果分层；sequence 指定 outcome | waiting_input 不触发成功动作 |
| R14 | P1 | disconnect 被标 cancelled，写入已提交但 UI 提示取消 | 连接/执行分离；状态查询；取消以真实提交证据为准 | 提交附近断线/取消显示已提交或 unknown |
| R15 | P1 | 重复/乱序/旧回执覆盖新结果，UI 多次执行后续动作 | executionId、stateRevision、outcome 身份去重 | 旧结果不覆盖当前线程；成功动作只执行一次 |
| R16 | P1 | 同线程并发提问、跨页改上下文导致执行串扰 | 单活跃执行；输入冻结；上下文变化新会话 | 等待确认时更换订单不会改变待执行参数 |
| R17 | P1 | 内部图 state、工具参数或模型配置被整包公开 | schema 白名单投影、脱敏、错误分类 | 事件、快照和日志不含未授权内部字段/凭据 |
| R18 | P1 | 结构化结果失败后自动重跑已提交写操作 | 结果校验与操作证据分离；失败停止、不重放 | 输出不合规仍保留提交事实，零自动重写 |
| R19 | P1 | 页面缺确认/澄清 UI，后台永久等待或被自动确认 | 页面消费合同完整性检查及模板通用交互组件 | 无交互入口禁止标记页面接入完成 |
| R20 | P1 | 正式修订覆盖待执行合同，旧批准续接新逻辑 | 活跃执行合同摘要校验；先处理活跃执行再改合同 | 不匹配续接明确拒绝，不读取旧合同兜底 |
| R21 | P1 | 并行 Agent 开发覆盖工厂/运行描述符或丢条目 | 共享锁、精确平台路径、受控合并和原子写入 | 两个 Agent 开发后注册条目均保留 |
| R22 | P1 | Agent 与 Endpoint 双份输入/结果 Schema 各自变化 | Agent 能力单一权威，Endpoint/页面仅引用与投影 | 编辑快照不能改变公开合同 |
| R23 | P1 | 内部 Tool 被前端调用来模拟 Agent，绕过交互/重复写入 | 页面依赖区分直接接口与实现依赖；服务端权限不依赖是否列入菜单 | 生成前端无内部 Tool 调用；服务仍校验身份/归属 |
| R24 | P1 | 只写文档/静态字典就通过验收，实际未接线 | 静态与动态证据分开；追踪实际路由/工厂/服务链 | 真实调用测试证明输入、确认和结果可达 |
| R25 | P1 | Prompt 修改触发全项目失效，或结果变更漏失效 | 直接语义摘要与共享集合摘要分开 | 失效矩阵逐类吻合，Pending 身份漂移拒绝 |
| R26 | P1 | 新业务能力开启未支持的登录/权限，产生错误安全承诺 | application.json 权威和原拓扑门禁继续生效 | 不支持组合明确阻断，无匿名降级绕过 |
| R27 | P1 | 超长消息/状态、无限模型或工具执行导致内存/成本失控 | 复用 Runtime 配置的时间、输入/状态大小、工具次数限制；限制命中明确失败 | 有界输入和执行，不在异常后自动继续 |
| R28 | P2 | 多页面仅靠各自组件状态保存会话，返回页面重复开始 | 单一当前会话服务和只读状态恢复；不隐式重发 | 切页返回恢复状态，不新增业务执行 |
| R29 | P1 | 无实体 Agent 触发数据库/实体必填校验，产生虚假表或数据库依赖 | kind 分派、允许空 entity_ids、来源驱动数据库需求 | 纯文本 Agent 无数据库也能设计/开发/启动 |
| R30 | P1 | 失败确认/拒绝被错误表现成异常，sequence 仍继续 | 拒绝表示未授权执行；仅匹配业务 outcome 推进 | 拒绝后零写入且页面不提示创建成功 |

首版发布阻断集合：R01–R12 全部关闭；首版开放的场景对应 P1 必须关闭。R07/R08/R09 未满足时不得开放写能力。只有只读场景通过时只能报告只读闭环完成。

## 13. 实施前需核实的具体边界

这些是明确的实施检查点，不是要求用户补充技术信息。首次实施定向读取后记录结论；发现不满足则停止相关批次并报告，不自动反复调试。

| 检查点 | 本轮证据 | 处理决定 |
| --- | --- | --- |
| 模板 forwardedProps 输入路径、公开状态和业务事件真实形状 | 已看到 Studio 消费端，未读取外部模板 | 定向读取模板 AG-UI/interaction 入口后同步新合同；不以本文示例当已支持 |
| 当前状态读取、取消和同线程并发控制 | 现有消费说明不足以证明 | 模板补齐后才开放相应 UI，状态恢复不可伪造 |
| checkpoint 续接及写入幂等存储 | 静态 verifier 只能证明确认控制流 | 动态验证原子消费及提交窗口；缺失则写能力阻断 |
| 能力输入进入真实图、结果从节点/Tool 产生 | 当前七模块不足以证明新能力合同 | 将合同编译接入 context/tools 等实际模块及模板校验，不能只生成 manifest |
| 现有平台任务注册/执行器可扩展点 | 已确认平台任务与 Pending 来源机制存在，未读取全部实现 | 沿索引定向读取并登记 runtime_binding，不新增平行 Runner |
| 外部模板写入权限与交付归属 | 模板不在当前工作区 | 单独列出精确补丁和验证；未授权不得跨工作区静默修改 |

## 14. 分批实施顺序与改动地图

各批按下面顺序推进，未满足批次门槛不进入后续执行。代码落地时每个函数/方法有中文用途注释；新增复杂逻辑补决策注释。超过约 350 行的活动模块优先按合同/解析/编译职责拆分，不把所有分支继续塞入 api_design.py。

| 批次 | 改动范围 | 已定位的主要文件/目录 | 完成门槛 |
| --- | --- | --- | --- |
| A：合同与权威 | kind、interactionContract、技术侧页面关系改为反向投影、Schema 子集、v8 判别封装 | `domain/api_design.py`；拟新增专用 Agent API 领域模块；`services/runtime_agent_contracts.py`、`runtime_agent_settings.py`、`api_contracts.py`；`agents/main/` 当前生成/局部编辑/同步入口；`workspace/runtime_agent_markdown.py`、正式计划文档模块 | 生成→Markdown 编辑→确认→规范化不丢字段，当前产品约束闭合 |
| B：接口设计与正式修订 | Agent 来源候选、输入绑定、保存/草稿、换来源的技术修订、摘要及文档 | `services/api_design.py`、`endpoint_design_detail.py`；Endpoint 文档存储；`protocols/endpoint_designs.py`；前端接口工作台和 service/types | 不套用 response 映射，不绕过确认，完整格式同批切换 |
| C：统一依赖与 DAG | 同一闭包、kind 职责、runtime_binding、冻结/切片、锁、失效 | `services/build_context_resolver.py`、`runtime_agent_development.py`、`runtime_agent_dag.py`、`runtime_agent_module_policy.py`、`execution_resource_scope.py`、`artifact_freshness.py`；原 Unit/冻结/编译/调度对应模块 | 页面/接口/Agent 独立目标均闭合；平台任务不可由模型伪造 |
| D：Runtime 模板配套 | AG-UI 输入、公开投影、结果校验、状态读取、交互与取消、描述符消费 | 外部 Runtime 模板明确文件；Studio launcher/health 与 TemplateState 消费入口 | 真实模板与 Studio 一致，缺能力明确阻断 |
| E：页面接入 | runtimeBindings、消费/sequence 规则、官方客户端、移除技术直连生成、主题 | `services/page_implementation_contract.py`；`agents/frontend/runtime_instruction.py`、`generator.py`；前端 Agent/Endpoint 详情、页面接入投影、工作台类型 | 正式页面引用唯一运行 Endpoint；Mock 与真实状态完整 |
| F：验收与交付 | kind 专项 verifier、运行入口验收、行为与幂等证据、完成状态 | `services/runtime_business_acceptance.py`、`business_acceptance_verifiers/`、`development_artifacts.py`、`runtime_agent_development.py`；定向后端/前端测试 | 所选场景风险关闭，证据分类真实，无静态通过冒充端到端 |

实现中从 `docs/CODEBASE_INDEX.md` 路由到最小关联文件集；上表未定向读取的文件不是已完成审计。合同/API/边界变化时同步更新索引、现有规划文档、字段规则、Agent DAG 和模板合同；本文作为目标设计保留实施状态，不覆盖当前合同文档宣称已上线。

建议先完成“无实体、无数据库、无 Tool 的文本 Agent”纵向闭环，再扩展只读 Tool 和结构化结果，最后开放写入/确认。每一条纵向路径覆盖从设计到真实页面，而非先生成全部面板再补后端。该顺序是实施切片，最终完整交付仍须覆盖本文已纳入的场景。

## 15. 验收场景与证据

| 编号 | 场景 | 预期 |
| --- | --- | --- |
| V01 | 无实体、无数据库、无 Tool 的文本 Agent | 正常设计确认、DAG、启动、页面流式展示；无虚假实体或数据库任务 |
| V02 | Agent 使用只读订单 Tool | 依赖精确纳入；orderId 真正进入图和 Tool；只读结果符合 Schema |
| V03 | 创建工单前澄清并确认 | 澄清不是批准；批准后唯一实际写入；结果含真实 ticketId |
| V04 | 拒绝确认 | 没有业务写入，不触发创建成功页面动作 |
| V05 | 双击确认、跨标签重复确认、图重入 | 同一操作不会产生重复工单；证据来自业务服务和持久事实 |
| V06 | 提交附近断线、超时、取消 | 不自动重发；显示真实提交状态或 unknown，不宣称回滚 |
| V07 | 过期交互/参数变化/换订单/其他身份响应 | 服务端拒绝，不执行旧确认参数之外的操作 |
| V08 | A/B 页面复用同一 Agent | 引用同一 Endpoint，明确会话归属和上下文；无跨页串执行 |
| V09 | 单页面、单运行接口、单 Agent 独立开发 | 三种目标均包含必要依赖，普通接口开发不反向全量扩展 |
| V10 | 输入缺失、结果不合规、事件晚到或重复 | 边界校验、明确失败、状态不倒退、页面动作去重 |
| V11 | business 来源切换为 Agent，或更换 Agent | 创建正式修订；确认前不改 canonical；取消不损失原设计 |
| V12 | Prompt 修改与能力结果结构修改 | 前者不迫使页面重做绑定；后者使相关设计/任务失效且重新确认 |
| V13 | Markdown 修改与并发修订 | 隐藏技术信息保留；不匹配基线拒绝保存；双文件保持一致 |
| V14 | Tool 引用 Agent Endpoint、身份重复或引用悬空 | 生成任务前返回明确引用错误和依赖链 |
| V15 | 模板缺能力、代理错配、注册错误 Agent | 不放行健康/接入验收，不用假流或合同副本替代 |
| V16 | 明暗主题下的等待、确认、错误、断线及空态 | 在已运行 Electron 内可读可操作，未自动批准或隐式重发 |
| V17 | 当前普通 business API 与 Java 路径 | 新 kind/v8 贯通，原映射语义不改变，不混入 Agent 生成职责 |
| V18 | 真实模型拒绝工具调用、仅回复文本、额度/认证错误 | 不伪造业务成功；业务结果与模型回答区分；错误准确可见 |

受控替身模型/工具测试用于稳定证明输入、交互、并发和结果处理；真实模型会话用于证明实际接线及当前能力。两种证据分别记录，不能互相替代。静态 verifier 不能证明任意自然语言目标和外部系统写入具备 exactly-once。

验证执行遵守本项目用户约束：每个约定验证命令最多执行一次；任何失败即停止，不自动修复后重跑，不自动循环调试。实施代码时需记录具体命令、退出码、错误原文与未完成项，由用户判断下一步。文档-only 本轮不执行任何校验。

代码批次的验证按实际改动选择最小集合：后端改动使用一次 focused 测试或语法/import 检查及一次 `curl -sS http://127.0.0.1:8000/health`；影响前端 TS/打包/UI 行为时从 Frontend 执行一次 `pnpm build`；UI 在已运行 Electron 内验证，不用浏览器 Vite 页面代替。验证若存在前后依赖则串行，失败即停止后续验证；不重复扩大测试范围。

## 16. 评审重点与交付判定

实施前按本设计评审五个关键决定：

1. Agent 公开能力归 TechnicalPlan，接口只绑定；技术规划提前区分 agent_runtime。
2. 标准运行入口纳入正式 Endpoint，页面不再持有独立技术直连关系。
3. 流式接口采用输入/状态/交互/业务结果合同，普通映射与 Agent 设计为严格判别分支。
4. 同一依赖闭包驱动设计门禁、DAG、锁、冻结、失效和验收。
5. 写入幂等、确认参数绑定与真实结果证据作为写能力开放条件，未具备时明确阻断。

完整交付要求：当前合同和模板一致；三类开发目标闭合；页面消费和运行控制完整；正式修订与定向失效可用；所开放场景的 P0/P1 风险有对应证据；所有失败和跳过验证如实报告。代码生成完成、设计已保存、health 正常或静态验收通过，任何单项均不等于整体完成。

本轮交付仅为本详细设计及索引入口。产品代码、外部模板和测试均未修改；不将本设计标记为当前已实施合同。
