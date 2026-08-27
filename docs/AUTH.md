# 内置 RBAC 权限体系分阶段实施计划

## 前置设计

Authorization V1 的业务权限资源点只分为两种，此外平台固定注入一种 `system` 资源：

- page：控制页面、菜单和路由访问。
- operation：控制前端操作入口及对应后端业务能力。
- system：平台固定控制面资源，仅用于权限管理能力。

V1 **不实现通用数据权限**，不生成 `data` 资源、数据策略、行级过滤或对象级数据授权。数据权限作为 V2 演进能力单独预留；V1 检测到明确的数据授权需求时必须通过 Capability Gate 阻断，不能静默忽略或降级。

### 页面资源点

页面资源点直接复用正式 `pageId`，权限契约不再重复保存前端实现使用的 PageKey：

```json
{
  "resourceKey": "page_order_list",
  "origin": "business",
  "type": "page",
  "name": "订单列表",
  "description": "访问订单列表页面",
  "sourceRuleIds": ["<ruleId>"],
  "targetResourceRef": "page:page_order_list"
}
```

规则：

- `resourceKey = pageId`，二者均为 `lower_snake_case`。
- 页面路由和菜单引用相同的页面资源点。
- 父菜单只用于组织展示，不自动授予子页面。
- 获得页面权限不代表自动获得页面中的操作权限。

### 操作资源点

操作资源点代表一项前后端共同的业务能力：

```json
{
  "resourceKey": "page_order_list_approve_order",
  "origin": "business",
  "type": "operation",
  "name": "审批订单",
  "description": "发起并完成订单审批",
  "sourceRuleIds": ["<ruleId>"],
  "targetResourceRef": "page:page_order_list/action:approve_order"
}
```

使用规则：

- `resourceKey = <pageId>_<actionId>`，组成字段和最终键均为 `lower_snake_case`，不再增加需要用户维护的 operationKey。
- ProductPlan 的 sequence 可以包含稳定 `stepId`，但 `stepId` 只表示父 action 内部的产品步骤，不是独立授权目标；需要独立授权的能力必须先建模为顶层 action。
- 前端按钮、菜单操作或交互入口通过相同 resourceKey 控制展示。
- 对应后端 Endpoint 使用相同 resourceKey 强制鉴权。
- 用户绕过前端直接请求 Endpoint 时仍然必须被拦截。
- 纯 UI 行为，如展开面板、切换页签，不创建操作资源点。
- PageImplementationContract 和 TechnicalPlan Endpoint 绑定只能引用 ProductPlan 已确认的顶层 action 资源点，不能临时创建 step 资源。
- 一个顶层 action 的直接业务 Endpoint，以及 sequence 中所有 business step 对应的 Endpoint，统一绑定该父 action 的同一个操作资源；interface、navigation 和 external 类型的 action 或 step 不生成操作资源。
- 同一个 Endpoint 可以被多个 business action 复用，但所有引用该 Endpoint 的 business action 必须具有一致的操作权限属性：要么全部为已生成 operation 资源的受控 action，要么全部为未生成 operation 资源的未受控 action。
- 全部引用均为未受控 action 时，该 Endpoint 的 `operationResourceKeys=[]`，不增加操作资源守卫；全部引用均为受控 action 时，聚合去重各 action 的 `resourceKey` 到 `operationResourceKeys`，并固定按 ANY-OF 裁决。
- 禁止同一 Endpoint 同时被受控 action 与未受控 action 引用；出现混用时 TechnicalPlan 必须要求拆分 Endpoint。不得通过前端 `actionId`、`resourceKey`、请求头、query/body 参数或其他调用来源信息区分本次调用采用哪套操作权限语义。

### 角色资源授权

V1 的页面、操作业务资源与平台系统资源通过同一张授权关系分配；RequirementSpec 中每条页面/操作权限规则使用 `defaultGrantedRoleIds` 明确首次默认授予哪些已确认业务角色：

```json
{
  "roles": [
    {
      "roleSeedKey": "manager",
      "name": "审批人员",
      "isSystemRole": false,
      "isInitialAdminRole": false
    }
  ],
  "roleResourceGrants": [
    {
      "roleSeedKey": "manager",
      "resourceKeys": ["page_order_list", "page_order_list_approve_order"]
    }
  ]
}
```

授权表只回答角色拥有哪些资源点，不包含 Endpoint 实现逻辑。

### 权限管理固定资源

权限管理模块使用平台固定的 `system` 资源类型和 snake_case 命名，只注入一个固定资源点：

```text
system_authorization_management  system，targetResourceRef=authorization-api.v1#management-control-plane
```

- `/roles` 菜单、路由、页面内操作和所有权限管理 Endpoint 统一绑定 `system_authorization_management`；固定系统页面是业务页面/操作资源规则的唯一例外。
- `/api/authorization/status` 和 `/api/authorization/me/effective-permissions` 不绑定系统管理资源；其认证与就绪语义以本文运行态状态矩阵为准。
- 系统管理员角色默认获得该唯一系统资源；后端仍须独立鉴权，不能只依赖前端入口隐藏。

## 文档地位与当前状态

本文是 XCodeAgent 权限体系改造的唯一实施依据。后续模型实施权限相关工作时，必须先读取本文，再读取对应阶段及步骤列出的代码入口；对话中的历史方案、旧测试假设和未写入本文的临时结论都不能覆盖本文。

最新确认优先级固定为：

1. 本文“不可违背的业务不变量”和决策表。
2. 当前阶段及步骤的产物、禁止事项和启动验收。
3. 公共契约中的字段、稳定标识和追踪关系。
4. 模型提示词或现有实现细节。

如果现有代码、测试或提示词与更高优先级规则冲突，必须先修正冲突，不能通过增加默认规则、兼容分支或自然语言关键词匹配绕过。

当前实施状态：

| 阶段                                     | 状态   | 说明                                                                                                                                                                                    |
| ---------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 第一阶段：页面、操作与系统权限完整闭环  | 实施中 | 应用配置、RequirementSpec、ProductPlan 与 UiDesign 已有基线；仍需按步骤收敛 V1 当前契约，完成 TechnicalPlan、模板、Build 运行时、目录协调和端到端启动验收。                              |
| 第二阶段：数据权限                       | 未启动 | 当前只保留设计储备，不实施 data resource、Data Policy、Relation、Source Binding 或 Endpoint 数据权限执行；必须等待第一阶段完成并另行确认实施计划。                                     |

### 当前工作流适配（本次实施）

需求与产品规划现在构成一个用户可见的“需求文档”联合节点：RequirementSpec 先记录业务角色及其职责和权限候选，ProductPlan 在同一节点内消费已校验草稿生成页面与操作；二者只能通过一次 `requirement_document_confirmation` 联合确认后，UiDesign、TechnicalPlan 才能读取正式文件。内部继续保留各自 Markdown/JSON，ProductPlan 的 `requirement_spec_sha256` 必须等于同轮已确认 RequirementSpec 的确定性哈希；不生成 `requirement-document-manifest.json`。因此本文所有“RequirementSpec 确认后再生成 ProductPlan”的旧描述均以此规则为准替换。

## 不可违背的业务不变量

### 实施范围固定为内置权限

- 本计划只实现内置数据库 RBAC，不实现外部授权 provider、外部权限资源同步、外部角色/成员适配或 provider capability 协商。
- 从授权领域的类型、表单、持久化、规划、模板、Build 和测试中删除 `providerMode` 与授权语义的 `external_api` 分支。
- 业务数据源类型 `external_api` 与外部接口实体设计不属于授权 provider，必须完整保留，不能因本计划被删除或改名。
- 内置 RBAC 依赖应用数据库。静态应用不能启用 RBAC；用户必须改为数据库应用或移除权限需求。

### 三层事实必须分离

- `application.json.authorization` 只声明是否启用内置 RBAC 和首次初始化的管理员成员种子，不是业务页面或操作权限的来源。
- RequirementSpec V1 只保存用户明确提出的页面、操作权限、首次默认授权业务角色及系统管理员角色选择，是 V1 权限业务语义的唯一产品事实来源。
- 明确的数据授权需求不进入 RequirementSpec V1 正式权限契约，而是由 requirement-document Capability Gate 产生结构化不支持诊断并阻止确认。
- TechnicalPlan 将已确认的 V1 页面/操作规则确定性编译为固定资源目录、目标绑定和 Endpoint 操作授权逻辑；资源定义不进入运行态人工配置。
- TechnicalPlan 可以保存由已确认事实确定性编译的首次角色种子和默认角色资源关系；运行时代码仍不得按角色名称、角色 ID 或系统属性分支，首次初始化后角色、成员和资源关系动态配置。

权限能力开启不等于业务对象自动受控。身份认证也不等于 RBAC 资源控制。

### 未提及功能默认不受 RBAC 控制

- 用户未明确提出控制的页面，不生成页面资源点，不生成菜单或路由权限守卫。
- 用户未明确提出控制的操作，不生成操作资源点，不隐藏或禁用该操作。
- 对已认证成员而言，未生成资源点的业务功能默认可见可用；是否要求登录仍由独立身份认证契约决定。
- `/roles` 与 `system_authorization_management` 是平台固定控制面，不受“未提及业务功能默认可见”规则影响。

### V1 数据权限 Capability Gate

Authorization V1 不实现通用数据权限。下列需求只要承担“授权”语义，即视为 V1 不支持的数据权限：

- 不同成员、角色、部门、组织、上下级、项目、客户、区域或其他业务关系决定同一业务对象中哪些记录可见、可修改或可创建。
- “本人数据”“本部门数据”“本人及下级组织数据”“我负责的客户/项目数据”等动态数据范围。
- 需要按当前主体或其业务关系对列表做行级过滤、对单对象做访问校验、或对创建/更新内容施加数据范围约束。

V1 必须区分**业务查询条件**与**数据授权**：

- `GET /my-applications`、查询“我提交的申请”等作为业务功能本身定义的固定查询语义，可以按普通业务逻辑生成，不因为使用当前 subject 就自动成为数据权限。
- “普通员工只能看自己的申请、经理还能看部门申请、管理员能看全部申请”属于数据授权，即使技术实现同样可能使用 `subjectId`，V1 仍必须阻断。
- 当自然语言无法确定某个“本人/部门/项目”等范围是业务功能还是授权边界时，只澄清这一语义，不得用关键词或正则直接分类。

Capability Gate 固定规则：

```text
明确检测到数据授权需求
→ code = DATA_AUTHORIZATION_NOT_SUPPORTED
→ capability = data_authorization
→ 保留 sourceRefs 和用户原始业务描述
→ 阻止 requirement_document_confirmation
→ 不生成 data resource、policyKey、dataRuleKey 或数据过滤算法
```

- 不允许静默丢弃数据权限要求后继续生成可以访问全部数据的 Endpoint。
- 不允许把数据权限自动降级为页面权限、操作权限或普通业务筛选。
- 不允许为了通过 V1 门禁而自动把用户描述改写成“我的数据”固定查询接口。
- 只有用户明确移除数据授权要求，或将其重新定义为不承担授权语义的普通业务功能后，当前 V1 流程才能继续。
- 任何历史草稿或旧实现中的 `dataRules`、`dataRuleKey`、`policyKey`、数据策略绑定或 `type=data` 均不属于 V1 当前契约；在进入 TechnicalPlan 前必须删除/失效并重新确认。

### 资源固定、关系动态

- V1 的固定资源目录只包含业务 `page`、业务 `operation` 和平台 `system` 资源。
- 系统资源由平台确定性注入；业务资源由 TechnicalPlan 根据已确认规则和稳定目标 ID 确定性编译。
- 资源定义包括 `resourceKey`、类型、语义和目标；TechnicalPlan 确认后冻结，运行态管理 API 不提供资源创建、修改或删除能力。
- 生成应用运行态只允许读取资源目录，以及创建、修改、启停角色，配置角色资源关系和成员角色关系。
- 应用规划修订只有经过重新确认、重新 Build 和目录协调后才能改变资源目录；普通运行态操作不能改变资源定义。

### 运行态授权代数固定

- V1 只支持 allow 关系，不支持显式 deny。
- 不支持角色继承、成员直接授权和按角色名称或角色 ID 的代码分支。
- 成员有效资源是其全部启用角色所绑定资源的并集。
- 同一 Endpoint 的所有 business action 引用必须具有一致的操作权限属性。“受控”表示该 action 已生成 operation 资源，“未受控”表示该 action 未生成 operation 资源；同一 Endpoint 禁止同时存在两类引用。
- 全部引用均为未受控 action 时，`operationResourceKeys` 必须为空；全部引用均为受控 action 时，聚合去重各 action 对应的操作资源，并固定采用 ANY-OF：成员有效资源与 `operationResourceKeys` 存在至少一个交集即可通过操作资源裁决，非空且无交集时返回 403。
- 受控与未受控 action 混用同一 Endpoint 时，TechnicalPlan 必须判定为不可编译并要求拆分 Endpoint；不得通过前端 `actionId`、请求头、query/body 参数或其他调用来源信息选择授权分支。
- 如果一个请求会同时执行多个彼此独立、分别受控的业务能力，TechnicalPlan 必须拆分 Endpoint，不能用 ANY-OF 让一个资源授权间接获得另一项能力。
- 无角色、角色停用、资源未知或受控目标没有有效资源授权时默认拒绝。

操作资源裁决公式固定为：

```text
effectiveResourceKeys(member) = union(resourceKeys of every active role assigned to member)
allowOperation(endpoint, member) =
  endpoint.operationResourceKeys is empty
  OR intersection(effectiveResourceKeys(member), endpoint.operationResourceKeys) is not empty
```

空 `operationResourceKeys` 只表示该 Endpoint 没有操作资源守卫，不会绕过其身份认证或其他独立安全校验。

### RequirementSpec 决策表

| 权限能力 | 用户业务描述                                     | RequirementSpec / Gate 结果                                                       | 是否澄清                                   |
| -------- | ------------------------------------------------ | --------------------------------------------------------------------------------- | ------------------------------------------ |
| 关闭     | 未提及权限控制                                   | `enabled=false`，页面/操作候选为空                                                | 否                                         |
| 开启     | 未提及页面或操作控制                             | `enabled=true`，页面/操作候选为空                                                 | 只确认系统管理员角色选择                   |
| 开启     | 明确页面/操作控制并说明角色                      | 只生成明确候选及其 `defaultGrantedRoleIds`                                        | 否                                         |
| 开启     | 明确受控页面/操作但未说明授权角色                | 保留已确认候选，不推断角色                                                        | 是，只确认该候选首次默认授予哪些角色       |
| 任意     | 明确提出数据授权                                 | 不进入 V1 RequirementSpec；返回 `DATA_AUTHORIZATION_NOT_SUPPORTED` 并阻止联合确认 | 否；属于能力不支持，除非用户要修改业务要求 |
| 任意     | “本人/部门/项目”等描述无法判断是业务查询还是授权 | 暂不生成该权限候选                                                                | 是，只澄清其是否承担授权语义               |
| 开启     | 存在一个管理员类业务角色                         | 保留业务角色和明确业务授权                                                        | 是，确认复用该角色或新建系统管理员角色     |
| 开启     | 存在多个管理员类业务角色                         | 保留全部业务角色                                                                  | 是，选择唯一系统管理员角色或新建           |
| 开启     | 明确提出权限但业务含义不完整                     | 保留已确认事实，不推断缺失语义                                                    | 是，只询问该业务歧义                       |
| 关闭     | 业务描述明确要求 V1 支持的权限控制               | 不得静默丢弃，也不得自动开启                                                      | 是，确认启用并补齐配置，或移除该需求       |

页面和操作两个候选数组彼此独立，任何一个数组为空都是合法状态。禁止为了“结构完整”补写默认受控页面、默认受控操作或默认业务授权。

### 代码职责边界

| 层级                        | 允许职责                                                                                                                                            | 禁止职责                                                                            |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| 需求模型                    | 理解用户业务语言；提取明确的页面/操作权限候选和角色授权事实；识别数据授权 Capability Issue；提出真实业务歧义                                        | 根据权限开关、初始管理员或登录配置推断业务权限；生成技术 ID；为数据授权生成实现方案 |
| RequirementSpec service     | 归一化当前 V1 字段；生成和保留 `ruleId`；校验角色引用和业务语义；拒绝 V1 不支持字段                                                                 | 使用关键词解释权限语义；补默认授权；生成页面/资源绑定；接收 data policy 字段        |
| requirement-document node   | 执行配置冲突、业务角色/职责、页面/操作权限语义、授权角色和系统管理员选择澄清；执行数据权限 Capability Gate；联合确认 RequirementSpec 与 ProductPlan | 检查问题文本关键词；自动选择角色；静默修改应用配置；忽略数据授权要求继续确认        |
| RequirementSpec UI/Markdown | 用业务语言展示页面/操作候选、默认业务角色、系统管理员选择和 Capability Issue；保存不可见稳定标记                                                    | 要求用户填写技术 ID、资源键、策略键、SQL 或数据库字段                               |
| ProductPlan                 | 承接已确认页面和顶层业务 action，建立稳定 `pageId/actionId`；sequence 的 `stepId` 只表达父 action 内部产品步骤                                      | 新增权限语义、step 资源、资源键、角色、数据策略或固定权限管理页                     |
| TechnicalPlan               | 确定性编译页面/操作/系统资源键、目标绑定、Endpoint 操作授权逻辑、首次角色种子和默认授权矩阵                                                         | 让模型决定最终键或角色授权；创建无来源资源；写入特定角色判断；创建 V1 数据权限策略  |
| 生成应用运行时              | 读取固定资源目录；动态配置角色和成员关系；执行最终页面/操作/系统资源裁决                                                                            | CRUD 资源定义；直接授权成员；按固定角色判断；解释或执行 V1 数据策略                 |

### 确定性逻辑允许与禁止清单

确定性代码允许：

- 读取系统生成的结构化配置事实。
- 归一化当前契约字段和空数组。
- 为新页面/操作权限候选生成 UUID `ruleId`，为已有候选保留稳定 ID。
- 按稳定 `pageId`、`pageId + actionId` 编译资源键，并对页面、操作和系统候选执行全局唯一校验。
- 校验 enum、条件必填、来源覆盖、目标绑定和资源目录一致性。
- 权限关闭时清空 V1 业务权限候选和管理员种子。
- 校验 `defaultGrantedRoleIds`、初始系统管理员角色和角色系统属性之间的一致性。
- 拒绝手写 `resourceKey` 以及任何 `dataRuleKey`、`policyKey`、data policy、SQL 和数据库字段进入 V1 权限契约。

确定性代码禁止：

- 使用“全部、本人、自己、部门、组织”等关键词或正则判断自由业务语言中的权限语义或是否属于数据授权。
- 从整段需求文本自动推断数据范围或自动转换为业务查询条件。
- 因为 `enabled=true`、登录开启或存在 `/roles` 而生成任何业务资源候选。
- 自动选择第一个页面或操作绑定权限规则。
- 对用户未提及的权限维度发起澄清。
- 为页面或操作候选补写默认未授权行为。
- 根据“管理员”等角色名称自动选择系统管理员角色，或生成按角色名称、角色 ID 和系统属性判断的裁决逻辑。
- 在授权领域重新引入 `providerMode` 或外部 provider 分支。

受控系统配置解析与自由业务语言解释必须区分：前者只能读取平台生成的固定事实，后者只能由需求模型完成。数据授权的识别也必须来自模型结构化事实并保留 `sourceRefs`，不能退化为关键词 gate。后续结构化规划上下文必须优先使用结构化字段，不能依赖文本标记作为长期契约。

### 权限候选来源与稳定追踪

- 每个权限候选在内部 JSON 中包含系统生成、不可变且唯一的 `ruleId`。
- `ruleId` 不在普通确认 UI 中显示，也不能由用户手填；Markdown 使用不渲染的内部标记保留 ID，以支持编辑同步。
- 结构化编辑保留原 `ruleId`；新增候选生成新 ID；删除后重新新增视为新规则，不复用旧 ID。
- Markdown 同步必须携带当前 JSON 隐藏状态；保留标记的候选保留 ID，缺失标记的新候选生成新 ID，重复或未知标记拒绝确认。
- 每个候选的 `sourceRefs` 必须指向用户原始业务描述、权限澄清回答或 RequirementSpec 确认修改。
- 权限开关和初始管理员配置不能作为业务候选的 `sourceRefs`。
- TechnicalPlan 的 `sourceRuleIds` 直接引用 RequirementSpec 的稳定 `ruleId`。
- 用户未提出的候选不存在“默认来源”，也不能使用 `authorization_default` 等伪来源。

## 模型实施执行协议

后续使用 Luna 或其他模型实施任一阶段的任一步骤时，必须按以下顺序执行：

1. 读取 `AGENTS.md`、`docs/CODEBASE_INDEX.md` 和本文完整内容。
2. 只读取当前步骤列出的入口文件及其直接依赖，不提前实现后续步骤或第二阶段。
3. 在修改前列出当前步骤的事实来源、允许推导、禁止推导和不会改动的边界。
4. 先补正向、负向和冲突场景测试，再实现代码；测试必须同时证明“应生成什么”和“不应多生成什么”。
5. 第一阶段的每个编号步骤都是独立工作包，完成后项目必须仍可启动，不允许用只能在后续步骤验证的中间结构作为交付结果。
6. 运行当前步骤要求的后端测试、前端构建和启动验收；失败必须在进入下一步骤前解决。
7. 搜索新增代码中的自然语言权限关键词表、角色名分支、默认权限规则、无来源资源、资源写接口和授权 provider 分支；发现即停止验收。
8. 更新本文步骤状态和必要的 `docs/CODEBASE_INDEX.md`，再申请进入下一步骤。
9. 每个步骤完成后必须实际启动受影响的 XCodeAgent、模板工程或生成应用，向用户提交修改文件、启动命令、测试结果、人工验收入口和遗留问题；只有用户明确确认达到预期后才能开始下一步骤，模型不得把多个未验收步骤合并执行。
10. 第一阶段步骤 5 只在 XCodeAgent 中完成模板分支选择与获取；前后端模板的 `main`/`auth` 分支公约是既有前提，不属于本步骤的改造、构建或独立验收范围。
11. 本计划不授权模型推送远端、创建 PR 或提交到模板仓库；这些外部写操作必须另行取得用户授权。

## 核心流程与事实来源

权限流程固定为：

```text
新建应用内置权限开关与管理员种子
→ RequirementSpec 确认明确提出的权限业务逻辑、默认授权角色和唯一初始系统管理员角色
→ ProductPlan 建立稳定业务页面与 action
→ UiDesign 只设计业务页面
→ TechnicalPlan 确定性编译固定资源目录、页面/顶层 action/Endpoint 操作绑定和首次默认授权
→ 工程初始化接入模板固定 /roles 页面和内置权限基础
→ Build 生成授权运行时和业务守卫
→ 运行态配置角色、角色资源关系和成员角色关系
```

事实归属固定为：

- 新建应用配置：声明是否启用内置 RBAC 和首次管理员成员种子。
- RequirementSpec：V1 页面/操作权限业务语义、默认授权业务角色和初始系统管理员角色选择的唯一产品事实来源；数据授权需求只形成 Capability Issue，不进入正式 V1 契约。
- ProductPlan：保持产品行为并建立稳定 `pageId/actionId`；sequence 的 `stepId` 只属于父 action 内部产品步骤，不定义资源键、角色或角色分配。
- TechnicalPlan：编译固定页面/操作/系统资源点、目标绑定、Endpoint 操作授权逻辑、角色种子和首次默认授权，不改变已确认权限业务含义。
- 固定权限管理页：系统拥有，不属于业务页面，不进入 ProductPlan、UiDesign 或普通页面 Build。
- 生成应用后端：最终授权裁决者和运行态关系事实来源。

应用配置与 RequirementSpec 的 `enabled` 在确认时必须一致。应用配置继续作为管理员种子的基础设施事实来源，但不得用于推导业务资源。

## 公共契约调整

### 新建应用配置 v5

应用配置升级为当前 `schemaVersion: 5`：

```ts
type ApplicationAuthorizationSeed = {
  enabled: boolean;
  initialAdministratorSubjects: string[];
};
```

表单规则：

- 认证区域后保留独立“权限控制”区域，只展示“涉及权限控制”开关和初始管理员成员标识。
- 启用权限会自动启用身份认证；权限开启期间不能单独关闭认证。
- 启用权限要求当前应用使用数据库，并至少填写一个认证系统中真实存在或可预配置的 `subjectId`。
- 不预填、不识别也不持久化 `current-user` 等魔法占位符；初始管理员成员标识按普通字符串精确匹配认证系统返回的 subject。
- 关闭权限时清空初始管理员种子。
- 删除权限 provider 和运行态页面选项；启用权限即确定性接入内置权限服务与 `/roles`。
- 打开工作区时只接受当前 v5 权限字段，不为旧结构增加探测、转换或回退。

应用创建后，表单值写入 `application.json.authorization` 和首次规划请求。若新建时关闭权限、业务描述又明确要求权限控制，前置澄清必须：

1. 确认启用权限或移除该业务要求。
2. 选择启用时确认数据库前提和初始管理员成员。
3. 通过现有 AG-UI 规划动作原子更新认证与 authorization 配置。
4. 配置持久化成功后重新生成 RequirementSpec，不允许只修改内存草稿。

### 可信认证身份边界

生成应用的认证方案固定通过 Spring Security 向授权模块提供可信身份：

- 一号通认证实现负责在服务端校验 Cookie/会话并建立 Spring Security `Authentication`，不能把前端解析结果视为可信身份。
- 授权模块通过 `CurrentSubjectProvider` 读取 `SecurityContext` 中已认证的 `Authentication.getName()`，并将其作为唯一当前 `subjectId`。
- 前端不解析 Cookie，不在请求体、请求头或查询参数中提交“当前用户 subjectId”；成员管理接口路径中的 `subjectId` 只表示被管理对象。
- 一号通认证未能提供经过验证的 `Authentication` 时，权限运行时不能标记为 ready，第一阶段步骤 7 不能通过验收。
- 未认证访问由 Spring Security 返回 401；身份认证成功后才进入资源裁决并可能返回 403。
- RBAC 模块不得自行复制一号通协议实现，也不得从 `authnSource`、`clientId` 或任意未验证字符串推导当前成员。

### RequirementSpec 权限需求

Authorization V1 正式字段调整为：

```ts
type AuthorizationRuleBase = {
  ruleId: string;
  name: string;
  description: string;
  rationale: string;
  sourceRefs: string[];
  defaultGrantedRoleIds: string[];
};

type RestrictedPageRule = AuthorizationRuleBase & {
  // 已确认业务页面的稳定身份，不以展示名称建立关联。
  targetPageId: string;
};

type AuthorizationRequirements = {
  enabled: boolean;
  restrictedPages: RestrictedPageRule[];
  restrictedOperations: AuthorizationRuleBase[];
  initialAdminRoleId?: string;
};

type RequirementRole = {
  id: string;
  name: string;
  description: string;
  isSystemRole: boolean;
  isInitialAdminRole: boolean;
};
```

`RequirementRole` 是现有 RequirementSpec 顶层 `user_roles[]` 的当前结构，不是在 `AuthorizationRequirements` 内复制第二份角色列表。其 `id` 必须是稳定 `lower_snake_case`，后续直接作为 `roleSeedKey`。

数据权限 Capability Issue 不进入正式 `AuthorizationRequirements`。规划过程使用统一诊断结构返回：

```ts
type AuthorizationCapabilityIssue = {
  code: "DATA_AUTHORIZATION_NOT_SUPPORTED";
  capability: "data_authorization";
  description: string;
  sourceRefs: string[];
};
```

当前契约校验：

```text
每条页面或操作规则
→ defaultGrantedRoleIds 必须非空，且每项都引用 user_roles[].id

每条页面规则
→ targetPageId 必须非空，且引用 RequirementSpec.pages[].pageId
→ ProductPlan 仅通过 targetPageId 生成 pageRules，不得按 name 推断页面目标

authorization.enabled=true
→ initialAdminRoleId 必须引用唯一 isInitialAdminRole=true 的 user_roles[]
→ isInitialAdminRole=true 必然同时 isSystemRole=true

authorization.enabled=false
→ restrictedPages/restrictedOperations 为空
→ 所有角色的两个系统属性为 false
→ initialAdminRoleId 不存在

存在 DATA_AUTHORIZATION_NOT_SUPPORTED
→ requirement_document_confirmation 必须阻止
→ 不写入正式 RequirementSpec/ProductPlan
```

补充约束：

- RequirementSpec 不保存 `unauthorizedBehavior` 或任何同义行为字段；未登录访问统一由身份认证层处理。
- 页面无权行为固定为菜单和入口隐藏，直接访问路由返回 403 禁止页。
- 操作无权行为固定为入口隐藏，后端 Endpoint 独立校验并返回 403。
- RequirementSpec V1 不携带 `dataRules`、`dataRuleKey`、`policyKey`、数据范围策略、`entityId` 或任何数据授权实现字段；历史字段一律视为旧契约并在进入 TechnicalPlan 前移除。
- RequirementSpec 不携带 `pageId`、`operationId`、路由、资源键或角色关系。
- `ruleId` 是内部稳定追踪字段，不是用户手填的技术绑定。
- 用户明确提出受控页面/操作却未说明默认授权角色时，必须通过现有 AG-UI 澄清，不得猜测或留空。
- 需求模型检测到明确数据授权语义时，只输出 `AuthorizationCapabilityIssue`，不能同时生成一个普通业务过滤来替代该权限要求。
- 业务描述中存在管理员类角色时，必须询问是否让其中一个角色承担系统权限管理；用户明确不合并时才新增独立系统管理员角色。
- 有多个管理员类角色时必须让用户选择唯一角色或新建独立角色；不得根据名称自动选择。
- `isSystemRole`、`isInitialAdminRole` 仅是角色种子元数据，不产生隐式授权，也不得用于业务裁决分支。

### 固定权限管理页

当 XCodeAgent 选择前后端 `auth` 分支时，模板确定性增加：

```text
route: /roles
pageId: system_authorization_management
name: 权限管理
```

固定页面包含：

- 只读页面、操作和系统资源目录。
- 角色列表、创建、修改、启用、停用和删除。
- 角色资源矩阵。
- 成员列表、预配置成员、JIT 成员和成员角色设置。
- 成员最终有效权限。
- 授权审计和当前 revision。
- 明暗主题完整状态。

固定系统资源键由平台确定性注入，且只包含：

```text
system_authorization_management  system
```

资源定义全部只读。拥有 `system_authorization_management` 的成员可以使用同一组管理 API 查看和维护资源关系、角色、成员角色关系、有效权限与审计，不再为页面、读写动作或接口拆分资源点。页面菜单对无该资源的成员隐藏；直接访问路由或调用管理 API 均返回 403。`/api/authorization/status` 和当前成员自己的 `/api/authorization/me/effective-permissions` 只要求可信认证，不绑定该系统资源。

页面所有权规则：

- 不加入 RequirementSpec `pages`，只在权限需求章节展示为系统固定页面。
- 不加入 ProductPlan `pages`，不生成 UiDesign。
- 不出现在业务页面设计入口和开发任务规划中。
- 不生成普通 PageImplementationContract 或业务 Endpoint 授权逻辑。
- 模板初始化直接复制完整默认实现，不创建待 Agent 填充的占位页。
- `/roles` 是保留系统路由，所有业务页面必须避免冲突。

### ProductPlan 与 TechnicalPlan 衔接

ProductPlan 使用 `product-plan.v5`，不保存资源键、策略键或角色矩阵。

确定性约束：

- ProductPlan 中带授权引用的页面和顶层 action 必须能追溯到已确认的页面或操作规则；未受控业务页面和操作不要求权限规则。
- ProductPlan 权限映射固定为 `pageRules[{ruleId,pageId}]` 和 `operationRules[{ruleId,pageId,actionId}]`。`pageId/actionId` 在 ProductPlan 阶段生成，不能反向要求用户填写技术 ID。
- sequence 内部可以保留稳定 `stepId`，但权限映射、资源目录、PageImplementationContract 权限投影和 Endpoint 资源绑定均不得保存 `stepId`；需要独立授权的业务步骤必须先提升为顶层 action。
- ProductPlan 不得出现 `allowed_roles`、`allowedRoleIds`、`dataRules`、数据策略或任何特定角色判断。
- ProductPlan 不包含固定权限管理页；UiDesign 只处理 ProductPlan 业务页面。
- 存在未解决的 `DATA_AUTHORIZATION_NOT_SUPPORTED` 时 ProductPlan 不得联合确认，更不能自行把数据授权要求转换为页面/action。

TechnicalPlan V1 资源点契约：

```ts
type PermissionResourcePoint = {
  resourceKey: string;
  origin: "system" | "business";
  type: "page" | "operation" | "system";
  name: string;
  description: string;
  sourceRuleIds: string[];
  targetResourceRef: string;
};
```

确定性资源键：

```text
页面资源：resourceKey = pageId
操作资源：resourceKey = <pageId>_<actionId>
系统控制面：system_authorization_management（type=system）
```

编译与校验规则：

- 所有 `pageId`、`actionId` 和 `resourceKey` 使用 `lower_snake_case`；禁止点号前缀和同义重复键。
- RequirementSpec 与 ProductPlan 联合确认前必须构造 `全部受控页面的 pageId ∪ 全部受控顶层 action 的 <pageId>_<actionId> ∪ system_authorization_management`，并要求结果全局唯一；`type` 只负责分类，不能作为同名 resourceKey 的命名空间。
- 未确认草稿发生页面、操作或系统键冲突时，将精确冲突目标回灌现有模型修复流程，不得静默追加数字、随机后缀或类型前缀；已确认稳定 ID 发生冲突时必须走正式修订并重新确认。
- TechnicalPlan 编译时必须重新执行同一全局唯一校验，防止篡改、过期上游或跨类型碰撞绕过联合确认门禁。
- 页面权限 manifest 只保存 `pageId`，不再保存 PageKey/pageKey；前端实现可有组件名，但不能进入权限契约。
- 每个受控页面规则必须绑定一个页面资源；多个规则指向同一页面时复用资源并聚合 `sourceRuleIds`。
- 每个受控操作规则必须绑定一个由 `pageId + actionId` 唯一定位的顶层 action 资源；多个规则指向同一 action 时复用资源并聚合来源。
- 顶层 business action 的直接 Endpoint，以及 sequence 中全部 business step 的 Endpoint，统一绑定父 action 的资源；interface、navigation 和 external 类型的 action 或 step 不生成操作资源或 Endpoint 操作绑定。
- TechnicalPlan 必须按 Endpoint 汇总全部 business action 引用并校验操作权限属性一致性：若全部引用均未生成 operation 资源，则该 Endpoint 的 `operationResourceKeys=[]`；若全部引用均已生成 operation 资源，则聚合去重其资源键；若同时存在受控与未受控引用，则以 `ENDPOINT_AUTHORIZATION_MIXED_CONTROL` 阻止 TechnicalPlan 确认并要求拆分 Endpoint。
- 混用场景不得通过增加前端 `actionId`、`resourceKey`、请求头、query/body 参数或其他调用来源字段规避；后端 Endpoint 的授权语义只能由已确认的 TechnicalPlan manifest 决定。
- 同一 Endpoint 聚合多个操作资源时固定按 ANY-OF 裁决：成员全部启用角色资源并集与非空 `operationResourceKeys` 存在任一交集即可通过，否则返回 403。若一个请求实际执行多个彼此独立的受控能力，必须拆分 Endpoint，不能改为 ALL-OF 或借 ANY-OF 扩权。
- V1 TechnicalPlan 出现 `type=data`、`dataRules`、`dataPolicyBindings`、`dataRuleKey`、`policyKey`、`requiredSubjectAttributes`、数据授权执行模式或任意数据策略字段时，必须以 `DATA_AUTHORIZATION_NOT_SUPPORTED` 或 `UNSUPPORTED_AUTHORIZATION_FIELD` 阻止确认，不能归一化删除后继续。
- 所有业务资源必须 `origin=business` 且至少包含一个有效 `sourceRuleIds`。
- 唯一系统资源必须 `origin=system`、`type=system`、`sourceRuleIds=[]`，由平台注入，模型不能改名、删除或拆分。
- 资源键重复但类型、目标或语义不一致时拒绝确认。
- TechnicalPlan 不能改变默认角色授权或初始系统管理员选择，也不能写入角色名/角色 ID 裁决逻辑。

TechnicalPlan 生成带稳定 fingerprint 的权限资源 manifest：

```ts
type AuthorizationManifestV2 = {
  schema_version: "authorization-manifest.v2";
  enabled: boolean;
  resources: PermissionResourcePoint[];
  bindings: {
    pages: Array<{ pageId: string; resourceKey: string }>;
    actions: Array<{
      pageId: string;
      actionId: string;
      resourceKey: string;
    }>;
    endpoints: Array<{
      endpointId: string;
      operationResourceKeys: string[];
    }>;
  };
  defaultRoleAuthorization: {
    roles: Array<{
      roleSeedKey: string;
      name: string;
      description: string;
      isSystemRole: boolean;
      isInitialAdminRole: boolean;
    }>;
    roleResourceGrants: Array<{ roleSeedKey: string; resourceKeys: string[] }>;
    initialAdminRoleSeedKey: string;
  };
  fingerprint: string;
};
```

- `roleSeedKey` 确定性复用已确认 `user_roles[].id`，不得由模型改写。
- 业务资源只按每条页面/操作规则的 `defaultGrantedRoleIds` 聚合到对应角色；不得把未明确授权的业务资源加入角色。
- 被选为初始系统管理员的角色固定获得唯一系统资源；若复用业务管理员角色，则同时保留该角色明确获得的业务资源；若新建独立角色，则默认只有该系统资源。
- PageImplementationContract 的操作权限投影必须包含 `{targetType:"action",pageId,actionId,resourceKey}`；Build DAG、前后端守卫和运行态资源投影必须引用同一 manifest，不生成第二份权限详设。
- TechnicalPlan Markdown 必须按页面资源、顶层 action 操作资源、Endpoint ANY-OF 绑定和角色默认授权分组展示人类可读表格，同时保留 `ruleId → target → resourceKey` 追踪关系；用户不需要手工编辑技术 ID。

### 生成应用运行态 OpenAPI 契约

本仓库的 [`contracts/authorization-api.v1.yaml`](../contracts/authorization-api.v1.yaml) 是前后端共享的权限管理接口唯一事实源，契约版本固定为 `authorization-api.v1`。后端 `auth` 分支提交与该文件字节一致的 `src/main/resources/openapi/authorization-api.v1.yaml` 副本；前端从该本地契约确定性生成 TypeScript 类型，但所有 HTTP 请求必须复用模板现有 `src/apis/service.ts` 的 axios 实例，不生成或引入第二个 HTTP 客户端。XCodeAgent 必须校验两个 YAML 文件的 SHA-256 一致，并验证前端生成类型无漂移。

固定接口：

```text
GET  /api/authorization/status
GET  /api/authorization/me/effective-permissions
GET  /api/authorization/resources
GET  /api/authorization/resources/{resourceKey}
GET  /api/authorization/roles
POST /api/authorization/roles
GET  /api/authorization/roles/{roleId}
PUT  /api/authorization/roles/{roleId}
DELETE /api/authorization/roles/{roleId}
PUT  /api/authorization/roles/{roleId}/status
GET  /api/authorization/roles/{roleId}/resources
PUT  /api/authorization/roles/{roleId}/resources
GET  /api/authorization/members
GET  /api/authorization/members/{subjectId}
PUT  /api/authorization/members/{subjectId}/roles
DELETE /api/authorization/members/{subjectId}
GET  /api/authorization/members/{subjectId}/effective-permissions
GET  /api/authorization/audit
```

- 除 `/status` 和 `/me/effective-permissions` 外，上述权限管理 Endpoint 全部绑定唯一的 `system_authorization_management`，不按页面、读写动作或接口继续拆分资源。
- `/roles` 页面也绑定 `system_authorization_management`，前端入口控制和后端不可绕过校验引用同一资源。
- `POST /roles` 请求为 `{name, description?, expectedRevision}`；`PUT /roles/{roleId}` 只允许更新 `{name, description?, expectedRevision}`；`PUT /roles/{roleId}/status` 为 `{active, expectedRevision}`。
- `PUT /roles/{roleId}/resources` 为 `{resourceKeys, expectedRevision}`，按全量替换处理；`DELETE /roles/{roleId}` 为 `{expectedRevision}`，不接受资源或系统属性字段。
- `PUT /members/{subjectId}/roles` 为 `{roleIds, displayName?, expectedRevision}`，按全量替换成员角色处理，并可作为精确 subject 的预配置入口；`DELETE /members/{subjectId}` 为 `{expectedRevision}`，只撤销本地授权记录。

响应与并发契约：

- 列表响应统一包含 `items`、`total`、`page`、`pageSize` 和读取时的 `revision`。
- 资源 DTO 至少包含 `resourceKey`、`origin`、`type`、`name`、`description`、`semanticDefinition`和可选 `targetResourceRef`。
- 角色 DTO 至少包含 `roleId`、`name`、可选 `description`、`active`、`deleted`、`isSystemRole`、`isInitialAdminRole` 和关联 `resourceKeys`；成员 DTO 至少包含 `subjectId`、可选 `displayName`、来源状态和关联 `roleIds`。
- `isSystemRole` 和 `isInitialAdminRole` 由首次种子创建过程写入，在普通角色创建、更新和资源关系 API 中只读；客户端提交修改时必须拒绝，不能静默忽略。
- 有效权限响应包含 `subjectId`、最终 `resourceKeys` 和 `revision`。
- 创建/更新/删除角色、角色状态替换、角色资源全量替换、成员角色全量替换和删除本地成员授权记录请求都必须携带 `expectedRevision`；成功响应返回最新 `revision`。
- `DELETE /roles/{roleId}` 只允许删除未拥有 `system_authorization_management` 的角色，采用逻辑删除并撤销其关联关系；`DELETE /members/{subjectId}` 只删除本地授权记录及角色关系，不删除外部身份。
- 错误响应统一包含 `code`、`message`、可选 `currentRevision` 和可选 `details`。
- 固定错误码至少包含 `unauthenticated`、`forbidden`、`authorization_not_ready`、`authorization_revision_conflict` 和 `last_administrator_required`。

接口注册与就绪状态矩阵：

| 状态                                    | `/api/authorization/status`                                                            | 当前成员有效权限                               | 权限管理接口                                   |
| --------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------- | ---------------------------------------------- |
| auth 分支、Build 运行时未就绪           | 公开 200，返回 `ready=false`、`contractVersion=authorization-api.v1` 和非敏感 `reason` | 在解析身份前统一 503 `authorization_not_ready` | 在解析身份前统一 503 `authorization_not_ready` |
| auth 分支、运行时就绪、未认证           | 公开 200，返回 `ready=true`                                                            | 401 `unauthenticated`                          | 401 `unauthenticated`                          |
| auth 分支、运行时就绪、已认证但无资源   | 公开 200，返回 `ready=true`                                                            | 200，仅返回当前成员有效资源，可以为空          | 403 `forbidden`                                |
| auth 分支、运行时就绪、拥有系统管理资源 | 公开 200，返回 `ready=true`                                                            | 200                                            | 200                                            |

未就绪 `reason` 只允许公开非敏感枚举：`runtime_not_built`、`database_not_initialized`、`resource_manifest_mismatch`；就绪时 `reason=null`。不得返回数据库地址、异常堆栈、Cookie、token 或内部配置。`AuthorizationReadinessFilter` 必须先于身份认证过滤器处理权限管理接口：未就绪时除 `/status` 外统一返回 503；就绪后再执行 Spring Security 的 401 和 RBAC 的 403 判定。

### 运行态数据与服务契约

内置权限运行时复用后端 `auth` 模板已经提供的权限表、数据访问和初始化扩展点。XCodeAgent 不为权限引入 Flyway、MyBatis、DDL、Mapper、数据源配置或 Maven 依赖；模板内部采用何种既有持久化实现不构成 XCodeAgent 的额外技术契约。

运行态至少包含：

```text
authorization_resources       resourceKey 主键、固定 manifest 投影与 fingerprint，只允许部署协调器写入
authorization_roles           UUID roleId 主键、标准化名称唯一、说明、active/inactive、两个只读系统属性、创建更新时间
authorization_role_resources  roleId + resourceKey 联合主键的 allow 关系
authorization_subjects        subjectId 主键、展示信息、seed/preconfigured/jit 来源和发现时间
authorization_subject_roles   subjectId + roleId 联合主键的成员角色关系
authorization_audit           操作者、动作、目标、变更前后 JSON 摘要、时间和 revision
authorization_revision        单例记录，保存当前 revision、初始化状态和 manifest fingerprint
```

稳定管理服务能力：

```text
listResources
listRoles
createRole
updateRole
setRoleStatus
replaceRoleResources
listSubjects
replaceSubjectRoles
getEffectivePermissions
listAudit
deleteRole
deleteSubjectAuthorization
```

运行态约束：

- 不提供 `createResource`、`updateResource` 或 `deleteResource`。
- `roleId` 是稳定 UUID；角色名称按标准化值唯一，可改名。
- 普通运行态新建角色的 `isSystemRole=false`、`isInitialAdminRole=false`；这两个属性不是授予权限的替代机制，也不能被普通角色 API 修改。
- 角色可启用、停用和逻辑删除；拥有 `system_authorization_management` 的角色不可删除，其他角色删除时撤销关联关系并保留审计历史。停用或已删除角色不贡献有效权限。
- `replaceRoleResources` 和 `replaceSubjectRoles` 是全量替换，不支持成员直接资源授权。
- 可以通过精确 subject 标识预配置尚未登录的成员；首次认证时 JIT 补充展示信息，但不得覆盖已有角色关系。
- 所有写操作携带 `expectedRevision`；服务在事务中校验 revision、计算变更后有效权限、执行防锁死检查、写关系与审计并递增 revision。
- 并发 revision 冲突不做自动合并，返回可重新加载的冲突结果。
- 权限表、数据访问和初始化扩展点由既有后端 `auth` 分支提供；步骤 6 只生成并确认权限约束 DAG，步骤 7 只将已确认权限事实落地到现有业务 Build Task 和模板运行时，不创建第二套权限存储或内存模拟持久化。

### 初始管理员与防锁死

- RequirementSpec 必须恰有一个 `isInitialAdminRole=true` 的角色，且该角色同时 `isSystemRole=true`；其他角色可以是系统角色，但不能再标记为初始管理员角色。
- 若用户选择复用业务管理员角色，首次初始化沿用其角色种子；若用户明确不合并，首次初始化创建独立系统管理员角色。两种情况都不得根据角色名称自动判断。
- 将 `system_authorization_management` 绑定给所选角色，再把 `initialAdministratorSubjects` 作为该角色的默认成员；复用业务角色时还保留其明确的业务资源，不能自动获得全部业务资源。
- 两个系统属性只记录角色来源和系统归属，不产生隐式权限；最终裁决始终只读取角色资源关系。
- 初始管理员角色后续可以改名、调整资源或停用；`initialAdministratorSubjects` 也可以通过成员角色配置调整，不要求这些成员或该初始角色永久承担管理职责。
- 每次管理写事务完成后，系统必须仍至少存在一个活跃成员，通过一个或多个启用角色的资源并集拥有 `system_authorization_management`；该成员和角色不必是初始种子。
- 移除自己的管理权限、停用最后有效管理角色、删除最后有效管理员成员、删除最后有效成员关系或撤销最后一组系统资源授权时，整笔事务拒绝。
- 管理员种子只在首次初始化生效；后续 Build 或启动不得重新覆盖运行态角色与成员配置。

## 分阶段实施与启动验收

权限体系只分为两个顶层实施阶段。第一阶段先跑通页面、操作和系统权限的完整流程；第二阶段再单独实施数据权限。第一阶段内部按以下编号步骤顺序实施，每一步都是必须单独启动、单独提交证据并等待用户验收的硬门禁，未得到用户明确确认不得开始下一步。

### 第一阶段：页面、操作与系统权限完整闭环

阶段目标：从新建应用权限配置开始，依次完成需求文档联合确认、UiDesign 边界、TechnicalPlan 权限编译、模板初始化、Build 运行时、生成应用启动和运行态权限管理，使页面、操作与固定系统资源可以在真实生成应用中完成端到端裁决。

阶段范围：

- 业务资源只包含 `page` 和 `operation`，平台固定注入唯一 `system_authorization_management`。
- 不实现 `data` 资源、Data Policy、Relation、Source Binding、数据过滤器或对象/创建约束。
- 明确的数据授权需求必须产生 `DATA_AUTHORIZATION_NOT_SUPPORTED` 并阻止需求文档联合确认；不得静默忽略、降级或提前实现第二阶段能力。
- 每一步均需完成该步骤列出的自动化验证，并实际启动受影响项目供用户验收；自动化测试通过不能替代用户启动验收。

第一阶段执行顺序固定为：

```text
步骤 1 应用权限配置
→ 步骤 2 RequirementSpec 权限语义
→ 步骤 3 ProductPlan 与 UiDesign 边界
→ 步骤 4 TechnicalPlan 权限编译
→ 步骤 5 XCodeAgent 模板分支获取
→ 步骤 6 Build DAG 权限事实投影与最终校验
→ 步骤 7 Build 执行阶段权限代码落地与验收
→ 步骤 8 失效、目录协调与完整回归
```

#### 步骤 1：原子修正应用权限配置

主要入口：`Frontend/src/renderer/src/typings/application.ts`、新建应用表单与默认值、规划请求、`Frontend/src/main/managedWorkspace.ts` 及相关测试。

改造内容：

- 将应用配置原子切换为 schema v5。
- 删除授权 `ApplicationAuthorizationProviderMode`、`providerMode`、外部授权选项和运行态页面开关。
- 启用权限时确定性启用认证和 `/roles`，校验数据库应用与管理员种子。
- 规划请求只携带权限开关和初始管理员，不再携带 provider 或页面开关。
- 增加权限关闭但需求要求权限时的前置配置澄清和原子持久化。
- 只删除授权 provider 逻辑，不修改业务数据源 `external_api`。

启动验收：

- 表单中没有 provider 和运行态页面选项。
- 权限开启后不能关闭认证，静态应用不能启用权限。
- 权限开启时至少存在一个真实管理员 `subjectId`，空值和 `current-user` 占位符均拒绝保存。
- 新工作区只写入 v5 当前字段，旧授权字段被拒绝。
- 配置冲突选择启用后，`application.json` 持久化成功才继续 RequirementSpec。
- 前端相关测试、`pnpm build`、后端 `/health` 和桌面开发态通过。
- 在桌面端实际完成权限关闭、权限开启和“配置关闭但需求要求权限”三条新建路径；模型提交 application.json 结果和操作入口后暂停，由用户确认步骤 1 达到预期。

#### 步骤 2：RequirementSpec 生成并确认权限逻辑

主要入口：需求分析器、`Backend/app/services/requirement_spec.py`、requirements node、Markdown 同步、RequirementSpec 权限摘要与编辑器。

步骤状态：部分实施。既有权限候选、稳定 `ruleId`、Markdown 同步和确认门禁基线已经落地；V1 当前契约收敛仍需在本步骤完成并独立验收。

已实施结果：

- 需求分析器已经具备提取权限候选的基线能力，服务端不采信模型传入的陌生 `ruleId`，以语义匹配保留已有 ID 或生成 UUID。
- Markdown 同步拒绝未知或重复隐藏标记；结构化编辑新增候选会记录 RequirementSpec 确认修改来源。
- 草稿保存与正式确认门禁已分离；固定 `/roles` 只作为系统页面说明展示。

本步骤待实施工作：

- 只保留 `restrictedPages`、`restrictedOperations` 两类正式权限候选，删除旧 `dataRules/dataRuleKey/includes/excludes` 及相关 Markdown/UI/提示词/测试路径。
- 需求模型发现明确数据授权要求时输出 `AuthorizationCapabilityIssue(code=DATA_AUTHORIZATION_NOT_SUPPORTED)`，由 requirement-document node 阻止联合确认；不得把旧 data candidate 静默丢弃后继续。
- 删除旧页面/操作行为字段，增加两类规则的 `defaultGrantedRoleIds`、角色系统属性和初始管理员角色选择。
- `user_roles[]` 增加并校验 `isSystemRole/isInitialAdminRole`，权限开启时必须确认唯一初始管理员角色；支持复用业务管理员角色或新建独立系统管理员角色，不得根据角色名称自动判断。
- 页面/操作规则的 `defaultGrantedRoleIds` 必须非空且全部引用有效 `user_roles[].id`；缺失角色授权时通过现有 AG-UI 澄清，不得猜测或补默认角色。
- 每条 `restrictedPages` 必须在需求文档确认前得到 `targetPageId`，且该值只能引用同一 RequirementSpec 的 `pages[].pageId`；权限事实提取读取页面目录后选择目标，页面名称只用于展示，不能参与 ProductPlan 映射。
- 同步更新 RequirementSpec JSON/Markdown、结构化编辑器、摘要、确认门禁、提示词和测试；确认 Markdown 修改时保留隐藏 `ruleId`、可见的 `targetPageId`、角色引用和来源追踪。

启动验收：

- 启动 XCodeAgent 前后端和 Electron 桌面应用，分别创建权限关闭、权限开启但无业务权限、仅页面权限、仅操作权限四类需求，RequirementSpec 草稿、Markdown、结构化编辑器和确认摘要保持一致。
- 页面/操作规则新增、编辑、删除后，隐藏 `ruleId`、`sourceRefs` 和 `defaultGrantedRoleIds` 正确保留或重新生成；每条受控页面的 `targetPageId` 必须始终指向当前页面清单，未知、重复或被篡改的隐藏标记阻止确认。
- 未明确默认授权角色时只产生对应权限候选的稳定澄清问题；回答后重新生成草稿，并且用户可以在桌面端完成 RequirementSpec/ProductPlan 联合确认。
- 明确数据授权需求稳定显示 `DATA_AUTHORIZATION_NOT_SUPPORTED` 并阻止联合确认；普通固定“我的数据”业务查询可以继续，语义不明确时只询问“业务查询还是授权边界”。
- 固定 `/roles` 只作为系统能力说明出现，不进入业务页面候选；页面/操作未授权行为字段和旧数据权限字段不能通过 JSON、Markdown 或编辑器重新写入。
- 相关后端测试、前端测试与 `pnpm build`、后端 `/health` 和桌面启动均通过；模型提交测试证据和实际操作路径后暂停，由用户确认步骤 2 达到预期。

#### 步骤 3：固化 ProductPlan 和 UiDesign 边界

主要入口：ProductPlan 生成/校验、ProductPlan Markdown、UI 设计生成池和确认面板。

步骤状态：部分实施。`product-plan.v5` 和业务页面/action 映射基线已经落地；操作目标闭合、联合确认门禁和资源键碰撞校验仍需在本步骤完成并独立验收。

- 页面候选通过已确认的 `targetPageId` 映射稳定业务页面，绝不按页面展示名称猜测；操作候选只映射稳定顶层 action；sequence 的 `stepId` 不进入权限目标。
- ProductPlan 不保存角色、角色关系、资源键、策略键或固定权限管理页。
- 没有对应 RequirementSpec 规则的页面/action 不得新增权限语义。
- UI 设计只处理业务页面；`/roles` 不产生 UI 设计任务，UI skip 也不影响固定页面。
- ProductPlan 权限目标固定为 `pageRules[{ruleId,pageId}]` 和 `operationRules[{ruleId,pageId,actionId}]`；不得复制角色、Capability Issue 或 step 权限目标。
- ProductPlan 在需求文档节点消费已校验 RequirementSpec 草稿，并与其通过同一次 `requirement_document_confirmation` 联合确认；正式 ProductPlan 的 `requirement_spec_sha256` 必须匹配同轮确认的 RequirementSpec。
- 联合确认前构造全部受控 `pageId`、全部受控 `<pageId>_<actionId>` 和 `system_authorization_management` 候选并执行全局唯一校验；冲突必须精确回灌修复，不能追加类型前缀、数字或随机后缀。
- 清除 ProductPlan planner、Markdown、UiDesign 输入和相关测试中的旧角色示例、点号资源前缀、PageKey 权限字段、operationKey 和 step 权限字段。

已有实施结果：`product-plan.v5` 不再持久化 `user_roles` 或页面 `allowed_roles`，当前服务以 `restrictedPages[].targetPageId` 直接映射页面，并将 `restrictedOperations` 映射为顶层 action。页面展示名称变化不会影响权限目标；目标被删除、映射被篡改、联合确认哈希不匹配或资源候选发生碰撞都会阻止确认；映射不包含角色、资源键、策略键或 `stepId`，也不会在 ProductPlan Markdown 或 UiDesign 中展示为角色判断。

启动验收：

- 页面/操作候选均为空时 ProductPlan 不新增业务授权引用。
- 只存在页面或操作单一候选类型时，不扩展到其他页面或操作。
- 修改目标稳定 ID 导致规则无法映射时不能确认。
- `authorizationTargets.operationRules` 必须完整保存 `{ruleId,pageId,actionId}`；sequence 的 `stepId` 不进入任何权限目标或资源候选。
- 相同 `actionId` 位于不同页面时保持不同操作目标；页面、`<pageId>_<actionId>` 与系统资源候选发生碰撞时阻止联合确认。
- `ui-designs.json` 中永远没有 `system_authorization_management`。
- 启动 XCodeAgent 前后端和 Electron 桌面应用，实际完成 RequirementSpec/ProductPlan 联合确认以及 UiDesign 生成或 skip；模型提交产物路径和操作入口后暂停，由用户确认步骤 3 达到预期。

#### 步骤 4：TechnicalPlan 确定性编译权限资源与 Endpoint 授权逻辑

主要入口：TechnicalPlan planner、planning node、project plan service、plan documents、PageImplementationContract 和 API contract。

步骤状态：未实施。当前工作区中即使已有部分试验性 TechnicalPlan 代码或旧字段，也不得视为本步骤完成；实施时按当前 V1 能力契约直接替换，不增加旧数据权限兼容读取或双写。

##### 步骤 4A：复核已确认上游当前契约

子步骤状态：未实施。步骤 4 只消费已经通过用户验收的步骤 2、3 正式产物，不在 TechnicalPlan 阶段回写或修复 RequirementSpec/ProductPlan。

- 读取同轮确认的 RequirementSpec、ProductPlan、UiDesign 正式产物或已确认的 UI skip 状态，以及应用权限配置；校验必需文件存在、确认状态有效且 `requirement_spec_sha256` 匹配。
- RequirementSpec 必须只包含页面/操作权限、有效 `defaultGrantedRoleIds`、唯一初始管理员角色和完整来源追踪；任何旧行为字段、data rule 或未解决 Capability Issue 均拒绝进入 TechnicalPlan。
- ProductPlan 必须只包含 `pageRules[{ruleId,pageId}]` 与 `operationRules[{ruleId,pageId,actionId}]`；任何角色字段、资源键、step 权限目标或固定权限管理页均拒绝进入 TechnicalPlan。
- 重新校验角色 ID、`pageId/actionId` 的 `lower_snake_case` 和唯一性，并复核页面、`<pageId>_<actionId>` 与系统资源候选的全局唯一性，防止已确认文件篡改或过期输入绕过联合确认门禁。
- 上游不合法时使 TechnicalPlan 草稿及其下游失效，并返回对应正式修订入口；不得在步骤 4 静默删除字段、追加后缀或代替用户重新确认上游。

4A 验收：

- 权限关闭、权限开启但没有业务候选、仅页面、仅操作和页面+操作五类已确认输入均可通过复核；没有业务候选时仍要求有效的唯一初始系统管理员角色，但不生成业务资源候选。
- 缺失或无效 `defaultGrantedRoleIds`、未知角色引用、多个初始管理员、`isInitialAdminRole=true && isSystemRole=false` 等上游错误均使 TechnicalPlan 停止，并返回 RequirementSpec 正式修订入口，不在步骤 4 自动修复。
- 未解决的 `DATA_AUTHORIZATION_NOT_SUPPORTED`、旧 `dataRules/dataRuleKey/policyKey` 或任何数据权限字段均使 TechnicalPlan 停止，并要求重新确认需求文档。
- RequirementSpec/ProductPlan 哈希不匹配、目标缺失、映射被篡改、CamelCase、kebab-case、重复 ID 或页面/操作/系统资源候选碰撞均在调用 TechnicalPlan 模型前被拒绝。
- ProductPlan sequence 可以保留产品 `stepId`，但 `authorizationTargets` 出现 `stepId`、step 资源或缺失 `pageId` 的 operation rule 时阻止 TechnicalPlan。
- 步骤 2 和步骤 3 的现有正向、负向、Markdown 编辑同步及确认门禁测试全部通过。

##### 步骤 4B：限制模型输出并执行归一化前校验

- TechnicalPlan 模型不负责生成业务权限资源键；页面和顶层 action 绑定直接消费 ProductPlan 的稳定映射，Endpoint 只输出/消费确定性的 action-to-endpoint 关系和必要技术事实。
- 在任何确定性归一化、字段删除或默认补全之前校验原始模型输出；出现模型手写 `resourceKey`、角色授权、点号前缀、角色型 `authentication`、`dataRules`、`dataPolicyBindings`、`policyKey`、数据授权 DSL 或未声明字段时直接进入现有自动修复/失败路径，不能通过归一化掩盖漂移。
- `api_contracts.py` 是 API `authentication` 唯一规范，所有 API Contract 和 Endpoint 的认证字段都只能是 `{required: boolean}`；planner、service、Markdown 和测试不得维护第二套角色认证结构。
- 删除 `permission_model`、`authentication.roles` 和角色型 `permissionBindings`；操作资源绑定只能进入 manifest 的 `bindings.endpoints`。
- 模型输出 `stepId` 权限绑定、step 资源、SQL、Java、数据库字段或任意可执行授权 DSL 时，在归一化前拒绝。

4B 验收：

- 原始模型输出 `authentication.roles`、字符串 authentication、手写资源键、step 权限、data policy 字段、可执行策略或未知授权字段时，在归一化前得到明确校验错误。
- 合法 `{required:boolean}` 不被自动修复改写；API Contract 与 Endpoint 使用同一验证器和错误文案来源。
- 代码搜索只存在 `api_contracts.py` 的认证字段定义，planner 中不存在旧角色认证示例和 V1 数据权限生成示例。

##### 步骤 4C：编译 `authorization-manifest.v2`

- RBAC 关闭时输出 `enabled=false`、空资源/绑定/默认授权；RBAC 开启时确定性注入唯一 `type=system` 的 `system_authorization_management`。
- 页面规则编译为 `resourceKey=pageId`，操作规则编译为 `resourceKey=<pageId>_<actionId>`；所有 key 必须为 `lower_snake_case`，页面、操作和系统组成的完整资源目录必须全局唯一。
- 多条规则指向同一页面或顶层 action 时复用资源并聚合去重 `sourceRuleIds`；同一 action 的直接业务 Endpoint 和 sequence 中全部 business step Endpoint 绑定同一父 action 资源，不生成 step 资源。
- 对每个 Endpoint 汇总全部 business action 引用并校验操作权限属性一致性：全部未受控时输出空 `operationResourceKeys`；全部受控时聚合去重多个 `operationResourceKeys` 并固定采用 ANY-OF；受控与未受控混用时以 `ENDPOINT_AUTHORIZATION_MIXED_CONTROL` 阻止 manifest 编译并要求拆分 Endpoint，禁止通过调用来源字段选择授权分支。
- 若一个 Endpoint 同时执行不可分离的多项独立受控能力，校验必须要求拆分 Endpoint。
- manifest 不允许 `type=data`、`dataRules`、`dataPolicyBindings`、`policyKey` 或其他数据权限字段；发现即阻止 TechnicalPlan 确认。
- `/roles` 与每个管理 Endpoint 统一绑定 `system_authorization_management`；状态接口和当前成员有效权限接口不绑定管理资源。
- 确定性编译 `defaultRoleAuthorization.roles`、`roleResourceGrants` 和 `initialAdminRoleSeedKey`。角色 seed key 复用 `user_roles[].id`，业务授权只来自 `defaultGrantedRoleIds`，初始管理员角色额外且仅固定获得该唯一系统资源。
- fingerprint 覆盖规范化后的 resources、bindings 和 defaultRoleAuthorization；数组排序与去重规则固定，重复编译必须字节级稳定。
- PageImplementationContract 页面投影只使用 `pageId`，操作投影使用 `{targetType:"action",pageId,actionId,resourceKey}`，不保存 PageKey/pageKey 或 `stepId`。

4C 验收：

- 页面/操作业务候选均为空时只生成唯一系统资源和初始系统管理员的一项默认授权。
- 只存在页面或操作一种业务候选时只生成对应资源类型；未提及业务目标没有资源、绑定或守卫。
- 两个页面使用相同 `actionId` 时产生不同 `<pageId>_<actionId>`；任何页面、操作或系统键冲突均阻止 TechnicalPlan 确认。
- 任何 `type=data`、`dataRules`、`dataPolicyBindings`、`policyKey` 残留均阻止 TechnicalPlan 确认，不允许作为“未来预留字段”进入 V1 manifest。
- 全部引用均未受控的 Endpoint 必须保持 `operationResourceKeys=[]` 且不生成操作资源守卫；全部引用均受控的 Endpoint 才允许聚合操作资源。
- 受控 action 与未受控 action 引用同一 Endpoint 时，必须产生 `ENDPOINT_AUTHORIZATION_MIXED_CONTROL` 并阻止 TechnicalPlan 确认；拆分 Endpoint 后方可继续编译，且不得通过前端参数、请求头或其他调用来源字段规避该校验。
- 一个 Endpoint 绑定多个受控操作资源时，成员拥有任意一个绑定资源即可通过；一个都没有时返回 403，资源来自不同角色时仍按角色资源并集通过。
- sequence 的多个 business step Endpoint 继承父 action 资源，interface/navigation/external step 不生成资源。
- 相同确认输入重复编译得到相同 manifest 和 fingerprint；只改文案但保留稳定 key/目标时资源键不变，语义变更仍触发重新确认。
- 复用业务管理员时该角色获得唯一系统资源和明确业务资源；拆分角色时独立管理员默认只有该系统资源。
- 无来源、规则漏覆盖、未知目标、key 冲突、未知角色引用、多个初始管理员或模型越权字段均阻止 TechnicalPlan 确认。

##### 步骤 4D：TechnicalPlan 文档整合与步骤交付

- TechnicalPlan JSON 只新增根字段 `authorization_manifest`，结构严格使用本文 `authorization-manifest.v2`，不在 API Contract、页面或实体中复制另一份资源目录或角色矩阵。
- TechnicalPlan Markdown 分别展示页面资源、顶层 action 操作资源、Endpoint ANY-OF 绑定和默认角色授权表；显示业务名称、稳定 key、目标和来源规则，禁止只输出 UUID 或难以理解的点号路径。
- V2 数据权限只存在于本文“V2 数据权限演进预留”设计章节，不进入 TechnicalPlan V1 JSON/Markdown 或生成项目。
- 步骤 4E 是 TechnicalPlan 确认前的权限语义与可交付性最终门禁：确认前必须完成 manifest、资源/角色/Endpoint 绑定、ANY-OF、mixed-control、数据权限阻断、fingerprint 和 PageImplementationContract 权限投影的确定性校验；Build 只检查正式产物的新鲜度、完整性和任务覆盖，不重新解释或修复权限设计。
- RequirementSpec、ProductPlan、UiDesign、TechnicalPlan 的重新确认和下游失效遵守现有正式文档确认门禁；自动修复后的 TechnicalPlan 仍必须重新确认。
- 更新 `docs/CODEBASE_INDEX.md` 中发生变化的契约、服务和文档边界，并记录步骤 4 的实际测试与启动结果；全部验收通过后才能把本步骤状态改为“已实施”。

步骤 4 完整启动验收：

- 后端聚焦测试、修改文件 `py_compile`、前端 `pnpm build`、后端 `/health` 和桌面开发态均通过。
- 生成并确认至少四组 TechnicalPlan：无业务权限、仅页面、仅操作、页面+操作并合并/拆分系统管理员。
- 额外验证至少三组数据权限描述稳定被 `DATA_AUTHORIZATION_NOT_SUPPORTED` 阻断，且没有产生 TechnicalPlan。
- TechnicalPlan JSON、Markdown、PageImplementationContract 和 API Contract 对同一资源/目标的引用一致，不存在旧 `business.*`、`system.authorization.*`、PageKey、step 权限、data policy 或第二份权限详设残留。
- 启动 XCodeAgent 前后端和 Electron 桌面应用，实际生成并确认至少一组包含页面、操作和系统管理员的 TechnicalPlan；模型提交 manifest、Markdown 和确认入口后暂停，由用户确认步骤 4 达到预期。

##### 步骤 4E：权限语义与可交付性最终门禁

在 TechnicalPlan 用户确认前，基于当前草稿、已确认 RequirementSpec/ProductPlan/UiDesign 和 PageImplementationContract 运行单一确定性验证器。验证器只产生门禁结果与自动修复输入，不生成成功报告页面、不向正常确认载荷注入权限校验明细，也不持久化第二份权限事实；任一阻断项失败时不得将 TechnicalPlan 标记为 `confirmed`，而是进入现有自动修复或正式修订路径。自动修复耗尽后，既有 `technical_plan_generation_error` 页面只展示失败检查项及其具体引用。

| 检查项 | 必须结果 |
| --- | --- |
| ResourceKey 是否存在 | 每个 page/action/endpoint binding 的资源键均存在于 manifest 资源目录。 |
| Page binding 是否有效 | 指向已确认 ProductPlan 的页面。 |
| Action binding 是否有效 | 指向该页面已确认的顶层 action。 |
| Endpoint binding 是否有效 | 指向当前 API Contract 的存在 Endpoint。 |
| Action → Endpoint → Resource 是否闭环 | 受控 action 的全部直接业务 Endpoint 和 sequence business step Endpoint 均包含该 action 资源；Endpoint 中每个资源也可反向追踪到 action。 |
| 受控能力是否可交付后端 guard | 每个非空 `operationResourceKeys` 都能编译为唯一 Controller Task 的 ANY-OF 约束；此处只校验计划覆盖，不读取尚未生成的代码。 |
| Endpoint 是否混用受控/未受控 action | 不允许混用；出现即以 `ENDPOINT_AUTHORIZATION_MIXED_CONTROL` 阻断确认。 |
| Initial Admin 是否拥有 system resource | 初始管理员角色的默认授权必须包含 `system_authorization_management`。 |

- 页面权限和 Endpoint 权限是独立维度：受控 Page 不自动向其调用的 Endpoint 传播资源键；未显式绑定的 Endpoint 默认可访问。受控 Page 调用未绑定的 `asset_api.list` 一类 Endpoint 时，只作为内部通过说明，不在正常确认页展示，也不是警告或阻断。
- 必须继续拒绝未知资源、重复或跨类型资源键、无效 page/action/endpoint 引用、双向闭环遗漏、V2 数据权限字段和 fingerprint 漂移。
- “Backend 是否真正有 guard”不属于步骤 4E：步骤 5/6 前代码尚未生成；步骤 7 EDD 必须根据这里确认的非空 Endpoint binding 验证真实 Controller 在业务逻辑前执行对应的 ANY-OF guard。

4E 验收：

- 覆盖有效闭环、未知资源、无效 Page/Action/Endpoint、Action→Endpoint 正向遗漏、Endpoint→Action 反向遗漏、mixed-control、初始管理员缺系统资源、V2 字段和 fingerprint 漂移。
- 验证受控 Page 使用未显式授权 Endpoint 时可确认，且该 Endpoint 的 `operationResourceKeys=[]`；不得将页面资源自动写入 Endpoint binding。
- 验证成功时确认卡不展示 4E 校验报告；验证失败时 `technical_plan_generation_error` 页面展示失败检查项及其具体 page/action/endpoint/resourceKey 引用，且不存在可编辑的第二份权限 JSON。

##### 步骤 4F：TechnicalPlanDocPanel 权限设计视图

在 `TechnicalPlanDocPanel` 现有“架构 / 实体 / API 契约 / 页面绑定”之后新增固定 Tab“权限”。该 Tab 只读取当前 TechnicalPlan 的 `authorization_manifest`，按首次初始化的默认角色授权展示权限模型；不读取 4E 校验报告，不新增后端接口、AG-UI 字段或第二份持久化权限事实。

固定实现边界：

- 扩展内部 `SectionKey` 增加 `authorization`，Tab 固定排在“页面绑定”之后；默认选中 Tab 仍为“API 契约”。
- 新建独立权限展示组件及纯数据投影函数，不继续扩展 `TechnicalPlanDocSections.tsx`；由 `defaultRoleAuthorization.roles` 作为角色全集，使用 `roleResourceGrants` 关联 `resources`，并由 `bindings.endpoints[].operationResourceKeys` 建立操作资源到 Endpoint 的反向索引。
- 采用“角色卡片 + 展开明细”视图。角色卡展示名称、seed key、描述、系统角色/初始管理员标记和默认资源数量；展开内容按 `system → page → operation` 分组，展示资源名称、`resourceKey`、目标和来源规则。
- 操作资源显示其关联 Endpoint；同一 Endpoint 绑定多个资源时明确显示 ANY-OF，且只说明该角色拥有的资源，不将 Endpoint 未绑定或页面资源自动推导为角色资源。
- 角色未获任何默认资源时仍展示为空授权角色；权限关闭时不显示这个tab。异常或未知资源引用显示“引用未解析”，不得令草稿查看崩溃；正式确认仍由步骤 4E 阻断。
- Tab 顶部明确说明页面权限与 Endpoint 权限相互独立，未显式授权的 Endpoint 按当前契约默认可访问；本视图仅表达默认角色授权，不表达成员级运行时有效权限。
- 样式只使用 DocPanel 既有 `--wb-*` 主题变量，覆盖明暗主题、窄面板换行、键盘焦点和资源明细展开；不得改造 `TechnicalPlanSummary`。
- 同步在 TechnicalPlan 文档展示说明中记录该 Tab 的位置与职责；不改变 `authorization_manifest`、fingerprint 或角色授权关系。

4F 验收：

- 覆盖权限关闭、无默认资源角色、初始管理员系统资源、页面/操作资源分组、多个角色共享资源、多个操作资源绑定同一 Endpoint 的 ANY-OF，以及未知引用的稳定展示。
- 验证受控页面不自动获得 Endpoint 权限，未绑定 Endpoint 不会写入任何角色资源。
- 运行新增前端聚焦测试与 `pnpm build`，并在 Electron 应用中验证 Tab 切换、角色展开/收起、空态、长名称/长 key 以及明暗主题。

#### 步骤 5：工程初始化接入模板权限基础

前后端模板的权限能力已分别存在于对应模板仓库的 `auth` 分支。本步骤只改造 XCodeAgent：根据已持久化的权限开关成对获取既有分支；`authorization.enabled=false` 时均使用 `main`，`authorization.enabled=true` 时均使用 `auth`。不允许混用分支或由调用方指定任意分支。

步骤状态：待用户启动验收。XCodeAgent 已完成确定性分支选择、成对浅克隆和来源记录；须按本步骤独立验收创建启用/关闭权限的两个真实项目并由用户确认后，才能进入步骤 6。

##### 既有后端模板分支公约（不属于本步骤交付）

模板仓库：<https://github.com/Hupy2118/springboot-template.git>

分支边界：权限契约、Controller 骨架、OpenAPI、`CurrentSubjectProvider` 和未就绪实现只存在于 `auth` 分支；`main` 分支不得包含运行态权限 API、OpenAPI 或权限运行时骨架。

既有分支公约：

- 在 `src/main/java/com/cmbchina/backend/authorization/` 增加 Controller、DTO、应用端口、领域类型、异常映射、就绪状态和 `CurrentSubjectProvider`。
- 在 `src/main/resources/openapi/authorization-api.v1.yaml` 提交与本仓库 `contracts/authorization-api.v1.yaml` 字节一致的副本；本地契约文件才是唯一事实源。
- 后端权限骨架只识别 `system_authorization_management` 一个 `type=system` 资源：除状态与当前成员权限查询外，资源目录、角色、角色资源、成员和审计接口均由该资源守卫；不得恢复页面资源与管理操作资源的双资源模型。
- 若模板尚未接入 Spring Security，本工作包只增加由 Spring Boot BOM 管理的 `spring-security-core` 编译依赖，用于 `SecurityContextHolder`/`Authentication` 类型；不得引入会默认保护全站的 starter 或伪造认证 Filter。实际一号通认证和 Web Security 配置由步骤 7 接入。
- `auth` 分支不保留 `xcodeagent.authorization.enabled` 运行时开关；公开 `/api/authorization/status` 和管理接口骨架始终注册。默认就绪状态为 `ready=false`；状态接口返回 200，其他接口统一返回结构化 503 `authorization_not_ready`。
- `CurrentSubjectProvider` 固定读取 Spring Security `Authentication.getName()`，但本工作包不伪造身份、不信任前端 subject，也不复制一号通认证协议。
- `auth` 分支应提供权限表、数据访问和固定授权 Bootstrap 脚本。模板下载完成后，该脚本负责创建所需权限表，并将已确认 TechnicalPlan 中的资源、角色、角色资源关系、初始管理员成员及成员角色关系写入数据库；脚本采用“缺失则新增、同键同内容跳过、同键冲突失败、永不删除”的幂等策略。脚本、DDL、数据库写入和初始化器实现均属于后端模板交付，不属于 XCodeAgent 项目实施范围；本文只记录其输入、结果和与 Build 的边界。
- 保持 Spring Boot 2.7 和 Java 8 兼容，所有新增或实质修改的方法按模板工程规范添加中文用途注释。

##### 既有前端模板分支公约（不属于本步骤交付）

模板仓库：<https://github.com/ruyue1/frontend-template.git>

分支边界：`AuthorizationManagementPage`、`src/authorization/` 和权限 service 封装只存在于 `auth` 分支；`main` 分支不得注册 `/roles`、权限菜单、Provider 或权限网络请求。

既有分支公约：

- 用完整页面替换现有 `src/pages/System/Role/index.tsx` 占位实现，固定页面目录为 `src/pages/System/AuthorizationManagementPage/`。
- 增加 `src/authorization/`，包含由本地 OpenAPI 生成的 TypeScript 类型、`authorizationApi.ts`、AuthProvider、RouteGuard、Permission、菜单/路由/操作守卫及统一错误状态处理；禁止生成独立 Axios 客户端。
- `authorizationApi.ts` 必须从模板现有 `src/apis/service.ts` 导入 `service`，按 OpenAPI operationId 封装所有权限请求；页面和 Provider 只能调用这些封装函数，不能直接调用 axios、fetch 或拼装第二套请求。

  ```ts
  export const getMyEffectivePermissions = () =>
    service.get<EffectivePermissions>(
      "/api/authorization/me/effective-permissions",
    );
  ```

- GET 列表参数统一通过 `{ params }` 传递；POST/PUT 直接传请求 DTO；带 `expectedRevision` 的 DELETE 使用 `service.delete(path, { data: request })`，不得改成查询参数。
- `AuthProvider` 只以 `can("system_authorization_management")` 守卫 `/roles`、权限菜单及管理页面全部操作；`RouteGuard` 用于页面资源点，`Permission` 统一用于操作资源点并支持 `hidden`/`disabled`；前端不得为管理页面、角色读写或成员操作派生额外资源键。
- `auth` 分支始终将 `/roles` 注册为 layout 下的独立系统路由和系统菜单，不把它放入业务 `/page` 菜单树或业务页面生成逻辑；不增加前端权限开关配置。
- `AuthProvider` 每次挂载时都通过 `authorizationApi.ts` 以 ahooks `useRequest` 请求当前成员资源点。请求完成前保持 loading 并按无权限处理；成功后仅将本次响应的 `resourceKeys` 放入当前 Provider 的 React 状态用于渲染，不写入 localStorage、sessionStorage、IndexedDB、Electron 存储、模块全局变量或 service 单例。页面刷新或 Provider 重新挂载必须重新请求接口；401 清空当前状态并走登录处理，503 直接展示权限运行时未就绪状态，其他错误进入统一错误态。
- 页面完整提供只读资源目录、角色创建/修改/启停/删除、角色资源全量替换、成员列表与预配置/移除、成员角色全量替换、有效权限和审计查询。
- `/roles` 路由、菜单和页面内所有管理请求统一使用 `system_authorization_management`；前端不得将入口隐藏视为后端鉴权的替代。
- 页面必须覆盖 loading、empty、error、401、403、409、503，以及明暗主题下的背景、文字、边框、hover/focus、弹层和禁用状态。
- 增加 `authorization:types` 脚本，使用 lockfile 固定版本的 `openapi-typescript` 只生成类型到 `src/authorization/generated/authorizationApiTypes.ts`。

##### XCodeAgent 实施边界

主要入口：application lifecycle、template generation、Electron 模板下载、对应 AG-UI protocol 和 frontend service。

固定实现边界：

- 从已持久化的 `application.json.authorization.enabled` 确定唯一 `templateBranch`：关闭为 `main`，开启为 `auth`；前后端必须使用同一分支，并通过 `git clone --branch <templateBranch> --single-branch --depth 1` 浅克隆到生成项目的 `frontend/` 和 `backend/`。
- 分支选择不得来自前端自由输入或请求参数。用户自定义模板仓库 URL 可以保留；目标分支不存在或获取失败时初始化失败，不得回退到其他分支。
- `auth` 分支固有提供 `/roles` 与权限接口，`main` 分支不包含权限能力；XCodeAgent 不为分支写入前后端权限开关配置，也不修改、生成或校验模板内权限实现。
- 权限开启时，先完成后端模板 Bootstrap，再初始化业务页面并校验所有业务路由不得与 `/roles` 冲突；权限关闭时直接初始化业务页面。固定页面不读取 UiDesign，不创建普通 PageImplementationContract、业务 Endpoint 授权逻辑或业务开发任务。
- 沿用既有模板来源记录与复用规则，记录前后端仓库 URL、所选 `templateBranch` 及各自实际 commit SHA；已有模板目录的来源 URL、分支或 commit SHA 不匹配时 fail closed，不能覆盖或混用。步骤 5 不写 `.xcodeagent/authorization/` 权限基础产物，也不新增权限专属 manifest 字段。
- 权限开启时，模板下载完成后的授权 Bootstrap 属于后端模板初始化方案：它读取已确认 TechnicalPlan 的 `authorization_manifest` 和 `application.json.authorization.initialAdministratorSubjects`，完成建表与初始种子落库后才允许继续页面/菜单初始化。XCodeAgent 不实现该脚本、DDL、数据库写入或初始化器；仅在后续集成时消费模板侧提供的非敏感成功/失败结果。
- XCodeAgent 的规划、配置持久化、模板生成进度、成功和失败仍通过 AG-UI 完整生命周期传递；不能为该流程新增普通 JSON/REST 产品接口。

步骤产物：

```text
authorization.enabled=true：
  frontend/ 与 backend/ 均为各自模板仓库的 auth 分支检出内容
authorization.enabled=false：
  frontend/ 与 backend/ 均为各自模板仓库的 main 分支检出内容
```

独立验收：

- XCodeAgent 前后端和桌面开发态启动通过，并能分别创建 `auth` 分支的 RBAC 开启应用与 `main` 分支的关闭应用；前后端分支选择、仓库 URL 和 commit SHA 必须成对一致。
- 生成项目模板完成后前后端立即可启动；`main` 分支不存在权限接口和 `/roles`，`auth` 分支的 `/roles` 显示未就绪状态。
- 业务页面设计列表中不存在权限管理页；修改业务路由为 `/roles` 时 ProductPlan 路由校验阻止确认。
- `auth` 分支缺失、目标分支获取失败、前后端分支混用或已有目录来源不匹配时，模板初始化稳定失败并指出具体原因；不回退到其他分支。
- 模型提交两个真实生成项目的目录、模板来源记录、启动命令和访问入口后暂停，由用户确认步骤 5 达到预期。

#### 步骤 6：Build DAG 权限事实投影与最终校验

主要入口：TechnicalPlan 最终确认门禁、task preparer、tasks node、build context resolver、unit compiler、build task planner 和 task documents。

步骤状态：部分实施。当前 Overlay 已能按 Build Unit 编译只读权限事实；本步骤只负责生成、确认和校验 `build-dag.v3`，不派发前后端 Agent、不写生成应用源码，也不执行权限运行时验收。授权 Bootstrap 已前移至模板下载完成后的后端模板初始化方案；本步骤不建表、不写资源/角色/成员数据、不执行 Bootstrap 脚本，也不创建 `authorization:*` Unit。

##### 步骤 6A：授权初始化完成与 Build 前置门禁

- `prepare_build_tasks` 必须从工作区重新读取最新 RequirementSpec、ProductPlan、UiDesign、TechnicalPlan 和模板就绪状态，不信任 checkpoint 中的旧副本。
- 只接受 `artifact_type=technical-plan`、`confirmation_status=confirmed` 且当前 schema/fingerprint 有效的 TechnicalPlan；校验其上游哈希、应用 `authorization.enabled`、已选 `main/auth` 模板分支和 manifest `enabled` 状态一致。
- 同时校验当前范围所需的 PageImplementationContract、Endpoint 契约、EntitySourceBinding 和模板初始化门禁均已就绪。
- `authorization.enabled=true` 时，前后端必须都来自 `auth` 分支，并通过只读模板能力检查：前端已在应用入口挂载 `AuthProvider`、进入应用会获取当前成员资源点、已有 `RouteGuard` 和支持 `hidden/disabled` 的 `Permission`、业务 API 可复用 `src/apis/service.ts` 和 ahooks `useRequest`；后端已有 `RequireAnyResource` 注解和 `AuthConstants`。同时必须存在模板侧 Bootstrap 的非敏感成功结果，且其中 manifest fingerprint 与当前 TechnicalPlan 一致。Build 只能调用这些既有接口，不能复制或改写模板授权核心。
- `authorization.enabled=false` 时，前后端必须都来自 `main` 分支，不编译权限 Overlay，不生成路由、操作或 Endpoint 权限接入。
- 若模板侧 Bootstrap 缺失、失败、fingerprint 过期，或模板能力、正式产物、确认状态、schema/fingerprint、上游哈希或分支配对不一致，必须在叶子任务生成前失败并返回模板初始化或步骤 4 正式修订入口；Build 不重新运行 4E，不补全、修复或推断权限设计，也不执行、补建或修复模板 Bootstrap。

##### 步骤 6B：在叶子任务生成前权限约束投影

子步骤状态：已实施，待步骤 6 的完整启动验收。Overlay 只从已确认 `authorization_manifest` 按当前 Build Unit 投射页面、操作和 Endpoint 事实，不创建权限 Unit 或运行态授权数据。

```text
已确认 TechnicalPlan
→ Build 完整性门禁
→ Base Unit Skeleton（现有 Entity / Endpoint / Page）
→ Authorization Overlay Compiler
→ 页面/路由与 Endpoint 权限投影编译
→ 现有 Task Generator
→ task_compilation
→ dag_validation（权限规则作为最后校验）
→ 待确认 Final Build DAG
```

- Base Unit Skeleton 保持当前 `build-dag.v3` 的 Entity 实现阶段、`frontend:api-client`、`backend:endpoint:*` 和 `page:*` Unit；Overlay 只标注现有 Unit/BuildContext，不新增权限 Unit、边、Bootstrap Task 或独立授权任务。
- Overlay 在模型生成叶子任务之前，从 `authorization_manifest` 裁剪当前目标所需事实：页面的 `{pageId,resourceKey}`、所属顶层 action 的 `{pageId,actionId,resourceKey}`，以及 Endpoint 的 `{apiContractId,endpointId,operationResourceKeys,semantics:"ANY_OF"}`。
- `TargetBuildContext.authorization_constraints` 是平台拥有的只读运行时投影；Unit 编译器确定性写入 `task.source_refs.authorization`。模型不得输出、修改或推断同名权限字段；冲突或漂移候选必须拒绝并自动重新生成。
- `page:*` Unit 只接收本页和本页 action 的权限切片；`frontend:api-client` 只接收当前页面实际使用的 Endpoint 契约，不获得页面或操作权限判断职责；`backend:endpoint:*` Unit 只接收对应 Endpoint 的操作资源切片。各 Unit 的权限切片必须进入输入 fingerprint；角色、默认授权或初始管理员种子变化只使模板 Bootstrap 结果失效，不使无关 Page/Endpoint Task 重新生成。

##### 步骤 6C：现有 `dag_validation` 的权限规则

`dag_validation` 必须仍是 `model_planning → task_compilation → dag_validation → artifact_persistence` 中持久化前的最后一个校验步骤。`artifact_persistence` 只保存已经通过校验的 DAG，不新增权限校验阶段，也不先保存无效 DAG 再校验。

- 保持现有 DAG 拓扑、循环依赖、执行批次、交付物和通用任务边界校验逻辑不变；只向既有任务语义校验追加权限规则。
- 每个受控 action 必须落在所属 `page:*` Unit，并由唯一 `frontend.page` 实现任务消费；Action 不建立独立 Unit 或 Task。
- 每个非空 `operationResourceKeys` 必须由唯一 `backend.endpoint_controller` 任务覆盖；空数组不产生额外鉴权任务或守卫要求。
- 顶层 RouteGuard/AuthConstants 投影必须与当前 Unit、`task.source_refs.authorization` 和输入 fingerprint 一致；权限关闭时不得残留权限切片或投影。
- 拒绝独立权限 Unit、Bootstrap 权限任务、后置补鉴权 Task、第二份资源目录，以及将共享 Router、菜单、RouteGuard 投影、AuthConstants 或模板权限核心纳入任务写范围的候选。
- 权限校验错误合并进现有 `task_graph.validation.errors`，继续沿用当前自动再生成和失败路径，不提供权限专用的人工修正入口。

步骤 6 验收：

- 覆盖权限关闭、仅页面、仅 action、非空/空 Endpoint 绑定、多个资源 ANY-OF、同名 action 跨页面和受控/未受控 Endpoint 混用。
- 验证模型伪造权限字段、创建权限 Unit、遗漏 Page/Controller owner、修改共享权限核心文件时均由既有验证链拒绝并自动再生成。
- 验证通用 DAG 校验行为、持久化时机和用户确认协议未因权限规则改变。

#### 步骤 7：Build 执行阶段权限代码落地与验收

主要入口：前后端 Build Agent、Build/Testing subgraph、RepairPlanner、授权投影服务和 EDD。

步骤状态：未实施。步骤 7 只消费步骤 6 已确认的 DAG 权限事实；Agent 不得新增、修改、删除、补全或推断权限资源及绑定关系。

##### 步骤 7A：Build 入口与平台共享投影

- Build 只加载最新、已确认且已通过步骤 6 `dag_validation` 的 `build-task-plan.json`；执行阶段不再重新运行 DAG 语义校验或权限设计。
- DAG 确认后、任何叶子任务派发前，平台幂等写入 `auth` 模板声明的 RouteGuard 和 AuthConstants 托管区。写入必须验证模板分支、声明、目标文件和边界标记；失败时 fail closed，Agent 不得补写共享 Router 或 AuthConstants。
- 平台投影的源码变化单独记录为平台证据，不归属前后端 Agent；Retry/Repair 只能重复应用同一份确认投影。

##### 步骤 7B：前端业务权限接入

- 前端 Prompt/Skill 只读取 `task.source_refs.authorization.pages/actions`。每个受控顶层 action 使用模板 `Permission` 和精确 `resourceKey`；保留或补充稳定 `data-action-id` 以支持确定性验收。未受控 action 不增加包装。
- 默认使用 `hidden`；只有控件可靠支持禁用且保留可见性有明确交互价值时使用 `disabled`，无法确认时仍使用 `hidden`。
- 应用级能力完全复用模板 `AuthProvider`、`RouteGuard`、`Permission` 和当前成员资源请求；Page Task 不创建第二个 Provider、权限缓存、资源目录、角色管理或 `/roles` 页面。
- 页面路由权限由步骤 7A 平台 RouteGuard 投影负责。Page Task 只生成页面、领域 API 调用和本页 Action 包装，不得修改路由、菜单、页面占位、共享 Router 或模板权限核心。
- 业务请求继续通过 `src/apis/`、模板 `service` 和 `useRequest`；禁止页面或组件直接调用 `fetch`、`axios` 或 `service`。

##### 步骤 7C：后端 Endpoint 注解接入

- 后端执行任务包从 `task.source_refs.authorization` 投射只读 `implementation_contract.authorization_constraints`，包含 Endpoint 身份、精确 `operationResourceKeys`、ANY-OF 语义及平台给定 AuthConstants 符号。
- 非空资源集合仅允许在真实 Controller Endpoint 上添加或校正一个 `@RequireAnyResource`；多资源置于同一注解中，保持 ANY-OF。空集合不新增注解。
- Entity、Repository、Service 和外部 API Client 不实现权限判断；不得从请求参数、请求头、角色名或调用来源推断权限。
- 不得修改 AuthConstants、权限切面、权限表、权限管理 Controller、Bootstrap 或异常映射；符号、Endpoint 或模板注解无法唯一定位时任务失败，不得创建替代实现。

##### 步骤 7D：Repair 与 EDD

- Repair Task 原样继承父任务的 `source_refs.authorization`、Unit 和文件范围；模型返回的权限字段由平台覆盖。Repair 不得扩大资源、修改 ANY-OF、触碰共享投影或权限核心；需要改变权限事实时返回步骤 4 正式修订。
- 权限验收始终启用，不受业务自检开关影响：前端验证 Action/Permission/资源键/模式，后端验证 Controller 目标、唯一注解、常量集合和 ANY-OF。可归属任务的失败进入现有 Scheduler/Repair 闭环。
- Build 完成后执行纯只读 EDD，不得再次调用投影写入函数掩盖漂移。EDD 验证共享 RouteGuard、AuthConstants、单一 AuthProvider、精确 Permission、Controller 注解、空绑定无守卫，以及 Service/Repository 无操作权限或数据权限逻辑。
- 资源、角色、角色资源关系、管理员成员、成员角色关系、revision 和 manifest fingerprint 的初始化仍由模板 Bootstrap 负责；步骤 7 不生成初始化 SQL、seed 文件、脚本调用或第二套初始化器。

步骤 7 启动验收：

- 验证模板下载后的 Bootstrap 先于页面/菜单初始化完成：权限表、资源、角色、角色资源关系、初始管理员成员及成员角色关系均来自已确认 TechnicalPlan；同键同内容重试无重复写入、缺失项仅新增、同键冲突失败且不删除历史数据。该脚本和数据库初始化仅记录为后端模板方案，不在 XCodeAgent 项目实施范围。
- 权限关闭时不生成任何权限 Overlay、RouteGuard/Permission 接入、业务权限常量或 Endpoint 注解；仅页面、仅 action、页面+action 时只覆盖精确 Page/Action/Endpoint，不扩大到同页其他目标。
- 不同页面使用相同 `actionId` 时仍生成不同操作资源；`ENDPOINT_AUTHORIZATION_MIXED_CONTROL`、资源键冲突、V2 字段和未确认/过期 TechnicalPlan 均在进入 Task 生成前阻止，并回到步骤 4 而不是由 Build 修复。
- 验证无权限页面菜单隐藏、直接访问路由被 RouteGuard 拒绝；受控操作同时覆盖 `hidden` 和可可靠禁用控件的 `disabled`，且资源键不串页、不串 action。
- 验证所有业务接口均由 `src/apis/` 的函数复用 `service`，页面以 `useRequest` 调用；代码搜索不存在页面直接 `fetch`、`axios`、第二个 HTTP Client 或组件直接调用 `service`。
- 同一 Endpoint 绑定多个操作资源时，真实 Controller 只生成一个 `@RequireAnyResource` 并包含全部 `AuthConstants`，成员启用角色资源并集命中任一资源时继续；全部未受控的 Endpoint 不生成注解。
- Page Task、Controller Task 和 Repair Task 都接收精确权限切片；Entity、Repository、Service 中不存在操作权限手写判断、数据过滤或数据权限逻辑。
- 代码搜索不存在独立权限 DAG、后置补鉴权 Task、资源写接口、成员直接授权、角色继承、显式 deny、角色名分支和外部授权 provider。
- 验证角色创建/改名/启停/删除、角色资源和成员角色全量替换、revision 冲突、审计原子性、自我移权和最后管理员保护，且前端伪造 subjectId、未验证 Cookie、空 SecurityContext 和匿名 Authentication 均不能获得权限。
- 验证首次启动完成完整幂等初始化、相同 fingerprint 重启无重复写入、不同 fingerprint fail closed，以及 401/403/503 状态矩阵。
- 通过 `/api/projects/launch` 启动至少一个完成 Build 的 `auth` 生成应用，验证权限服务 `ready=true`、`/roles` 可访问、RouteGuard、Permission 和 Endpoint 注解均生效；`app:integration` 同时验证 Bootstrap fingerprint、当前 manifest 的资源/角色/关系已存在且同键不冲突。模型提交运行态地址和验证账号/前提说明后暂停，由用户确认步骤 7 达到预期。

#### 步骤 8：失效、目录协调和完整回归

主要入口：planning revision、planning persistence、application lifecycle、planning workflow 和各步骤测试。

失效规则：

- 新建配置只影响首次 RequirementSpec 和首次管理员 bootstrap；正式需求确认后，V1 业务权限以 RequirementSpec 为准。
- RequirementSpec 新增或删除页面/操作权限规则使 TechnicalPlan 和相关下游失效。
- RequirementSpec 新增或重新识别出数据授权需求时立即产生 `DATA_AUTHORIZATION_NOT_SUPPORTED`，阻止重新确认并使尚未执行的权限下游工作失效；不能继续沿用旧 TechnicalPlan 绕过门禁。
- 只修改规则文案且保留 `ruleId`/目标时资源键保持稳定，但权限语义变更仍重新确认 TechnicalPlan。
- ProductPlan `pageId/actionId` 变化使对应资源绑定和下游失效。
- TechnicalPlan resourceKey、Endpoint 操作授权逻辑或 manifest fingerprint 变化使相关实现契约、DAG 和测试失效。
- 部署协调器在事务中写入当前 manifest、删除已移除资源的角色关系并记录目录协调审计。
- 普通启动发现运行态资源投影与 manifest fingerprint 不一致时 fail closed，不自动猜测或兼容。
- 纯业务代码错误进入 SmallTask，不回退权限需求。

步骤 8 启动验收与完整测试样例：

- 不启用权限的数据库应用。
- 启用权限但没有业务候选的数据库应用。
- 仅页面、仅操作、页面+操作三种业务权限应用。
- 两个页面使用相同 `actionId` 但生成不同操作资源的应用，以及页面、操作和系统键跨类型冲突被联合确认门禁拒绝的场景。
- 同一 Endpoint 绑定多个操作资源的 ANY-OF 场景，包括单角色命中、多角色并集命中和完全未命中。
- 受控与未受控 action 复用同一 Endpoint 时被 `ENDPOINT_AUTHORIZATION_MIXED_CONTROL` 阻止的场景。
- “本人订单”“部门及下级部门”“项目成员数据范围”“不同角色看到不同记录”等明确数据授权场景全部产生 `DATA_AUTHORIZATION_NOT_SUPPORTED`，且不会生成 data resource、ProductPlan 权限目标或 TechnicalPlan。
- 固定“我的申请”业务查询且没有角色差异化授权的场景能够正常生成；与数据授权语义歧义时只澄清一次。
- 动态扩展角色和运行态配置成员的应用。
- 认证开启但 RBAC 关闭的应用。
- 静态应用申请 RBAC 时的前置拒绝。
- 新建关闭权限、需求阶段确认开启并持久化配置的路径。
- UI 确认和 UI skip 两条模板生成路径。
- 规划修订新增、保留和删除固定资源的目录协调路径。
- 初始管理员为空或使用 `current-user` 占位符时拒绝保存，真实 subject 可以完成幂等 bootstrap。
- 权限关闭、权限开启未 Build、运行时就绪未认证、已认证无资源四种接口状态矩阵。
- 一号通认证只通过 Spring Security `Authentication.getName()` 提供当前 subject，前端伪造 subject 不生效。
- 后端 OpenAPI 与前端生成类型一致，权限 service 封装覆盖全部前端使用的 operationId；`getAuthorizationStatus` 保持后端运维接口且不由前端调用。人为制造契约漂移或直接调用 axios/fetch 时 capability gate 失败。
- 前后端 `main` 分支不包含权限能力；`auth` 分支提供唯一系统资源控制的 `/roles` 和未就绪/就绪状态，且前端生成类型与本地契约一致、两个 YAML 副本 SHA-256 一致。

每个代码工作包均执行：

- 后端相关单元测试和 `py_compile`。
- `cd Frontend && pnpm build`。
- `scripts/start-backend.sh` 后检查 `/health`。
- `pnpm dev` 验收新建、需求确认或工作台界面。
- 从步骤 5 开始，通过 `/api/projects/launch` 启动生成应用验收。
- 步骤 5 只验证 XCodeAgent 对 `main`/`auth` 的确定性分支选择、成对获取、来源记录和生成应用启动；不在 XCodeAgent 中重新生成、构建或深度校验既有模板分支。
- 涉及目录、API、存储格式或边界变化时，同一工作包更新 `docs/CODEBASE_INDEX.md`。

第一阶段完成判定：

- 步骤 1–8 均已有独立修改记录、自动化测试结果、真实启动命令、人工验收入口和用户确认；任何一步只完成代码或测试但未经过用户启动验收时，第一阶段仍视为未完成。
- 使用 XCodeAgent 分别生成 `main` 与 `auth` 分支应用，完整走通新建配置、需求文档联合确认、UiDesign、TechnicalPlan、模板初始化、Build DAG、生成应用启动和权限运行时 ready。
- 在真实生成应用中通过 `/roles` 创建或修改角色、替换角色资源、配置成员角色，并验证刷新后页面入口、路由、操作按钮和后端 Endpoint 立即按最新资源关系生效。
- 验证多角色资源并集、Endpoint 多操作资源 ANY-OF、受控/未受控 Endpoint 混用门禁、系统管理资源、防锁死、revision 冲突、审计和 manifest fingerprint 全部符合本文契约。
- 验证第一阶段所有规划产物、模板和生成代码均不存在 data resource、Data Policy、Relation、Source Binding、数据过滤器或数据权限执行字段；明确数据授权需求只能停在 Capability Gate。
- 全部完整回归通过且用户确认第一阶段整体流程达到预期后，才能把第一阶段状态改为“已实施”。

### 第二阶段：数据权限（待单独规划）

第二阶段当前只保留能力方向，不补充实施步骤、工作明细或启动验收。它不属于第一阶段的交付或隐含待办，也不能以“预留字段”方式提前进入第一阶段实现。

启动第二阶段前，必须在第一阶段稳定完成后基于下文“V2 数据权限演进预留”另行确认正式实施计划、公共契约版本、数据迁移边界、逐步骤启动验收和端到端验收；未经该次确认，不得实现 data resource、Data Policy IR、RelationContract、PolicySourceBinding 或 Endpoint 数据权限执行。

## V2 数据权限演进预留

本节只定义未来演进方向，**不属于 Authorization V1 的实施范围、公共契约、运行时接口或 Build 任务**。任何 V2 字段都不得以“提前预留”的名义进入 V1 manifest 或生成代码。

V2 启动时必须通过正式 OpenSpec/变更提案重新确认需求、影响范围、迁移方案和 EDD 验收，不直接修改 V1 基线语义。

### V2 目标模型

V2 目标不是预置“本人/部门/下级组织/项目”等有限数据范围枚举，而是引入可声明、可组合、可验证的数据权限语义：

```text
Role
  ↓
Data Resource
  ↓
Data Policy IR
  ↓
Policy Source Binding
  ↓
Endpoint Enforcement
  ↓
Backend Predicate / Object Check / Create Constraint
```

核心公式预留为：

```text
Data Permission
= RBAC(Data Resource)
× Policy(Subject, Entity, Relation, Context, Expression)
× Source Binding
× Enforcement
```

### Data Policy IR V1 设计储备

未来 Data Policy IR 只表达：

```text
allow(subject, entity, context) -> true | false
```

首版 IR 候选运算保持有限：

- `all` / `any` / `not`：布尔组合。
- `compare`：`eq/ne/gt/gte/lt/lte/in/not_in` 等基础比较。
- `relation`：通过开放的 `relationRef` 表达组织、上下级、项目、客户等业务关系。
- `subject`：初始只暴露可信 `subject.id`，避免通过不断增加 subject attribute 穷举业务范围。
- `entity`：只引用逻辑业务字段，不保存数据库表名、列名或 Java 字段。
- `context`：只允许平台登记的可信上下文，如 tenant、服务器时间等；客户端可控输入不能直接成为授权事实。
- `constant`：表达固定业务范围值。

IR 本身不得包含 SQL、Java、Repository、HTTP URL、脚本或任意可执行 DSL。

### Relation 与 Source Binding 预留

复杂数据权限通过开放 Relation 表达，而不是增加 `departmentIds/projectIds/customerIds` 等无限 subject 属性：

```text
organization.manages_department_tree(subject.id, order.department_id)
project.is_member(subject.id, order.project_id)
customer.is_primary_owner(subject.id, order.customer_id)
```

每个 `relationRef` 必须有稳定 RelationContract，定义参数、语义边界、正反例；TechnicalPlan 再通过 PolicySourceBinding 决定该关系来自数据库 JOIN/EXISTS、组织中心、业务服务或其他可信来源。

### V2 Enforcement 与可编译能力门禁

未来至少区分：

- `collection_filter`：列表查询，必须支持分页前过滤。
- `object_check`：读取返回以及更新/删除前校验。
- `create_constraint`：持久化前校验。

合法 Policy IR 不等于一定可执行。TechnicalPlan 必须根据 SourceBinding 判断目标 Endpoint 是否具备对应执行能力。例如外部 API 或只能逐对象调用的关系服务若无法将 predicate 下推到分页前，就不能用于 `collection_filter`，必须 fail closed。

### V2 EDD 要求

每条 Data Policy 必须带结构化 allow/deny 示例。相同样例从 Policy IR 验证、TechnicalPlan SourceBinding 验证、Build 生成代码测试一直复用到 Endpoint 集成测试，用于证明“业务语义 → IR → 后端实现”没有漂移。

### V2 与 V1 的兼容边界

- V1 稳定保留 `Subject → Role → Resource` 和角色资源关系模型，为未来增加 `data` 资源类型保留架构空间。
- V2 才允许在新的 manifest schema/version 中重新引入 `type=data`、Data Resource、Data Policy、RelationContract、PolicySourceBinding 和 EnforcementBinding。
- V1 的 `authorization-manifest.v2` 不包含任何 data 字段；未来 V2 必须显式升级契约版本，不能在同一 schema 下偷偷增加可选字段。
- V1 期间保留 `DATA_AUTHORIZATION_NOT_SUPPORTED` 的结构化 sourceRefs，未来升级 V2 时可以作为变更需求输入，但不能提前转成可执行策略。

## 默认决策

- Authorization V1 只支持内置数据库 RBAC；授权领域不保留外部 provider。
- V1 业务资源只有 `page` 和 `operation`，平台固定资源只有 `system_authorization_management`；不实现 `data` 资源和通用数据权限。
- 明确数据授权需求必须通过 `DATA_AUTHORIZATION_NOT_SUPPORTED` 阻止联合确认；不能静默忽略、降级为页面/操作权限或改写成普通业务过滤。
- 普通业务查询可以使用可信当前 subject 作为查询条件；是否属于数据权限以“是否承担不同主体/角色的数据可见范围授权语义”为判断标准，而不是是否出现 `subjectId`。
- 权限控制必须依赖身份认证和数据库。
- 启用 RBAC 必然接入 `/roles`，不保存独立运行态页面开关。
- 未提及的业务页面/操作不生成资源点或守卫，对已认证成员默认可见可用。
- RequirementSpec 不保存未授权行为字段；页面入口固定隐藏且直接访问返回 403，操作入口固定隐藏且后端 Endpoint 返回 403。
- `unauthenticated` 属于认证层，不属于 RBAC RequirementSpec。
- 系统资源和业务资源在运行态均为只读固定目录；业务资源由 TechnicalPlan 确定性编译。
- RequirementSpec/TechnicalPlan 只确定首次角色种子和默认授权；首次初始化后，角色、成员角色关系和角色资源关系在运行态动态配置。
- 授权采用 allow 并集，不支持角色继承、显式 deny 或成员直接授权；普通角色可逻辑删除，拥有系统管理资源的角色不可删除。
- 初始管理员是角色的系统元数据属性，不是独立的隐式权限类型；系统始终通过显式资源关系保留至少一个拥有 `system_authorization_management` 的活跃成员。
- 初始管理员必须填写真实、精确的 subject，不使用 `current-user` 或首次访问者占位符。
- 当前 subject 固定来自 Spring Security `Authentication.getName()`；权限模块不信任前端身份输入。
- 本仓库 `contracts/authorization-api.v1.yaml` 是运行态接口唯一事实源，后端 `auth` 分支副本和前端 TypeScript 类型由它同步或生成；前端请求统一复用模板现有 `src/apis/service.ts`。
- `main` 分支不存在权限接口；`auth` 分支运行时未就绪时只有公开状态接口返回 200，其他接口返回 503。
- 模板分支由已持久化的权限开关唯一确定：关闭使用 `main`，开启使用 `auth`；前后端必须成对使用同一分支，调用方不能自由指定分支。
- 后端 `auth` 模板提供权限表、数据访问和启动初始化扩展点；步骤 6 不生成运行时源码，步骤 7 不生成 DDL、Flyway migration、MyBatis Mapper 或额外持久化依赖，只将已确认权限约束覆盖到现有 Build Task 并驱动模板首次启动初始化。
- 不增加用户可选 tag、SHA 或分支；模板生成 manifest 仅记录本次 `main`/`auth` 分支实际拉取的 commit SHA，用于来源核验和安全复用。
- ProductPlan 使用 `product-plan.v5`，不保存 `resourceKey`、`policyKey` 或角色字段；`policyKey` 在 V1 中属于不支持字段。
- ProductPlan 的权限操作目标固定为顶层 `{ruleId,pageId,actionId}`；sequence 的 `stepId` 只描述父 action 内部步骤，不进入权限资源、投影或 Endpoint 绑定。
- 页面、操作和系统资源共用全局 `resourceKey` 空间：页面使用 `pageId`，操作使用 `<pageId>_<actionId>`；`type` 只分类，跨类型碰撞必须在联合确认和 TechnicalPlan 编译时拒绝。
- 同一 Endpoint 的所有 business action 引用必须具有一致的操作权限属性：全部未受控时 `operationResourceKeys=[]`，全部受控时才聚合 `operationResourceKeys` 并按 ANY-OF 裁决；受控与未受控混用时以 `ENDPOINT_AUTHORIZATION_MIXED_CONTROL` 阻止编译并要求拆分 Endpoint，禁止依赖调用来源字段区分授权语义。
- Endpoint 绑定多个受控 `operationResourceKeys` 时固定在成员全部启用角色的资源并集上按 ANY-OF 裁决；同时执行多项独立受控能力的请求必须拆分 Endpoint。
- 不设置独立 Endpoint 权限详设阶段；V1 的资源点和 Endpoint 操作绑定全部在 TechnicalPlan 的 `authorization_manifest` 中确认。
- V2 数据权限只能通过正式变更提案引入新的 manifest schema/version，不在 V1 中预埋可选 data 字段或半成品实现。
- 不读取旧权限字段或旧 application schema，不实现兼容分支。
