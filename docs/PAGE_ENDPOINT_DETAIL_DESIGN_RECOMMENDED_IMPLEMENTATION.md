# Page/Endpoint 详细设计推荐实现方案

> 状态：To-Be，仅作为后续实现参考，当前代码尚未具备本文全部能力。
>
> 现状审计保留在 `docs/PAGE_ENDPOINT_DETAIL_DESIGN_AUDIT.md`；本文只记录推荐实现。

## 1. 目标与边界

`detail_confirmation` 负责在代码生成前消除目标 Page/Endpoint 的实现歧义，并让用户确认最终实现行为。

推荐约束：

1. Page 模式同时设计目标 PageDetail 和本轮缺少或明确要求重建的 EndpointDetails。
2. PageDecision 依赖已确认 UI Design、API Contract、页面引用和目标相关工程事实；PageDetail 不复制 EndpointDetail refs/hash，关联详情完整性由 DetailDesignBatch 在 join 时校验。
3. EndpointDetail 依赖 API Contract、数据源事实和目标相关后端源码；database 类型还依赖候选表完整事实。
4. 用户确认前允许结构化修改或自然语言重生成；最终对本批次整体确认。
5. ProjectPlan/API Contract、UI Design 和源码事实只读，本阶段不能静默补齐或覆盖。
6. 确认前只保存 draft/checkpoint，确认后才写正式详情和 ProjectPlan refs。
7. 详情生成或审核不得调用 `_repair_missing_request_schemas` 修改 API Contract；缺少 request schema/ref 时返回 ProjectPlan 修订要求。

非目标：不新增 Page/Endpoint/权限/导航/schema/data source；不修改 API Contract；不重画 UI；不扫描整个仓库；不修改业务代码或数据库。

## 2. 推荐执行流程

`detail_confirmation` 对外仍为一个阶段，内部按 fan-out/join 组织：

~~~text
prepare_detail_context
├─ design_page_decision（Endpoint 单独模式跳过）
└─ design_endpoint_decisions
   ├─ endpoint A
   ├─ endpoint B
   └─ endpoint N（限流并行）
→ join_detail_decisions
→ assemble_detail_batch
→ confirm_detail_batch
→ inspect_workspace
~~~

| 逻辑节点 | 类型 | 职责 |
| --- | --- | --- |
| `prepare_detail_context` | 确定性逻辑 + 只读工具 | 校验 ProjectPlan/UI 引用；计算目标字段闭包；扫描目标相关 Workspace Facts；database endpoint 取得候选表完整 Database Facts。 |
| `design_endpoint_decisions` | 结构化 LLM workers | 每个 worker 只读取单个 endpoint 的只读上下文并返回 EndpointDecision；不同 endpoint 限流并行，不修改共享 ProjectPlan 或文件。 |
| `design_page_decision` | 结构化 LLM worker | 基于 UI、页面引用、API Contract 和 workspace facts 生成 PageDecision；不读取 Endpoint 的数据库或后端实现决策，可与 Endpoint workers 并行。 |
| `join_detail_decisions` | 确定性逻辑 | 等待本批 PageDecision 和全部 EndpointDecisions 完成独立校验；检查跨 Endpoint 数据库操作、复用目标和批次引用冲突。 |
| `assemble_detail_batch` | 确定性逻辑 | 组装 PageDetail/EndpointDetail drafts，并按 ProjectPlan dependencies 执行批次完整性校验；不向 PageDetail 复制 EndpointDetail refs/hash。 |
| `confirm_detail_batch` | 人工确认 + 确定性提交 | 处理修改/重生成；校验 lifecycle `basedOnRevision` 和 `draft_sha256`，并重新校验上游引用；整体确认后原子提交。 |

Page 模式不要求 Page 模型等待 Endpoint 模型。PageDecision 与各 EndpointDecision 可以并行生成；全部 drafts 校验完成且 Batch 依赖完整后，系统才能打开整体确认。

### 2.1 Agent 流程优化对比

| 模块 | 当前实现 | 推荐结构 | 优化收益 |
| --- | --- | --- | --- |
| 内部编排 | `detail_confirmation` 在一个同步函数内顺序完成上下文提取、Endpoint 生成、Page 生成和审核准备 | 保持对外单节点，内部拆成 `prepare → parallel design → join → assemble → confirm` | 显式表达真实依赖，便于并行、重试和问题定位。 |
| Endpoint 生成 | 普通 `for` 循环逐个调用同步模型；每生成一个 Endpoint 就立即挂回共享 `updated_plan` | 为每个 Endpoint 构造独立只读输入，限流并行生成 EndpointDecision；worker 只返回结果，join 后按稳定顺序统一合并 | 降低多 Endpoint 页面等待时间，避免并发修改共享 ProjectPlan。 |
| Page 生成时机 | 所有关联 EndpointDetail 生成后才调用 Page 模型 | PageDecision 只依赖 UI、API Contract、页面引用和 workspace facts，与 EndpointDecisions 并行；Batch 组装等待 join | 移除 Page 模型对后端内部实现决策的不必要依赖，同时保持最终批次完整。 |
| 数据库事实读取 | 每个 Endpoint 在自身生成链路中同步准备数据库上下文 | `prepare_detail_context` 先按候选表去重并缓存完整 Database Facts，再把只读切片传给相关 Endpoint workers | 避免同一数据库或表被重复读取，并使并行模型使用同一事实快照。 |
| 并行结果合并 | 当前没有并行 join；Endpoint 结果随生成顺序直接写入计划内存态 | join 确定性检查重复或冲突的数据库 operations、reuse targets、target ids 和校验状态；只合并全部有效结果 | 并行不牺牲跨 Endpoint 一致性，失败时可只重试受影响目标。 |
| 模型运行形态 | Page/Endpoint 使用无工具的直接 ChatModel 调用，但流程命名容易被理解成 Agent | 明确定义为结构化 LLM workers；上下文读取、工具调用、校验、组装和持久化由编排器负责 | 避免引入不需要的 Agent 循环、工具权限和不可控上下文。 |

## 3. 上游引用解析规则

字段都来自 `.xcodeagent/plans/project-plan.json`。“唯一解析”表示引用必须恰好匹配一个定义。

### 3.1 Endpoint dependency

~~~jsonc
{
  "reference": {
    "pointer": "/frontend_pages/**/references/endpoint_dependencies/*/endpoint_id",
    "required": true
  },
  "target": {
    "pointer": "/api_contracts/*/endpoints/*/id",
    "must_match_count": 1
  },
  "metadata": {
    "usage": "必需：用途，不参与身份解析",
    "trigger": "必需：触发点，不参与身份解析",
    "required_for_initial_load": "必需：首屏依赖，不参与身份解析"
  }
}
~~~

当前页面引用没有 `api_contract_id`，因此 `endpoint.id` 必须在全部 contracts 中全局唯一。

### 3.2 Navigation target

~~~jsonc
{
  "reference": {
    "pointer": "/frontend_pages/**/references/navigation_targets/*/targetPageId",
    "required": true
  },
  "target": {
    "pointer": "/frontend_pages/**/pageId",
    "must_match_count": 1
  },
  "metadata": {
    "trigger": "必需：跳转触发点，不参与身份解析"
  }
}
~~~

### 3.3 Page permission role

`references.permissions` 的当前语义是角色 ID，不是独立 permission ID。

~~~jsonc
{
  "references": [
    {
      "from": "/frontend_pages/**/references/permissions/*",
      "to": "/permission_model/roles/*/id",
      "must_match_count": 1
    },
    {
      "from": "/permission_model/page_access/*/allowed_roles/*",
      "to": "/permission_model/roles/*/id",
      "must_match_count": 1
    },
    {
      "from": "/permission_model/operation_permissions/*/role_id",
      "to": "/permission_model/roles/*/id",
      "must_match_count": 1
    }
  ]
}
~~~

推荐新增确定性权限引用校验；当前代码尚未完整校验这些关系。

### 3.4 Schema ref

Endpoint schema ref 只能解析到拥有该 endpoint 的同一个 contract：

~~~jsonc
{
  "request_ref": {
    "from": "/api_contracts/<contract>/endpoints/*/request_schema_ref",
    "to": "/api_contracts/<same-contract>/schemas/<schema-name>",
    "required_when": "POST/PUT/PATCH"
  },
  "response_ref": {
    "from": "/api_contracts/<contract>/endpoints/*/response_schema_ref",
    "to": "/api_contracts/<same-contract>/schemas/<schema-name>",
    "required_when": "method != DELETE"
  },
  "nested_ref": {
    "from": "/api_contracts/<contract>/schemas/*/**/$ref",
    "to": "/api_contracts/<same-contract>/schemas/<schema-name>"
  },
  "cross_contract_ref": "forbidden"
}
~~~

失败返回 `invalid_project_plan_artifact`，包含来源/目标 JSON Pointer 和匹配数量；detail 阶段不得修复 ProjectPlan。

### 3.5 ZA21 API 规范引用与成功码

当前 Endpoint 详设不能单独完成这项改造：`success_status_code` 必须先由上游 ProjectPlan/API Contract 阶段依据 ZA21 确定。上游应读取 ZA21 原始规范并生成结构化 policy facts；本文不预设 ZA21 的具体规则和值。

推荐 ProjectPlan Contract 最小增加：

~~~jsonc
{
  "api_contracts": [
    {
      "id": "<contract-id>",
      "policy_ref": {
        "policy_id": "ZA21",
        "policy_version": "<实际版本>",
        "facts_path": "<ZA21结构化policy facts路径>",
        "sha256": "<policy facts sha256>"
      },
      "endpoints": [
        {
          "id": "<endpoint-id>",
          "success_status_code": "<由ZA21规则确定的HTTP成功码>",
          "error_codes": "<由ZA21和业务契约确定的错误码集合>"
        }
      ]
    }
  ]
}
~~~

ProjectPlan 确认前应确定性校验 policy ref/hash、Endpoint 成功码以及错误码是否符合对应 ZA21 facts。Endpoint 详设把已确认 Contract 作为只读约束，并遵守以下边界：

- 不让模型重新选择或覆盖 `success_status_code`。
- 不把成功码重复写入 EndpointDetail；任务准备、代码生成和验收按 `api_contract_id + endpoint_id` 读取 Contract 的唯一值。
- 只在实现层补充异常条件到 Contract 既有错误码的映射，不新增对外错误码。
- Contract 缺少成功码、ZA21 policy ref/hash，或两者不一致时，返回 `project_plan_revision_required`；不得在 EndpointDetail 中静默修复。

因此，ZA21 policy facts 的准备和 API Contract 改造是上游前置任务；Page/Endpoint 详设不再读取或保存独立 ZA21 facts/hash。

## 4. 推荐最小输入闭包

### 4.1 PageDetail

~~~jsonc
{
  "project_plan": {
    "path": ".xcodeagent/plans/project-plan.json",
    "confirmation_status": "必需：必须已确认",

    "target_page": {
      "pageId": "必需、只读",
      "name": "必需、只读",
      "path": "必需、只读",
      "description": "必需、只读",
      "references.endpoint_dependencies": "必需字段，可为空数组",
      "references.navigation_targets": "必需字段，可为空数组",
      "references.permissions": "必需字段，可为空数组；内容为 role id"
    },

    "api_contract_closure": {
      "contract_fields": ["id", "data_source_id"],
      "endpoint_fields": [
        "id",
        "method",
        "path",
        "summary",
        "parameters",
        "request_schema_ref",
        "response_schema_ref",
        "error_codes",
        "authentication"
      ],
      "schemas": "只读取所引用 request/response schemas 的递归 $ref 闭包"
    },

    "permission_subset": "只读取目标 page/operation 实际引用的 roles/page_access/operation_permissions/default_policy"
  },

  "ui_design": {
    "index_path": ".xcodeagent/specs/ui-designs.json",
    "required_index_fields": [
      "confirmation_status",
      "page.pageId",
      "page.status",
      "page.page_key"
    ],
    "source_path": ".xcodeagent/ui-design/pages/<page_key>/index.tsx",
    "source_content": "必需：UI 结构、组件、布局和视觉交互事实"
  },

  "workspace_facts": "目标页面、相关 API client/types、共享组件/hooks、route/menu 的最小事实切片"
}
~~~

PageDetail 不读取：完整 ProjectPlan、无关 pages/modules/contracts/endpoints/schemas、数据库凭据、整个仓库、UI 汇总 JSON 中重复的内嵌 code。

### 4.2 EndpointDetail

~~~jsonc
{
  "project_plan": {
    "contract": {
      "id": "必需、只读",
      "data_source_id": "必需、只读",
      "resource": "必需、只读",
      "base_path": "必需、只读",
      "authentication": "必需字段，可为空、只读",
      "policy_ref": "只由上游 API Contract 校验读取；不发送给详设模型"
    },
    "endpoint": {
      "id": "必需、只读",
      "method": "必需、只读",
      "path": "必需、只读",
      "summary": "必需、只读",
      "parameters": "必需字段，可为空、只读",
      "request_schema_ref": "必需字段，可为空、只读",
      "response_schema_ref": "除 DELETE 外必需、只读",
      "success_status_code": "由上游按 ZA21 确定；组装/验收读取，详设模型不需要",
      "error_codes": "必需字段，可为空、只读",
      "authentication": "必需字段，可为空、只读"
    },
    "schema_closure": "目标 endpoint request/response schemas 的递归 $ref 闭包",
    "data_source": {
      "id": "必需、只读",
      "type": "必需、只读",
      "entities": "database 时条件必需；只是候选线索，不代替 DB facts"
    },
    "dependent_pages": "仅用于建立依赖索引，不发送给 Endpoint 模型"
  },
  "workspace_facts": "Controller/Service/Repository/DTO/Entity/Mapper/migration 或 static module 的最小事实切片",
  "database_tables": "data_source.type=database 时条件必需：候选表完整结构"
}
~~~

EndpointDetail 不读取：完整 contract 的其他 endpoints、无关 schemas、页面正文、UI Design、完整 frontend page tree、无关 data sources、整个仓库。

### 4.3 `prepare_detail_context` 的预处理职责与实现缺口

不新增或持久化 `preparation_only`。下列值都是 `prepare_detail_context` 的函数内局部变量：读取权威来源后立即解析为 `llm_input` 或 control evidence，用完即丢弃。

| 预处理能力 | 当前代码事实 | 推荐实现 |
| --- | --- | --- |
| 权限子集解析 | `page_design_references` 会复制页面 role ids；`_operation_visibility` 直接使用这些 ids，没有把目标操作解析到 `permission_model.operation_permissions` | 从目标 page 的 `references.permissions`/`page_access.allowed_roles` 与 `operation_permissions` 确定性计算 `operation_visibility`；原始 role ids 和计算结果都不需要进入 LLM 输入 |
| Schema 闭包 | `extract_endpoint_detail_context` 会读取直接 request/response schema，同时把包含全部 schemas 的目标 Contract 放入 endpoint context；Page context 只过滤 endpoints，仍保留目标 Contract 的全部 schemas；当前没有生成递归最小闭包 | 从目标 request/response schema 开始递归解析同 Contract 内 `$ref`，只把闭包写入 `llm_input.endpoints[].schemas`；ref 不可解析时在模型调用前失败 |
| Workspace Facts | 当前 `workspace_inspector` 能确定性识别文件、React 组件、API client、后端路由等，Code Graph 能查询符号和引用，受控文件工具能读取源码；但这些能力在详设前没有按目标串联，现有 Page/Endpoint 模型输入不含源码事实 | `prepare_detail_context` 复用现有 Workspace Inspector、Code Graph 和受控文件读取，按目标定位候选文件、读取必要源码片段并生成裁剪后的 `workspace_facts`；不增加事实提取 LLM |
| Database 候选表 | `prepare_endpoint_database_context` 使用 `table_name=None` 读取数据库摘要；`database_schema_summary` 最多保留 12 表、每表 18 列，当前没有确定性候选表选择和逐表完整 facts | 使用 Contract resource/schema fields、`data_sources[].entities` 和 workspace entity/repository/migration facts 生成候选表名；调用现有 MySQL 工具按 `table_name` 定向读取，并新增不截断列的 targeted facts 投影，写入 Database Facts |
| 消费页面反查 | `extract_endpoint_detail_context` 已遍历 `frontend_pages/**/references/endpoint_dependencies`，生成 `dependent_pages`，包含 pageId/name/path/usage/trigger | 只在系统内建立 Endpoint→Page 依赖索引；它不改变 Endpoint 实现决策，不进入 LLM 输入，也不新增 `consumer_page_ids` 字段 |
| 数据库连接隔离 | `prepare_endpoint_database_context` 已通过 `get_mysql_table_info_for_workspace` 使用工作区连接，传给模型的是结构摘要而不是连接凭据 | 保留现有工具边界；连接配置属于工具权限，不进入 `DetailDesignContext`、LLM、artifact、日志或 AG-UI |
| 上一版 Decision 加载 | revise/regenerate 当前只把 feedback 传入 Prompt；Page/Endpoint Prompt 没有读取目标上一版 Decision 正文 | 通过当前 run/interaction 关联的草稿读取 PageDecision/EndpointDecision，写入 `llm_input.previous_decisions`，再与 feedback 一起调用模型；不新增 `previous_draft_ref` 输入字段 |

推荐函数边界：

~~~text
prepare_detail_context(target, revise?)
→ 读取并校验 ProjectPlan/API Contract/UI refs
→ 函数内解析 permission subset、schema closure、consumer pages
→ 确定性定位并读取目标相关源码，生成 workspace facts
→ database endpoint 选择候选表并获取完整 Database Facts
→ revise 时加载 previous decision
→ 返回 {page_input?, endpoint_inputs[]}
~~~

目标选择保留在 Graph State；run/interaction id 来自现有调用上下文。权限原始值、ZA21 facts/ref、输入 hashes、schema refs、entities、consumer page ids、上一版草稿 ref 和数据库连接都不是新的持久化输入字段。

### 4.4 LLM Decision 字段契约

下面是单次模型调用的内部输出，不直接持久化，也不是用户最终确认的 Batch。模型只输出实现决策；身份、Contract、UI 和权限等只读事实由确定性代码校验和组装。

Page LLM 输出：

~~~jsonc
{
  "implementation_strategy": {
    "mode": "<reuse|extend|create>", // 复用、扩展或新建页面实现
    "reuse_targets": [], // 已有文件、组件、hook 或 symbol；必须来自 workspace_facts
    "planned_changes": [] // 页面实现改动，不得修改 UI Design、ProjectPlan 或 API Contract
  },
  "response_bindings": [
    {
      "ui_target": "<component or region>", // 已确认 UI 中的数据落点
      "api_contract_id": "<contract id>",
      "endpoint_id": "<endpoint id>", // 必须来自页面已声明依赖
      "source_path": "<response field path>", // 必须存在于 response schema
      "page_field": "<binding purpose>"
    }
  ],
  "operation_interactions": [
    {
      "trigger": "<user or lifecycle trigger>",
      "action": "<page behavior>",
      "api_contract_id": "<contract id|null>",
      "endpoint_id": "<endpoint id|null>", // 调 API 时填写
      "target_page_id": "<page id|null>", // 导航时填写且必须来自 navigation targets
      "success_behavior": "<feedback or state change>",
      "failure_behavior": "<error feedback and retained context>"
    }
  ],
  "state_feedback": [
    {
      "state": "<loading|empty|error|ready|success|validation|confirm>",
      "trigger": "<state trigger>",
      "behavior": "<visible behavior>",
      "scope": "<page|region|form|operation>"
    }
  ],
  "form_rules": [
    {
      "field": "<form field or request field path>",
      "rule": "<pure frontend validation behavior>", // 不改变 Request Schema 的交互校验
      "message": "<validation message>"
    }
  ],
  "acceptance_criteria": [] // 可验证的页面实现行为
}
~~~

Endpoint LLM 输出：

~~~jsonc
{
  "implementation_strategy": {
    "mode": "<reuse|extend|create>",
    "reuse_targets": [], // Controller/Service/Repository/DTO/Entity 等，必须来自 workspace_facts
    "planned_changes": []
  },
  "data_origin": {
    "effective_source": {
      "kind": "<mysql_existing|mysql_new_table|frontend_mock>",
      "database": "<database|null>",
      "tables": [],
      "module_path": "<workspace-relative path|null>"
    },
    "field_mappings": [
      {
        "target_field": "<request/response schema field>",
        "source": "<table column|static field|existing code field>",
        "rule": "<mapping or deterministic transform>"
      }
    ],
    "database_operations": [
      {
        "operation": "<create_table|add_column|alter_column_type|alter_column_nullable|alter_column_default>",
        "table": "<table name or create-table definition>",
        "column": "<column|null>",
        "from": "<current definition|null>",
        "to": "<target definition>",
        "reason": "<contract-based reason>"
      }
    ]
  },
  "operation_semantics": {
    "operation_kind": "<read|create|update|delete|action>",
    "target_cardinality": "<exactly_one|zero_or_one|many|not_applicable>",
    "selector": {
      "source": "<path|query|request_body|contract|none>",
      "fields": [] // 只能来自 Contract parameters 或 request schema
    },
    "transaction_required": "<boolean>",
    "zero_match_behavior": "<behavior>",
    "multiple_match_behavior": "<behavior>",
    "side_effect": "<none|insert|update|delete|custom>"
  },
  "acceptance_criteria": []
}
~~~

PageDecision 不得输出 `pageId`、路由、UI 源码、依赖/导航/权限定义、API schema 或状态码。EndpointDecision 不得输出或修改 Contract identity、method/path/schema、data source type、ZA21 policy、成功码或对外错误码。模型输出与只读事实冲突时拒绝该 Decision，不静默改写模型负责的语义。

## 5. Target Workspace Facts

这是 `prepare_detail_context` 的临时只读结果，不新增长期 artifact、路径或独立 schema。较大的扫描结果可由现有 checkpoint/cache 按 run 复用；进入模型输入的只是一组目标相关事实：

~~~jsonc
[
  {
    "path": "必需：workspace-relative path",
    "role": "必需：文件在目标实现中的角色",
    "symbols": "可为空",
    "fact": "必需：由解析结果确定性生成、会影响实现决策的短事实"
  }
]
~~~

新项目允许结果为空；扫描状态另由工具/checkpoint 区分“没有相关文件”和“扫描失败”，不把状态字段传给模型。

### 5.1 确定性生成方式

不调用额外大模型生成 `workspace_facts`。推荐复用现有能力，由 `prepare_detail_context` 完成目标级组装：

~~~text
Workspace Inspector 建立文件清单和基础工程事实
→ Code Graph 按目标查询符号、引用、入口和关联文件
→ 受控文件读取只加载候选文件的必要源码片段
→ 解析 import、调用、路由、组件和后端分层关系
→ 按 Page/Endpoint 职责裁剪为 workspace_facts
→ Page/Endpoint LLM 基于事实决定 reuse/extend/create、binding 和实现语义
~~~

现有能力边界：

- `workspace_inspector` 已读取源码并确定性识别 React 组件、页面、API client、后端路由等基础事实。
- Code Graph 已支持 `search_symbols`、`file_summary`、`references`、`impact` 和 `entrypoints`，但只用于导航，不返回源码正文。
- `workspace.read_file` 已提供工作区边界内的受控源码读取。
- 当前 `inspect_workspace` 位于详细设计确认之后，且没有把以上能力组装成某个 Page/Endpoint 的输入；实现时应复用底层能力，不必再建设第二套全仓扫描器。

Page 目标按已确认的 `pageId`、route 和 UI source 定位页面文件，再沿 import/reference 关系读取直接相关组件、hooks、API client、types、route/menu 和 permission 文件。Endpoint 目标按 `api_contract_id + endpoint_id` 的 method/path 定位 Controller/route，再沿引用关系读取直接相关 Service、Repository、DTO、Entity、Mapper 和 migration/static module。

`fact` 不是模型总结，而是解析器根据源码证据生成的短投影，例如“`HomePage` imports `TravelCard`”或“`HomePage` calls `travelApi.list`”。文件行号、原始源码片段和查询结果作为 preparation/checkpoint evidence 保留，不重复发送给 LLM；模型输入只携带会改变实现决策的 `path/role/symbols/fact`。

无法唯一定位目标文件或符号时不得让模型猜测：预处理返回候选路径和定位依据，由用户选择，或返回 UI Design/ProjectPlan 补充稳定映射后重试。源码读取失败、代码图不可用和“确实没有相关实现”必须是不同状态；代码图不可用时可退化为确定性文件清单、路径规则和受控文本检索，但不能退化为模型自由搜索。

Page 检索范围：目标 page、同 module 已完成 pages、直接导航 pages、关联 API client/types、相关 shared components/hooks、route/menu/permission/theme。

Endpoint 检索范围：同 contract/resource 的 Controller/Service/Repository、DTO/Entity/Mapper、共享 response/error/auth、相关 migration/schema。

只把会改变 reuse/extend/create、binding 或数据来源决策的事实发送给对应 worker。

## 6. Database Facts

适用条件：

~~~text
/api_contracts/*/data_source_id
→ /data_sources/*/id
AND data_sources[*].type == database/mysql/db
~~~

推荐策略：

~~~text
读取数据库和表名索引
→ 根据 contract.resource、data_source.entities、schema fields 和源码确定候选表
→ 对候选表逐表读取完整结构
→ 读取与候选表直接关联的外键表
→ 形成当前 run 的 database facts 切片
~~~

不是把整个数据库完整注入模型；但候选表内容不得截断。

模型输入只保留 `candidate_tables[].name/columns/primary_key/indexes/foreign_keys/unique_constraints`。数据库是否存在、候选选择证据和工具错误属于预处理/checkpoint；连接配置和密码始终只允许数据库工具读取。

目标表不存在时，facts 必须可靠证明数据库存在状态、完整表名索引中无该表、相似表是否存在；不能因摘要截断而错误生成 `mysql_new_table` 或 `add_column`。

不为 Database Facts 新增长期 artifact 或 schema；确认时重新执行 database facts 可用性和引用校验。

## 7. Draft、确认和可编辑边界

推荐 schema：

- `xcodeagent.detail-design-context.v1`
- `xcodeagent.endpoint-detail.v1`
- `xcodeagent.page-detail.v1`
- `xcodeagent.detail-design-batch.v1`

### 7.1 正式输出字段

用户确认的是确定性组装后的完整 Batch，不是模型原始 Decision。正式详情只保存 target identity 和下游实现所需决策，不复制 Contract、权限投影、模型元数据或 lifecycle 状态。

~~~jsonc
{
  "schema_version": "xcodeagent.detail-design-batch.v1",
  "draft_sha256": "<sha256-of-batch-body>",
  "endpoint_details": [
    {
      "schema_version": "xcodeagent.endpoint-detail.v1",
      "api_contract_id": "<string>", // 上游事实
      "endpoint_id": "<string>", // 上游事实
      "implementation_strategy": {
        "mode": "<reuse|extend|create>",
        "reuse_targets": [],
        "planned_changes": []
      },
      "data_origin": {
        "effective_source": {
          "kind": "<mysql_existing|mysql_new_table|frontend_mock>",
          "database": "<database name>", // 仅 mysql 来源存在
          "tables": [], // 仅 mysql 来源存在
          "module_path": "<workspace-relative path>" // 仅 frontend_mock 存在
        },
        "field_mappings": [],
        "database_operations": [] // 仅确有数据库结构差异时存在
      },
      "operation_semantics": {
        "operation_kind": "<read|create|update|delete|action>",
        "target_cardinality": "<exactly_one|zero_or_one|many|not_applicable>",
        "selector": {"source": "<path|query|request_body|contract|none>", "fields": []},
        "transaction_required": "<boolean>",
        "zero_match_behavior": "<behavior>",
        "multiple_match_behavior": "<behavior>",
        "side_effect": "<none|insert|update|delete|custom>"
      },
      "acceptance_criteria": []
    }
  ],
  "page_detail": { // Endpoint-only 模式省略
    "schema_version": "xcodeagent.page-detail.v1",
    "pageId": "<string>", // 上游事实
    "implementation_strategy": {},
    "response_bindings": [],
    "operation_interactions": [],
    "state_feedback": [],
    "form_rules": [], // 仅存在不改变 Request Schema 的纯前端输入约束时填写
    "acceptance_criteria": []
  }
}
~~~

字段来源只有四类：target identity 来自上游正式事实；实现策略、binding、interaction、mapping、operation semantics 和 acceptance criteria 来自通过校验的 Decision；operation visibility 等可重算投影由任务准备阶段确定性生成且不持久化；`schema_version` 和 `draft_sha256` 由系统生成。PageDetail 不保存 EndpointDetail refs/hash，Batch 按 ProjectPlan dependencies 校验关联详情齐全。

### 7.2 版本与一致性策略

“读取最新文件”只能决定读取哪个文件，不能证明当前详细设计是基于最新上游事实生成的，也不能证明用户确认的是自己实际查看过的草稿。

| 标识 | 是否需要 | 作用 |
| --- | --- | --- |
| `schema_version` | 必需 | 标识文件结构，供读取方选择对应 schema 和迁移逻辑；不是业务内容的历史版本。 |
| `draft_sha256` | 必需 | 绑定用户实际审核的草稿，避免旧审核界面确认后台已经生成的新草稿。 |
| lifecycle `basedOnRevision` | AG-UI 交互需要 | 只用于状态快照的乐观并发，不作为 PageDetail/EndpointDetail 的历史版本。 |
| artifact 整数 `revision` | 不需要 | 本节点不按版本号查找或回退历史产物；维护 `1/2/3` 没有额外价值。 |

#### `schema_version` 具体改造

只实现结构识别，不建设通用迁移框架，也不保存 artifact 整数版本历史。

1. 在 `create_page_detail_plan` 和 `create_endpoint_detail_plan` 生成的顶层对象中分别写入：

~~~jsonc
{
  "schema_version": "xcodeagent.page-detail.v1"
}
~~~

~~~jsonc
{
  "schema_version": "xcodeagent.endpoint-detail.v1"
}
~~~

2. `detail_design_documents.py` 持久化时原样保留 `schema_version`；`hydrate_external_detail_designs` 读取详情后按以下规则处理：

   - 字段缺失：视为当前旧格式，按现有归一化逻辑加载，并在内存中补成对应 `v1`；读取动作不回写文件。
   - 值为当前支持的 `xcodeagent.page-detail.v1` 或 `xcodeagent.endpoint-detail.v1`：按对应结构校验后加载。
   - 值存在但不受支持：停止加载该详情并返回 `invalid_page_detail_artifact` 或 `invalid_endpoint_detail_artifact`，不得猜测结构。

3. 当前审核载荷继续使用 `xcodeagent.detail_review.v1`。只有实现本文新增的 `action`、`draft_sha256`、`editable_paths` 或 readonly facts，导致审核载荷结构实际变化时，才把 `detail_review_payload.question_schema` 升级为 `xcodeagent.detail_review.v2`。

4. 升级审核载荷时，后端与前端审核组件必须在同一改动中支持 `v2`。若需要恢复仍停留在旧确认界面的运行，只保留 `v1` 的读取兼容；新生成的审核载荷统一写 `v2`。

5. 最小验证覆盖：无版本旧详情可加载、两个 `v1` 详情可加载、未知版本被拒绝、`v1` 审核载荷仍可恢复、`v2` 审核载荷可完成 revise/confirm/reject。版本号不会因用户修改草稿而递增。

只读取最新文件会遗漏三类过期状态：

- 草稿生成后，上游正式引用、Workspace Facts 或 Database Facts 失效；草稿文件仍是最新的，但确认前复验会失败。
- EndpointDetail 修改后，Batch 尚未重新完成关联详情齐全性和冲突校验；单个文件最新不代表整个 Batch 可确认。
- 用户打开审核界面后后台生成了新草稿；确认请求不携带 `draft_sha256` 时，可能确认用户没有看过的内容。

最小一致性结构：

~~~jsonc
{
  "schema_version": "xcodeagent.detail-design-batch.v1",
  "draft_sha256": "<sha256-of-batch-body>", // 不包含本字段
  "endpoint_details": [],
  "page_detail": {} // Endpoint-only 模式省略
}
~~~

确认时校验 lifecycle `basedOnRevision`、重新计算 `draft_sha256`，并重新执行 target、引用和条件事实校验；全部通过才允许提交。每次修改直接覆盖当前 draft 并重算 `draft_sha256`；用户要求恢复旧方案时，将其作为新的修改反馈重新生成，不保存整数版本历史。

确认前写：

~~~text
.xcodeagent/drafts/detail-design/<runId>/<interactionId>/batch.json
.xcodeagent/drafts/detail-design/<runId>/<interactionId>/endpoints/*.json
.xcodeagent/drafts/detail-design/<runId>/<interactionId>/pages/*.json
~~~

确认后原子提升到：

~~~text
.xcodeagent/plans/endpoints/*
.xcodeagent/plans/pages/*
.xcodeagent/plans/project-plan.json refs
~~~

确认载荷：

~~~jsonc
{
  "id": "必需：复用 lifecycle pendingInteraction.id",
  "action": "必需：confirm/revise/reject",
  "basedOnRevision": "必需：复用 lifecycle pendingInteraction.basedOnRevision",
  "draft_sha256": "必需：用户实际看到的整个 DetailDesignBatch hash",
  "changes": "revise 时条件必需；必须符合服务端 editable schema",
  "feedback": "自然语言重生成时条件必需；不等于确认"
}
~~~

lifecycle revision 或 `draft_sha256` 不匹配即返回 `stale_detail_design_review`；上游引用/事实重新校验失败则返回对应上游修复路径。批次必须全部 confirmed 或全部不提交。

审核页现有“整体补充说明”输入框不再作为只读备注使用，推荐改造为“修改要求”：

1. 用户输入修改要求后点击“按要求修改”，前端提交 `action=revise` 和 `feedback`，不得同时提交 `confirm`。
2. 后端把 `feedback` 与当前 PageDecision/EndpointDecision 一并作为修订输入，只重生成受影响的 Decision。EndpointDecision 变化时重新组装对应 EndpointDetail 并复验 Batch；PageDecision/PageDetail 不依赖该后端实现决策，因此不重复调用 Page 模型。
3. 修订完成后执行 Endpoint/Page/batch 校验，生成新的 `draft_sha256`，并通过 `detail_confirmation.review.required` 返回完整待确认草稿。
4. 审核界面展示修订后的最终内容；用户随后点击“确认设计并进入构建”，单独提交 `action=confirm`。
5. `revise` 只更新当前 draft，不写 confirmed 产物，不设置 confirmed 状态，也不进入 Build。

因此，新审核契约中的 `feedback` 是设计修改输入；当前 `overall_note` 的“仅保存备注”语义不再作为修改入口保留。

当前前端还允许编辑 `api_dependencies`、`response_bindings`、`page_navigation` 和 `permissions`，但后端 `PAGE_EDITABLE_FIELDS` 不接受这些字段；推荐由服务端下发唯一 `editable_paths`，前端只渲染这些路径，避免用户可编辑但提交必然被拒绝。`endpoint/navigation/permission` 引用始终只读；`response_bindings` 只允许在既有 Contract 和 UI 范围内修改。

当前 PageDetail/EndpointDetail 构造器会先写 `status=confirmed`、`confirmed_at`、`approved=true`，planning 随后仅覆盖 `status/approved`，导致待确认草稿仍可能带确认时间。推荐 draft 正文不保存 lifecycle 状态；确认时间和用户 evidence 只在整体确认成功后写 checkpoint/正式提交记录。

PageDetail 可编辑：

- 既有 UI 组件与 contract 字段的 response binding。
- 既有 endpoint 下的操作顺序、触发和反馈。
- loading/empty/error/success 状态行为和文案。
- 不改变 Request Schema 的纯前端输入约束与交互提示。
- 显示格式和范围内 acceptance criteria。

EndpointDetail 可编辑：

- 已知 contract/source 字段之间的 mapping/transform。
- 事实允许范围内的复用或新增实现选择。
- 与 Database Facts 一致的 database operations。
- 在 API Contract 约束内的事务、基数、零/多结果和已声明副作用。
- acceptance criteria。

只读：页面身份/路由/范围、UI 事实、endpoint/navigation/permission 引用、API Contract、data source 身份、Database Facts、workspace facts、dependent pages。

自然语言修改覆盖当前 draft 并重算 `draft_sha256`；EndpointDetail 修改后重新校验 Batch 的关联详情齐全性和跨 Endpoint 冲突，不使独立的 PageDecision/PageDetail 失效。

## 8. 错误、重试和路由

### 8.1 阶段成功门槛

| 阶段 | 进入下一步的必要条件 | 失败后的处理 |
| --- | --- | --- |
| 目标解析 | `selectedPageId` 或 `api_contract_id + endpoint_id` 唯一解析到已确认 ProjectPlan target | 选择失效则刷新目标；目标缺失则返回 ProjectPlan 补充并重新确认。 |
| 上下文准备 | UI（Page 模式）、最小 Contract/schema 闭包、Workspace Facts 以及条件必需的完整 Database Facts 均可用 | 返回拥有该事实的 UI/ProjectPlan/Workspace/Database 阶段；输入完整前不调用模型。 |
| Decision | 输出通过结构、只读引用、Workspace symbol、schema mapping 和 data source 校验 | 临时模型错误只重试受影响 worker；正式事实错误返回上游，不生成降级草稿。 |
| Join/Batch | 本轮结果齐全、target 无重复、ProjectPlan dependencies 闭合，database operations/reuse targets 无不可合并冲突 | 只重试受影响 Decision；上游冲突返回对应正式流程。 |
| Revise | action、interaction、`basedOnRevision`、`draft_sha256` 匹配，且修改限于 editable paths；新 Batch 再次校验通过 | 保留当前有效草稿；越界修改返回所属上游，过期提交展示最新草稿后重新修改。 |
| Confirm | 无待应用修改，revision/hash 匹配，上游引用和 Batch 在确认时复验通过 | 不确认过期或失效草稿；修复后必须展示新 Batch 并再次整体确认。 |
| Commit | 全部详情和 ProjectPlan refs 批次写入、磁盘回读及 schema/ref 校验成功 | 使用同一有效确认 checkpoint 重试；输入失效则重新生成并确认，不保留部分提交。 |

任何失败结果都必须包含目标或字段、失败原因、应返回的阶段和修复后重试入口，不能只返回错误码。

### 8.2 错误分类与路由

| 错误码 | 含义 | 路由 |
| --- | --- | --- |
| `invalid_project_plan_artifact` | ProjectPlan 未确认、缺字段或引用不能唯一解析 | 停止 batch，返回上游正式流程。 |
| `invalid_ui_design_artifact` | Page UI 未确认或不能定位 TSX | 停止 batch，返回 UI 上游。 |
| `workspace_baseline_failed` | 无法生成目标工程事实；相关文件不存在不算失败 | transient 可重试，耗尽后失败。 |
| `database_context_unavailable` | 无法取得候选表完整事实 | 停止 endpoint 设计，不允许猜测。 |
| `invalid_endpoint_detail_draft` | Endpoint draft 不满足契约 | 修订 endpoint draft。 |
| `invalid_page_detail_draft` | Page draft 越过 UI/API/权限边界 | 修订 page draft。 |
| `invalid_endpoint_detail_artifact` | 已持久化 EndpointDetail 的 `schema_version` 不受支持或内容不符合该版本结构 | 停止读取，不进入详情生成或确认。 |
| `invalid_page_detail_artifact` | 已持久化 PageDetail 的 `schema_version` 不受支持或内容不符合该版本结构 | 停止读取，不进入详情生成或确认。 |
| `project_plan_revision_required` | 需要修改正式 Page/API/schema/permission/data source | 结束 batch，交由 ProjectPlan Workflow。 |
| `stale_detail_design_review` | 用户提交基于旧输入或旧 draft | 返回最新 batch，重新确认。 |
| `detail_review_rejected` | 用户拒绝 | 保留 checkpoint，结束本轮。 |

当前 Page 在节点层最多调用两次且首次失败等待 0.8 秒，耗尽后仍可能生成 fallback 草稿；Endpoint 只有一次节点级调用。推荐两类 worker 使用同一套节点级 bounded retry，耗尽后返回结构化失败，不生成可确认的降级草稿。只对明确 transient 的模型、只读工具和网络错误重试；Validation、缺字段、认证失败、Contract 冲突和用户拒绝不重试。Attempts/backoff/timeout 进入统一 Settings 和 evidence。

Endpoint draft 修订后：重验 EndpointDecision → 重新组装对应 EndpointDetail → 重验整个 Batch → 再次整体确认；不重复调用 Page 模型。

成功路由：

~~~text
confirm_detail_batch
→ 原子提交正式详情
→ inspect_workspace
→ 确定性任务规划
→ Build
~~~

## 9. AG-UI 与可观测证据

产品流继续使用 AG-UI，推荐 review schema 为 `xcodeagent.detail_review.v2`。

建议事件：

~~~jsonc
[
  "detail_confirmation.context.started",
  "detail_confirmation.context.completed",
  "detail_confirmation.endpoint.started",
  "detail_confirmation.endpoint.completed",
  "detail_confirmation.page.started",
  "detail_confirmation.page.completed",
  "detail_confirmation.review.required",
  "detail_confirmation.completed",
  "detail_confirmation.failed"
]
~~~

`batch.json` 只保存可确认的 `DetailDesignBatch` 及 `draft_sha256`。模型尝试、校验错误、Workspace/Database 检索证据和用户 action 继续写现有 checkpoint/log，不再建设独立 batch manifest 生命周期。

大型源码、数据库快照和模型原始输出外置保存；AG-UI/Graph State 只传稳定引用、hash、脱敏摘要和结构化结果。

## 10. 实现入口索引

| 领域 | 当前代码入口 | 推荐改动 |
| --- | --- | --- |
| 上游 ZA21/API Contract | `Backend/app/agents/main/planner.py`、`Backend/app/services/project_plan.py`、`Backend/app/services/api_contract_validation.py` | ProjectPlan 生成前读取结构化 ZA21 policy facts；Contract 记录 policy ref/hash，Endpoint 明确 `success_status_code` 和符合 ZA21 的错误码；确认前确定性校验。该项是 Endpoint 详设成功码修复的前置任务。 |
| 主节点 | `Backend/app/graph/nodes/planning.py::detail_confirmation` | 拆出四个逻辑服务/子节点，保留对外阶段名。 |
| 页面依赖 | `Backend/app/services/page_dependencies.py` | 保留 endpoint/navigation 校验，补 permission role 校验。 |
| API/schema 校验 | `Backend/app/services/api_contract_validation.py` | 复用闭包校验，错误增加 JSON Pointer evidence。 |
| 上下文/组装 | `Backend/app/services/page_detail_plan.py` | 改为最小闭包，接入 UI/Workspace/Database facts；PageDetail/EndpointDetail 构造器写入各自 `schema_version`；删除详情中重复的 Contract/interface/processing 字段，按 target identity 解析 Contract。 |
| Prompt | `Backend/app/agents/main/page_designer.py` | 只消费已验证 context；移除“待补 API”；统一 retry/failure；不再让模型决定 ZA21 已约束的成功码和对外错误码。 |
| 数据库上下文 | `Backend/app/services/database_context.py`、`database_schema_summary.py` | 先列索引，再逐候选表完整读取。 |
| MySQL 工具 | `Backend/app/tools/mysql_info.py` | 复用 `table_name` 定向读取；连接信息保持工具内。 |
| 审核 | `Backend/app/services/detail_review.py` | 下发 editable schema，统一结构化修改和自然语言重生成；删除审核阶段 `_repair_missing_request_schemas` 对 API Contract 的自动修补；载荷实际改为新结构时将 `question_schema` 升级为 `xcodeagent.detail_review.v2`。 |
| lifecycle/AG-UI | `Backend/app/services/application_lifecycle.py`、`Backend/app/protocols/workflow/lifecycle.py`、`runtime.py` | 复用 pending interaction id/`basedOnRevision`；接入 `draft_sha256`、revise/confirm/reject 和完整 Page/review progress 事件。 |
| 重试配置 | `Backend/app/config.py`、`Backend/app/agents/model_factory.py` | 在现有模型 timeout/retry 基础上统一 Page/Endpoint 节点级 attempts/backoff，耗尽后返回失败，不生成 fallback 草稿。 |
| 持久化 | `Backend/app/workspace/detail_design_documents.py` | 隔离 draft/confirmed；用现有 run/interaction id 组织草稿目录，保存最小 Batch 和 `draft_sha256` 并原子提交；读取时兼容无版本旧详情、拒绝未知版本。 |
| 前端审核 | `Frontend/src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/DetailReview.tsx` | 只展示服务端允许字段，提交 interaction id、basedOnRevision 和 `draft_sha256`。 |
