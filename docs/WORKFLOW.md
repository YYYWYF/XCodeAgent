## workflow目标

workflow根据用户需求生成可在本地运行的前后端工程，并通过需求确认、计划生成、代码生成、集成测试和用户验收形成完整闭环。

> 2026-08-13 起，创建链路和页面实现边界以 `docs/PRODUCT_UI_TECHNICAL_PLANNING.md` 为准。本文后续仍保留的 PageDetail 描述仅解释历史工作区兼容，不得用于新运行生成页面详设。

## 核心架构原则

1. 外层 LangGraph 管理确定性的项目生命周期。
2. Deep Agents 负责需要自主推理、工具调用、文件操作和多步执行的任务。
3. Agent 不得自行决定或绕过项目阶段、用户确认、任务依赖和质量门禁。
4. Graph State 保存小型结构化状态和文件引用，不保存完整代码、大型日志或全部 Agent 消息。
5. 项目文件、Spec、计划和测试报告是跨节点共享的事实来源；工作流元数据统一写入项目工作区的 `.xcodeagent/`，业务代码仍写入正常工程目录。
6. Deep Agent 的消息和工具结果属于任务级临时上下文，不直接合并进整体 Graph State。
7. 所有 Agent 结果必须结构化，并经过确定性校验后才能更新业务状态。
8. 测试是否通过由确定性的质量门禁判断，不能只相信 Agent 的自然语言结论。

### 工具调用流式可视化映射

- learn-coding-agent：保持“生成工具输入、执行、立即回传结果并继续验证”的紧凑循环，工具事件不改变工作流阶段边界。
- OpenCode：使用稳定的 tool call id 更新同一条前端记录，参数按 delta 追加，执行结果到达后将该记录收敛为完成状态。
- Deep Agents 与 128k 上下文：LangGraph 的 `messages` 流只转发当前工具片段；大型参数和结果仅在展示层限长保留，不写入整体 Graph State。

后端通过 AG-UI 标准 `TOOL_CALL_START`、`TOOL_CALL_ARGS`、`TOOL_CALL_END`、`TOOL_CALL_RESULT` 事件实时传输工具调用。`agent-process` 自定义事件仅补充思考和工作流阶段展示，不作为工具卡片的数据协议。

`LANGSMITH_TRACING` 未配置时默认关闭；当显式设置为 `true` 时，主 workflow 会依赖 LangGraph/LangChain 的 LangSmith 集成生成外部 trace，并在 runnable config 中注入 `run_id`、`thread_id`、`project_id`、`workspace` 和 workflow 标签。桌面端仍以当前 AG-UI 事件展示本轮 workflow trace 日志；LangSmith 作为外部技术 trace 后端，用于查看节点、模型调用、工具调用、耗时和错误。

## 已确认的主 Graph

主流程顺序如下：

```text
START
  → detail_confirmation //仅为页面依赖或显式接口目标补齐 EndpointDetail
  → inspect_workspace //确定性工作区快照
  → inspect_database_context //仅当 EndpointDetail.data_origin 明确来源于数据库时执行
  → prepare_build_tasks //direct ChatModel 基于快照和可选数据库摘要生成静态 Build DAG
  → build //BuildScheduler 派发给 CodeRunner
  → integration_test
      ├─ 测试与质量门禁通过 → launch_project
      │                         → 提示用户验收并结束本轮
      │                         → 用户确认后从 acceptance 续跑
      │                         → finalize_project
      │                         → END
      ├─ 可自动修复 → RepairPlanner 生成 repair_task_plan
      │              → small_task_repair 并行执行局部 repair tasks
      │              → integration_test 复测
      │              → 复杂任务经用户确认后转入对应正式节点
      ├─ 需要用户确认 → END
      └─ 不可恢复失败 → handle_failure
                         → END
```

需求、产品、UI 和技术规划由首页独立 `application_planning_workflow` 完成。主 `/workflow/run` 只读取 `.xcodeagent/plans/technical-plan.json`；页面选择从 `pages[].references` 解析实现范围并在运行时编译 PageImplementationContract，只有缺失 EndpointDetail 时才停在 `detail_confirmation`，纯静态页面可直接继续。

### 主 Graph 起点的参考架构映射与上下文预算

- learn-coding-agent：沿用“先从文件获取任务上下文，再执行、验证”的紧凑循环；主 Graph 不重复生成已经持久化的 RequirementSpec 和 ProjectPlan。
- OpenCode：沿用 session/run 从可序列化文件状态恢复的边界；正式 ProjectPlan 是跨阶段事实来源，后续节点只恢复所需的小型结构化状态。
- Deep Agents：外层确定性 Graph 负责阶段门禁，页面设计和后续专业 Agent 只接收已确认计划及相关文件引用。
- 128k 上下文：正式 `project_plan.frontend_pages` 以菜单树保留用户确认过的目录关系，运行态只把拍平后的页面叶子概览显式传入需要执行的节点；完整计划作为结构化初始状态传入，仓库源码、历史消息和大型工具输出仍不注入主 Graph 上下文。

### 新建应用四阶段规划 Graph

首页“创建并规划页面”使用独立 `requirements → product_planning → ui_confirmation → technical_planning` Graph，并负责生成主 Graph 的正式初始产物。产品确认 RequirementSpec、ProductPlan 与 React UI 稿（UI 阶段也可明确跳过），开发确认 TechnicalPlan；它不会进入工作区检查、任务拆分、代码生成和测试阶段。

- 独立入口仍为 `/application-page-planning/run`，统一使用 AG-UI Workflow 事件、状态快照、`resumeState` 和 `clarificationAnswers`；需求概览的“保存并退出编辑”复用同一端点的 `requirementSpecDraft.action = save` AG-UI 动作，只持久化草稿，不续跑 Graph。
- RequirementSpec、ProductPlan 和 TechnicalPlan 使用 Markdown 确认入口；React UI 稿及 `ui-designs.json` 使用 UI 确认界面，用户可提交结构化 `ui_design_action.action = skip` 跳过 UI 设计并直接进入 TechnicalPlan。澄清回答不能替代产物确认。RequirementSpec、ProductPlan 与 UI 产品确认不展示或询问数据源、数据库、持久化和 API 选择；这些内部技术输入只在 TechnicalPlan 由开发角色确认。
- TechnicalPlan 确认后校验四类正式产物；UI Manifest 的 `confirmation_status` 可以是 `confirmed` 或用户明确提交跳过后的 `skipped`，再推进 lifecycle 到 `generating_application_template_files`。模板完成动作使用同一规则复核四类 JSON 后才写入 `ready_for_workbench`。
- 创建弹窗展示“需求确认、产品规划、UI 确认、技术规划”四阶段；RequirementSpec 与 ProductPlan 不保存模型生成的产品假设或产品风险，不确定的产品事实通过需求澄清解决；产品验收只描述生成应用的用户可见结果，XCodeAgent 的预览、构建、测试、质量门禁和工作流推进条件由确定性过滤器剔除；ProductPlan 使用 `product-plan.v4` 保存产品可见行为，页面事实只保留 `pages`，不生成或兼容读取 `frontend_pages`，也不含数据源/API/数据库；模型输出先按完整 JSON 示例执行精确字段校验；UI 使用 `ui-manifest.v3`，正式 JSON 在生成 UI 时保存 React 稿引用、产品 ID 到控件 ID 的映射、本地 UI 效果和验证证据，跳过时保存空 `pages` 与 `confirmation_status: skipped`，不复制 ProductPlan 文案、正式路由、角色或状态事实；TechnicalPlan 使用 `artifact_type: technical-plan`，只持久化技术架构、工程设计、API Contract 和 `pages[].references`，不重复需求、产品或 UI 事实。
- 主 Workflow 运行时从 RequirementSpec、ProductPlan、UiManifest 和 TechnicalPlan 按需编译 PageImplementationContract；编译结果不写回 TechnicalPlan。
- 创建规划不执行构建后的集成测试质量门，也不生成 `quality_gate_passed`；AG-UI 摘要只在主 Workflow 明确产生布尔质量门结果时展示“通过/未通过”，不得把缺失值误报为未通过。

该澄清边界映射到参考架构时，沿用 learn-coding-agent 的 AskUserQuestion 工具循环与可恢复会话记录、OpenCode 的 session/tool-call 问答关联，以及 Deep Agents 的外层确定性门禁：需求模型只生成结构化问题，Graph 在 `requirements` 后结束当前轮次，AG-UI 持久化公开状态与回答，恢复轮次将答案合并回 RequirementSpec。澄清回答只补足需求，不能替代后续 RequirementSpec 的显式确认。每轮仅携带当前请求、紧凑 RequirementSpec 和结构化回答，不加载仓库或完整会话历史，继续满足 128k 上下文预算。

需求草稿保存遵循 learn-coding-agent 的“读取当前事实、执行一次确定性写入、立即返回验证结果”紧凑边界，并沿用 OpenCode 的可恢复 session/event 思路，通过完整 AG-UI 生命周期返回新文档状态。它不调用 Deep Agent，也不把仓库或会话历史注入上下文；请求仅包含当前 RequirementSpec 草稿，后端再以工作区 JSON 为基线合并，因此继续满足 128k 上下文预算且不引入新的 Agent 权限。

### 工作区应用生命周期

`.xcodeagent/application-lifecycle.json` 是用户可见、跨会话应用初始化、工作台 execution 和资源锁的持久化权威来源，schema 与完整状态机见 `docs/APPLICATION_LIFECYCLE.md`。初始化期间由 `initialization.threadId` 定位同一 checkpoint，成功进入工作台时清空；初始化交互正文和确认令牌不在根节点重复保存。它使用版本化 Pydantic schema、单调 revision、同目录临时文件 + fsync + 原子替换，损坏或不支持的版本不会被当作缺失静默忽略。当前对话的 Graph 运行状态以实时 AG-UI 流和同一 `threadId` 的 LangGraph checkpoint 为准，不会在每个节点运行前从状态文件重建。

应用冷启动恢复到 `awaiting_user` 时，前端通过 `/application-page-planning/run` 的 `applicationPlanningRecovery.get` AG-UI 动作只读获取同一 `threadId` 的 checkpoint，并重新投影确认卡和 Markdown 工件；该动作不得调用 Graph 节点、改变 lifecycle 或伪造用户消息。需求与计划确认还要求本轮存在非空 `clarificationAnswers` 结构化提交，普通恢复文案、原始需求和重新挂载组件都不能越过确认门禁。运行中阶段仍可按原线程恢复执行，失败或取消阶段只展示显式重试入口。

职责边界固定如下：

- `application-lifecycle.json`：顶层 `initialization.stage/status/threadId` 只保存进入工作台前的初始化门禁和 checkpoint 定位，完成后固定为 `ready_for_workbench/completed` 并清空 thread；工作台阶段另由按 run 隔离的 `activeExecutions`、页面/API 契约/数据源 `resourceLocks`、execution 交互门禁、活动 run 和恢复审计表示；
- 已停止或失败的执行继续运行时，客户端显式提交旧 `runId` 作为恢复令牌；服务端只允许同一 `threadId`、scope 和 target 接替，并原子地把该 run 当前可见的资源登记转给新 `runId`，不使用 lifecycle 快照覆盖当前 Graph 状态；
- `checkpoints.sqlite`：LangGraph 技术执行断点和节点状态，继续保留；
- RequirementSpec / ProjectPlan Markdown + JSON：正式文档内容和 `confirmation_status`，继续保留；
- Build DAG / ExecutionRun / TestReport：任务、执行和测试事实，继续由各自产物负责。

创建流程覆盖 `collecting_requirement -> analyzing_requirement -> awaiting_requirement_clarification -> generating_requirement_spec -> awaiting_requirement_confirmation -> generating_product_plan -> awaiting_product_plan_confirmation -> generating_ui_designs -> awaiting_ui_design_confirmation -> generating_technical_plan -> awaiting_technical_plan_confirmation -> generating_application_template_files -> application_template_generation_failed/ready_for_workbench`。旧 ProjectPlan 阶段只用于历史恢复。

进入工作台后的主 Workflow 不再改写应用初始化阶段；运行、等待确认、失败、停止和验收只更新对应 execution。后端从正式 ProjectPlan 为页面执行解析页面、导航关联页、API 契约和数据源资源集合并写入 `resourceLocks`，但当前不以集合交集、同页面、同工作区或应用级范围拒绝新运行；进程内 lease 同样只跟踪活动 run 的释放，不再执行互斥。重叠资源键显示最近一次写入的 owner，完成或明确结束只清理该 run 当前拥有的登记。中央消息、现有进度卡、侧栏与预览布局不改变。停止、结束、结构化确认、重试、计划调整和最终验收均复用 `/workflow/run` 的 AG-UI 完整事件生命周期。停止操作先用本地 Workflow 快照即时显示 `stopping/stopped`，并让该瞬时状态优先于可能 revision 更高但尚未刷新的文件快照；后端 AG-UI 回包随后校准权威 execution，乐观更新不得改写顶层 `initialization`。

新应用在创建目录后立即通过 `applicationLifecycle.action = create` 建立 lifecycle；后续启动只使用 `get` 读取已有文件。实现不读取旧 active-planning localStorage、旧完成线程列表、`planningThreadId/planningConfirmedAt` 或 checkpoint 来推导业务阶段，缺失、损坏和未来版本都会显式失败。

首页最多同时挂载三个未完成的新应用初始化计划；每个计划按 application id 和独立 `threadId` 隔离 Workflow 快照、AG-UI 会话、停止句柄、删除状态与模板生成任务。一次只显示用户选中的全屏规划页，其余会话保持挂载并在后台继续运行。后台计划完成时只更新自己的应用索引和 lifecycle，不得抢占当前规划页或切换当前工作台；只有三个名额都被未完成计划占用时，“新建应用”才禁用。

参考架构映射保持克制：learn-coding-agent 当前公开提交只能核验 README 中的 JSONL 会话恢复、HITL、关键消息同步写和上下文压缩，不能声称存在未发布的 `src/*` 原子状态实现；OpenCode 采用稳定 session/message/question/permission ID 与事件投影，并把读取待处理问题和提交回答分成不同动作；Deep Agents/LangGraph 要求同一 thread/checkpointer 保存暂停状态，并只用显式 decision 恢复。XCodeAgent 因而把冷启动 checkpoint 读取与用户确认提交分离，同时继续由业务 lifecycle 协调首页和跨会话阶段。状态文件不复制文档、DAG、日志或会话历史，读取时按引用渐进加载，继续满足 128k 上下文预算。

当前节点逻辑允许使用占位实现，但节点名称和职责边界应保持稳定。

当节点进入 `requires_user_input` 时，前端提交上一轮 workflow payload 作为 `resumeState`，由后端推断阻断节点。首页独立规划 Graph 支持 `requirements`、`product_planning`、`ui_confirmation`、`technical_planning` 恢复；历史 `project_planning` 映射为 `technical_planning`。

所有涉及 `ProjectPlan` 生成或调整的节点，在真正进入任务拆分、构建或任何代码修改前都必须让用户确认。未确认的计划只能作为 `pending_project_plan` 或待确认状态存在，不能作为 Build/Codegen 的执行依据。`inspect_workspace` 只生成内部事实快照，不改变用户确认过的产品语义，不需要单独用户确认。

`prepare_build_tasks` 是代码生成前的最后硬保护：即使前序路由、旧会话状态或手工续跑误入该节点，只要 `project_plan.confirmation_status != confirmed`，该节点必须停止并通过 `ask_user` 要求确认，绝不能生成任务 DAG 或进入 `build`。定向 Build Context 以该已确认 ProjectPlan 的 `api_contracts` 为 endpoint 请求/响应权威来源；页面外置详情仍是页面任务的正式输入，但 endpoint 外置详情只作可选补充，缺失、未确认或失效时不得替代或否定 ProjectPlan 契约。集成测试失败后的修复不回到 `build`：`integration_test` 只负责确定性检查、质量门禁和调用只读 RepairPlanner 生成任务包；局部任务统一进入 `small_task_repair`，由共享 SmallTask Agent 执行后再回到 `integration_test`。

页面任务只继承 `frontend:*` 公共 Unit 和同页面 Unit 内部依赖，不把 `backend:endpoint:*` 或 `database:*` 编译成任务依赖。数据库前置任务完成后，BuildScheduler 可把依赖已满足且文件锁不重叠的 backend 与 page 任务放入同一批次，Build Subgraph 再按 owner 并发调用前后端 Agent；并发工作区快照按各自任务的 `change_scope/target_files/allowed_paths` 过滤后再归属，防止前端文件计入后端结果。API Contract 是并行期间的共同事实来源，`app:integration` 仍等待 endpoint 与 page 两边完成后统一验证。该设计沿用 learn-coding-agent 的契约先行、执行后验证循环，采用 OpenCode 的独立 owner/tool 执行边界，并让 Deep Agents 的专业 Agent 仅获得各自任务范围；每个 owner 仍只接收当前批次的紧凑任务与正式产物引用，符合 128k 上下文预算。

`inspect_database_context` 是任务规划前的条件节点，不属于固定主干。`inspect_workspace` 完成后，后端只读取已确认 EndpointDetail 中的 `data_origin.effective_source.kind` 或 `data_origin.source_type` 判断是否进入该节点：`mysql_existing/mysql_new_table/mysql/database/db` 进入数据库上下文检查；`third_party/external_api/http_api/api/rest_api` 直接进入 `prepare_build_tasks`，前端不展示数据库节点；`unknown/needs_user_confirmation/missing` 不再被数据库节点解释为库表错误，当前 scope 没有明确数据库来源时直接按非数据库 scope 处理。该判断不使用 `ProjectPlan.data_sources` 作为真实库表事实，也不再按 HTTP method 推断，因此 GET 列表接口只要来源于数据库也会检查真实库表。

该节点调用受控 `get_mysql_table_info` 形成 `database-context.v1` 并写入 Graph State。工具从当前 `workspaceRoot/.xcodeagent/application.json` 的 `datasource.db.plantMode` 解析连接配置，在后端内存中解密 `pwd`，连接 MySQL Server 后检查配置的 `schema` 是否存在；工作区路径和密码不进入模型可见工具参数。目标库不存在、目标表不存在、字段或约束不足都不是节点错误，而是写入 `actual_schema`、`required_schema`、`resolution_items`、`gaps` 和 `task_intents`。EndpointDetail 通过 `data_origin.differences[].resolution_kind/operation_refs/backend_adaptation` 和 `data_origin.database_operations` 明确表达字段决策；所有字段级操作统一使用 `column` 字段名和 `to` 目标字段定义，`create_table` 使用完整 `table` 对象。数据库上下文只把结构化 `create_table/add_column/alter_column_type/alter_column_nullable/alter_column_default` 编译为 required schema，不再根据 differences、processing_logic 或说明文本猜测是否改库。`database_change` 经真实 Schema Diff 后交给任务规划，`backend_adaptation` 交给后端任务处理转换、计算或默认值，`needs_user_confirmation` 在 EndpointDetail 确认前阻断。只有当前应用缺少/损坏 `application.json` 或 `plantMode` 字段、密码密文无法解密，或网络、认证、information_schema 访问失败时，节点才返回 `requires_user_input`。`prepare_build_tasks` 只消费该前置上下文，不在任务规划内部再次查库；若当前 scope 来自数据库但缺少 `schema_version=database-context.v1` 且 `status=completed` 的上下文，必须停止并要求重新检查数据库上下文。

这一路由映射到参考架构时，沿用 learn-coding-agent 的“先收集真实上下文、再规划行动”的紧凑循环；采用 OpenCode 风格的显式 session 节点和可恢复状态；符合 Deep Agents 的外层确定性门禁。为满足 128k 上下文预算，节点只保存压缩 actual schema、从已确认 EndpointDetail/API Contract 推导出的 required schema、稳定 gap id、目标 endpoint/source 标识和摘要哈希，不把原始数据库工具输出、完整 ProjectPlan 或仓库内容塞进模型上下文。

EndpointDetail 在单个 endpoint 设计链路内部固定分为两步，不新增主 Graph 节点。第一步只把单个 API Contract、依赖页面摘要和压缩数据库事实交给直接 ChatModel，生成唯一的结构化 `EndpointDecision`：数据来源、字段差异、数据库操作，以及操作类型、目标基数、选择器、零/多匹配行为、事务和副作用。只要来源或任一字段差异仍为 `needs_user_confirmation`，链路就停在决策阶段，正式详情中的 `processing_logic` 与 `acceptance_criteria` 保持为空。第二步不再调用模型，而是从已闭合决策和不可变 API Contract 确定性投影接口设计、处理逻辑和验收标准；用户审核修改数据来源或决策后也重新执行同一投影，不能分别编辑派生字段。

该边界对应 learn-coding-agent 的“先收集事实并形成行动决定，再执行并验证”循环，也对应 OpenCode 将 plan 与 build 分离的做法；XCodeAgent 的差异是把这两个阶段约束在 EndpointDetail 内部，并用正式结构化决策作为跨确认轮次的事实来源。每次只加载当前 endpoint 的契约、相关页面摘要和压缩数据库上下文，不携带完整仓库、完整 ProjectPlan 或历史会话，继续满足 128k 上下文预算。

### `classify_request_complexity`

模型辅助路由节点，负责判断用户请求进入哪条流程：

- 复杂需求：生成新应用、创建工程、涉及多页面、API、数据源、权限、登录、全栈协作等，进入完整开发流程；
- 简单需求：局部修改、文案调整、样式调整、按钮/标题改动、小范围 Bug 修复等，进入直接修改流程；
- 模糊需求：默认按复杂需求处理，因为完整流程包含需求确认，更安全。

该节点使用 `services/request_complexity.py` 中的模型语义分类器实现，并在模型不可用或返回无效结果时保守进入复杂需求流程，输出：

- `request_complexity`：`simple` 或 `complex`；
- `complexity_reason`：用于前端展示和日志排查的简短原因；
- `complexity_decision.confidence`：当前判断置信度；
- `complexity_decision.signals`：模型给出的语义判断信号或兜底信号。

该节点只决定进入完整开发流程还是直接修改流程，不负责需求分析、澄清或计划生成。

### `requirements`

由 requirements 专用 ChatModel 负责：

- 理解用户的原始需求；
- 发现缺失信息并提出澄清问题；
- 生成结构化 `RequirementSpec`；
- 生成需求 Spec Markdown 文档；
- 暂停并等待用户确认需求文档正确。

`RequirementSpec` 至少包含：

- 应用信息；
- 用户角色；
- 功能模块；
- 页面清单；
- 业务流程；
- 待确认问题。

当前实现通过 `agents/main/requirements_analyzer.py` 直接调用 `create_chat_model()`，并只绑定通用 `tools/ask_user.py`。该边界不创建 Main DeepAgent，不加载 workspace backend，也不暴露 `task`、Frontend/Data Source/Test subagent 或文件读写工具。需求分析提示词明确要求覆盖应用信息、用户角色、功能模块、页面清单、业务信息需求和业务流程，不生成数据源或验收标准；若缺失信息会实质改变产品设计，应生成 `ask_user` tool call，由后端解析为 1-4 个待确认问题；用户未要求的可选细节直接省略，不生成默认假设。

`ask_user` 是通用的人机确认工具，不包含 requirements 专用问题规则。后续项目计划、单页面设计、数据源确认等阶段需要用户输入时，也应复用该工具，由对应 Agent 根据上下文决定问题内容。

当 requirements direct ChatModel 边界判断需求不清晰时，必须先一次性审视所有关键产品事实：应用信息、角色、模块、页面清单、支撑业务信息的需求和业务流程。数据源、存储、API、数据库和验收标准分别由后续 ProductPlan、TechnicalPlan 和工程验收边界负责，不进入需求确认。它将所有无法安全推断的产品缺口合并为一次 1-4 题的 `clarification.status = requires_user_input`，Graph 在该节点后结束本轮运行并等待用户回答。前端提交回答时同时携带上一轮 workflow payload、上一版归纳需求和本轮结构化答案；后端据此推断续跑节点并生成扁平的当前请求，不重复嵌套完整会话。模型基于上一版 `RequirementSpec` 和本轮反馈返回完整 JSON，新反馈覆盖冲突旧内容，确定性服务只负责字段校验和缺省补齐。

无论初始需求是否需要澄清，只要 `requirements` 生成或更新了需求文档，就必须进入 `requirement_spec_confirmation`，要求用户明确确认文档是否正确。澄清问题的回答只用于补充需求，不能等同于对生成后文档的确认；只有用户确认当前版本后，节点才输出 `status = completed` 并继续进入 `product_planning`。若用户补充后仍存在重要缺口，模型可以再次发起一次集中澄清。用户提出修改意见时，需要重新生成文档，并再次经过确认。

等待 `requirement_spec_confirmation` 时，AG-UI workflow payload 通过只读 `confirmationArtifact` 返回当前 `requirement-spec.md` 的文件名、路径和完整 Markdown 正文，供确认卡片展示。普通需求澄清不返回该正文。首页独立规划流程还会从公开 `requirement_spec` 状态生成结构化概览；用户可在概览右上角进入编辑态，修改应用定位、页面、角色和核心业务流程。“保存并退出编辑”通过同一 AG-UI 端点的 `forwardedProps.requirementSpecDraft` 保存动作立即重写待确认 Markdown/JSON 并刷新确认卡，但保持 `pending_user_confirmation`；最终确认仍通过恢复请求的 `forwardedProps.editedRequirementSpec` 进入确认门禁。确认卡右下角意见框的自然语言内容直接作为本轮确认答案提交：空输入或“好的/OK/确认/没问题/继续”等认可语义会确认当前需求文档并继续规划；“增加/删除/修改/调整/补充/不需要/改为/不是/而是”等修改语义优先于认可语义，必须基于现有 RequirementSpec 重新生成完整需求文档并再次等待确认。`forwardedProps.requirementSpecFeedback` 仅保留兼容审计信息，不再决定确认语义。

确认时以 Markdown 作为用户可读、可编辑的文档。概览编辑器保存时，后端只合并白名单可见字段，已有条目按稳定 id 保留隐藏结构，然后由同一份待确认 RequirementSpec 同时重写 `requirement-spec.md` 与 `requirement-spec.json`；该保存动作不等同于确认，也不允许进入 ProductPlan。最终确认时再次以当前草稿为准同步并标记 confirmed。如果用户在确认前直接修改了 RequirementSpec Markdown，节点必须先与当前结构化状态对比，以原 JSON 为基线同步 Markdown 中的业务变更并保留 Markdown 未表达的内部字段，然后更新内部 JSON；不得先重写 Markdown 或直接使用旧 JSON 继续。JSON 文件只供工作流节点读取，不作为前端可编辑产物展示。

当前等待/续跑机制是显式的后端推断续跑点，还不是 LangGraph 原生 `interrupt` resume。主 Graph 已接入 SQLite checkpointer，用于持久化 ProjectState、支持服务重启后的状态恢复和调试；但用户输入后的续跑路由仍由后端根据当前状态显式推断。后续如果切换到 LangGraph `interrupt` 和 command resume，应保持同样的原则：前端提交用户回答和 thread 标识，不硬编码后端阶段名。

所有选项型 `ask_user` 问题（单选、多选、是/否）都自动包含“其他”选项。用户选中“其他”后必须填写补充内容；前端提交结构化答案 `{ selected, other }`，后端将其归并为“已选：…；其他补充：…”，与原始需求和既有选项一起输入给后续模型。文本题本身就是自由输入，不额外显示“其他”。

生成选项题前，模型必须先判断选项是否互斥。搜索、筛选、导入导出、分页等可叠加能力必须使用 `multiSelect = true`，并将每项能力作为独立选项；不得通过“搜索 + 导入导出”这类组合选项伪造单选。只有数据源类型、认证策略等真正的二选一或多选一决策使用单选。

Graph 节点只接收直接 ChatModel 边界产出的结构化 `RequirementSpec` 和澄清结果，负责写入需求文档并更新状态，不应自行进行需求分析。

### `direct_modification`

简单需求专用节点，负责在已有工程上下文中直接完成小范围修改：

- `classify_request_complexity` 只有在 AG-UI 请求带有合法 `editorMode` 时才允许简单需求进入该节点；
- `frontend` 会话的局部修改交给 Frontend Generation Agent，`backend` 会话交给 Data Source Generation Agent；
- 缺少合法 owner、归属不清或需要跨层修改时回退完整需求与规划流程；
- 识别修改目标和允许修改的文件范围；
- 必要时按修改范围委派给 Frontend 或 Data Source CodeRunner；
- 执行局部文件修改；
- 输出结构化修改结果、变更文件、执行命令和风险提示；
- 进入 `integration_test`，复用后续测试、质量门禁、启动和验收流程。

该节点不生成完整 RequirementSpec、项目级计划或任务 DAG。若执行过程中发现需求实际涉及架构、契约、数据模型或多页面联动，应升级为复杂需求并回到完整开发流程。

该设计映射到 learn-coding-agent 的显式工具分派模式和 OpenCode 的命名专业 Agent 模式：外层 Graph 根据已校验的会话模式确定 owner，现有 CodeRunner 在其既有 workspace backend、memory 和权限边界内执行；不恢复已删除的 Main DeepAgent，也不通过任意字符串动态扩展 Agent 权限。

### `project_planning`

负责生成项目级计划：

- 需求概述；
- 技术架构；
- API 契约；
- 页面清单；
- 数据源清单；
- 权限模型；
- 页面和数据源之间的依赖。

该节点由 project-planning 专用 ChatModel 执行项目级规划：读取 `RequirementSpec`，产出结构化 `ProjectPlan` 和用户可确认的总体计划书 Markdown 文档。这个阶段不生成业务代码。后续任务拆分直接从 `ProjectPlan` 的页面、数据源、API 契约和已确认详情中派生执行输入，避免在计划阶段持久化重复的 `task_inputs`。

该节点通过 `agents/main/planner.py` 直接调用 `create_chat_model()` 生成结构化 JSON 规划建议，再由确定性 schema 合并和归一化后写入 Graph State。该调用不绑定任何工具，不创建 DeepAgent，也不扫描 workspace；模型输出只用于细化项目级判断，确定性归一化负责保证稳定 id、必需字段和后续任务拆分可读取的结构。

`project_planning` 在输出计划前会内部核对 API 契约、页面清单、数据源清单、依赖、角色、流程和验收标准；确定性门禁同时校验每个 Endpoint 的请求/响应 Schema 引用、契约内 `$ref`、数据源 Schema 引用、页面 Endpoint 引用和响应字段绑定。校验失败时最多把完整错误回灌给规划模型自动修订一次，修订后仍失败则停留在计划阶段；即使自动修订成功，也必须展示新版本并重新等待用户确认。普通缺口以明确假设和风险写入同一份计划，而不是拆成后续多轮追问。生成计划书后进入一次 `project_plan_confirmation` 等待状态。用户确认“正确，继续”等语义后，节点会再次执行同一套一致性校验，只有通过后才输出 `status = completed`；首页的新建应用规划流程校验 specs/plans 产物后直接打开工作台，由用户从 `frontend_pages` 中选择页面，再启动主 Workflow 的 `detail_confirmation`。如果用户提出调整意见，则重新生成/调整 `ProjectPlan` 并再次等待确认。

等待 `project_plan_confirmation` 时，AG-UI workflow payload 的只读 `confirmationArtifact` 只返回当前 `project-plan.md`，不会同时返回 RequirementSpec 正文。用户提交调整意见并重新生成计划后，下一轮确认展示新写入的 Markdown；`detail_confirmation` 不复用该载荷展示 ProjectPlan。

ProjectPlan 同样以 Markdown 作为用户确认入口。确认前若 Markdown 被直接编辑，节点先将改动同步到内部 ProjectPlan JSON，并执行 API 契约、页面清单和数据源一致性校验；同步成功后才允许确认并进入后续节点。AG-UI 产物列表只展示 Markdown 等用户可读文件，所有 JSON 路径和 JSON 任务文件都属于内部工作流状态，不向用户呈现为可编辑产物。

API 契约在此阶段作为前后端共享的唯一字段事实来源生成。为保持简约和可扩展，每个 contract 只包含资源级 `schemas`、稳定 endpoint id、HTTP method、path、参数、请求/响应 schema 引用、错误码和权限要求。`data_sources` 只能保存 `schema_refs`，不得复制字段；ProjectPlan 不生成页面与数据源/API 的绑定关系。页面详细设计只能通过 `api_dependencies` 和 `response_bindings` 引用已声明 endpoint 与响应字段。`detail_confirmation` 若发现字段或接口缺口，应提出 ProjectPlan 调整并经过确认，不能自行补字段或发明独立接口。

`ProjectPlan` 至少包含：

- `requirements_overview`：需求概述、应用目标、用户角色、功能模块、业务流程和验收重点；
- `project_acceptance_criteria`：整个需求在项目完成时必须满足的验收标准；
- `architecture`：前端、后端、数据和测试策略；
- `api_contracts`：唯一的业务字段 Schema、资源 endpoint 和输入输出 Schema 引用；
- `frontend_pages`：菜单树与页面叶子混合结构；菜单节点至少包含 `name`、`unique_path`、`children`，页面叶子保留 `pageId`、名称、路由、描述、模块归属、状态、权限及 `references`；
- `data_sources`：数据源类型、实体、`schema_refs` 和 Seed 策略，不重复保存字段；
- `permission_model`：角色、页面访问规则、操作权限和默认权限策略；
- `risks`：后续细节确认阶段需要消化的风险和待细化点。

### `detail_confirmation`

新流程中，该节点不再生成 PageDetail。页面目标读取 `PageImplementationContract.requiredEndpointIds`：已具备全部 EndpointDetail 时直接完成；存在缺口时批量生成并确认接口详情。显式 endpoint 目标仍使用同一接口分支。以下页面详设段落仅用于旧 checkpoint 和旧 `plans/pages/` 文件的只读兼容。

> 历史行为（不参与新运行）：以下 PageDetail 生成、审核、持久化和字段说明只用于解释旧 checkpoint。新运行不得进入这些分支，也不得新写 `plans/pages/`。

基于用户在工作台选择的页面生成并审阅详细设计，负责：

- 读取正式 `ProjectPlan.frontend_pages` 菜单树和 `ProjectPlan.data_sources`；
- 将工作台通过 AG-UI 提交的 `selectedPageId` 写入 `ProjectState.selectedPageId`；
- 为所选页面和计划内数据源生成初版详细设计；未提供选择时按全量页面审阅；
- 在同一审阅界面按页面和数据源分组展示，默认折叠；
- 用户只展开需要调整的对象，按页面目标、布局、交互、权限、关系、校验和 Seed 等模板字段修改；
- 核对数据模型、关系、校验规则和 API 映射；如需字段变更则返回 ProjectPlan 契约调整；
- 未修改时允许一键确认全部设计；
- 将确认后的页面详细设计写入 `.xcodeagent/plans/pages/`，EndpointDetail 写入 `.xcodeagent/plans/endpoints/`；`ProjectPlan` 只保存每个产物的路径、状态、哈希和生成依赖，作为单页面生成的唯一索引入口。

该阶段由只读规划逻辑和 page-design 专用 ChatModel 负责，不由代码生成 Agent 负责。

当前实现使用 `xcodeagent.detail_review.v1` 审阅 payload。首次进入该节点时，节点从 `ProjectPlan` 读取用户选择的页面和数据源；如果正式计划中的 `frontend_pages` 是菜单树，则先递归拍平业务页面叶子，再生成对应 `page_detail_plans` 和 `data_source_detail_plans`，写入 `pending_project_plan`，然后一次性暂停。前端提交 `detail_review` 结构化结果并携带 `resumeState`，后端只合并白名单模板字段、执行契约一致性校验并确认当前计划，不再逐个选择对象或产生多轮中断。

页面初版设计结合 `frontend_pages`、`api_contracts`、`data_sources`、`permission_model` 和业务流程，覆盖页面目标、页面布局设计、页面交互设计、API 依赖、响应字段绑定、页面跳转与依赖、权限与操作可见性、页面验收标准。`detail_confirmation` 在调用 `page_designer` 前逐项检查当前页面声明的 `endpoint_dependencies`：缺少正式 EndpointDetail 时复用独立 endpoint 设计链路补生成，并把待确认的 EndpointDetail 与 PageDetail 放入同一轮审核；已有正式详情不重复生成。`page_designer` 只读取这些 EndpointDetail 的接口标识、状态、数据来源类型、Schema 引用和处理逻辑摘要，不接收完整 EndpointDetail 正文。`PageDetail.api_dependencies` 是页面详细设计确认后的实际 API 依赖。页面详细设计面向页面实现视角，页面数据访问必须直接引用具体 API/Endpoint，而不是把底层数据源作为主要确认对象。布局设计只描述信息组织、区域职责、主要内容呈现、操作入口位置和响应式/信息密度策略；loading、empty、error、success、confirm、validation 等反馈属于交互设计，不作为布局区域。页面的 endpoint、Schema 和 `response_bindings` 在审阅界面只读；用户不能在详情层新增字段或接口。需要修改契约时必须返回 `project_planning`，更新 ProjectPlan 后重新确认。

接口详细设计是 `detail_confirmation` 的 endpoint 内部分支，不新增独立主 Graph 节点。该分支先定位接口契约、分析请求参数、确认数据来源，再设计返回格式、处理逻辑和接口验收标准。确认数据来源时，如果当前 endpoint 关联的数据源是 MySQL/数据库，且 `XCODEAGENT_ENDPOINT_DATABASE_CONTEXT_ENABLED` 显式开启，后端会调用绑定当前工作区的 `get_mysql_table_info` 获取库表信息，并将结果提取概括为 `endpoint_context.database_context` 后输入 endpoint 详细设计模型；模型只能使用该摘要作为现有数据库参考，不接收原始全量工具输出、应用配置或明文密码。连接字段来自 `workspaceRoot/.xcodeagent/application.json` 的 `datasource.db.plantMode`；Renderer 持久化前使用 `/health` 公钥加密 `pwd`，后端只在本次查询内存中解密。EndpointDetail 不兼容旧的自由文本数据库决策：每个差异必须显式选择 `already_supported/database_change/backend_adaptation/needs_user_confirmation`，数据库变更必须引用完整 `database_operations`；`database_context` 完成后模型必须直接给出数据库设计——目标表已存在时使用 `mysql_existing` 并提供字段映射与增列/改列 `database_operations`，目标表缺失或数据库不存在时使用 `mysql_new_table` 并直接设计完整 `create_table` 操作；只有 `database_context` 不可用（跳过或失败）且来源确实无法判断时才进入 `needs_user_confirmation`；`operation_refs` 只允许出现在 `database_change` 差异上，`mysql_new_table` 的 `create_table` 由来源类型本身声明、不要求额外字段差异重复引用，也不能产生可执行操作。

批量初版设计生成后统一进入一次整体确认。用户提交的页面/数据源修改是对当前可见模板字段的最终确认，后端不得在提交后继续生成用户未审阅的新内容。确认成功后 `pending_project_plan` 才提升为正式 `project_plan`；详细设计文件和轻量 ProjectPlan 索引一起持久化，随后才允许进入 `prepare_build_tasks` 和后续代码生成。

正式 `project-plan.json` 不内嵌 `page_detail_plans` 或 `endpoint_detail_plans` 正文。项目规划模型在首次生成时必须完成页面依赖自检：每个页面叶子的 `pageId` 和 `path` 全局唯一，数据型页面声明已存在的 `endpoint_dependencies`，跳转目标属于 `frontend_pages`；菜单节点本身不参与页面唯一性或任务执行校验。后端只从 endpoint 反查数据源，不接受第二份自由维护的 `data_dependencies`。页面详情只原样投射 `permissions`、`endpoint_dependencies` 和 `navigation_targets` 到 `references`，并为每个依赖记录独立 EndpointDetail 的 JSON/Markdown 路径、状态和哈希；页面文件不复制 EndpointDetail 的 `data_origin`、`interface_design` 或 `processing_logic` 正文。持久化时必须先写 `.xcodeagent/plans/endpoints/`，再写 `.xcodeagent/plans/pages/`，任何页面 endpoint 依赖缺少独立详情索引都应直接失败。页面设计模型不得新增、删除或替换这些引用。后续单页面任务生成必须从 endpoint id 反查 API contract、Schema 与 EndpointDetail，不得重新加载全部页面详情；模型发现 ProjectPlan 缺少接口或跳转时，必须停止并要求回到 ProjectPlan 修订、重新确认。

当前等待/续跑机制仍是显式状态推断而非 LangGraph 原生 `interrupt`。SQLite checkpointer 负责持久化每个 `thread_id` 的主 Graph 状态；后续若切换到 checkpointer + command resume，应保留同样的状态边界：Graph 节点只恢复阻断节点需要的 ProjectPlan 和 detail review 小型结构化状态，不把完整会话历史重新塞回上下文。

页面详细设计至少包含：

- 页面基本信息：页面 ID、名称、路由路径、页面目标；
- 页面布局设计：整体布局、区域划分、主要内容呈现方式、操作入口位置、响应式与信息密度；
- 页面交互设计：查询、新增、编辑、删除、刷新、批量操作、提交/取消、页面内跳转，以及 loading、empty、error、success、confirm、validation 等状态反馈；
- API 依赖：页面直接使用的 API contract、endpoint、method、path、用途和请求/响应 Schema；
- 响应字段绑定：接口响应字段到页面字段、表格列、详情项或表单初值的映射；
- 页面跳转与依赖：内部页面跳转、目标页面/路径和触发条件；
- 权限与操作可见性：页面访问角色、按钮/危险操作可见性和无权限行为；
- 页面级验收标准。

`prepare_build_tasks` 生成任务 DAG 前必须执行确定性的 API 契约一致性检查：数据源不得包含独立 `schema`；所有 schema/endpoint 引用必须存在；页面 `response_bindings.source_path` 必须来自所依赖 endpoint 的响应 Schema；写接口必须声明请求 Schema，非删除接口必须声明响应 Schema。任何错误都会在任务拆分前阻止构建；`integration_test` 不重复校验已经确认并通过任务准备门禁的 ProjectPlan。

### `inspect_workspace`

确定性、可缓存的工作区检查节点，负责在任务拆分前生成 `WorkspaceSnapshot`，并为后续执行 Agent 准备工作区级代码图索引：

- 解析当前 workspace revision，包含 Git HEAD、暂存区 diff、未暂存 diff、未跟踪文件清单、关键 lock/config 文件和 inspector schema 版本；
- 命中 `.xcodeagent/cache/workspace-snapshots/{workspace_revision}.{schema_version}.json` 时直接复用；
- 未命中时用轻量扫描识别项目根、技术栈、入口文件、构建/测试命令、FastAPI 路由、Pydantic 模型、Workflow 节点、React 组件、API client、Electron IPC、AG-UI 使用点和共享契约候选；
- 将完整 snapshot 写入 `.xcodeagent/cache/workspace-snapshots/`，Graph State 只保存 `workspace_snapshot_summary`、`workspace_snapshot_path`、`workspace_snapshot_hash` 和 `workspace_revision`；
- 只对显式用户 `workspaceRoot` 的安全源码清单内嵌调用 `code-review-graph`，在 `.xcodeagent/cache/code-graph/v1/` 维护 `graph.sqlite3` 与 `index.json`；按 revision 执行 cache hit、增量更新或全量构建，超时或失败时继续使用文件搜索。
- `snapshot.code_graph` 只保存有界的文件、节点、关系、语言、代表性符号和脱敏 warning 统计，供 AG-UI 工作区扫描卡片展示；扫描节点不针对当前请求查询符号，也不保存 request-scoped 导航上下文。

该节点不生成任务、不修改业务代码，也不把快照写入 `ProjectPlan`。它只回答“当前工作区事实是什么”并准备可查询索引，供后续模型规划、确定性调度和执行 Agent 导航使用。

### `prepare_build_tasks`

先由确定性服务根据已经确认并写回的 `TechnicalPlan` 生成完整 `build-dag.v3` Unit DAG 骨架，再由 planning-only ChatModel 只根据当前范围的 `PageImplementationContract`、React UI 引用、EndpointDetail、真实数据库摘要和 `WorkspaceSnapshot` 生成可执行静态 task DAG：

- 使用 `inspect_workspace` 生成的 `WorkspaceSnapshot` 作为唯一工作区事实来源，不读取、创建、修改或删除代码文件，也不查询或注入 request-scoped 代码图上下文；
- 生成稳定的 `task_id`；
- 指定任务 owner，只允许 `database`、`backend`、`frontend`；
- 指定任务类型，例如 `database.change`、`database.seed`、`database.verify`、`backend.code`、`backend.verify`、`frontend.code`、`frontend.verify`；
- 数据库相关任务必须写入 `database_scope`，并通过 `requires_capabilities` / `provides_capabilities` 表达“数据库已准备好”等能力边界；
- 删除字段、删除表、删除数据、不可逆迁移等高危数据库任务必须标记 `risk = high` 并写入 `approval`，后续调度不得在未获用户同意时执行；
- 计算任务依赖；
- 标记可并行任务；
- 以 `change_scope` 记录新增、修改、删除文件及每项改动目的，并据此设置允许修改的文件范围；
- 以 `impact_scope` 记录受影响模块、公共契约、风险和影响摘要；
- 绑定验收标准；
- 初始化任务状态为 `pending`，后续只在 `pending/running/completed/failed` 中流转；
- 校验循环依赖和缺失依赖。

Unit Graph 是跨 Unit 依赖的唯一权威来源。页面 scope 必须从已确认 `PageImplementationContract.requiredEndpointIds` 得到 `required_endpoint_ids`，逐个加载对应独立 EndpointDetail，再把每个接口交给既有 endpoint Unit 任务规划规则；endpoint 任务不嵌入 page Unit。模型显式依赖只用于同 Unit 内排序；已知的跨 Unit 显式边会被确定性移除，并在任务 `dependency_rewrites` 中保存改写证据，再由 Unit Graph 编译正确的跨 Unit 任务边。未知任务依赖仍保留为 validation error。无效 task graph 必须保留 `task_registry` 的全部任务和错误，读取方不得用部分 `topological_order` 静默缩减任务数；`prepare_build_tasks` 会在校验错误时阻止进入构建。

`build-dag.v3` 的 Unit ID 采用新的层次：

- `application:root` 表示整应用根；
- `database:<dataSourceId>` 聚合同一数据源下所有建表、迁移、Seed 和数据库校验任务；
- `backend:bootstrap` 表示后端工程基础能力；
- `backend:endpoint:<apiContractId>:<endpointId>` 表示单个接口的后端实现范围；
- `frontend:shell`、`frontend:route-registry`、`frontend:api-client`、`frontend:auth-guard` 表示前端公共能力；
- `page:<pageId>` 表示页面实现范围。

页面 Unit 依赖它使用的 backend endpoint Unit。database Unit 保留在全局 Unit 骨架中，但不会仅因 API Contract 声明了 `data_source_id` 就自动成为依赖；只有当前 scope 的已确认 EndpointDetail 明确指向数据库时，才动态增加 `database:<dataSourceId> → backend:endpoint:<apiContractId>:<endpointId>`，从而形成 `database → endpoint → page`。mock、静态或第三方接口只形成 `endpoint → page`。该实现直接复用既有 `database-context.v1.gaps/task_intents`、database task 归一化和审批逻辑，不新增另一套数据库任务生成器。

代码图不参与 DAG 任务生成。进入 `build` 后，Frontend Agent 与承载 backend task 的 DataSource Agent 才按 `task_id` 使用绑定当前 `workspaceRoot` 的 `code_graph_context`：已有目标文件优先查询 `file_summary`，未知业务符号优先查询 `search_symbols`，命中后再按需查询引用、影响和相关测试。只有 `status=ready` 且 `matches/relations/relatedTests/impactedFiles` 至少一项非空的结果才作为导航；空结果、异常或不可用状态会立即降级为任务 `target_files/allowed_paths/change_scope` 内的文件搜索和真实源码读取，不会令任务失败或扩大写入授权。代码图始终不是源码事实，修改前必须读取当前文件。

任务规划模型输入中的 `database_planning_context` 只来自前置 `inspect_database_context` 节点：当当前 endpoint scope 或页面/API scope 内存在 `EndpointDetail.data_origin` 明确指向数据库的接口时，前置节点已经调用 `get_mysql_table_info` 读取真实库表事实，并生成 `schema_version=database-context.v1` 的上下文；`prepare_build_tasks` 再把该上下文与 EndpointDetail、API Contract 一起放入 `TaskPreparationContext.executable_details`。模型必须把 `actual_schema` 作为唯一真实数据库结构来源，把结构化操作编译出的 `resolution_items/gaps/task_intents` 作为拆分依据：只有真实 Schema Diff 仍存在的 `database_change` gap 才能生成 database task，且任务必须携带对应 `task_intent.database_scope`；`backend_adaptation` 只能生成 backend task；`needs_user_confirmation` 不得被转成半截 build task。外部 API scope 不携带数据库上下文，也不得生成 database Unit 或 database task。

任务 DAG 保存前会执行确定性语义校验，不只校验拓扑：`owner=database` 的任务必须挂在 `database:*` Unit，`task_type` 只能是 `database.change`、`database.seed` 或 `database.verify`，必须声明非空 `database_scope`，不得包含 Java/TypeScript/Python/样式等代码文件路径；高危数据库操作如删表、删字段、删除数据、truncate 必须声明 `approval.required=true`。Backend task 不得声明 `database_scope` 或 `database.*` task_type；backend/page/frontend Unit 的 owner 必须与 Unit 语义一致。违反这些规则时 `task_graph.validation.is_valid=false`，`prepare_build_tasks` 不会进入 Build。

Build Task 使用独立的工程验收层，不复制 ProductPlan、UiDesign、TechnicalPlan 或 EndpointDetail 中的业务验收。角色数据范围、业务状态转换、页面可见内容、筛选交互、loading/empty/error 和点击行为分别保留在 ProductPlan 与 React UI 稿中，并通过 `PageImplementationContract` 进入代码生成上下文；任务规划模型必须返回空 `acceptance_criteria`。确定性编译器忽略模型输出的验收文案，依据 `owner/task_type/change_scope/allowed_paths/source_refs/build_context` 生成带稳定 ID 的内部 `acceptance_checks`，再把检查描述投影到兼容前端的 `acceptance_criteria: string[]`。检查范围仅包括精确文件操作、授权路径边界、菜单登记、前端集中 service 与正式 API/Schema、Spring Mapping 与 DTO/响应模型、数据库 schema gap 和必要审批；build、lint、typecheck 和 test 仍属于后续集成质量门禁，不重复成为 Build Task 验收点。

恢复旧 `build-dag.v3` 时会从任务元数据重新编译工程检查并清除旧业务验收文案。若旧接口任务缺少生成 API/Spring 契约检查所需的 Endpoint/API Contract 上下文，任务图校验失败并要求重新执行 `prepare_build_tasks`，不得用泛化文案或 Agent 自报证据继续执行。

数据库任务的生成条件由 `database-context.v1.gaps/task_intents` 兜底：如果 database_change gaps 为空，模型生成的多余 `database.change` 会被移除；如果 database_change gaps 非空，每个 gap 都必须被 database task 覆盖。模型生成数据库任务但漏 `database_scope` 时，编译器会在能唯一匹配 gap intent 的情况下补齐；无法唯一匹配时返回精准语义错误，而不是泛化为缺失依赖或循环依赖。缺库、缺表、明确需要补的缺字段、明显类型不兼容、nullable 不满足等才转成数据库任务；聚合字段、返回 null/0、后端类型转换等进入 backend_adaptation。真实库里多出来的对象不会自动生成删除任务。

Build DAG 只注册具有 `change_scope`、`allowed_paths`、`target_files` 或数据库 `database_scope` 的可执行任务。仅检查已有前端壳、路由或布局的候选任务不会进入任务注册表，统一交给 `integration_test` 验证；`WorkspaceSnapshot` 能证明已有能力时，相应 `build_units.status` 记为 `reused` 并保存 `reuse_evidence`。

该节点不生成新需求，也不编写业务代码。`ProjectPlan` 只参与 `unit_graph` 和 `build_units` 骨架生成，不包含具体可执行 task；模型输入中的 `application_skeleton` 仅作非执行背景。数据库上下文在前置节点通过受控工具获取并压缩为 `database_planning_context`，不从 `ProjectPlan.data_sources` 臆测真实库表。模型负责将当前已确认的页面详细设计、相关 endpoint 详情、API Contract、新版数据库上下文和当前工程结构转换成可执行 task DAG；Graph 节点只接收结构化 `build_task_plan`、执行确定性归一化与 DAG 校验、更新 `tasks`，并交给后续 Build Subgraph 执行。

该边界与参考架构的映射保持明确：沿用 learn-coding-agent 的紧凑“收集目标上下文→规划动作→验证 DAG”循环；沿用 OpenCode 的 plan/build 分离，由 planning-only 模型产生候选、确定性编译器掌握执行边界；沿用 Deep Agents 的渐进上下文原则，只把当前 `PageImplementationContract`、React UI 路径、required EndpointDetail、相关 API Contract 和压缩数据库事实放入模型上下文，完整内容继续保存在独立文件中。XCodeAgent 的差异是跨 Unit 顺序不交给模型或子 agent 自由决定，而由可恢复、可审计的 Unit Graph 固定编译，以适应页面级重复规划和 128k 上下文预算。

任务 DAG 的用户心智必须按应用级和页面级组织：用户看到和推进的是应用基础能力、页面生成、页面验收和整体集成验证。内部 DAG 仍保留 API、数据、共享组件、权限、路由和测试等支撑任务，并通过依赖边把它们挂到对应页面任务之前或页面任务组内。不得把用户可见计划退化为底层 Agent/文件操作清单；后续生成执行应优先以“生成某个页面及其支撑 API/交互/验证”为自然工作单元。

该节点通过 `agents/main/task_preparer.py` 调用 direct ChatModel 生成任务编排建议，再由确定性 schema 归一化为静态 Build DAG。`build_task_plan.workspace_analysis` 优先使用模型返回的结构化摘要；缺省时由 `WorkspaceSnapshot` 兜底，并记录 `workspace_snapshot_ref` 以便恢复和审计。模型未返回可解析任务时，节点必须阻止进入 `build`，不能用硬编码任务清单代替模型规划结果。

调用模型生成任务 DAG 前，节点必须检查 `ProjectPlan.confirmation_status == confirmed`。若计划未确认，节点返回 `requires_user_input` 并停止在当前阶段；用户确认后可从 `prepare_build_tasks` 续跑，再生成任务 DAG。

`build_task_plan` 至少包含：

- `tasks`：可执行任务 DAG；
- `summary`：任务数量统计；
- `workspace_analysis`：任务拆分前实际检查到的代码结构和工程约定；
- `prepared_by`：执行任务编排的 Agent、运行方式和模型信息；
- `coordination`：任务分发顺序、依赖策略和串并行执行批次。

节点成功后必须写入两个任务 DAG 产物：

- `.xcodeagent/plans/build-task-plan.json`：内部结构化状态，供 BuildScheduler、调试续跑和后续节点读取；v3 task registry 使用 snake_case 单一字段，不再写入或读取 `task_id/dependsOn/targetFiles/acceptanceCriteria/canRunInParallel` 等旧 DAG 同义字段，`agent_note` 只保留短摘要和响应 hash；
- `.xcodeagent/plans/BUILD_TASK_DAG.md`：人类可读的任务 DAG 摘要，展示任务、owner、依赖、change scope 和验收标准。该 Markdown 不作为用户编辑入口，修改任务 DAG 仍应通过确认后的 ProjectPlan 或重新执行 `prepare_build_tasks` 完成。

任务准备期间通过 LangGraph custom stream 发送 `prepare_build_tasks.progress` 完整快照，AG-UI 运行层将其投射到同一个 `workflow:prepare_build_tasks` 的 `agent-process.dagGeneration` 字段。快照固定按 Unit 骨架、目标上下文、契约校验、模型规划、任务编译、DAG 校验和产物保存七阶段排列；每个阶段可携带冻结的结构化 `output`，前端将候选任务归入模型规划阶段、最终任务表归入任务编译阶段、产物表归入产物保存阶段。最终任务按有效拓扑序展示，无效图则保留完整 task registry。公开快照只包含安全摘要、变更路径、验收标准和 Markdown 产物标签，不发送模型原文、WorkspaceSnapshot 正文或内部 JSON 路径。

`stages[].output` 是严格的 `kind` 判别联合：`unit_graph` 包含 Unit（id/type/status/taskCount）、Unit 依赖边和骨架校验；`build_context` 包含目标 type/id、关联 Unit/Endpoint/API Contract/数据源及数据库摘要状态；`contract_validation` 包含校验范围、通过状态和问题；`candidate_tasks` 包含候选任务、负责人、依赖和 owner 汇总；`compiled_tasks` 包含最终拓扑任务、变更文件、验收标准、任务依赖边和 owner 汇总；`dag_validation` 包含根/叶任务、拓扑顺序、执行批次（串/并行）和校验错误；`artifacts` 仅包含内部计划状态与 Markdown DAG 的安全标签。列表字段最多 200 条、文本最多 1000 字符，依赖边最多 500 条并带 `truncated` 标记；顶层 `tasks`、`artifacts` 仅作为兼容投影保留。阶段完成或失败后产物冻结，后续阶段更新不得覆盖早期详情。历史会话重入时，前端以已完成 Workflow 事件、状态和结果中的 DAG 快照回填已持久化的步骤；若多个来源同时存在，优先选择包含更多阶段 `output` 的完整快照，避免旧的中间进度帧覆盖完成产物。

该设计沿用 learn-coding-agent 的“先侦察、再计划、执行后验证”循环，并采用 OpenCode 风格的稳定任务 ID、显式状态和文件冲突串行化。与 Deep Agents 的默认 harness 映射是：`prepare_build_tasks` 只负责 planning，不挂载文件工具；后续 BuildScheduler 与代码执行 runner 负责 action/verification。为控制 128k 上下文预算，模型只接收已确认计划、快照摘要和精确文件清单，不把完整目录树或文件内容复制进 Graph State。

该节点的结构化产物必须落盘，供后续恢复执行和单节点验证使用：

```text
{workspace}/.xcodeagent/specs/requirement-spec.{md,json}
{workspace}/.xcodeagent/plans/project-plan.{md,json}
{workspace}/.xcodeagent/checkpoints/checkpoints.sqlite
{workspace}/.xcodeagent/cache/workspace-snapshots/{workspace_revision}.{schema_version}.json
{workspace}/.xcodeagent/plans/build-task-plan.json
{workspace}/.xcodeagent/plans/repair-task-plan.json
{workspace}/.xcodeagent/reports/test-report.json
```

`project-plan.md` 和 `requirement-spec.md` 面向人类阅读；节点恢复执行必须优先使用同目录下的 JSON 文件。若要跳过前序节点单独验证任务 DAG 生成，可执行：

```bash
app-demo-prepare-build-tasks var/workspaces/demo-project/.xcodeagent/plans/project-plan.json
```

本地调试某个节点时，使用前端 Chat Composer 的“Workflow 调试”面板选择开始节点，并填写已落盘 JSON 产物路径，避免每次从头生成需求文档。调试面板通过 AG-UI `forwardedProps.workflowDebug` 传入 `resumeFrom`、`requirementSpecPath`、`projectPlanPath`、`workspaceSnapshotPath` 和 `buildTaskPlanPath`；工作台的失败任务恢复则使用独立的 `forwardedProps.workflowAction = retry_failed_tasks`，优先重试瞬时失败任务；若当前已有无需额外确认的 RepairPlanner 计划，则执行该修复任务集。恢复快照缺少计划时，协议适配器会从当前 workspace 的 `.xcodeagent/plans/build-task-plan.json` 与 `.xcodeagent/plans/repair-task-plan.json` 补回内部状态，不依赖自然语言或调试节点选择。

如果要从已经生成的项目计划调试后续节点，可填写 `project-plan.json` 并把开始节点设置为 `detail_confirmation`、`inspect_workspace`、`inspect_database_context` 或 `prepare_build_tasks`。从 `prepare_build_tasks` 或后续节点续跑时，也可以填写 `.xcodeagent/cache/workspace-snapshots/<revision>.1.0.0.json` 复用已生成的工作区快照。调试续跑仍会遵守确认闸口；未确认的 `ProjectPlan` 不会进入代码生成。为兼容已有项目，调试目录解析仍能读取旧的 `specs/` 和 `plans/` 路径；新写入统一使用 `.xcodeagent/`。

### `build`

`build` 在外层主 Graph 中表现为一个节点，但内部应实现为 Build Subgraph。它负责：

- 选择依赖已经满足的任务；
- 将页面任务派发给 Frontend Generation Agent；
- 将数据源任务派发给 Data Source Generation Agent；
- 收集结构化执行结果；
- 校验实际文件和命令结果；
- 更新任务状态；
- 在没有文件冲突时并行执行任务。

Build Subgraph 当前由确定性 `BuildScheduler` 驱动：

```text
scheduler loop:
  → select dependency-ready and lock-compatible task batch
  → dispatch each task batch to owner CodeRunner
  → validate structured TaskResult
  → apply results and update ProjectPlan/build_task_plan/tasks
  → continue until completed, blocked, failed, needs_repair, or requires_confirmation
```

`BuildScheduler` 只做确定性调度，不作为 DeepAgent：

- `select_ready_build_batch`：只选择 `pending` 且依赖全部 `completed` 的任务；依赖 `failed` 的下游任务保持阻塞；
- 文件锁来自 `lock_scope`、`change_scope.path`、`target_files` 和 `allowed_paths`，同一批 ready task 之间不能冲突；
- 任务按 `owner` 派发给对应 CodeRunner：`database` 使用 Database Change Agent，`backend` 使用 Data Source Generation Agent，`frontend` 使用 Frontend Generation Agent；
- CodeRunner 只返回结构化 `TaskResult`，不更新 DAG；
- 同 owner 批次的 workspace diff 必须按每个任务的授权路径重新归属；一个任务只能记录命中自身范围的 `changed_files`，不能把整批变更复制给所有结果；
- `database` owner 不参与 workspace diff 归属；它必须在执行前重新获取当前真实数据库摘要，由只读 Database Change Agent 生成 SQL 计划，再由确定性数据库执行服务完成风险分类、审批和执行证据记录；
- Frontend/Data Source CodeRunner 通过 Deep Agent `messages + values` 主图及子图流捕获 `ls/read_file/glob/grep/write_file/edit_file/delete_file`；内置 `task` 委派后的子代理调用使用流命名空间隔离调用 ID，保证活动持续刷新。系统只把归一化中文文案和虚拟路径作为临时工具活动投影；文件内容、替换参数、工具结果、宿主机路径以及 `write_todos/task/execute` 不进入 UI；
- 工具活动优先按 `allowed_paths`、`target_files`、`change_scope` 归属具体运行任务，无法精确命中时回退当前 owner 批次。它只存在于实时 `buildExecutionSlice.tasks[*].activeToolActivity`，新活动覆盖旧活动，批次结算后清除，不写入 BuildTaskPlan 或 Workflow 历史；
- 调度器校验缺失或非法结果：合法 JSON 但遗漏已派发任务时记为 `runner_protocol_error`；发现字符串正文中的明显未转义双引号时只执行一次确定性修复；仍无法恢复的顶层报告记为可重试的 `invalid_structured_response`；
- 失败结果先分类为 `retry`、`repair`、`requires_confirmation` 或 `terminal_failure`。`repair` 会触发 Build Repair Planner 生成受约束 repair task，并 append 到运行时 Build DAG；repair task 成功后调度器关闭原 failed task 并继续释放下游依赖。`requires_confirmation` 和 `terminal_failure` 仍会阻断构建并写入摘要。收到显式 `retry_failed_tasks` 动作时，协议适配器先从公开快照或 workspace 落盘计划恢复内部 Build/Repair 计划，调度器再按当前切片最近一次结果恢复 `retry` 分类的失败任务为 `pending`；如果没有瞬时候选，则执行已有且无需额外确认的 RepairPlanner task，保留历史 `build_results` 和重试计数，再按原 DAG 继续执行。没有可重试任务且没有 ready 修复计划时才返回明确提示，不会静默重跑确认类或终止类失败。

数据库任务的审批点在 SQL 计划生成之后、任何数据库写入之前。`database.execute` 审批绑定 `task_ids + database + latest schema_hash + plan_hash + statements` 指纹；用户批准后同一操作可在下一次调度中被一次性消费。如果 SQL、目标库、结构摘要或计划指纹变化，旧审批不再匹配，必须重新审批。高危规则包括删除字段、删除/批量修改数据、`DROP`、`TRUNCATE`、无 `WHERE` 的 `DELETE/UPDATE`、破坏性 `ALTER` 等。审批待定时调度器返回 `requires_user_input` / `agent_approval`，把本轮 running 数据库任务恢复为 `pending`，因此下游 Backend/Frontend Unit 不会释放。

这一路径的参考架构映射如下：learn-coding-agent 的“读取实时事实—计划—行动—验证”循环体现在执行前重新扫描数据库并记录执行证据；OpenCode 的权限模型体现在审批由工具/调度层按操作指纹拦截，而不是只写进 prompt；Deep Agents 只负责数据库计划推理，工作区权限为只读，实际 SQL 执行、人类审批和状态推进都在外层确定性 harness 中。为满足 128k 上下文预算，Database Agent 只接收当前批次任务、最新压缩 schema summary、API Contract 摘要和计划要求，不加载完整数据库、完整仓库或历史消息。

Build Repair Planner 是独立的只读 RepairPlanner DeepAgent 节点，不是 Main Agent。它由 `BuildScheduler` 严格约束输入和输出，只在调度器已将失败分类为 `repair` 后被调用，不直接操作 DAG、任务状态或调度循环。调度器传入的 `RepairPlannerInput` 包含原 task、失败 attempt result、允许修改范围 `change_scope/allowed_paths`、当前 `WorkspaceSnapshot` 或 targeted snapshot、失败日志引用和原工程验收检查。Planner 返回 `RepairPlan`，只能是三种决策之一：

- `repair`：包含修复策略、边界说明和一个或多个带精确 `change_scope` 的 repair task；服务层强制继承 owner、最大 allowed_paths、依赖隔离和结果型验收边界，但按本轮精确范围重新编译文件操作检查；
- `requires_user_confirmation`：表示需要扩大修改范围、变更已确认需求/API 契约或做用户可见产品决策，调度器停止继续释放后续任务；
- `terminal_failure`：表示证据不足、修复预算耗尽或失败不可自动处理，调度器停止构建并保留失败证据。

外层 `build` 使用确定性条件路由：仅 `build_summary.status == completed` 可进入 `integration_test`；`requires_confirmation` 以 `repair_scope_confirmation` 暂停并返回稳定 `planId`、精确 `requestedPaths` 和原因；阻塞、仍有 pending/failed、不可修复或终止失败全部进入 `handle_failure`。用户批准时只从原任务授权范围编译 repair task，拒绝则终止，不允许测试节点抢跑。

因此失败处理不是统一“重跑”：可重试的 runner/tool/网络类失败只通过显式 AG-UI 恢复动作重试；实现、编译、测试、验收类失败进入 RepairPlanner，已有 ready 修复计划也可由同一恢复入口继续执行；契约或计划边界类失败进入用户确认；不可恢复失败终止当前 build。摘要中的 `recovery_available`、`recovery_task_ids` 和兼容保留的 `retry_available`/`retryable_task_ids` 是前端是否显示恢复入口及其失败提示的依据。

专业代码生成 Agent 必须以 Deep Agent 形式存在，具备受控文件读写能力，并从已批准任务中读取 `allowed_paths`、依赖、验收标准和上下文。它们只执行任务，不负责更新计划文档、修改需求或重写任务 DAG。任务完成、失败、变更申请和计划一致性由 `BuildScheduler` 与确定性协调服务统一更新；需要修复规划时再调用独立 RepairPlanner Agent。

`workspaceRoot` 是 Backend 的宿主机目录，只能用于 Graph State、Agent filesystem backend、确定性文档写入和 workspace diff 捕获。Deep Agent 的文件工具始终以 `/` 作为虚拟工作区根；例如任务中的 `app/frontend/**` 必须解释为 `/app/frontend/**`，不得把 `/Users/...`、Windows 盘符或其它真实 `workspaceRoot` 拼入工具路径。Frontend/Data Source generation prompt 不暴露真实根目录，filesystem permission 和 `delete_file` 还会拒绝把真实根目录重复成虚拟子目录的路径。已经存在的错误嵌套目录不会被工作流自动迁移或删除。

### Skill 与上下文预算

当前一等 Deep Agent 是 Frontend Generation、Data Source Generation、Database Change、Test、RepairPlanner、SmallTask，不再声明或创建 Main DeepAgent。Frontend Generation Agent 通过 Deep Agents 原生 `skills` 参数按“内置、用户”顺序加载 Skill，用户同名 Skill 后加载并覆盖内置 Skill；Data Source、Database、Test、RepairPlanner、SmallTask Agent 只加载用户 Skill。Database Agent 的 workspace filesystem 权限为只读，不能编辑后端代码或任务文档；数据库写操作只能通过外层 `database.execute` 审批与执行服务完成。SmallTask Agent 只接收精确任务包和授权路径，不能使用任务编排工具，不执行数据库 schema/DDL 或正式工件修改。requirements、project_planning、detail_confirmation、prepare_build_tasks 等 direct ChatModel 节点不加载 Skill，也不绑定 workspace backend 或文件工具。

内置 skill 的宿主目录在源码模式为 `Backend/app/builtin_skills/`，在 PyInstaller onedir 模式为后端资源目录 `_internal/app/builtin_skills/`。Agent 不接触宿主绝对路径，而是通过只读 CompositeBackend 路由 `/.xcodeagent/builtin-skills/` 发现和读取 skill；文件权限与 `delete_file` 都拒绝写入或删除该命名空间。Backend Python 是必需 skill 名称和文件的唯一事实来源：PyInstaller staging 和 Backend 启动执行完整性校验并在缺失时 fail fast；Electron 打包前和启动前只检查通用 `builtin_skills` 资源目录，不复制具体 skill 清单。

用户 Skill 来自当前环境的 `~/.xcodeagent[_dev|_st|_uat]/skills`。技能目录按“用户 / 内置”分类展示：用户技能可以创建、编辑、删除、导入和启停，内置技能只读。用户技能默认开启，关闭项以相对 `SKILL.md` 路径写入同一环境的 `skill-settings.json`；状态文件采用版本化结构、进程锁和原子替换，损坏或不可读时按 fail-closed 处理，不把用户技能加载进新运行。创建和 ZIP 导入默认开启，删除同步清理残留状态。

Chat Composer 通过既有 `/skills/run` AG-UI 目录接口提供搜索和多选，只展示已开启用户技能，并在 `/workflow/run` 的 `forwardedProps.selectedSkillNames` 中发送稳定、去重的名称数组。该数组写入 `ProjectState.selected_skill_names`，在 RequirementSpec、ProjectPlan 确认以及 Build/Testing Subgraph 恢复时保持不变；恢复请求试图替换集合会返回 `selected_skill_conflict`。用户消息同时保存技能名称/描述快照，因此历史会话只展示当次发送的标签，不依赖当前目录是否仍存在。

当 `selectedSkillNames` 非空时，Backend 会精确验证所有名称，只把已开启的所选技能完整目录复制到 `/.xcodeagent/user-skills/` 不可变只读快照；关闭或未选技能不可发现且虚拟路径不可读，显式选择关闭技能返回 `selected_skill_unavailable`。所选 `SKILL.md` 会由 Backend 在模型调用前完整读取，并以明确的 `<selected-skill>` 边界强制拼入 Frontend、Data Source、Database、RepairPlanner、SmallTask、Workspace Assistant 六个 Deep Agent 的 system prompt；references、scripts、assets 仍只从筛选后的快照按需读取。无工具 ChatModel 节点仍不加载技能。空数组或字段缺失时，全部已开启用户技能只通过 SkillsMiddleware 按需发现，不强制注入正文。启停集合参与用户技能 revision，因此切换状态会产生新的 Agent bundle；进行中的单次模型调用不被强制中断。

显式选择的 `SKILL.md` 正文按 UTF-8 总字节设置独立 64 KiB 上限，整体超限返回 `selected_skills_context_too_large`，不会截断指令；无效格式、不可用技能和恢复冲突分别返回 `invalid_selected_skills`、`selected_skill_unavailable`、`selected_skill_conflict`。技能指令不能扩大 filesystem permissions、任务 `allowed_paths`、已确认需求、API 契约、确认门禁或 Agent 角色边界。bundle 缓存键包含规范化技能集合、工作区、用户技能 revision 和 AGENTS.md revision；顺序不同但集合相同会复用，集合不同绝不复用。任务执行元数据记录 `requiredSkillsLoaded`，Workflow 开始事件记录选择名称和 snapshot revision。

该设计映射到参考架构：learn-coding-agent 的紧凑“收集上下文—行动—验证”循环只读取当前任务需要的规范；OpenCode 风格把用户 Skill 作为显式可选、错误隔离的 Agent 能力；Deep Agents 继续使用原生 SkillsMiddleware、FilesystemBackend 和 CompositeBackend。为遵守 128k 上下文预算，默认模式只常驻技能元数据；只有用户显式选择的有限正文进入 system prompt，辅助资源和未选技能正文都不固定进入上下文。

环境级 `~/.xcodeagent[_dev|_st|_uat]/AGENTS.md` 是六个顶层 DeepAgent 的共享指令源。保存后的内容上限为 32 KiB；每个 bundle 创建时，它被复制为不可变只读快照并挂载到 `/.xcodeagent/agent-memory/AGENTS.md`，通过 `create_deep_agent(memory=[...])` 由原生 MemoryMiddleware 注入系统上下文。AGENTS.md revision 也属于 bundle 缓存键，因此下一次调用加载新快照，运行中的 Agent 保持其启动版本；Deep Agents 自动创建的通用子 Agent 不继承该 memory。本设计沿用 learn-coding-agent 的小而可验证的上下文收集循环，采用 OpenCode 的环境级 AGENTS 指令边界，并复用 Deep Agents 的 memory/CompositeBackend 权限模型；32 KiB 上限为 128k 窗口保留任务、工具结果与模型输出空间，且不会授予 Agent 宿主机文件访问权限。

外层主 Graph 不关心单个生成任务的执行细节，只根据 Build Subgraph 的确定性终态路由；构建完整成功才进入 `integration_test`。

### `integration_test` / Testing Subgraph

`integration_test` 在外层主 Graph 中表现为一个节点，但内部应实现为 Testing Subgraph，并合并原 `quality_gate` 的职责。

测试命令和质量判定完全以确定性结果为准。确定性规则负责更新 `test_report`、`quality_gate_passed`、`needs_revision` 和 `revision_requests`；只有门禁失败且启用自动修复时才调用独立 RepairPlanner Agent。

Testing Subgraph 的最小内部结构：

```text
testing.START
  → collect_unit_test_targets
  → build_project_checks
  → unit_test_confirmation (用户选择)
  → generate_unit_tests
  → validate_generated_unit_tests
  → actual_project_checks (unit tests)
  → main_quality_gate
  → repair_planning
  → testing.END
```

`main_quality_gate` 是历史节点名，实际职责是确定性质量门禁：根据测试证据生成 `test_report`、`quality_gate_passed`、`needs_revision` 和 `revision_requests`，不代表 Main DeepAgent。`repair_planning` 只有在质量门禁未通过时才调用独立 RepairPlanner Agent；质量门禁通过时跳过修复计划并输出 `integration_next_action = launch_project`。

当前质量门禁覆盖：

- 前端 TypeScript 依赖安装；
- 前端 TypeScript 构建；
- 前端 typecheck；
- 后端 Java 构建。
- 前端和后端单元测试生成校验，以及存在对应测试文件时的 Jest/Surefire 单元测试。

单元测试生成是正式 Workflow 中的尽力而为阶段：测试子图从刚完成的 Build 代码变更集合中提取目标业务源码及有界真实 diff，并先完成前后端依赖、类型和构建检查；构建完成后暂停并通过 AG-UI 展示“是否跳过单元测试”的两个按钮。选择跳过时，生成和单元测试检查记录为 `passed=true, skipped=true` 后直接进入质量门禁；选择继续时才交给 TestGeneration Agent，再执行前后端单元测试。本轮没有对应测试文件、生成 Agent 无输出或 Agent 初始化失败时，生成和单测检查仍按尽力而为策略放行；已有或已生成的测试文件必须执行，编译、用例或业务代码失败仍进入 RepairPlanner → SmallTask 修复闭环。子图通过 `build_project_checks → unit_test_confirmation → (skip_unit_tests | generate_unit_tests → validate_generated_unit_tests → actual_project_checks)` 固定执行顺序，并在确认恢复时复用已完成的构建检查快照，避免重复安装和构建；TestGeneration Agent 调用期间通过 `integration_test.checks` 发布逐层 `running` 快照，前端原位展示集成检查矩阵、旋转 loading 和生成说明，校验完成后再更新为通过、跳过或失败；实时快照与最终报告均按依赖/类型检查、构建、测试生成检查、单元测试的稳定顺序展示。前端测试平铺在 `frontend/tests/<module>-<feature>.test.ts(x)`，后端测试镜像 Java package 到 `backend/src/test/java/**/*Test.java`，前后端合计最多五个测试文件。源码、测试映射缓存保存于工作区 `.xcodeagent/cache/unit-test-mappings.json`，用于源码摘要未变化时复用映射。测试生成期间 LangGraph 对 `.xcodeagent/checkpoints/` 的技术性写入不属于工程变更；其他 `.xcodeagent` 正式工件仍受越权修改检查保护。

输出至少包含：

- `test_results`：每项测试的通过状态、命令和证据；单元测试命令若能从 Jest、Vitest 或 Maven 输出中解析数量，还会提供 `passed_tests` 与 `total_tests` 供集成检查矩阵展示；
- `test_report`：确定性测试汇总和质量门禁结果；
- `test_report_path`：结构化测试报告 JSON 路径；
- `quality_gate_passed`：是否允许进入启动和验收；
- `needs_revision`：是否需要返回修改；
- `revision_requests`：返回给 RepairPlanner Agent 的结构化返修请求；
- `repair_task_plan`：RepairPlanner Agent 基于失败证据生成的修复任务计划；
- `repair_task_plan_path`：结构化修复任务计划 JSON 路径；
- `repair_tasks`：RepairPlanner 生成、随后交给 SmallTask Agent 的受限局部修复任务；
- `small_task_tasks` / `small_task_results`：SmallTask 执行器的任务状态、实际改动、验证和升级结果；
- `integration_next_action`：外层 Graph 的下一步路由，取值为 `launch_project`、`small_task_repair`、`await_user_input` 或 `handle_failure`；
- `repair_iteration` / `max_repair_iterations`：集成测试修复闭环预算。
- `unit_test_generation_context`、`unit_test_generation`、`unit_test_mapping_path`：本轮源码目标、生成/同步结果、warning、校验和可重建映射缓存；`unit_test_code_change_sets` 与 `unit_test_generation_code_change_sets` 保存实际测试文件差异（后者为生成阶段别名）。

`actual_project_checks` 复用项目已有行业标准工具，而不是自定义测试逻辑：

- 前端：读取 `Frontend/package.json`（兼容 `frontend/`、`app/frontend/` 和根 `package.json`），根据 lockfile 选择 `pnpm` 或 `yarn`，执行 install、可选 `tsc` script、build，并在有对应测试文件时优先执行 `test:unit`、否则执行 `test`；
- 后端：仅识别当前平台的 Maven Wrapper / Maven（`mvnw`、`mvnw.cmd`、`pom.xml`），先执行 `-B -Dmaven.test.skip=true clean install`，确认构建通过且存在对应 `*Test.java` 后再执行 `-B -DfailIfNoTests=true test`；不探测 Python 工程或执行 pytest；
- 未声明可选 `tsc` script 时会以 `skipped=true` 且 `passed=true` 记录；缺失必需入口（如前端 package.json、frontend build script）会失败。没有对应单测文件时不执行 Jest/Maven test，并以明确原因跳过；不执行 E2E。

任务编译和执行还必须遵守以下确定性边界：

- 页面任务进入 DAG 前，以实时工作区校对模型计划路径。只有当计划入口不存在，且实时 `frontend/src/pages` 中存在唯一的同义目录（忽略大小写、分隔符和 `Page` 后缀）时，才把目标路径改写到该既有入口并把 `add` 改成 `modify`；多候选时不得猜测。这样可修复 WorkspaceSnapshot 在长流程中变旧造成的 `DashboardPage`/`Dashboard` 重复入口，同时保留可审计的 `path_reconciliation`。
- 模板页面必须具备页面范围内的菜单与自动路由登记结果。任务编译器先读取实时 `frontend/src/constants/menus.ts`：脚手架已写入完全一致的 `{ path, name, key }` 时，不再补充重复任务，并将模型已有菜单任务标记为 `already_satisfied`；仅在条目缺失时生成只能追加 `BIZ_MENUS.firstLevel.children` 的受限任务。PageKey 必须与规范页面目录完全一致，不得修改路由生成器或既有菜单项。
- 任何具有精确 `target_files` 的可执行任务都交给对应 Frontend/Data Source 受限 runner。共享路径、公共契约和重叠目标仍然串行，但不得标记为不存在后续集成步骤的 `subagent-plan-only`。无精确目标的候选不能进入代码执行器。
- Frontend/Data Source owner Agent 只负责任务范围内的源码读取和实现，不在 task 内执行依赖安装、build、lint、typecheck、unit test 或 dev-server 命令；这些项目级检查统一由后续 `integration_test` 执行，缺少依赖或命令时由 Agent 在结构化结果中报告，不得自行安装恢复。
- 专业 Agent 最终返回 `task_results` 结构化对象，逐任务给出 `completed`、`already_satisfied` 或 `failed`，但状态声明和自然语言证据都不构成验收结果。调度器对 `completed` 与 `already_satisfied` 统一逐项执行 `acceptance_checks`：前者使用工作区前后快照核对每个精确文件的 `changeType`，后者核对当前磁盘目标状态；整个并发批次按所有任务授权范围的并集检查越权变更；已存在且一致的菜单目标在混合页面任务执行前从本轮写入范围移除；菜单使用共享解析器核对 `{ path, name, key, hideInMenu }`；前端 API 文件必须声明正式方法、路径和请求/响应类型，页面文件只检查 `PageImplementationContract.responseBindings` 明确绑定的响应字段，不要求消费完整响应。后端 Spring 契约检查只附加到实际拥有 Controller/Resource/Endpoint/Handler 等端点实现路径的任务，基础设施或配置前置任务即使引用 endpoint id 也不代替下游端点任务接受 Mapping/DTO 验收；恢复旧 BuildTaskPlan 时也会移除这类历史误分配检查。契约字段和数据源以已确认 EndpointDetail 为当前事实，覆盖 TechnicalPlan 中可能过期的 source type。Spring 端点任务必须具备 Mapping、DTO 字段和可证明的 JSON wire-name 映射，允许 Java camelCase 配合 `@JsonProperty`、`@JsonNaming` 或 Spring Jackson 全局 snake_case 策略。响应包装模型引用既有 DTO 时，验收器保持任务写入范围不变，只从目标模型的真实 Java 类型引用出发，在工作区内有限深度读取被引用 DTO 作为嵌套 Schema 的只读证据，不进行全仓库同名字段扫描。数据库任务执行后重新读取真实 Schema 并确认 gap 消除，必要审批必须先匹配计划指纹。任何检查缺少确定性证据或不匹配都转为 `acceptance_verification_failed` 并进入 RepairPlanner。合法 JSON 遗漏已派发任务时记为 `runner_protocol_error`；明显未转义双引号会先做一次确定性恢复，仍损坏的顶层报告记为 `invalid_structured_response` 并进入受控重试分类。
- Deep Agent 工具活动继续使用根图和子图流；执行器按“根图优先、浅层 namespace 优先、同层最新优先”恢复最终 `values/messages`。根 `values` 快照不含 `messages` 时先使用根消息分片，再回退到最浅层 Agent namespace，且不得把工具结果或更深子 Agent 文本误作主 Agent 报告。
- RepairPlanner 必须为每个修复任务返回父任务授权内的精确 `change_scope`。修复任务不继承父任务历史执行的 `add/modify/delete` 差异检查，而是按本轮范围重新编译文件检查；父任务的菜单/API/Schema 等最终结果检查继续继承。兼容旧 RepairPlanner 且无法确定精确文件时，`completed` 至少要产生一处授权变更，`already_satisfied` 必须证明原精确目标状态成立。只有本轮文件检查和继承的结果检查全部通过才可关闭父任务，从而避免要求 DTO 小修复重新新增整个模块，也避免重复菜单等非幂等改动。
- 恢复旧 DAG 时，如果 Repair 正是因为继承父任务 `added` 差异而产生 `acceptance_verification_failed`，调度器会从旧修复描述中恢复明确提及的精确父任务路径、按 `modify` 重编译检查并把该 Repair 恢复为 `pending`；其他失败类型和无法确认父任务的 Repair 不会被自动重置。

该边界继续对应 learn-coding-agent 的“收集实时事实—执行—立即验证”循环；对应 OpenCode 的稳定任务 ID、显式任务状态和权限受限执行；对应 Deep Agents 的根/子图消息分流与结构化 subagent 结果。任务状态、文件归属和验收仍由外层确定性调度器裁决，Agent 输出视为不可信输入；Graph State 只保存紧凑报告和证据引用，不复制完整消息流或工具日志，保持在 128k 上下文预算内。

每个真实命令都会写入 `.xcodeagent/runtime/tests/<check_id>/stdout.log` 和 `stderr.log`。`test_results.execution` 同时提供宿主日志引用、Agent 可读取的虚拟工作区日志路径以及有长度上限的 `stdout_tail/stderr_tail`，另保存命令、cwd、returncode、timeout 和失败分类。Test/RepairPlanner 必须以这些证据为依据；摘要和日志都不可读时只能报告证据不足，不得猜测根因。

Graph 不应把 npm/maven/lint/typecheck/unit test 全部暴露成一等节点，避免主流程过碎；但 `test_results` 里必须保留每个具体检查项的结构化证据。

测试不通过时，Testing Subgraph 必须把失败项转成足够详细的 `revision_requests`，包括失败检查、命令、证据、建议 owner。随后由只读 RepairPlanner Agent 汇总生成 `repair_task_plan`，再由 SmallTask Agent 执行不改变产品语义的局部代码修复：

- 前端检查失败 → RepairPlanner 生成带精确路径和验收标准的 SmallTask；
- 后端实现检查失败 → RepairPlanner 生成带精确路径和验收标准的 SmallTask；
- API 契约、数据库结构、正式工件、扩大文件范围或产品决策 → SmallTask 停止并请求用户确认，路由到对应正式 Workflow 节点；
- 前后端集成失败 → RepairPlanner 先判断归因，再拆分为互不冲突的 SmallTask 批次。

`revision_requests[*].failed_attempt` 使用统一格式返回给 RepairPlanner / 后续调度器，至少包含：

- `check_id`、`check_name`、`status`；
- `failure_category`（如 `dependency_install_failed`、`compile_error`、`lint_failure`、`type_error`、`test_failure`、`integration_test_failure`）；
- `command` 和 `execution.argv/cwd/returncode/timed_out`；
- `logs.stdout`、`logs.stderr`；
- `agent_note` / evidence 摘要。

外层 Graph 根据 `integration_next_action` 路由：

- `launch_project`：质量门禁通过，进入 `launch_project`，写入 `preview_url` 和 `acceptance_request`，本轮结束等待用户验收；
- `small_task_repair`：RepairPlanner 返回可执行 repair tasks，SmallTask 调度器按依赖、路径和资源锁选择最多 2 个（硬上限 3 个）互不冲突任务并行执行；任务完成后再次进入 `integration_test`，本路由不直接回到 `build`；
- `await_user_input`：修复需要扩大范围、改变契约或做产品决策，本轮结束等待用户确认；
- `handle_failure`：证据不足、修复预算耗尽或不可恢复失败，进入失败处理。

为避免卡死，Graph State 记录 `repair_iteration` 和 `max_repair_iterations`。计数只在 SmallTask 真实派发并完成一轮局部修复时增加，生成计划、重复测试或等待用户确认都不消耗预算。SmallTask 调度开始时只读取 RepairPlanner 生成的精确路径、依赖和验收标准，不重建主 Build DAG；每个任务都必须带 `allowed_paths`、`acceptance_criteria` 和失败证据引用。超过预算后持久化 `terminal_failure` plan 并路由到 `handle_failure`。

AG-UI `agent-process` 为 Workflow 步骤增加向后兼容的可选字段 `nodeName`、`attempt`、`iterationKind`、`buildExecutionSlice` 和 `dagGeneration`。`dagGeneration` 使用同一稳定步骤 ID 更新完整七阶段快照，阶段详情默认折叠且保留用户手动展开状态；完成或失败事件保留各阶段冻结的结构化产物、最终任务和安全产物摘要，供历史会话恢复。首次节点仍使用 `workflow:build` / `workflow:integration_test`，局部修复使用独立的 `workflow:small_task_repair`，复测使用 `workflow:integration_test:2` 等唯一 ID，历史事件按节点恢复为“首次构建 → 首次测试未通过 → 局部修复 → 复测”。构建进度卡由对应 build 步骤详情承载，不再在消息列表末尾重复渲染；任务卡默认折叠，运行任务的 `activeToolActivity` 在折叠 Header 下方显示，展开时只移动到详情底部。质量门禁失败显示 `failed`，等待确认显示 `requires_user_input`。

该修复闭环沿用以下参考架构边界：learn-coding-agent 的执行—验证—修复紧凑循环覆盖代码、测试和 API 契约错误；OpenCode 风格的可恢复 session 状态持久化修复计数、计划和终止原因；Deep Agents 的 RepairPlanner 只接收结构化失败证据并生成受限任务。为满足 128k 上下文预算，不注入完整仓库、全量日志或会话历史。

### `launch_project`

确定性运行节点，负责：

- 使用经过校验的启动命令；
- 分配端口；
- 启动前后端服务；
- 执行健康检查；
- 返回本地预览地址；
- 写入 `acceptance_request` 并提示用户验收；
- 保存和清理进程信息。

`launch_project` 是质量门禁通过后的本轮终点。LangGraph 节点先通过 `workspace_root(state)` 解析普通工作目录，再使用确定性的 `find_backend_project_root(workspace_path)` 探测直属 `backend/Backend/pom.xml`。识别到 Maven 后端时，节点依次调用与 Graph State 解耦的 `launch_backend_project(workspace_path)` 和 `launch_frontend_project(workspace_path)` 公共服务；未识别到后端时，将 `launch_result.backend.status` 记为 `skipped` 并直接启动前端。后端 launcher 在每个工作区的串行锁内，先停止内存登记或 `backend.pid` 恢复出的上一轮 Java 进程，确认退出后才执行 Maven；PID 恢复必须校验命令确实是当前工作区 `target` 下的 `java -jar`，清理失败则以 `backend_cleanup` 中止构建。只有新 Java 进程仍存活且本次日志出现约定的版本标志后才启动前端。前端启动进程仍存活且预览地址通过 HTTP 或本次启动日志检查后，节点才返回 `preview_url` 和 `acceptance_request`，并将状态设为 `requires_user_input`；任一实际启动的进程提前退出或就绪检查超时都会返回启动失败，不进入验收。若 Java 已启动但前端失败，节点停止本次 Java 进程并记录清理结果；纯前端启动失败时不执行后端清理。前端收到实时 Workflow 的成功 `summary.previewUrl` 后，会自动打开右侧预览面板并导航到该地址；重复状态快照不会重复导航，历史会话也不会自动弹出预览。工作流不会自动进入 `acceptance`；用户确认验收后，下一轮从 `acceptance` 续跑。

当前启动策略：

- 两个公共 launcher 仅接收 `str | Path` 工作目录，自行从 `<workspace>/.xcodeagent/runtime/launch/` 推导日志与 PID 目录，因此可被 LangGraph 之外的调用方直接复用；
- 后端探测器枚举工作区直属目录并识别 `backend/pom.xml` 或 `Backend/pom.xml`，保留磁盘上的真实目录大小写；缺少 `pom.xml` 表示工作流没有可启动的 Maven 后端，节点跳过后端，但直接调用后端 launcher 仍返回 `backend_validation`；
- 识别到 Maven 后端后，通过 `shutil.which` 解析 `mvn`、`java` 的完整可执行路径；Windows 上直接使用解析到的 `mvn.cmd` 和 `java.exe`，不依赖 `cwd` 再次搜索 PATH；
- 后端 Java 进程按规范化工作区路径保存在内存注册表中；同一工作区的停止、构建、启动和登记由可重入锁串行化，不同工作区互不阻塞；
- 每次 Maven 构建前优先停止内存登记的进程；Backend 服务重启导致内存记录丢失时，从 `.xcodeagent/runtime/launch/backend.pid` 恢复 PID，通过完整进程命令行确认 `java`、`-jar` 和当前 `backend/target` JAR 绝对路径均匹配后才终止，拒绝按 Java 进程名批量清理；
- 进程先温和终止并等待 5 秒，超时后强制结束；只有确认退出才删除 PID 和内存登记。无法读取 PID、无法确认身份或强杀后仍存活时返回 `failed_stage=backend_cleanup`，不执行 Maven；`prebuild_cleanup` 保存来源、PID、身份校验、强杀和错误摘要；
- 在 `backend/` 执行 `mvn clean install`，构建输出写入 `.xcodeagent/runtime/launch/backend-build.stdout.log` 和 `backend-build.stderr.log`；
- 在 `backend/target/` 查找唯一的 `*-SNAPSHOT.jar` 主包，排除 `original-*`、sources、javadoc 和 tests/test 等附属包；无主包或存在多个主包均启动失败；
- 如果唯一主包是普通 Maven JAR 且清单缺少 `Main-Class`，启动器会追加执行 `mvn -B package spring-boot:repackage`，并将结果写入 `backend-repackage.stdout.log` 和 `backend-repackage.stderr.log`；补打包失败或仍未得到可执行 JAR 时以结构化启动失败返回；
- 在启动 Java 子进程前，从当前工作区 `.xcodeagent/application.json` 的 `datasource.db.plantMode` 解析应用数据库配置，清除继承环境中的 `MYSQL_*` 和 `SPRING_DATASOURCE_*`，再注入当前应用对应的数据库变量；配置存在但非法时以 `failed_stage=backend_database_config` 在 Maven 前失败，缺少配置文件时也不回退到 Backend 服务 `.env`；
- 在 `backend/target/` 以 `java -jar <JAR绝对路径>` 启动后台进程，将 pid 和 stdout/stderr 写入 `.xcodeagent/runtime/launch/backend.pid`、`backend.stdout.log` 和 `backend.stderr.log`；
- Java 就绪检查只读取本次启动后追加的 stdout/stderr；进程存活且日志包含精确标志 `Spring Boot Version` 或 `ZA21 Version` 才继续启动前端，普通 `Started ...` 日志不构成就绪证据；
- 在工作区内优先读取 `Frontend/package.json`，其次尝试 `frontend/package.json`、`app/frontend/package.json` 和根 `package.json`；
- 根据 lockfile 选择包管理器：`pnpm-lock.yaml → pnpm`，`yarn.lock → yarn`，否则使用 `npm`；执行安装和开发服务器时使用 `shutil.which` 返回的完整路径，兼容 Windows 的 `npm.cmd`、`pnpm.cmd` 和 `yarn.cmd`；
- 执行 `<package-manager> install` 安装依赖；
- 优先执行 `dev` script，其次执行 `start` script；
- 启动时设置 `BROWSER=none`；对于 `react-scripts` 不强制注入 `HOST=127.0.0.1`，避免带代理配置的 CRA 项目生成非法 `allowedHosts`；其它启动脚本继续使用本地 loopback host；
- 将前端 dev server 作为后台进程启动，pid、stdout/stderr 日志和安装日志写入 `.xcodeagent/runtime/launch/`；
- 调试续跑时，如果 pid 文件对应的预览地址已经可访问，则复用现有服务，不重复启动并争抢同一端口；
- 根据 script 推断预览地址：若脚本声明 `--port`、`--port=` 或 `PORT=` 则使用声明端口，否则统一使用 `http://127.0.0.1:80`；
- 健康检查在配置的启动窗口内持续监督启动进程：优先通过 urllib 接收 2xx–4xx HTTP 响应；如果运行沙箱禁止 Python 主动连接本地端口，则只读取本次启动后追加的 stdout，通过 CRA/Vite/Webpack 的 `Compiled successfully`、`ready in`、`Local:` 等标志确认就绪。日志读取记录启动前偏移量，不会被历史成功日志误导；
- Maven 或前端依赖安装的同步命令若抛出 `OSError` / `FileNotFoundError`，launcher 将错误写入 stderr 日志和结构化结果，由 `launch_project` 正常返回失败原因，而不是让异常冒泡为 AG-UI `Workflow failed`；
- 将启动结果写入 `launch_result`：保留前端兼容字段，并增加 `backend`、`frontend` 和 `failed_stage`；纯前端工程使用 `backend.status=skipped`，成功时 `preview_url` 是前端预览地址，失败时顶层、`launch_result` 和 `acceptance_request` 的 `preview_url` 均写入启动失败原因。失败状态不会触发前端自动预览导航。

该边界沿用参考 coding-agent harness 的执行—观察—验证循环：Maven 构建、Java 启动和前端启动均使用显式 argv、cwd、超时与进程退出监督，完整命令输出落盘而不进入 Graph State。`launch_result` 和 AG-UI 只携带状态摘要、PID 与稳定日志路径，不注入完整构建/运行日志，从而保持确定性节点可审计并符合 128k 上下文预算。

### `acceptance`

暂停并等待用户验收。

用户选择：

- 通过：进入 `finalize_project`；
- 局部修复：进入 `small_task_repair`，只允许修改当前已确认范围内的文件；
- 页面布局或交互调整：不再返回 PageDetail；应回到产品侧 React UI 设计流程，历史请求暂按技术规划调整兼容处理；
- 接口行为或字段调整：返回 `detail_confirmation`，重新生成并确认 EndpointDetail；
- 数据来源或数据库调整：返回 `detail_confirmation`，重新生成接口及数据来源相关详细设计；
- 项目计划或架构调整：返回 `project_planning`，重新生成并确认 ProjectPlan，再进入细节确认；
- 取消：停止任务和运行进程。

前端通过 `clarificationAnswers.acceptance_adjustment` 提交 `type + feedback`，后端在协议边界校验类型并决定安全恢复节点。验收调整不再统一视为“重新调整执行计划”；只有局部修复可以直接进入受限 SmallTask，任何正式设计或计划版本变化都必须生成新版本并再次经过用户确认闸口。

当前实现应等待用户验收；只有用户明确接受后，`acceptance` 才设置 `accepted = true` 并进入 `finalize_project`。如果用户提出局部、页面、接口、数据来源或架构调整，前端必须提交结构化 `acceptance_adjustment`，后端按类型回到 `small_task_repair`、`detail_confirmation` 或 `project_planning`，并对所有新生成的正式设计/计划版本重新执行确认闸口。

### `finalize_project`

负责：

- 固化最终 Spec 和计划；
- 保存测试报告；
- 生成 README 和运行说明；
- 输出工程目录或压缩包；
- 将项目状态标记为完成。

## 一等 Deep Agent

本项目只保留五个一等 Deep Agent：Frontend Generation、Data Source Generation、Database Change、Test、RepairPlanner。`agents/main/` 只作为历史命名下的 direct ChatModel 边界目录，用于需求、规划、页面设计、任务准备和 Markdown 同步；它不再声明或创建 Main DeepAgent。

### Frontend Generation Agent

目录：`agents/frontend/`

职责：

- 根据已批准的页面执行计划生成前端代码；
- 实现布局、组件、交互、权限和 API 接入；
- 实现 loading、empty 和 error 状态；
- 编写页面测试；
- 执行前端 lint、typecheck 和单元测试。

它不负责页面需求确认，也不负责自行修改 ProjectPlan 或 API 契约。

### Data Source Generation Agent

目录：`agents/data_source/`

职责：

- 根据已批准的数据源执行计划生成数据模型；
- 实现数据库迁移、Seed 或 Mock 数据；
- 实现 API、校验和权限；
- 编写后端测试；
- 遵守已经确认的 API 契约。
- 数据源生成时使用绑定当前工作区的 `get_mysql_config` 读取 `.xcodeagent/application.json` 中的 `datasource.db.plantMode`，不读取 Backend 服务 `.env`；未绑定工作区的兼容入口直接失败。

如果契约不可实现，应返回变更申请，不得静默修改契约。

### Database Change Agent

目录：`agents/database/`

职责：

- 负责数据库 owner 任务的最新结构扫描、SQL 计划生成和执行前风险分析；
- 工作区文件系统只读，不修改后端、前端、ProductPlan、TechnicalPlan、历史 PageDetail 或任务 DAG；
- 真实数据库摘要和 DDL 执行都使用绑定当前 `workspaceRoot` 的应用级 `application.json` 数据源配置；后端只在本次操作内存中解密 `plantMode.pwd`，未绑定工作区的兼容入口直接失败，不回退到 Backend 服务的 `.env`；
- 高危 SQL 计划必须先创建 `database.execute` 审批，请求批准的是计划指纹而不是自然语言任务；
- 只有低风险或已审批的同指纹计划才交给确定性执行服务事务执行，并以数据库执行证据完成任务。

### RepairPlanner Agent

目录：`agents/repair_planner/`

职责：

- 在 build task 或 integration test 失败后分析失败证据；
- 接收调度器约束的 `RepairPlannerInput` 或测试返修请求；
- 输出 `RepairPlan` / `repair_task_plan`；
- 对需要扩大范围或改变契约的情况返回用户确认需求；
- 只读工作区，不直接修改代码、计划、DAG 或调度状态。

修复任务由 `small_task_repair` 的共享 SmallTask Agent 执行。它只允许局部代码改动，不执行数据库 schema/DDL、不改正式 RequirementSpec/ProjectPlan、不改变已确认 API 契约，也不自行新增页面、接口或数据源；其成功结果回到 `integration_test` 复测。多个任务只有在依赖、目标路径和资源锁均不冲突时才并行；共享配置、路由、菜单和锁文件默认串行。

SmallTask Agent 同时服务自由对话阶段。自由对话中的简单修改沿用同一 Agent；若它识别出扩大范围、正式契约/工件变化或产品决策，会先生成 AG-UI 确认卡，用户确认后在同一工作台界面启动新的正式主 Workflow，并从 `detail_confirmation`、`inspect_database_context`、`prepare_build_tasks` 或其他确定性节点继续。

## 目录职责

```text
graph/          LangGraph 主业务流程、节点、路由和 Graph State
agents/         一等 Deep Agent 与 direct ChatModel 边界的声明与配置
domain/         不依赖 LangGraph、Deep Agents、FastAPI 的核心数据模型
services/       任务编译、调度、质量门禁等确定性业务规则
tools/          暴露给 Deep Agents 的受控工具
protocols/      AG-UI 等外部协议适配
workspace/      用户本地工作目录、文件锁和任务级并发锁
middleware/     Deep Agent 限流、权限、重试、审计等横切能力
persistence/    业务数据、checkpoint、Store 和运行状态持久化
observability/  日志、Tracing、Metrics 和 Agent 运行诊断
```

`graph/nodes/` 按节点职责拆分文件；`graph/workflow.py` 只负责声明节点连接和路由，不承载节点内部业务逻辑。

不得将确定性业务规则写进 Agent Prompt。可以用普通代码完成的校验、路由和状态转换应放在 `services/` 或 `graph/`。

## 自由对话 Graph

`POST /conversation/run` 是独立于 `/workflow/run` 的 AG-UI LangGraph，不保留旧 `/direct-modification/run` 协议。工作台在无目标自由对话，或当前页面/endpoint 已设计且用户选择“自由协作”模式、正式计划不占用输入区时使用该端点；页面/API 输入区默认选择“设计修改”模式，显式模式只负责决定入口 Graph，不改变后续正式详情确认门禁。请求发送标准 AG-UI user message 和 `forwardedProps.conversation.workspaceRoot/selectedSkillNames`，不发送左侧页面/API target。公开事件名、状态快照键和 checkpoint thread 前缀均为 `conversation`。

入口顺序固定为 `scan_workspace_code -> classify_intent`。扫描节点先生成可缓存的只读 WorkspaceSnapshot 和代码图摘要；分类器只接收有界的技术栈、工程入口、页面、组件、API 路由、共享契约和示例符号，不接收源码正文或完整仓库。`classify_intent` 再输出五类意图：`casual_chat`、`workspace_question`、`workspace_change`、`formal_workflow`、`needs_clarification`。用户已经给出存在于扫描快照中的页面/组件和明确局部结果时，即使尚未定位到最终编辑文件，也必须进入 `workspace_change`，由执行 Agent 继续精确定位，不能在分类阶段重复询问修改位置。对身份询问、问候和通用解释，分类调用同时生成可直接展示的 `response`；若模型未返回可用 `response`，才回退到无工具 `respond_conversation` ChatModel。需要当前工程事实但不修改文件的问题进入只读 `answer_workspace` Agent；新应用、正式工件、确认过的契约、数据库迁移、广泛架构替换和无法安全局部决定的产品范围先展示确认卡，再由前端切换 `/workflow/run`；扫描后仍然缺少实质目标或预期结果的输入继续在同一 conversation thread 澄清。

`workspace_change` 继续区分 `frontend | backend | fullstack | workspace` owner。前后端代码复用共享 SmallTask Agent，`fullstack` 固定后端优先并以结构化 `backend_handoff` 交接给前端，随后复用独立集成测试和预览启动。快速修改流程显式关闭 `unit_test_generation_enabled`，不生成或执行本轮新增单元测试；正式 Workflow 才启用测试收集与生成。`workspace` owner 只处理分类器明确返回的精确相对路径或窄 glob，用于普通文档、测试、脚本和仓库配置；它禁止 Frontend/Backend 产品代码、`.env`、数据库迁移和 `.xcodeagent` 正式工件，完成 Agent 内相称验证后直接收口，不强制启动应用预览。任何写分支最终文件清单仍以工作区前后快照为准，模型声明不能替代真实 diff。

前端、后端或 `fullstack` 的独立集成测试失败时，路由进入 `direct_modification_repair`：只读 RepairPlanner 接收测试报告、失败证据和本轮真实变更路径，生成受限 SmallTask，再回到 `integration_test` 复核。自由对话最多执行 3 轮真实局部修复；预算耗尽、证据不足、路径越权、数据库/正式工件/契约变更或需要扩大范围时停止，并保留失败证据或发出确认卡。该节点不直接回到 `build`，也不修改确认过的产品语义。

权限在路由后收紧：所有消息只先经过确定性只读扫描；`casual_chat` 仅额外获得扫描摘要，不创建 Deep Agent、不加载可写 workspace backend、不获得工具；`workspace_question` 使用独立 Workspace Assistant，只允许渐进读取工作区和已选 Skill/AGENTS 记忆，显式拒绝写入、命令、task、todo 和 subagent；只有 `workspace_change` 才获取 workspace run lease 并调用具备受限写权限的 SmallTask Agent。工具活动和文本增量通过 `conversation` 自定义事件、AG-UI `TEXT_MESSAGE_CONTENT` 与快照传输；自由对话界面实时展示助手正文、当前节点和工具活动，避免在整个运行期间只显示笼统的“正在思考”。常规回复和只读回答仍跳过集成测试、项目启动和 diff 生成。

前端代码修改仍必须完整读取 `/.xcodeagent/builtin-skills/code-block-template/SKILL.md` 和 `/.xcodeagent/builtin-skills/react-develop-specification/SKILL.md`；后端当前没有必读内置 Skill。扫描快照中的页面、组件、API client、后端路由、模型和高价值工程配置路径作为分类/执行候选；读取顺序固定为本轮动态加入的精确文件、扫描命中的候选源码、最窄 `src` 源码根、必要的 package/build 元数据。SmallTask 文件后端拒绝读取或写入 `node_modules`、`dist`、`build`、`target`、`.next`、`.turbo`、缓存和虚拟环境目录；无路径 grep/glob 从当前任务的源码授权根开始，而不是遍历整个工作区。自由对话前端写入范围默认收紧为 `Frontend/src/**` 或 `frontend/src/**`，后端默认收紧为 `Backend|backend` 下的 `app/src/tests` 源码根。只要局部修改需要源码根之外的现有工作区文件，分类器就必须返回精确文件路径；后端不再按配置文件类型做白名单判断，而是在路径属于当前 owner、文件真实存在且未落入敏感/依赖/生成/迁移目录时，把任意文件类型动态并入本次运行的 `approvedPaths` 和优先读取候选。该授权不持久化、不接受目录或 glob，`.env`、凭据文件、lockfile、数据库 schema/migration 和 `.xcodeagent` 工件仍不可动态加入。快速执行禁用 `task`、`write_todos` 和默认通用子 Agent，继承共用 Provider 的正常超时/重试配置；写任务执行“读取相关上下文—修改—验证—修复—复查”循环，不得以管道截断或强制成功掩盖检查退出码。Agent 异常后仍执行 after-snapshot，保留异常前已落盘差异供审核和撤销。

参考架构映射在实现前已明确：learn-coding-agent 的“收集相关上下文—行动—验证”最小循环对应先生成只读工作区事实，再决定是否行动；OpenCode 在 session prompt 中显式绑定 worktree/path、角色和权限，对应 XCodeAgent 的扫描上下文、无工具 ChatModel、只读 Workspace Assistant 和受限 SmallTask 分层；Deep Agents 提供只读 workspace backend、Skill/AGENTS 记忆、权限和 checkpoint。XCodeAgent 的有意差异是使用确定性 conversation Graph 在模型分类前统一生成有界 WorkspaceSnapshot，而不是让主 Agent先自由调用探索工具；这样分类可以识别已有页面和组件，同时普通聊天仍不会获得 workspace lease 或写工具。Graph State 只保留不超过 4000 字符的滚动摘要、扫描引用、意图、结构化交接、diff 元数据和日志引用；分类 Prompt 中的扫描事实上限为 16000 字符，不保存全量历史、源码或工具输出，符合 128k 上下文预算。

## 上下文管理

上下文分为四层：

### Graph State

保存：

- 项目和线程 ID；
- 当前阶段；
- Spec 和计划版本；
- 文件路径；
- 任务状态；
- 测试结果引用；
- 用户确认状态；
- Sandbox 和预览信息。

不要保存：

- 完整工程代码；
- 完整测试日志；
- 全部 Agent 消息；
- 大型工具输出；
- 二进制文件。

### Workspace

保存当前项目的真实文件：

```text
specs/
plans/
app/
tests/
reports/
runtime/
artifacts/
```

Graph State 只保存这些文件的路径和版本。

### Agent 临时上下文

每个 Agent 任务使用独立上下文。不同页面、数据源、构建任务和测试轮次不共享完整消息历史。

任务调用只传递：

- 任务目标；
- 相关 Spec 路径；
- API 契约路径；
- 允许修改的路径；
- 依赖任务结果；
- 验收标准；
- 输出报告路径。

### 长期记忆

只保存跨项目仍有价值的信息，例如：

- 用户默认技术栈；
- 编码风格；
- 团队规范；
- 统一 API 规则；
- 常用设计偏好。

当前项目的任务状态、错误日志和 Sandbox ID 不得写入长期记忆。

## Workspace 和并发

- 每个项目必须有独立工作目录。
- 所有文件操作必须限制在项目工作目录中。
- 前端历史会话的消息、草稿、运行状态和停止控制必须按 `workspaceRoot + editorMode + sessionId` 隔离；本地消息记录只负责 UI 展示和持久化，每次执行创建请求级 AG-UI `HttpAgent`，只发送当前用户消息且不复用上一轮客户端 state。`threadId` 只属于对应会话并跨请求稳定复用，每次执行使用独立 `runId`。
- 同一个 `workspaceRoot` 允许多个 `/workflow/run` 进入 Graph。Backend 的进程内 workspace lease 只登记活动 run 并负责结束清理，不再以工作区、页面或资源交集返回 `workspace_busy`。
- 停止生成必须是端到端取消：前端先中止当前 SSE 消费以停止渲染，再通过同一 `/workflow/run` 发送带 `forwardedProps.cancelRunId` 的独立 AG-UI 控制运行。后端的进程内运行表按 `runId` 调用对应 `asyncio.Task.cancel()`，使 `graph.astream()` 和其正在等待的异步模型 HTTP 流收到取消；控制运行也返回完整 AG-UI 开始、消息、状态快照和结束事件。模型供应商对已在其服务端排队的 token 的最终停止时点仍是 best-effort，不把取消响应误报为模型已计费归零。
- 该设计对应 learn-coding-agent 的“执行后立刻反馈/停止”紧凑循环，采用 OpenCode 风格的稳定运行标识和显式任务生命周期，并保持 Deep Agents 的人类可控边界。运行表只保存 `runId -> asyncio.Task`，不复制对话或仓库内容，因此不会扩大 128k 上下文预算；当前单 Uvicorn 进程是该进程内表的适用边界，未来多进程部署需要共享取消协调器。
- 当前阶段不由 Workflow 运行登记阻止修改相同文件；文件写入安全仍由具体工具和原子写入边界负责。
- 共享入口文件、依赖清单、API 契约和路由配置应使用文件锁。
- 任务锁和文件锁由 `workspace/` 提供，不由 Agent 自行约定。
- 生成代码和执行命令最终应运行在隔离 Sandbox 中。

会话隔离不等于项目文件隔离：同一 workspace 内不同会话会并行共享代码、Spec、Plan 和 Report。当前进程内 lease 仅用于活动运行观测和清理；如果未来恢复业务互斥，应以显式策略或独立 worktree 实现，不能依赖 `resourceLocks` 的存在隐式阻断。

## 当前不实现的内容

以下能力已经预留边界，但不要求在最简版本中完成：

- 多轮需求澄清和人工中断；
- Spec、计划和任务的数据库持久化；
- 项目级 Sandbox；
- 文件锁和任务锁；
- 真实前后端代码生成；
- 本地进程生命周期管理；
- AG-UI 状态同步和前端界面；
- 生产级鉴权、限流、预算和审计。

实现这些能力时，应扩展现有目录和节点，不应重新设计一套平行流程。
