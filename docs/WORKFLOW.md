## workflow目标

workflow根据用户需求生成可在本地运行的前后端工程，并通过需求确认、计划生成、代码生成、集成测试和用户验收形成完整闭环。

## 核心架构原则

1. 外层 LangGraph 管理确定性的项目生命周期。
2. Deep Agents 负责需要自主推理、工具调用、文件操作和多步执行的任务。
3. Agent 不得自行决定或绕过项目阶段、用户确认、任务依赖和质量门禁。
4. Graph State 保存小型结构化状态和文件引用，不保存完整代码、大型日志或全部 Agent 消息。
5. 项目文件、Spec、计划和测试报告是跨节点共享的事实来源。
6. Deep Agent 的消息和工具结果属于任务级临时上下文，不直接合并进整体 Graph State。
7. 所有 Agent 结果必须结构化，并经过确定性校验后才能更新业务状态。
8. 测试是否通过由确定性的质量门禁判断，不能只相信 Agent 的自然语言结论。

## 已确认的主 Graph

主流程顺序如下：

```text
START
  → classify_request_complexity
      ├─ 复杂需求 → requirements //main agent负责
      │            → project_planning //main agent负责
      │            → detail_confirmation //main agent负责
      │            → prepare_build_tasks //main agent负责
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

### `classify_request_complexity`

确定性路由节点，负责判断用户请求进入哪条流程：

- 复杂需求：生成新应用、创建工程、涉及多页面、API、数据源、权限、登录、全栈协作等，进入完整开发流程；
- 简单需求：局部修改、文案调整、样式调整、按钮/标题改动、小范围 Bug 修复等，进入直接修改流程；
- 模糊需求：默认按复杂需求处理，因为完整流程包含需求确认，更安全。

该节点使用 `services/request_complexity.py` 中的确定性决策器实现，输出：

- `request_complexity`：`simple` 或 `complex`；
- `complexity_reason`：用于前端展示和日志排查的简短原因；
- `complexity_decision.confidence`：当前判断置信度；
- `complexity_decision.signals`：命中的规则信号，例如 `complex_scope:权限` 或 `simple_scope:标题`。

后续如果需要模型辅助判断，也应由 Main Agent 给出结构化建议，再由 Graph 的确定性路由规则决定最终分支。

### `requirements`

由 Main Agent 负责：

- 理解用户的原始需求；
- 发现缺失信息并提出澄清问题；
- 生成结构化 `RequirementSpec`；
- 生成需求 Spec Markdown 文档；
- 暂停并等待用户确认。

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

当前最简版通过 `agents/main/requirements_analyzer.py` 作为 Main Agent 需求分析边界，并通过 `tools/clarification.py` 生成待确认问题和默认假设，不阻塞等待用户输入；正式实现时，Main Agent 应调用澄清工具，经 LangGraph `interrupt`、checkpointer 和 AG-UI 事件等待用户确认。

Graph 节点只接收 Main Agent 产出的结构化 `RequirementSpec` 和澄清结果，负责写入需求文档并更新状态，不应自行进行需求分析。

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

该节点由 Main Agent 执行项目级规划：读取 `RequirementSpec`，产出结构化 `ProjectPlan` 和总体计划书 Markdown 文档。这个阶段不生成业务代码。

该节点调用真实 Main Deep Agent 生成规划建议，再由确定性 schema 归一化后写入 Graph State。

`ProjectPlan` 至少包含：

- `architecture`：前端、后端、数据和测试策略；
- `api_contracts`：资源、路径、方法、响应结构；
- `frontend_pages`：页面路径、模块归属、数据依赖、状态和权限；
- `data_sources`：数据源类型、实体、初版字段模型和 Seed 策略；
- `task_inputs.frontend`：后续前端任务拆分输入；
- `task_inputs.data_source`：后续数据源任务拆分输入；
- `coordination_plan`：Main Agent 对细节确认、构建分发、测试反馈的协调策略；
- `planned_by`：执行规划的 Agent、运行方式和模型信息；
- `risks`：后续细节确认阶段需要消化的风险和待细化点。

### `detail_confirmation`

逐个处理页面和数据源，负责：

- 向用户展示当前页面清单；
- 引导用户选择一个页面进入详细设计；
- 引导用户确认该页面的 `PageSpec`；
- 确认页面布局、组件、交互、权限和异常状态；
- 确认数据模型、关系、校验规则和 API 映射；
- 生成单页面或单数据源的执行计划；
- 等待用户逐项确认；
- 将确认后的页面详细设计写回 `ProjectPlan` 和总体计划书 Markdown，保证 Graph State 与规划文档一致。

该阶段由主 Agent 的需求/规划能力负责，不由代码生成 Agent 负责。

当前最简版通过 `tools/page_selection.py` 生成页面选择交互 payload。若输入 state 包含 `selected_page_id`，则使用该页面；否则默认选择第一个页面并记录为自动选择。

页面详细设计不能只从 `ProjectPlan` 中读取。`ProjectPlan` 只提供页面候选、API 契约、数据源和依赖上下文；真实页面设计必须基于用户确认后的 `PageSpec`，再由 Main Agent 生成。当前最简版通过 `tools/page_spec_confirmation.py` 使用传入的 `confirmed_page_spec` 模拟用户确认；若未传入，则根据选中页面自动生成默认 `PageSpec`。

正式实现时应通过 AG-UI 事件展示页面清单和 PageSpec 表单，并用 LangGraph `interrupt` 等待用户选择和确认。

页面详细设计至少包含：

- 页面目标；
- 页面基本布局；
- 页面交互；
- 数据来源；
- 页面权限；
- 页面级验收标准。

### `prepare_build_tasks`

由 Main Agent 根据已经确认并写回的 `ProjectPlan` 生成可执行任务 DAG：

- 生成稳定的 `task_id`；
- 指定任务执行 Agent；
- 计算任务依赖；
- 标记可并行任务；
- 设置允许修改的文件范围；
- 绑定验收标准；
- 校验循环依赖和缺失依赖。

该节点不生成新需求，也不编写业务代码。`ProjectPlan` 是输入上下文，Main Agent 负责将已确认的页面详细设计和相关数据源转换成可执行任务；Graph 节点只接收结构化 `build_task_plan`、更新 `tasks`，并交给后续 Build Subgraph 执行。

该节点通过 `agents/main/task_preparer.py` 调用真实 Main Deep Agent 生成任务编排建议，再由确定性 schema 归一化。

`build_task_plan` 至少包含：

- `tasks`：可执行任务 DAG；
- `summary`：任务数量统计；
- `prepared_by`：执行任务编排的 Agent、运行方式和模型信息；
- `coordination`：任务分发顺序和依赖策略。

该节点的结构化产物必须落盘，供后续恢复执行和单节点验证使用：

```text
var/workspaces/{project_id}/plans/project-plan.json
var/workspaces/{project_id}/plans/build-task-plan.json
```

`project-plan.md` 和 `requirement-spec.md` 面向人类阅读；节点恢复执行必须优先使用同目录下的 JSON 文件。若要跳过前序节点单独验证任务 DAG 生成，可执行：

```bash
app-demo-prepare-build-tasks var/workspaces/demo-project/plans/project-plan.json
```

### `build`

`build` 在外层主 Graph 中表现为一个节点，但内部应实现为 Build Subgraph。它负责：

- 选择依赖已经满足的任务；
- 将页面任务派发给 Frontend Generation Agent；
- 将数据源任务派发给 Data Source Generation Agent；
- 收集结构化执行结果；
- 校验实际文件和命令结果；
- 更新任务状态；
- 在没有文件冲突时并行执行任务。

Build Subgraph 的最小内部结构：

```text
build.START
  → select_ready_build_tasks
  → generate_data_sources
  → main_update_after_data_sources
  → generate_frontend
  → main_update_after_frontend
  → collect_build_results
  → build.END
```

后续正式实现时，这个子图可以扩展为按任务 DAG 循环调度：

- `select_ready_build_tasks`：选择依赖满足、文件锁可获得、尚未完成的任务；
- `generate_data_sources`：调用 Data Source Generation Deep Agent 生成模型、迁移、API、校验和后端测试，只返回结构化执行结果；
- `main_update_after_data_sources`：由 Main Agent 边界汇总 Data Source Agent 结果，更新 `ProjectPlan`、`build_task_plan` 和任务状态；
- `generate_frontend`：调用 Frontend Generation Deep Agent 生成页面、组件、交互、API 接入和前端测试，只返回结构化执行结果；
- `main_update_after_frontend`：由 Main Agent 边界汇总 Frontend Agent 结果，更新 `ProjectPlan`、`build_task_plan` 和任务状态；
- `collect_build_results`：收集最终构建摘要，记录生成文件和命令证据；
- 如果任务失败，应生成修复任务或缺陷记录，而不是直接进入通过状态。

专业代码生成 Agent 必须以 Deep Agent 形式存在，具备受控文件读写能力，并从已批准任务中读取 `allowed_paths`、依赖、验收标准和上下文。它们只执行任务，不负责更新计划文档、修改需求或重写任务 DAG。任务完成、失败、变更申请和计划一致性由 Main Agent 统一协调。

外层主 Graph 不关心单个生成任务的执行细节，只根据 Build Subgraph 输出的任务状态和结果继续进入 `integration_test`。

### `integration_test` / Testing Subgraph

`integration_test` 在外层主 Graph 中表现为一个节点，但内部应实现为 Testing Subgraph，并合并原 `quality_gate` 的职责。

测试命令和质量判定应以确定性结果为准，不应让大模型凭空判断是否通过。Test Deep Agent 的职责是审阅确定性证据、生成测试摘要和返修建议；Main Agent/确定性规则负责更新 `test_report`、`quality_gate_passed`、`needs_revision` 和 `revision_requests`。

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
- `revision_requests`：返回给 Main Agent 的结构化返修请求。
- `repair_task_plan`：Main Agent 基于失败证据生成的修复任务计划；
- `repair_task_plan_path`：结构化修复任务计划 JSON 路径；
- `repair_tasks`：可被重新分发给 Frontend/Data Source/Main Agent 的修复任务。

当前最简版不执行真实 npm/pytest/playwright 命令，而是通过确定性 demo 检查根据 `build_summary` 生成结果。后续正式实现时，每个 check 节点替换为真实命令执行和证据采集即可。

其中 `frontend_checks` 和 `backend_checks` 是按技术栈聚合的业务级节点，内部可以继续执行多个具体命令。Graph 不应把 npm/maven/lint/typecheck/unit test 全部暴露成一等节点，避免主流程过碎；但 `test_results` 里仍需保留每个具体检查项的结构化证据。

测试不通过时，Testing Subgraph 必须把失败项转成足够详细的 `revision_requests`，包括失败检查、命令、证据、建议 owner。随后由 Main Agent 汇总生成 `repair_task_plan`，再由后续修复循环分发给对应代码修改 Agent：

- 前端检查失败 → Frontend Generation Agent；
- 后端或 API 契约检查失败 → Data Source Generation Agent；
- 前后端集成或 E2E 失败 → Main Agent 先判断归因，再拆分给专业 Agent。

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

## 四个一等 Deep Agent

本项目只保留四个一等 Deep Agent。

目录：`agents/main/`

职责：

- 需求分析；
- RequirementSpec 生成和更新；
- 项目级规划；
- 页面和数据源细节规划；
- 构建任务协调；
- 根据任务类型委派给专业 Agent；
- 汇总专业 Agent 的结构化结果。

Main Agent 不直接替代外层 Graph，不得控制项目阶段转换。

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

修复任务由 Main Agent 重新派发给 Frontend 或 Data Source Agent。

## 目录职责

```text
graph/          LangGraph 主业务流程、节点、路由和 Graph State
agents/         四个一等 Deep Agent 的声明与配置
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
- 修改相同文件的任务不得并发执行。
- 共享入口文件、依赖清单、API 契约和路由配置应使用文件锁。
- 任务锁和文件锁由 `workspace/` 提供，不由 Agent 自行约定。
- 生成代码和执行命令最终应运行在隔离 Sandbox 中。

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
