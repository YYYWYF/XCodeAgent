# Application Development Planning

## Scope

Workbench 读取 `.devagentstudio/plans/technical-plan.json`，以 ProductPlan `pages` 作为页面事实，并按 `pageId` 合并 TechnicalPlan `pages[].references`；API 大纲从 `api_contracts` 投射 Endpoint。Endpoint 只有在当前版 `.devagentstudio/plans/endpoints/endpoint--<contractId>--<endpointId>.json/.md` 均存在、JSON 已确认且其中的 TechnicalPlan 契约指纹与当前文件一致时才标记“已设计”。仅有 TechnicalPlan 声明或单个 Markdown 文件都不能放行。实体大纲只展示 TechnicalPlan 顶层 `entities`；实体没有全局数据源绑定状态。

点击大纲只选择本次目标。Endpoint 的配置动作打开右侧“字段映射”工作台，已有复杂映射保留 `ApiDesignConfigModal` 完整编辑器；通过 `/endpoint-designs/run` 的 AG-UI `prepare/save_draft/save` 动作分别准备、暂存和确认正式映射，不进入主工作流；页面或 API 开发先进入 `api_design_readiness_gate`，只要目标包含 Endpoint，首次进入就展示目标范围内全部 Endpoint 的已完成、待配置和已失效状态，用户点击任一条目后打开并定位同一字段映射工作台。字段映射的步骤、保存和修改均在右侧工作台处理，不再额外渲染对话区引导卡。全部映射已完成时，门禁卡仍用于查看或修改；用户点击“确认并检测”后统一复检，全部有效时直接继续开发，不再进入独立的 API 映射确认步骤。无关联 Endpoint 的纯静态页面直接放行。会话不归属于页面、接口或实体，已有 Workflow 消息及用户显式打开的历史会话继续展示运行结果。

页面视觉、组件、交互入口和状态呈现以已确认 React UI 稿为权威；UI 阶段被跳过时依据 ProductPlan、TechnicalPlan 和模板技能实现。`PageImplementationContract` 仍由 ProductPlan、UiManifest 和 TechnicalPlan 在运行时确定性编译，不写入独立页面详设。

当前正式契约为 `endpoint-field-mapping.v7`，详见 [单数据源字段取值规则](FIELD_VALUE_RULES.md)。工作台沿用原有数据来源选择和更换入口，不新增数据表 / 外部 API 场景 Tab。`sourceBinding` 保存单表或单 Operation 身份；`databaseWrites`、`databaseQuery` 和 `externalApiBindings` 按目标保存统一 `right` 取值规则，支持接口参数、固定值、内置上下文和业务生成。业务加工显式保存依赖、自然语言规则、缺值策略和默认值；同一请求参数可以复用。响应的多字段加工保留 `source_mapping`，无数据源字段的返回取值使用 `value_mapping`，纯业务入参用途使用 `business_description`。规则在行内应用，正式保存仍须显式确认；草稿不推进开发。查询树保留顶层及一层子组 AND/OR，空值运算符不带右值，update/delete 不允许无条件执行。

Endpoint 还可以填写可选的 `implementationDescription`，用于描述整个接口的实现思路，例如查询步骤、事务、缓存、异常处理或外部 API 编排。该描述会写入 Endpoint JSON/Markdown 并传给任务规划与后端代码生成，但不参与字段映射完整性判断，也不能改变 TechnicalPlan 契约或成为独立验收硬门禁。

Source Field 节点可实时读取直属 MySQL 表列，也可读取数据源目录中最新保存的外部 API Operation Schema；外部来源不发起真实网络请求。Builtin 与 DBID 数据库明确返回“不支持实时读取元数据”，不得伪造候选字段。运行时数据库凭据只在内存中按 `sourceId` 解析，不写入 Endpoint 设计、事件或日志。

保存配置时后端校验所有必填 API 叶子字段，生成无敏感信息的来源快照，并写入带同一 `artifactRevision` 修订号的 Endpoint JSON 与用户可见 Markdown；双文件任一替换失败会回滚上一版，缺失、残缺或修订号不一致一律视为 stale。保存配置只固化可复用版本，不自动开始开发；用户回到门禁点击“确认并检测”后，门禁再次校验 Endpoint、契约和 revision，检测通过便进入当前 Endpoint 的工作区检查、任务规划、代码生成、测试、审查和验收流程。TechnicalPlan 改变导致契约指纹不匹配时，Endpoint 变为“需重新设计”；数据源目录后续变化不会主动使设计失效，Build 使用当前已确认的磁盘产物，但数据源被删除或运行凭据不可用会作为 Build 失败报告。

页面开发时，就绪检查一次性返回全部关联 Endpoint 的当前状态，并以缺少或过期设计的子集决定是否阻断；只要存在关联 Endpoint，首次门禁都展示完整状态列表，卡片可逐项打开同一工作台查看或配置，保存并确认后仍由用户主动触发原页面门禁检查，不绕过现有检查。全部配置有效后，用户确认检测通过才继续 `inspect_workspace -> prepare_build_tasks -> Build DAG`，不再产生 `api_design_confirmation` 交互；单 Endpoint 开发同样先展示自身当前版设计，`prepare_build_tasks` 在 Build 边界再次执行同一 Endpoint 设计复检。

旧 EntitySourceBinding 的节点、服务、页面和独立入口继续保留，可单独使用；其结果不参与 API 设计状态、正常开发旅程门禁或 Build 上下文。独立映射配置和页面/API 开发门禁都使用 AG-UI 生命周期；不新增 REST 产品接口，普通协作继续使用独立 `/conversation/run`。

独立的 `/application-development-planning/run` 编号任务规划能力保持原状，但不承担 Endpoint 动态映射；Endpoint 映射统一属于 `/endpoint-designs/run`，主 `/workflow/run` 只负责开发门禁检测并在通过后继续开发。

门禁不再生成或持久化聚合 `apiDesignResult` 确认快照，也不渲染历史映射确认卡片。独立 `/endpoint-designs/run` 按 `workspaceRoot + apiContractId + endpointId` 提供 `get/prepare/save/save_draft/discard_draft`，右侧“开发产物”直接读取当前正式产物；缺失结果显示 pending，TechnicalPlan 指纹变化或双文件异常显示 stale。任务规划继续读取当前正式磁盘映射，不增加基于 lifecycle 或开发状态的映射锁定。

Build DAG 的生产入口是主 `/workflow/run` 中的 async Planning adapter。它把已确认 ProductPlan、TechnicalPlan、PageImplementationContract、API Contract、当前有效 Endpoint API Design 和权限切片冻结到 PlanningRun；EntitySourceBinding 不进入该输入。Unit Candidate 由平台 FIFO Worker Pool 有界并行生成并执行 Unit Local Retry，完整 Scope Assembly 和 Global Validation/Repair 通过后只写 `.devagentstudio/drafts/plans/build-task-plan.pending.json`。已有正式 `.devagentstudio/plans/build-task-plan.json` 保持不变。

确认卡是只读 Planning-result 门禁：`confirm` 精确验证 `planning_run_id + draft_digest` 后提升当前 Pending 并进入 Build；`abandon` 删除当前 Pending、结束对应 Workflow execution，但保留聊天会话和已有正式计划；结构化 `regenerate` 先删除旧 Pending，再回到 `prepare_build_tasks` 创建全新 PlanningRun，后续失败不恢复旧 Pending。同一应用的所有页面和 Scope 共用一个 DAG Planning/待确认互斥域。活跃生成只允许取消整个 Workflow/PlanningRun，当前权威运行卡显示“取消运行”；待确认阶段改用确认卡上的放弃/重新生成/确认，不提供 Unit 级取消。刷新只恢复服务端权威状态投影，不保证原请求继续执行或事件补发；唯一 Pending 和精确 DraftIdentity 是确认权威，没有 Pending 时不得从聊天历史、旧卡片或旧 execution 恢复待确认状态。

## 数据源配置与直接映射工作台

- 左侧拆为“数据源”和“外部 API”两个互斥入口，均采用列表与详情双层抽屉，打开不改变当前会话或右侧映射选择。数据源侧只展示数据库连接和已添加表；外部 API 侧先创建接口域，再按域 Tab 展示域内平铺接口。当前界面隐藏目录管理和目录选择，新增接口统一写入当前域的“默认目录”；域名、目录、Operation 的正式存储结构不变。数据库仍只允许一个，连接编辑按 ID 读取完整详情，不用列表摘要回填连接字段。
- 数据库仅已添加表可进入简化绑定候选。“添加数据表”读取实时直连 MySQL 元数据、搜索与批量添加；“移除”只移除应用候选，不执行 DDL。Builtin/DBID 保留配置但不伪造元数据读取。删除源、目录、接口和移除表之前查询正式 Endpoint 引用，正式映射不会被自动删除。
- 右侧“应用文件”之后提供常驻“字段映射”页签。目录按现有 API Contract/Endpoint 投影；读取契约→选择类型→选择对象→配置映射是前端交互步骤，不是新增 Workflow 节点。单个 Endpoint 选择一张表或一个外部接口，每字段只允许直接映射；数据库沿用行式选择，外部 API 的入参和出参分别用一个大箭头说明方向，再按目标字段逐行下拉选择来源，支持搜索和“仅看未配置”，不展示逐字段连线。不引入登录用户条件、表达式、常量、SQL 编辑或模拟调试；外部 Path/Query 的实时必填字段未配置时只能保存草稿，不能正式确认。无可映射字段的 Endpoint 可保存来源选择草稿，但不能确认来源绑定，避免正式产物丢失选择；未选择来源时，工作台可确认空字段映射；已选择来源的草稿需用户明确清除选择后才能确认无来源映射，不能提交时静默丢弃。该路径仍校验 TechnicalPlan 指纹与 baseRevision，后续沿用开发门禁。
- “保存”只写中间草稿，允许未配置字段；“保存并确认”校验来源清单、单对象一致性、TechnicalPlan 指纹与正式 baseRevision，再调用原有确认服务。成功后清空草稿并展示常驻只读结果。两处确认按钮共用提交锁。编辑、保存和正式确认均不会自动推进开发或修改开发完成计数。
- 对应数据库 JSON 的 `managedTables` 保存 source 所属的已添加表名和说明；表字段结构在数据源详情、绑定选择和正式确认时实时读取，不写入数据源。`.xcodeagent/binding-workspace` 仅保存 `draft-<复合身份 SHA256>.json` 草稿、selection、baseRevision、technicalPlanHash、savedAt；无凭据副本，不改变正式 Endpoint JSON/Markdown。连接设置更新保留最新表清单，模式、地址、端口、Schema 或 DBID 变化会清空清单；确认冲突保留输入，用户可明确放弃草稿重新加载。
- 数据源与 Endpoint 的独立 AG-UI 动作继续负责元数据、暂存和确认。正式产物只接受 `endpoint-field-mapping.v7`；旧正式产物显示“需重新配置”，旧草稿不加载。用户从当前 Endpoint 契约重新配置后可覆盖旧产物，不做迁移、回填或双写。
- 定向回归：`tests.test_binding_workspace`、Endpoint 详情/协议、数据源路由、API Design/readiness，覆盖外部必填字段缺失和重复来源。UI 静态检查使用 `pnpm typecheck:web`；本次按用户要求不执行前端测试、pnpm build、/health 或 Electron 验证，视觉与运行时验收未执行。

## Initial Development Completion and Test Entry

页面和 Endpoint 必须分别作为显式开发目标走完一次初次流程，全部完成后才能进入测试阶段。页面开发顺带实现依赖 Endpoint 不替接口标记完成。实体也单独计数，只有用户显式确认且正式 EntitySourceBinding 成功写盘后才完成；选表、生成设计和等待确认均不算完成。三类产物全部完成后才能进入测试阶段。

`.devagentstudio/application-lifecycle.json.developmentArtifacts` 保存 `pages[pageId]` 与 `endpoints[apiContractId][endpointId]` 的 `initialDevelopmentStatus`：`pending`、`in_progress`、`completed`。绿色完成记录同时保存首次 `completedAt`、`completedRunId`、`completedThreadId`，二次修改、测试失败、重复或迟到事件均不能覆盖。未完成运行在等待用户操作时保持紫色；失败、停止或放弃且无同目标其他初次执行时回到灰色。叶子点击只浏览，不更新开发状态。

实体状态保存于 `developmentArtifacts.entities[entityId].initialDevelopmentStatus`，直接使用当前正式 EntitySourceBinding 的确认状态，不伪造页面/API 的 Build 完成时间和 run/thread。未确认实体有活动中的同目标 `data_source` execution 时为 `in_progress`，其余为 `pending`；缺失或损坏的绑定不能计完成。新会话卡片右下角使用绿色“已初次完成”、紫色“开发中”、灰色“未开发”，右侧实体组同步显示计数和圆点。

后端 execution 的 `developmentPurpose` 和 `developmentTarget` 由正式入口及原 execution 决定，不能由客户端声明。初次开发的实体续接、DAG 确认、单元测试及重试保留该身份，普通会话及正式修订不能替未开发目标补记完成。只有服务端 Build 完成且 `unit_test_gate_passed=true`（通过或显式跳过）后，`test_phase_confirmation` 才先写入当前目标完成，再计算全部产物门禁；不等待测试确认、审查或验收。

独立实体确认写入正式绑定后立即重新计算全量门禁。如果该实体是最后一项，实体对话末尾展示与页面/Endpoint 共用的“进入测试阶段”确认卡。跨测试会话的确认资格从来源 execution 的服务端 checkpoint 和待确认交互读取，确认节点再次核验目标实体及全量门禁；实体没有独立 Build Unit，不生成虚假的 Build 或单元测试通过事实。

产物目录使用已确认 ProductPlan 页面和 TechnicalPlan Endpoint、实体。新增 ID 从 `pending` 开始；删除目标移出统计，迟到事件不能重建；同 ID 改名或修改需求保留初次完成。未确认草稿或损坏文件不替换既有完成事实，通过 `catalogError` 关闭测试入口。只支持当前合同，不从旧会话、Build 文件或历史 checkpoint 推断完成状态。

`testEntryGate` 随 AG-UI lifecycle 投影提供 `allowed/total/completed/pending/inProgress/blockers/reason`，不重复持久化。放行要求工作台就绪、目录有效、至少一个页面、接口或实体、全部初次完成；当前 Build 计划只约束本次执行，不会缩小应用级门禁分母。前端顶部、历史确认卡、自动阶段与本地阶段恢复使用同一门禁，测试及后续阶段均受约束。顶部点击只浏览；真实测试提交、execution 接替、直接恢复/调试测试及修复返回测试均由后端复检。拒绝返回 `development_artifacts_incomplete`，保留未消费确认；测试启动接替与凭据消费原子提交，失败后原执行可重试。

圆点和分组计数只表示初次开发，不反映二次修改活动。分母不受搜索、折叠及相关项过滤影响。前端仅通过已有 AG-UI lifecycle store 同步，按 revision 拒绝旧快照；无状态文件轮询、独立 REST 接口或重复完成状态。

下述编号开发任务规划使用独立 `/application-development-planning/run` AG-UI endpoint 和独立 thread id，不进入或恢复主 LangGraph Workflow。正常生成使用一次模型调用；只有模型返回真实阻断问题时，回答才进入第二次生成。确认过程是确定性的，不调用模型。

## Context Budget

The backend reads the fixed `<workspaceRoot>/.devagentstudio/application.json` and sends only application identity, scenario, terminal, layout, the datasource type without connection mode or credentials, auth, menus, APIs, and at most five short clarification answers. It never sends source files, repository trees, workflow history, tool logs, chat history, or secrets. The output is bounded by existing menu count, twenty tasks per menu, two to six acceptance criteria per generated task, and short field limits. This remains far below the 128k model context budget.

## Task and Persistence Contract

- The selected page receives a non-empty, ordered `developmentTasks` array; other pages are preserved and may remain unplanned. Array order is the visible 1, 2, 3 task order. Each task has a globally unique id, concise title and scope, `todo`/`in_progress`/`completed` status, direct `dependsOn`, derived `blocks`, covered feature names, and a separate acceptance-criteria list. Model-generated tasks always start as `todo`; the broader status enum allows later task completion updates without changing the storage shape.
- Routing, API-call infrastructure, navigation, and layout are treated as existing project capabilities. Generation must return `sharedModules: []`, and deterministic validation rejects newly proposed shared modules. The field is fixed as an empty current-contract declaration.
- `menus.developmentPlan` stores the plan summary, schema version, and global topological `executionOrder`.
- Generation and confirmation carry the selected page key through the AG-UI payload. Confirmation rereads the current workspace file, derives missing `menus`, `apis`, `schemas`, and `dataSources` from the confirmed ProjectPlan when necessary, validates that the plan covers exactly the selected page, derives reverse blockers, checks dependency existence and acyclicity, preserves other page plans, then writes through a sibling temporary file and atomic replacement.
- Existing page purposes, features, interactions, APIs, and unrelated application configuration are preserved.

## AG-UI Lifecycle

Generation and confirmation both emit run start, assistant message start, structured progress custom events, state snapshots, assistant text, a completed or failed custom result, message end, and run finish. Generation forwards model chunks as `TEXT_MESSAGE_CONTENT`; the frontend consumes the endpoint through `@ag-ui/client` and `@ag-ui/core` without handwritten SSE parsing.
