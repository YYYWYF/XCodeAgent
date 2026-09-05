# T2.4 frontend:shell Prerequisite Contract

`frontend:shell` 只表示模板前置能力，不是模型 Task 生成边界。

## 接口与行为

`build_task_reuse.resolve_template_prerequisite_facts` 接收 `unit_skeleton`、
`build_context`、`workspace_snapshot` 和 `template_readiness`，返回冻结的 `ReuseFacts`。
本接口只填充 `external_capabilities` 与 `issues`，历史任务索引为空；不替代完整
`resolve_reuse_facts`，也不切换其他 Unit 的生成或组装决策。

证据复用 T2.2 的确定性规则：真实 `inspect_template_generation_readiness` 返回
`ready=True`、无 errors、有效 manifest 和 main/auth 变体，并有 workspace revision。
证据包括 `frontend.shell.ready`、模板变体、manifest 路径与内容 SHA-256。
扫描到 package/App 文件、模型宣称检查成功、历史 Task completed 均不能证明该能力。

Skeleton 固定 shell 的当前状态，并在重建指纹中使用当前策略身份：

```text
generation_strategy = prerequisite_only
participation = prerequisite_only
generation_status = not_required
```

`prepare_build_tasks` 在模型调用前检查模板事实，将 external capabilities 放入 BuildContext。
模板或证据无效时返回已有 AG-UI 前置失败投影；事实层问题保留
`level=pre_generation`、`retryable=false`、空 `retry_unit_ids`。
Endpoint Scope 同样不能忽略二次模板检查失败。

shell 不进入 `planning_unit_ids` 或模型允许的 Unit 集合。原始候选校验与节点合并边界
明确拒绝 shell Task，不静默丢弃。Page Unit Graph 仍保留 shell 架构依赖；
编译器不把该边转换成 Task 依赖，也不报告 shell 缺少 Task provider。
历史 shell Task 及其执行状态保留，pending/failed/completed 不影响前置判断。

Planning 继续沿用正式路径 `.xcodeagent/plans/build-task-plan.json`。
文件缺失可开始首次规划；文件存在但不满足当前 confirmed v3 DAG 门槛，或读取失败，
入口必须阻断生成，不能按空基线继续或回退到 checkpoint/pending sidecar。
既有确认恢复分支不启动 Planning，仍可处理当前 pending DAG 的确认。

T2.4 收尾补丁将非法正式基线独立投影：

```text
mode = confirmed_baseline_error
code = confirmed_baseline_invalid
artifact = .xcodeagent/plans/build-task-plan.json
status = requires_user_input
issue.code = CONFIRMED_BASELINE_INVALID
issue.level = pre_generation
issue.category = platform
retryable = false
automatic_routing = false
```

外层 `requires_user_input` 表示等待人工处置；恢复要求平台维护者检查文件内容、
读取权限、确认状态及 DAG 校验结果，修复并验证为合法 ConfirmedPlan 后重新发起规划。
不携带上游阶段路由，不提示重做 TechnicalPlan、模板或 EntitySourceBinding；
普通回复不豁免正式基线检查。节点事件、最终摘要、公开快照及 `/health` 能力元数据
使用一致的独立错误身份。上游 prerequisite 缺项仍使用原有投影。

收尾补丁修改 `Backend/app/graph/nodes/tasks.py`、
`Backend/app/protocols/workflow/projection.py`、`definition.py`，更新原有 shell 门禁断言，
新增 `Backend/tests/test_confirmed_baseline_projection.py`，并同步本文和代码索引。

## 范围

没有新增产品 API、shell Task、菜单、路由、占位页、layout、provider 或模板修复职责。
`application_template_generation.py` 已有只读门禁足够，保持实现不变。
本任务不切换其他 Unit 的 replacement/append-only 规则，不实现 Planning/Pending/Confirmed
三文件分离、auth-guard writer、并发或重试重构。

## 文件

- 生产：`services/build_unit_skeleton.py`、`services/build_task_reuse.py`、
  `services/build_unit_compiler.py`、`services/build_task_planner.py`、
  `graph/nodes/tasks.py`、`agents/main/task_preparer_prompt.py`（均位于 `Backend/app`）。
- 新增测试：`Backend/tests/test_frontend_shell_prerequisite.py`。
- 更新测试夹具/旧断言：`test_prepare_build_tasks_guard.py`、
  `test_confirmed_build_task_plan_loader.py`、`test_dag_planning_baseline_gates.py`。
- 文档：本文件、`CODEBASE_INDEX.md`、`BUILD_TASK_REUSE.md`、`UNIT_GENERATION_REQUIREMENTS.md`。

## 验证

新测试覆盖 main/auth ready、模板 missing、历史三种执行状态、空历史首次规划、
shell 三字段与骨架复用、模型输入排除、非法候选拒绝、Page 架构边保留、
Task 依赖隔离、workspace revision 缺失、ready=false 且 errors 为空、
二次模板检查失效、非法正式计划阻断且不覆盖文件。

在 `Backend` 使用 `.venv/bin/python -m unittest` 执行：

```sh
# R-PLAN
.venv/bin/python -m unittest tests.test_build_task_planner tests.test_build_unit_skeleton tests.test_prepare_build_tasks_guard tests.test_build_dag_v3_contract tests.test_page_build_context_resolver
# R-TEMPLATE
.venv/bin/python -m unittest tests.test_application_template_generation tests.test_template_scaffold_injection tests.test_generation_workspace_paths
# 新增测试与直接前置、正式门禁回归
.venv/bin/python -m unittest tests.test_frontend_shell_prerequisite tests.test_confirmed_build_task_plan_loader tests.test_build_task_reuse tests.test_build_task_reuse_workspace tests.test_unit_generation_requirements tests.test_unit_generation_requirements_auth tests.test_dag_planning_baseline tests.test_dag_planning_baseline_gates
```

后端健康命令：`curl -sS http://127.0.0.1:8000/health`。
本任务没有前端代码修改，不要求 pnpm build 或 Electron UI 验证。

2026-09-04 验收结果：R-PLAN 116 项、R-TEMPLATE 28 项、新增及直接相关回归
79 项（其中本 Task 新增 10 项），合计 223 项全部通过。修改文件的 `py_compile`、
`git diff --check` 通过，后端 `/health` 返回 `status=ok`。
T2.4 无未解决事项；工作区并行发生的权限资源目录改动不属于本 Task，未修改或回退。

## 收尾补丁验证

R-PLAN / R-TEMPLATE 合计 144 项通过；以下新增及相关投影回归 37 项通过，
其中 `test_confirmed_baseline_projection.py` 新增 6 项，包含真实 Graph 与完整 AG-UI 流：

```sh
.venv/bin/python -m unittest tests.test_confirmed_baseline_projection tests.test_frontend_shell_prerequisite tests.test_workflow_projection
```

修改文件 `py_compile`、`git diff --check` 通过。实时 `/health` 返回 `ok`，并包含
`clarificationModes.confirmed_baseline_error` 元数据。没有前端代码改动，未运行前端构建/UI 验证。

额外运行下面命令时，`test_workflow_ag_ui` 中有两个本补丁之前已存在的失败：

```sh
.venv/bin/python -m unittest tests.test_confirmed_baseline_projection tests.test_frontend_shell_prerequisite tests.test_workflow_projection tests.test_workflow_ag_ui
```

- `WorkflowAgUiStreamTests.test_confirmation_artifact_is_limited_to_the_active_gate`
- `WorkflowAgUiStreamTests.test_unconfirmed_requirement_projects_draft_markdown_artifact`

两项均为需求 Markdown 工件投影返回 None；将 HEAD 版 `projection.py` 只读加载到内存，
使用修改前的 `_workflow_confirmation_artifact` 重跑这两项，失败均可复现。
它们不涉及 Confirmed baseline 投影，未扩大范围修复；收尾补丁相关测试均已通过。

## R-PERSIST 回归断言补齐

2026-09-05，T5.4 执行 R-PERSIST 时发现 `test_build_task_plan_recovery.py` 的应用范围
测试仍读取 T2.4 已移除的 `reusable_tasks_by_unit`。隔离 HEAD 副本可复现相同 KeyError，
属于 T2.4 契约变更后的测试同步遗漏。

该测试现改为 `test_application_scope_preserves_historical_shell_independent_of_execution_status`，
覆盖历史 shell Task 的 pending、failed、completed 三种状态：历史记录和执行状态均保留，
但不能证明模板前置能力，也不能成为新页面 Task 的执行依赖。继续检查 Unit 架构边、
历史交付物/验收字段、累计任务图有效性及原输入不变；其他 Unit 仍按既有合并规则验证。
本次仅调整测试及本文，不恢复旧字段、不修改生产行为、不提前接入完整 append-only 流程。

使用现有 `.venv/bin/python -m unittest` 复跑：

- R-PERSIST、R-PROGRESS 与 T5.1/T5.3/T5.4 状态机、文件持久化、Controller 测试：76 项通过。
- R-PLAN 与 `test_frontend_shell_prerequisite`、`test_build_task_reuse`、
  `test_build_task_reuse_workspace`：144 项通过。

合计 220 项全部通过，原 R-PERSIST 阻塞已消除；`git diff --check` 通过，后端 `/health`
返回 `status=ok`。没有前端改动，未运行前端构建/UI 验证。
