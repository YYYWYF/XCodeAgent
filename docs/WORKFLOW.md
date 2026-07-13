## workflow目标

workflow根据用户需求生成可在本地运行的前后端工程，并通过需求确认、计划生成、代码生成、集成测试和用户验收形成完整闭环。

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

## 已确认的主 Graph

主流程顺序如下：

```text
START
  → classify_request_complexity
      ├─ 复杂需求 → requirements //direct ChatModel负责
      │            → project_planning //direct ChatModel负责
      │            → detail_confirmation //direct ChatModel负责页面设计
      │            → inspect_workspace //确定性工作区快照
      │            → prepare_build_tasks //direct ChatModel 基于快照生成静态 Build DAG
      │            → build //由mainagent分发给
      └─ 简单需求 → direct_modification
  → integration_test
      ├─ 测试与质量门禁通过 → launch_project
      │                         → acceptance
      │                         → finalize_project
      │                         → END
      └─ 需要返修或失败 → handle_failure
                            → END
```

项目初始化不属于本 Graph 的职责。进入 Graph 前，外部系统应已经完成项目创建、工作目录准备、持久化上下文初始化和运行资源准备，并将必要的 `project_id`、`workspace` 或上下文引用作为 Graph 输入传入。

当前节点逻辑允许使用占位实现，但节点名称和职责边界应保持稳定。

当某个节点通过 `ask_user` 等机制进入 `requires_user_input` 状态时，前端不应硬编码续跑阶段。前端应提交上一轮 workflow payload 作为 `resumeState`，由后端根据 `resumeState.events/state/summary` 推断阻断节点，并设置内部 `resume_from`。当前已支持从 `requirements`、`project_planning`、`detail_confirmation`、`inspect_workspace`、`prepare_build_tasks` 和后续执行节点续跑；后续计划确认等节点接入用户确认时，应扩展后端推断逻辑，而不是让前端传固定阶段名。

所有涉及 `ProjectPlan` 生成或调整的节点，在真正进入任务拆分、构建或任何代码修改前都必须让用户确认。未确认的计划只能作为 `pending_project_plan` 或待确认状态存在，不能作为 Build/Codegen 的执行依据。`inspect_workspace` 只生成内部事实快照，不改变用户确认过的产品语义，不需要单独用户确认。

`prepare_build_tasks` 是代码生成前的最后硬保护：即使前序路由、旧会话状态或手工续跑误入该节点，只要 `project_plan.confirmation_status != confirmed`，该节点必须停止并通过 `ask_user` 要求确认，绝不能生成任务 DAG 或进入 `build`。

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
- 数据源清单；
- 业务流程；
- 验收标准；
- 待确认问题；
- 默认假设。

当前实现通过 `agents/main/requirements_analyzer.py` 直接调用 `create_chat_model()`，并只绑定通用 `tools/ask_user.py`。该边界不创建 Main DeepAgent，不加载 workspace backend，也不暴露 `task`、Frontend/Data Source/Test subagent 或文件读写工具。需求分析提示词明确要求覆盖应用信息、用户角色、功能模块、页面清单、数据源清单、业务流程和验收标准；若模型判断信息缺失、模糊或不适合假设，应生成 `ask_user` tool call，由后端解析为 1-4 个待确认问题。

`ask_user` 是通用的人机确认工具，不包含 requirements 专用问题规则。后续项目计划、单页面设计、数据源确认等阶段需要用户输入时，也应复用该工具，由对应 Agent 根据上下文决定问题内容。

当 Main Agent 判断需求不清晰时，必须先一次性审视所有关键产物所需信息：应用信息、角色、模块、页面清单、数据源清单、支撑 API 契约的业务信息、业务流程和验收标准。它将所有无法安全推断的缺口合并为一次 1-4 题的 `clarification.status = requires_user_input`，Graph 在该节点后结束本轮运行并等待用户回答。前端提交回答时同时携带上一轮 workflow payload、上一版归纳需求和本轮结构化答案；后端据此推断续跑节点并生成扁平的当前请求，不重复嵌套完整会话。Main Agent 基于上一版 `RequirementSpec` 和本轮反馈返回完整 JSON，新反馈覆盖冲突旧内容，确定性服务只负责字段校验和缺省补齐。

无论初始需求是否需要澄清，只要 `requirements` 生成或更新了需求文档，就必须进入 `requirement_spec_confirmation`，要求用户明确确认文档是否正确。澄清问题的回答只用于补充需求，不能等同于对生成后文档的确认；只有用户确认当前版本后，节点才输出 `status = completed` 并继续进入 `project_planning`。若用户补充后仍存在重要缺口，模型可以再次发起一次集中澄清。用户提出修改意见时，需要重新生成文档，并再次经过确认。

等待 `requirement_spec_confirmation` 时，AG-UI workflow payload 通过只读 `confirmationArtifact` 返回当前 `requirement-spec.md` 的文件名、路径和完整 Markdown 正文，供确认卡片展示。普通需求澄清不返回该正文；用户仍通过原确认输入框提交确认或修改意见，前端不提供额外的文档编辑/写回协议。

确认时以 Markdown 作为用户可读、可编辑的文档。如果用户在确认前直接修改了 RequirementSpec Markdown，节点必须先与当前结构化状态对比，以原 JSON 为基线同步 Markdown 中的业务变更并保留 Markdown 未表达的内部字段，然后更新内部 JSON；不得先重写 Markdown 或直接使用旧 JSON 继续。JSON 文件只供工作流节点读取，不作为前端可编辑产物展示。

当前等待/续跑机制是显式的后端推断续跑点，还不是 LangGraph 原生 `interrupt` resume。后续如果切换到 LangGraph `interrupt`、checkpointer 和 command resume，应保持同样的原则：前端提交用户回答和 workflow 状态，不硬编码后端阶段名。

所有选项型 `ask_user` 问题（单选、多选、是/否）都自动包含“其他”选项。用户选中“其他”后必须填写补充内容；前端提交结构化答案 `{ selected, other }`，后端将其归并为“已选：…；其他补充：…”，与原始需求和既有选项一起输入给后续模型。文本题本身就是自由输入，不额外显示“其他”。

生成选项题前，模型必须先判断选项是否互斥。搜索、筛选、导入导出、分页等可叠加能力必须使用 `multiSelect = true`，并将每项能力作为独立选项；不得通过“搜索 + 导入导出”这类组合选项伪造单选。只有数据源类型、认证策略等真正的二选一或多选一决策使用单选。

Graph 节点只接收直接 ChatModel 边界产出的结构化 `RequirementSpec` 和澄清结果，负责写入需求文档并更新状态，不应自行进行需求分析。

### `direct_modification`

简单需求专用节点，负责在已有工程上下文中直接完成小范围修改：

- 识别修改目标和允许修改的文件范围；
- 必要时由 Main Agent 将任务委派给 Frontend 或 Data Source Agent；
- 执行局部文件修改；
- 输出结构化修改结果、变更文件、执行命令和风险提示；
- 进入 `integration_test`，复用后续测试、质量门禁、启动和验收流程。

该节点不生成完整 RequirementSpec、项目级计划或任务 DAG。若执行过程中发现需求实际涉及架构、契约、数据模型或多页面联动，应升级为复杂需求并回到完整开发流程。

### `project_planning`

负责生成项目级计划：

- 需求概述；
- 技术架构；
- API 契约；
- 页面清单；
- 数据源清单；
- 权限模型；
- 页面和数据源之间的依赖；
- 面向后续任务拆分的 `task_inputs`。

该节点由 project-planning 专用 ChatModel 执行项目级规划：读取 `RequirementSpec`，产出结构化 `ProjectPlan` 和总体计划书 Markdown 文档。这个阶段不生成业务代码。

该节点通过 `agents/main/planner.py` 直接调用 `create_chat_model()` 生成结构化 JSON 规划建议，再由确定性 schema 合并和归一化后写入 Graph State。该调用不绑定任何工具，不创建 DeepAgent，也不扫描 workspace；模型输出只用于细化项目级判断，确定性归一化负责保证稳定 id、必需字段和后续任务拆分可读取的结构。

`project_planning` 在输出计划前会内部核对 API 契约、页面清单、数据源清单、依赖、角色、流程和验收标准；普通缺口以明确假设和风险写入同一份计划，而不是拆成后续多轮追问。生成计划书后进入一次 `project_plan_confirmation` 等待状态。用户确认“正确，继续”等语义后，节点才输出 `status = completed` 并进入 `detail_confirmation`；如果用户提出调整意见，则重新生成/调整 `ProjectPlan` 并再次等待确认。

等待 `project_plan_confirmation` 时，AG-UI workflow payload 的只读 `confirmationArtifact` 只返回当前 `project-plan.md`，不会同时返回 RequirementSpec 正文。用户提交调整意见并重新生成计划后，下一轮确认展示新写入的 Markdown；`detail_confirmation` 不复用该载荷展示 ProjectPlan。

ProjectPlan 同样以 Markdown 作为用户确认入口。确认前若 Markdown 被直接编辑，节点先将改动同步到内部 ProjectPlan JSON，并执行 API 契约、页面依赖和数据源一致性校验；同步成功后才允许确认并进入后续节点。AG-UI 产物列表只展示 Markdown 等用户可读文件，所有 JSON 路径和 JSON 任务文件都属于内部工作流状态，不向用户呈现为可编辑产物。

API 契约在此阶段作为前后端共享的唯一字段事实来源生成。为保持简约和可扩展，每个 contract 只包含资源级 `schemas`、稳定 endpoint id、HTTP method、path、参数、请求/响应 schema 引用、错误码和权限要求。`data_sources` 只能保存 `schema_refs`，不得复制字段；`page_data_dependencies` 只能保存 endpoint 引用；页面详细设计只能通过 `response_bindings` 绑定已声明响应字段。`detail_confirmation` 若发现字段或接口缺口，应提出 ProjectPlan 调整并经过确认，不能自行补字段或发明独立接口。

`ProjectPlan` 至少包含：

- `requirements_overview`：需求概述、应用目标、用户角色、功能模块、业务流程和验收重点；
- `project_acceptance_criteria`：整个需求在项目完成时必须满足的验收标准；
- `architecture`：前端、后端、数据和测试策略；
- `api_contracts`：唯一的业务字段 Schema、资源 endpoint 和输入输出 Schema 引用；
- `frontend_pages`：页面路径、模块归属、数据依赖、状态和权限；
- `data_sources`：数据源类型、实体、`schema_refs` 和 Seed 策略，不重复保存字段；
- `page_data_dependencies`：页面、数据源、API 契约和具体 endpoint 之间的显式引用关系；
- `permission_model`：角色、页面访问规则、操作权限和默认权限策略；
- `task_inputs.frontend`：后续前端任务拆分输入；
- `task_inputs.data_source`：后续数据源任务拆分输入；
- `coordination_plan`：Main Agent 对细节确认、构建分发、测试反馈的协调策略；
- `planned_by`：执行规划的直接模型、运行方式和模型信息；
- `risks`：后续细节确认阶段需要消化的风险和待细化点。

### `detail_confirmation`

批量生成并整体审阅全部页面和数据源详细设计，负责：

- 读取 `ProjectPlan.frontend_pages` 和 `ProjectPlan.data_sources`；
- 一次性为所有页面和数据源生成初版详细设计；
- 在同一审阅界面按页面和数据源分组展示，默认折叠；
- 用户只展开需要调整的对象，按页面目标、布局、交互、权限、关系、校验和 Seed 等模板字段修改；
- 核对数据模型、关系、校验规则和 API 映射；如需字段变更则返回 ProjectPlan 契约调整；
- 未修改时允许一键确认全部设计；
- 将确认后的页面详细设计写回 `ProjectPlan` 和总体计划书 Markdown，保证 Graph State 与规划文档一致。

该阶段由只读规划逻辑和 page-design 专用 ChatModel 负责，不由代码生成 Agent 负责。

当前实现使用 `xcodeagent.detail_review.v1` 批量审阅 payload。首次进入该节点时，节点从 `ProjectPlan` 读取全部页面和数据源，生成完整 `page_detail_plans` 和 `data_source_detail_plans`，写入 `pending_project_plan`，然后一次性暂停。前端提交 `detail_review` 结构化结果并携带 `resumeState`，后端只合并白名单模板字段、执行契约一致性校验并确认当前计划，不再逐个选择对象或产生多轮中断。

页面初版设计结合 `frontend_pages`、`api_contracts`、`page_data_dependencies` 和相关 `data_sources`，覆盖页面目标、基本布局、交互、状态、权限、依赖、响应字段绑定和验收标准。数据源初版设计覆盖实体引用、关系、校验、API 契约、依赖页面、Seed/Mock 策略和验收标准。页面的数据源、endpoint、Schema 和 `response_bindings`，以及数据源的实体、Schema 和 API 契约在审阅界面只读；用户不能在详情层新增字段或接口。需要修改契约时必须返回 `project_planning`，更新 ProjectPlan 后重新确认。

批量初版设计生成后统一进入一次整体确认。用户提交的页面/数据源修改是对当前可见模板字段的最终确认，后端不得在提交后继续生成用户未审阅的新内容。确认成功后 `pending_project_plan` 才提升为正式 `project_plan`，随后才允许进入 `prepare_build_tasks` 和后续代码生成。

当前等待/续跑机制仍是显式状态推断而非 LangGraph 原生 `interrupt`。后续若切换到 checkpointer + command resume，应保留同样的状态边界：Graph 节点只恢复阻断节点需要的 ProjectPlan/PageSpec 小型结构化状态，不把完整会话历史重新塞回上下文。

页面详细设计至少包含：

- 页面目标；
- 页面基本布局；
- 页面交互；
- 数据来源；
- 页面权限；
- 页面依赖；
- 页面级验收标准。

`prepare_build_tasks` 生成任务 DAG 前必须执行确定性的 API 契约一致性检查，并在 `integration_test.api_contract_check` 再次执行：数据源不得包含独立 `schema`；所有 schema/endpoint 引用必须存在；页面 `response_bindings.source_path` 必须来自所依赖 endpoint 的响应 Schema；写接口必须声明请求 Schema，非删除接口必须声明响应 Schema。任何错误都会阻止任务拆分或令质量门禁失败。

### `inspect_workspace`

确定性、可缓存的工作区检查节点，负责在任务拆分前生成 `WorkspaceSnapshot`：

- 解析当前 workspace revision，包含 Git HEAD、暂存区 diff、未暂存 diff、未跟踪文件清单、关键 lock/config 文件和 inspector schema 版本；
- 命中 `.xcodeagent/cache/workspace-snapshots/{workspace_revision}.{schema_version}.json` 时直接复用；
- 未命中时用轻量扫描识别项目根、技术栈、入口文件、构建/测试命令、FastAPI 路由、Pydantic 模型、Workflow 节点、React 组件、API client、Electron IPC、AG-UI 使用点和共享契约候选；
- 将完整 snapshot 写入 `.xcodeagent/cache/workspace-snapshots/`，Graph State 只保存 `workspace_snapshot_summary`、`workspace_snapshot_path`、`workspace_snapshot_hash` 和 `workspace_revision`；
- 预留 `CodeGraphProvider` 扩展点。第一期默认使用空 provider，后续可接 Codebase Memory MCP、SCIP、Serena 或 tree-sitter 图索引，把图查询结果并入 `snapshot.code_graph`。

该节点不生成任务、不修改代码、不调用写工具，也不把快照写入 `ProjectPlan`。它只回答“当前工作区事实是什么”，供后续模型规划和确定性调度引用。

### `prepare_build_tasks`

由 planning-only ChatModel 根据已经确认并写回的 `ProjectPlan` 和 `WorkspaceSnapshot` 生成可执行静态 Build DAG：

- 使用 `inspect_workspace` 生成的 `WorkspaceSnapshot` 作为唯一工作区事实来源，不读取、创建、修改或删除代码文件；
- 生成稳定的 `task_id`；
- 指定任务执行 Agent；
- 计算任务依赖；
- 标记可并行任务；
- 以 `change_scope` 记录新增、修改、删除文件及每项改动目的，并据此设置允许修改的文件范围；
- 以 `impact_scope` 记录受影响模块、公共契约、风险和影响摘要；
- 绑定验收标准；
- 初始化任务状态为 `pending`，后续只在 `pending/running/completed/failed` 中流转；
- 校验循环依赖和缺失依赖。

该节点不生成新需求，也不编写业务代码。`ProjectPlan` 和 `WorkspaceSnapshot` 是唯一输入上下文；模型负责将已确认的页面详细设计、相关数据源和当前工程结构转换成可执行任务 DAG；Graph 节点只接收结构化 `build_task_plan`、执行确定性归一化与 DAG 校验、更新 `tasks`，并交给后续 Build Subgraph 执行。

该节点通过 `agents/main/task_preparer.py` 调用 direct ChatModel 生成任务编排建议，再由确定性 schema 归一化为静态 Build DAG。`build_task_plan.workspace_analysis` 优先使用模型返回的结构化摘要；缺省时由 `WorkspaceSnapshot` 兜底，并记录 `workspace_snapshot_ref` 以便恢复和审计。模型未返回可解析任务时，节点必须阻止进入 `build`，不能用硬编码任务清单代替模型规划结果。

调用模型生成任务 DAG 前，节点必须检查 `ProjectPlan.confirmation_status == confirmed`。若计划未确认，节点返回 `requires_user_input` 并停止在当前阶段；用户确认后可从 `prepare_build_tasks` 续跑，再生成任务 DAG。

`build_task_plan` 至少包含：

- `tasks`：可执行任务 DAG；
- `summary`：任务数量统计；
- `workspace_analysis`：任务拆分前实际检查到的代码结构和工程约定；
- `prepared_by`：执行任务编排的 Agent、运行方式和模型信息；
- `coordination`：任务分发顺序、依赖策略和串并行执行批次。

该设计沿用 learn-coding-agent 的“先侦察、再计划、执行后验证”循环，并采用 OpenCode 风格的稳定任务 ID、显式状态和文件冲突串行化。与 Deep Agents 的默认 harness 映射是：`prepare_build_tasks` 只负责 planning，不挂载文件工具；后续 BuildScheduler 与代码执行 runner 负责 action/verification。为控制 128k 上下文预算，模型只接收已确认计划、快照摘要和精确文件清单，不把完整目录树或文件内容复制进 Graph State。

该节点的结构化产物必须落盘，供后续恢复执行和单节点验证使用：

```text
{workspace}/.xcodeagent/specs/requirement-spec.{md,json}
{workspace}/.xcodeagent/plans/project-plan.{md,json}
{workspace}/.xcodeagent/cache/workspace-snapshots/{workspace_revision}.{schema_version}.json
{workspace}/.xcodeagent/plans/build-task-plan.json
{workspace}/.xcodeagent/plans/repair-task-plan.json
{workspace}/.xcodeagent/reports/test-report.json
```

`project-plan.md` 和 `requirement-spec.md` 面向人类阅读；节点恢复执行必须优先使用同目录下的 JSON 文件。若要跳过前序节点单独验证任务 DAG 生成，可执行：

```bash
app-demo-prepare-build-tasks var/workspaces/demo-project/.xcodeagent/plans/project-plan.json
```

本地调试某个节点时，使用前端 Chat Composer 的“Workflow 调试”面板选择开始节点，并填写已落盘 JSON 产物路径，避免每次从头生成需求文档。调试面板通过 AG-UI `forwardedProps.workflowDebug` 传入 `resumeFrom`、`requirementSpecPath`、`projectPlanPath`、`workspaceSnapshotPath` 和 `buildTaskPlanPath`。

如果要从已经生成的项目计划调试后续节点，可填写 `project-plan.json` 并把开始节点设置为 `detail_confirmation`、`inspect_workspace` 或 `prepare_build_tasks`。从 `prepare_build_tasks` 或后续节点续跑时，也可以填写 `.xcodeagent/cache/workspace-snapshots/<revision>.1.0.0.json` 复用已生成的工作区快照。调试续跑仍会遵守确认闸口；未确认的 `ProjectPlan` 不会进入代码生成。为兼容已有项目，调试目录解析仍能读取旧的 `specs/` 和 `plans/` 路径；新写入统一使用 `.xcodeagent/`。

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
- 文件锁来自 `lock_scope`、`change_scope.path`、`targetFiles` 和 `allowed_paths`，同一批 ready task 之间不能冲突；
- 任务按 `owner` 派发给对应 CodeRunner：`data_source` 使用 Data Source Generation Agent，`frontend` 使用 Frontend Generation Agent；
- CodeRunner 只返回结构化 `TaskResult`，不更新 DAG；
- 调度器校验缺失或非法结果，并将其转为 `runner_protocol_error`；
- 失败结果先分类为 `retry`、`repair`、`requires_confirmation` 或 `terminal_failure`。`repair` 会触发 Build Repair Planner 生成受约束 repair task，并 append 到运行时 Build DAG；repair task 成功后调度器关闭原 failed task 并继续释放下游依赖。`requires_confirmation` 和 `terminal_failure` 仍会阻断构建并写入摘要。

Build Repair Planner 是独立的只读 RepairPlanner DeepAgent 节点，不是 Main Agent。它由 `BuildScheduler` 严格约束输入和输出，只在调度器已将失败分类为 `repair` 后被调用，不直接操作 DAG、任务状态或调度循环。调度器传入的 `RepairPlannerInput` 包含原 task、失败 attempt result、允许修改范围 `change_scope/allowed_paths`、当前 `WorkspaceSnapshot` 或 targeted snapshot、失败日志引用和原验收标准。Planner 返回 `RepairPlan`，只能是三种决策之一：

- `repair`：包含修复策略、边界说明和一个或多个 repair task；服务层会强制 repair task 继承原任务的 owner、change_scope、allowed_paths、依赖隔离和验收边界；
- `requires_user_confirmation`：表示需要扩大修改范围、变更已确认需求/API 契约或做用户可见产品决策，调度器停止继续释放后续任务；
- `terminal_failure`：表示证据不足、修复预算耗尽或失败不可自动处理，调度器停止构建并保留失败证据。

因此失败处理不是统一“重跑”：可重试的 runner/tool/网络类失败可以由调度策略处理；实现、编译、测试、验收类失败进入 RepairPlanner；契约或计划边界类失败进入用户确认；不可恢复失败终止当前 build。

专业代码生成 Agent 必须以 Deep Agent 形式存在，具备受控文件读写能力，并从已批准任务中读取 `allowed_paths`、依赖、验收标准和上下文。它们只执行任务，不负责更新计划文档、修改需求或重写任务 DAG。任务完成、失败、变更申请和计划一致性由 `BuildScheduler` 与确定性协调服务统一更新；需要修复规划时再调用独立 RepairPlanner Agent。

`workspaceRoot` 是 Backend 的宿主机目录，只能用于 Graph State、Agent filesystem backend、确定性文档写入和 workspace diff 捕获。Deep Agent 的文件工具始终以 `/` 作为虚拟工作区根；例如任务中的 `app/frontend/**` 必须解释为 `/app/frontend/**`，不得把 `/Users/...`、Windows 盘符或其它真实 `workspaceRoot` 拼入工具路径。Frontend/Data Source generation prompt 不暴露真实根目录，filesystem permission 和 `delete_file` 还会拒绝把真实根目录重复成虚拟子目录的路径。已经存在的错误嵌套目录不会被工作流自动迁移或删除。

### Skill 与上下文预算

Main Deep Agent 会直接执行简单分支的局部修改，并负责复杂分支的任务拆分和返修规划；Frontend Deep Agent 负责复杂分支的前端实现。因此这两个 Agent 通过 Deep Agents 原生 `skills` 参数按“内置、用户”顺序加载 Skill，用户同名 Skill 后加载并覆盖内置 Skill。Data Source/Test Agent 只加载用户 Skill；直接 ChatModel 节点不加载 Skill。Main 注册的三个 `CompiledSubAgent` 各自保留独立的 SkillsMiddleware，不依赖 Main 隐式继承。

内置 skill 的宿主目录在源码模式为 `Backend/app/builtin_skills/`，在 PyInstaller onedir 模式为后端资源目录 `_internal/app/builtin_skills/`。Agent 不接触宿主绝对路径，而是通过只读 CompositeBackend 路由 `/.xcodeagent/builtin-skills/` 发现和读取 skill；文件权限与 `delete_file` 都拒绝写入或删除该命名空间。Backend Python 是必需 skill 名称和文件的唯一事实来源：PyInstaller staging 和 Backend 启动执行完整性校验并在缺失时 fail fast；Electron 打包前和启动前只检查通用 `builtin_skills` 资源目录，不复制具体 skill 清单。

用户 Skill 来自当前环境的 `~/.xcodeagent[_dev|_st|_uat]/skills`。每次创建 Agent bundle 前，Backend 会把有效直属 Skill 的完整目录复制为不可变只读快照，并通过 `/.xcodeagent/user-skills/` 挂载；符号链接、非常规文件和超限 Skill 会被隔离跳过。bundle 缓存键包含工作区和快照 revision，因此保存或外部修改会在下一次调用生效，已经运行中的 Agent 继续读取原快照。

该设计映射到参考架构：learn-coding-agent 的紧凑“收集上下文—行动—验证”循环只读取当前任务需要的规范；OpenCode 风格把用户 Skill 作为可发现、可覆盖且错误隔离的 Agent 能力；Deep Agents 使用原生 SkillsMiddleware、FilesystemBackend 和 CompositeBackend。为遵守 128k 上下文预算，system prompt 只常驻 Skill 名称、描述和虚拟路径，模型命中任务后再读取完整 `SKILL.md` 与所需辅助资源，不把全部正文固定拼进每次请求。

外层主 Graph 不关心单个生成任务的执行细节，只根据 Build Subgraph 输出的任务状态和结果继续进入 `integration_test`。

### `integration_test` / Testing Subgraph

`integration_test` 在外层主 Graph 中表现为一个节点，但内部应实现为 Testing Subgraph，并合并原 `quality_gate` 的职责。

测试命令和质量判定应以确定性结果为准，不应让大模型凭空判断是否通过。Test Deep Agent 的职责是审阅确定性证据、生成测试摘要和返修建议；确定性规则负责更新 `test_report`、`quality_gate_passed`、`needs_revision` 和 `revision_requests`，需要生成修复计划时调用独立 RepairPlanner Agent。

Testing Subgraph 的最小内部结构：

```text
testing.START
  → frontend_checks
  → backend_checks
  → api_contract_check
  → joint_integration_check
  → e2e_check
  → test_agent_review
  → main_quality_gate
  → testing.END
```

质量门禁至少应覆盖：

- 前端 TypeScript 依赖安装；
- 前端 TypeScript 构建；
- 前端 lint；
- 前端 typecheck；
- 前端单元测试；
- 后端 Java 构建；
- 后端 Java 静态检查；
- 后端 Java 单元测试；
- API 契约有效；
- 前后端集成测试通过；
- E2E 测试通过。

输出至少包含：

- `test_results`：每项测试的通过状态、命令和证据；
- `test_report`：测试汇总、Test Agent 审阅说明和质量门禁结果；
- `test_report_path`：结构化测试报告 JSON 路径；
- `quality_gate_passed`：是否允许进入启动和验收；
- `needs_revision`：是否需要返回修改；
- `revision_requests`：返回给 RepairPlanner Agent 的结构化返修请求。
- `repair_task_plan`：RepairPlanner Agent 基于失败证据生成的修复任务计划；
- `repair_task_plan_path`：结构化修复任务计划 JSON 路径；
- `repair_tasks`：可被重新分发给 Frontend/Data Source 等代码执行 Agent 的修复任务。

当前最简版不执行真实 npm/pytest/playwright 命令，而是通过确定性 demo 检查根据 `build_summary` 生成结果。后续正式实现时，每个 check 节点替换为真实命令执行和证据采集即可。

其中 `frontend_checks` 和 `backend_checks` 是按技术栈聚合的业务级节点，内部可以继续执行多个具体命令。Graph 不应把 npm/maven/lint/typecheck/unit test 全部暴露成一等节点，避免主流程过碎；但 `test_results` 里仍需保留每个具体检查项的结构化证据。

测试不通过时，Testing Subgraph 必须把失败项转成足够详细的 `revision_requests`，包括失败检查、命令、证据、建议 owner。随后由 RepairPlanner Agent 汇总生成 `repair_task_plan`，再由后续修复循环分发给对应代码修改 Agent：

- 前端检查失败 → Frontend Generation Agent；
- 后端或 API 契约检查失败 → Data Source Generation Agent；
- 前后端集成或 E2E 失败 → RepairPlanner Agent 先判断归因，再拆分给专业 Agent。

### `launch_project`

确定性运行节点，负责：

- 使用经过校验的启动命令；
- 分配端口；
- 启动前后端服务；
- 执行健康检查；
- 返回本地预览地址；
- 保存和清理进程信息。

### `acceptance`

暂停并等待用户验收。

用户选择：

- 通过：进入 `finalize_project`；
- 页面或数据源调整：返回细节确认阶段；
- 架构级调整：返回项目规划阶段；
- 取消：停止任务和运行进程。

当前最简版自动通过。

### `finalize_project`

负责：

- 固化最终 Spec 和计划；
- 保存测试报告；
- 生成 README 和运行说明；
- 输出工程目录或压缩包；
- 将项目状态标记为完成。

## 一等 Deep Agent

本项目只保留四个一等 Deep Agent：Frontend Generation、Data Source Generation、Test、RepairPlanner。`agents/main/` 只作为历史命名下的 direct ChatModel 边界目录，用于需求、规划、页面设计、任务准备和 Markdown 同步；它不再声明或创建 Main DeepAgent。

### Frontend Generation Agent

目录：`agents/frontend/`

职责：

- 根据已批准的页面执行计划生成前端代码；
- 实现布局、组件、交互、权限和 API 接入；
- 实现 loading、empty 和 error 状态；
- 编写页面测试；
- 执行前端 lint、typecheck 和单元测试。

它不负责页面需求确认，也不负责自行修改 PageSpec。

### Data Source Generation Agent

目录：`agents/data_source/`

职责：

- 根据已批准的数据源执行计划生成数据模型；
- 实现数据库迁移、Seed 或 Mock 数据；
- 实现 API、校验和权限；
- 编写后端测试；
- 遵守已经确认的 API 契约。

如果契约不可实现，应返回变更申请，不得静默修改契约。

### Test Agent

目录：`agents/test/`

职责：

- 执行集成测试和 E2E 测试；
- 收集确定性测试证据；
- 输出结构化测试报告和缺陷；
- 只负责发现问题，不直接修改业务代码。

### RepairPlanner Agent

目录：`agents/repair_planner/`

职责：

- 在 build task 或 integration test 失败后分析失败证据；
- 接收调度器约束的 `RepairPlannerInput` 或测试返修请求；
- 输出 `RepairPlan` / `repair_task_plan`；
- 对需要扩大范围或改变契约的情况返回用户确认需求；
- 只读工作区，不直接修改代码、计划、DAG 或调度状态。

修复任务由 BuildScheduler 或后续修复循环重新派发给 Frontend/Data Source 等代码执行 Agent。

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
- 前端历史会话的消息、草稿、运行状态、停止控制和 AG-UI client 必须按 `workspaceRoot + editorMode + sessionId` 隔离；`threadId` 只属于对应会话，每次执行使用独立 `runId`。
- 同一个 `workspaceRoot` 同时只允许一个 `/workflow/run` 进入 Graph。Backend 使用进程内非阻塞 workspace lease 保护共享代码、固定计划文档和全目录 diff 快照，冲突请求通过现有 AG-UI 失败事件返回 `workspace_busy`。
- 停止生成必须是端到端取消：前端先中止当前 SSE 消费以停止渲染，再通过同一 `/workflow/run` 发送带 `forwardedProps.cancelRunId` 的独立 AG-UI 控制运行。后端的进程内运行表按 `runId` 调用对应 `asyncio.Task.cancel()`，使 `graph.astream()` 和其正在等待的异步模型 HTTP 流收到取消；控制运行也返回完整 AG-UI 开始、消息、状态快照和结束事件。模型供应商对已在其服务端排队的 token 的最终停止时点仍是 best-effort，不把取消响应误报为模型已计费归零。
- 该设计对应 learn-coding-agent 的“执行后立刻反馈/停止”紧凑循环，采用 OpenCode 风格的稳定运行标识和显式任务生命周期，并保持 Deep Agents 的人类可控边界。运行表只保存 `runId -> asyncio.Task`，不复制对话或仓库内容，因此不会扩大 128k 上下文预算；当前单 Uvicorn 进程是该进程内表的适用边界，未来多进程部署需要共享取消协调器。
- 修改相同文件的任务不得并发执行。
- 共享入口文件、依赖清单、API 契约和路由配置应使用文件锁。
- 任务锁和文件锁由 `workspace/` 提供，不由 Agent 自行约定。
- 生成代码和执行命令最终应运行在隔离 Sandbox 中。

会话隔离不等于项目文件隔离：同一 workspace 内不同会话仍然顺序共享代码、Spec、Plan 和 Report。当前桌面端只启动一个 Uvicorn 进程，因此进程内 lease 覆盖所有 Renderer 请求；未来引入多进程 Backend 时必须将 lease 升级为跨进程锁或独立 worktree。

## 当前不实现的内容

以下能力已经预留边界，但不要求在最简版本中完成：

- 多轮需求澄清和人工中断；
- Spec、计划和任务的数据库持久化；
- 项目级 Sandbox；
- 文件锁和任务锁；
- 真实前后端代码生成；
- 测试失败后的自动修复循环；
- 本地进程生命周期管理；
- AG-UI 状态同步和前端界面；
- 生产级鉴权、限流、预算和审计。

实现这些能力时，应扩展现有目录和节点，不应重新设计一套平行流程。
