# Application Development Planning

## Scope

Workbench 读取 `.xcodeagent/plans/technical-plan.json`，以 ProductPlan `pages` 作为页面事实，并按 `pageId` 合并 TechnicalPlan `pages[].references`；API 大纲从 `api_contracts` 投射 Endpoint。Endpoint 只有在当前版 `.xcodeagent/plans/endpoints/endpoint--<contractId>--<endpointId>.json/.md` 均存在、JSON 已确认且其中的 TechnicalPlan 契约指纹与当前文件一致时才标记“已设计”。仅有 TechnicalPlan 声明或单个 Markdown 文件都不能放行。实体大纲只展示 TechnicalPlan 顶层 `entities`；实体没有全局数据源绑定状态。

点击大纲只选择本次目标。Endpoint 的“设计 API/重新设计”动作打开独立的 `ApiDesignConfigModal`，通过 `/endpoint-designs/run` 的 AG-UI `prepare/save` 动作保存正式映射，不进入主工作流；页面或 API 开发先进入 `api_design_readiness_gate`，门禁缺失时暂停并展示缺失清单，用户点击具体条目后才打开同一弹窗，保存后仍需在原会话确认继续开发。会话不归属于页面、接口或实体，已有 Workflow 消息及用户显式打开的历史会话继续展示运行结果。

页面视觉、组件、交互入口和状态呈现以已确认 React UI 稿为权威；UI 阶段被跳过时依据 ProductPlan、TechnicalPlan 和模板技能实现。`PageImplementationContract` 仍由 ProductPlan、UiManifest 和 TechnicalPlan 在运行时确定性编译，不写入独立页面详设。

独立 API 映射配置只补充某个 Endpoint 的自包含字段映射，不允许修改 TechnicalPlan 中的方法、路径、参数或 Schema。`fieldMappings` 覆盖 Path、Query、Header、请求体叶子字段和响应业务叶子字段；对象容器与纯包装节点不要求映射。草稿阶段每个字段恰好保存一条 `unconfigured`、`source_mapping` 或 `business_description` 记录；确认后的正式产物只允许后两者，且包括可选字段在内不得遗留 `unconfigured`。`source_mapping` 内嵌完整 `sourceFields`，并用 `processingType` 区分直接映射、单字段业务处理和多字段业务处理；说明支持多行，直接映射不带说明。无真实来源字段时使用 `business_description`。TechnicalPlan 顶层 `entities` 继续表达全局业务语义，但不再复制为 Endpoint 场景实体或参与 Endpoint↔Source 映射。

Endpoint 还可以填写可选的 `implementationDescription`，用于描述整个接口的实现思路，例如查询步骤、事务、缓存、异常处理或外部 API 编排。该描述会写入 Endpoint JSON/Markdown 并传给任务规划与后端代码生成，但不参与字段映射完整性判断，也不能改变 TechnicalPlan 契约或成为独立验收硬门禁。

Source Field 节点可实时读取直属 MySQL 表列，也可读取数据源目录中最新保存的外部 API Operation Schema；外部来源不发起真实网络请求。Builtin 与 DBID 数据库明确返回“不支持实时读取元数据”，不得伪造候选字段。运行时数据库凭据只在内存中按 `sourceId` 解析，不写入 Endpoint 设计、事件或日志。

保存配置时后端校验所有必填 API 叶子字段，生成无敏感信息的来源快照，并写入带同一 `artifactRevision` 修订号的 Endpoint JSON 与用户可见 Markdown；双文件任一替换失败会回滚上一版，缺失、残缺或修订号不一致一律视为 stale。保存配置只固化可复用版本，不自动开始开发；开发门禁再次校验 Endpoint、契约和 revision，工作流回显本次版本并等待“确认并继续开发”。确认成功后才进入当前 Endpoint 的工作区检查、任务规划、代码生成、测试、审查和验收流程。TechnicalPlan 改变导致契约指纹不匹配时，Endpoint 变为“需重新设计”；数据源目录后续变化不会主动使设计失效，Build 使用当前已确认的磁盘产物，但数据源被删除或运行凭据不可用会作为 Build 失败报告。

页面开发时，就绪检查一次性返回全部缺少或过期设计的关联 Endpoint；卡片可逐项打开同一独立弹窗，保存后重新检查原页面门禁，不绕过现有检查。全部配置有效后仍等待用户确认继续开发。单 Endpoint 开发只检查自身当前版设计。通过确认后继续 `inspect_workspace -> prepare_build_tasks -> Build DAG`，`prepare_build_tasks` 在 Build 边界再次执行同一 Endpoint 设计复检。

旧 EntitySourceBinding 的节点、服务、页面和独立入口继续保留，可单独使用；其结果不参与 API 设计状态、正常开发旅程门禁或 Build 上下文。独立映射配置和页面/API 开发门禁都使用 AG-UI 生命周期；不新增 REST 产品接口，普通协作继续使用独立 `/conversation/run`。

独立的 `/application-development-planning/run` 编号任务规划能力保持原状，但不承担 Endpoint 动态映射；Endpoint 映射统一属于 `/endpoint-designs/run`，主 `/workflow/run` 只负责开发门禁和继续开发确认。

开发确认成功后，门禁立即把页面或接口对应的完整映射集合随原工作流消息保存；该快照只代表当次确认结果，后续开发停止、失败或重新配置都不会覆盖历史卡片。切回会话时优先读取消息中的确认快照。独立 `/endpoint-designs/run` 按 `workspaceRoot + apiContractId + endpointId` 提供 `get/prepare/save`，右侧“开发产物”与门禁确认卡片共用只读投影；缺失结果显示 pending，TechnicalPlan 指纹变化或双文件异常显示 stale。任务规划继续读取当前正式磁盘映射，不消费门禁快照，也不增加基于 lifecycle 或开发状态的映射锁定。

Build DAG 的生产入口是主 `/workflow/run` 中的 async Planning adapter。它把已确认 ProductPlan、TechnicalPlan、PageImplementationContract、API Contract、当前有效 Endpoint API Design 和权限切片冻结到 PlanningRun；EntitySourceBinding 不进入该输入。Unit Candidate 由平台 FIFO Worker Pool 有界并行生成并执行 Unit Local Retry，完整 Scope Assembly 和 Global Validation/Repair 通过后只写 `.xcodeagent/plans/build-task-plan.pending.json`。已有正式 `.xcodeagent/plans/build-task-plan.json` 保持不变。

确认卡是只读 Planning-result 门禁：`confirm` 精确验证 `planning_run_id + draft_digest` 后提升当前 Pending 并进入 Build；`abandon` 删除当前 Pending、结束对应 Workflow execution，但保留聊天会话和已有正式计划；结构化 `regenerate` 先删除旧 Pending，再回到 `prepare_build_tasks` 创建全新 PlanningRun，后续失败不恢复旧 Pending。同一应用的所有页面和 Scope 共用一个 DAG Planning/待确认互斥域。活跃生成只允许取消整个 Workflow/PlanningRun，当前权威运行卡显示“取消运行”；待确认阶段改用确认卡上的放弃/重新生成/确认，不提供 Unit 级取消。刷新只恢复服务端权威状态投影，不保证原请求继续执行或事件补发；唯一 Pending 和精确 DraftIdentity 是确认权威，没有 Pending 时不得从聊天历史、旧卡片或旧 execution 恢复待确认状态。

## Initial Development Completion and Test Entry

页面和 Endpoint 必须分别作为显式开发目标走完一次初次流程，全部完成后才能进入测试阶段。页面开发顺带实现依赖 Endpoint 不替接口标记完成。实体也单独计数，只有用户显式确认且正式 EntitySourceBinding 成功写盘后才完成；选表、生成设计和等待确认均不算完成。三类产物全部完成后才能进入测试阶段。

`.xcodeagent/application-lifecycle.json.developmentArtifacts` 保存 `pages[pageId]` 与 `endpoints[apiContractId][endpointId]` 的 `initialDevelopmentStatus`：`pending`、`in_progress`、`completed`。绿色完成记录同时保存首次 `completedAt`、`completedRunId`、`completedThreadId`，二次修改、测试失败、重复或迟到事件均不能覆盖。未完成运行在等待用户操作时保持紫色；失败、停止或放弃且无同目标其他初次执行时回到灰色。叶子点击只浏览，不更新开发状态。

实体状态保存于 `developmentArtifacts.entities[entityId].initialDevelopmentStatus`，直接使用当前正式 EntitySourceBinding 的确认状态，不伪造页面/API 的 Build 完成时间和 run/thread。未确认实体有活动中的同目标 `data_source` execution 时为 `in_progress`，其余为 `pending`；缺失或损坏的绑定不能计完成。新会话卡片右下角使用绿色“已初次完成”、紫色“开发中”、灰色“未开发”，右侧实体组同步显示计数和圆点。

后端 execution 的 `developmentPurpose` 和 `developmentTarget` 由正式入口及原 execution 决定，不能由客户端声明。初次开发的实体续接、DAG 确认、单元测试及重试保留该身份，普通会话及正式修订不能替未开发目标补记完成。只有服务端 Build 完成且 `unit_test_gate_passed=true`（通过或显式跳过）后，`test_phase_confirmation` 才先写入当前目标完成，再计算全部产物门禁；不等待测试确认、审查或验收。

产物目录使用已确认 ProductPlan 页面和 TechnicalPlan Endpoint、实体。新增 ID 从 `pending` 开始；删除目标移出统计，迟到事件不能重建；同 ID 改名或修改需求保留初次完成。未确认草稿或损坏文件不替换既有完成事实，通过 `catalogError` 关闭测试入口。只支持当前合同，不从旧会话、Build 文件或历史 checkpoint 推断完成状态。

`testEntryGate` 随 AG-UI lifecycle 投影提供 `allowed/total/completed/pending/inProgress/blockers/reason`，不重复持久化。放行要求工作台就绪、目录有效、至少一个页面、接口或实体、全部初次完成。前端顶部、历史确认卡、自动阶段与本地阶段恢复使用同一门禁。顶部点击只浏览；真实测试提交、execution 接替、直接恢复/调试测试及修复返回测试均由后端复检。拒绝返回 `development_artifacts_incomplete`，保留未消费确认；测试启动接替与凭据消费原子提交，失败后原执行可重试。

圆点和分组计数只表示初次开发，不反映二次修改活动。分母不受搜索、折叠及相关项过滤影响。前端仅通过已有 AG-UI lifecycle store 同步，按 revision 拒绝旧快照；无状态文件轮询、独立 REST 接口或重复完成状态。

下述编号开发任务规划使用独立 `/application-development-planning/run` AG-UI endpoint 和独立 thread id，不进入或恢复主 LangGraph Workflow。正常生成使用一次模型调用；只有模型返回真实阻断问题时，回答才进入第二次生成。确认过程是确定性的，不调用模型。

## Context Budget

The backend reads the fixed `<workspaceRoot>/.xcodeagent/application.json` and sends only application identity, scenario, terminal, layout, the datasource type without connection mode or credentials, auth, menus, APIs, and at most five short clarification answers. It never sends source files, repository trees, workflow history, tool logs, chat history, or secrets. The output is bounded by existing menu count, twenty tasks per menu, two to six acceptance criteria per generated task, and short field limits. This remains far below the 128k model context budget.

## Task and Persistence Contract

- The selected page receives a non-empty, ordered `developmentTasks` array; other pages are preserved and may remain unplanned. Array order is the visible 1, 2, 3 task order. Each task has a globally unique id, concise title and scope, `todo`/`in_progress`/`completed` status, direct `dependsOn`, derived `blocks`, covered feature names, and a separate acceptance-criteria list. Model-generated tasks always start as `todo`; the broader status enum allows later task completion updates without changing the storage shape.
- Routing, API-call infrastructure, navigation, and layout are treated as existing project capabilities. Generation must return `sharedModules: []`, and deterministic validation rejects newly proposed shared modules. The field is fixed as an empty current-contract declaration.
- `menus.developmentPlan` stores the plan summary, schema version, and global topological `executionOrder`.
- Generation and confirmation carry the selected page key through the AG-UI payload. Confirmation rereads the current workspace file, derives missing `menus`, `apis`, `schemas`, and `dataSources` from the confirmed ProjectPlan when necessary, validates that the plan covers exactly the selected page, derives reverse blockers, checks dependency existence and acyclicity, preserves other page plans, then writes through a sibling temporary file and atomic replacement.
- Existing page purposes, features, interactions, APIs, and unrelated application configuration are preserved.

## AG-UI Lifecycle

Generation and confirmation both emit run start, assistant message start, structured progress custom events, state snapshots, assistant text, a completed or failed custom result, message end, and run finish. Generation forwards model chunks as `TEXT_MESSAGE_CONTENT`; the frontend consumes the endpoint through `@ag-ui/client` and `@ag-ui/core` without handwritten SSE parsing.
