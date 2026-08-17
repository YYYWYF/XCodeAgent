# `integration_test` 节点详细分析

## 1. 节点定位

`integration_test` 是主 Graph 中的 Testing Subgraph 包装节点。当前内部拓扑为：

```text
actual_project_checks
  -> main_quality_gate
  -> repair_planning
```

节点只承担三类职责：

1. 执行真实前端和 Maven 工程命令并保存结构化证据；
2. 根据命令结果执行确定性质量门禁并生成返修请求；
3. 门禁失败时调用只读 RepairPlanner，生成受限 SmallTask 修复任务。

API 契约一致性由 ProjectPlan 确认和 `prepare_build_tasks` 前置门禁负责，Testing Subgraph 不重复校验。节点没有 Test Agent，也不探测 Python 工程或执行 pytest。

## 2. 主图入口与出口

### 2.1 入口

- `build_summary.status == completed` 时从 `build` 进入；
- `small_task_repair` 完成一轮真实局部修复后回到本节点复测；
- 调试或恢复请求可以通过 `resume_from == integration_test` 直接进入；
- 自由对话代码修改复用同一个节点，但设置 `integration_repair_enabled = False`，失败证据交给独立的 `direct_modification_repair`。

每次进入会清空本轮 `test_results`、`test_events`、`code_changes`、`code_change_sets` 和内部 `timeline`，保证结果来自本轮真实命令；修复预算和 SmallTask 变更历史继续继承。

### 2.2 出口

| 条件 | `integration_next_action` | 主图行为 |
| --- | --- | --- |
| 所有检查通过 | `launch_project` | 启动项目并进入验收边界 |
| 有受限修复任务 | `small_task_repair` | 执行局部修复，成功后重新测试 |
| 修复需要扩大范围 | `await_user_input` | 暂停并等待范围确认 |
| 证据不足、用户拒绝或预算耗尽 | `handle_failure` | 进入失败终态 |

## 3. `actual_project_checks`

源码：`Backend/app/services/integration_test_runner.py`。

### 3.1 前端发现与检查

前端 `package.json` 按以下顺序发现：

1. `frontend/package.json`
2. `Frontend/package.json`
3. `app/frontend/package.json`
4. 根目录 `package.json`

包管理器按 lockfile 选择 `pnpm` 或 `yarn`，无对应 lockfile 时默认 `pnpm`。当前实际检查为：

| 检查 ID | required | 行为 |
| --- | ---: | --- |
| `frontend_install` | true | `<package-manager> install` |
| `frontend_typecheck` | false | 存在 `scripts.tsc` 时执行 `<package-manager> run tsc` |
| `frontend_build` | true | 执行 `<package-manager> run build` |

未找到前端 `package.json` 时生成 required failure；可选 `tsc` script 缺失时生成 passed/skipped 结果。当前节点不执行 lint、前端单元测试或 E2E。

### 3.2 后端发现与检查

`.xcodeagent/application.json` 的 `datasource.type == static` 时完全跳过后端检查。其他应用只按以下顺序寻找 Maven 工程：

1. 根目录 `pom.xml`
2. `backend/pom.xml`
3. `Backend/pom.xml`

发现 Maven 工程后优先使用当前平台的 `mvnw` 或 `mvnw.cmd`，否则使用全局 `mvn`，并执行：

```text
<maven-command> clean install
```

未找到 Maven 工程时生成可选、passed/skipped 的 `backend_build`。`pyproject.toml`、`pytest.ini`、`setup.cfg` 或 Python 测试目录不会改变判断，也不会触发解释器探测或 `python -m pytest`。

### 3.3 命令证据

真实命令默认最多运行 180 秒。每项结果包含：

```text
id / name / layer / language
passed / skipped / required
command / evidence / failure_category
execution:
  tool / argv / cwd / returncode / timed_out / error
  started_at / finished_at
  stdout_log / stderr_log
  stdout_log_virtual / stderr_log_virtual
  stdout_tail / stderr_tail
```

完整 stdout/stderr 写入 `.xcodeagent/runtime/tests/<check-id>/`，Graph State 只保留有界尾部和稳定日志引用。超时、非零退出码、缺失必需命令和 `OSError` 都转换为结构化失败，不用模型推断结果。

检查开始和终止状态通过 `integration_test.checks` custom stream 增量发送。事件只包含稳定 ID、名称、状态、required 和简短 evidence；完整日志不进入 AG-UI payload。

## 4. `main_quality_gate`

源码：`Backend/app/services/test_validation.py`。

质量门禁完全确定性执行：

```text
quality_gate_passed = all(result["passed"] for result in test_results)
needs_revision = any(not result["passed"] for result in test_results)
```

每个失败检查会转换为一个 `revision_requests[]` 项，包含：

- 失败 check 的完整结构；
- `failure_category`；
- 命令、cwd、退出码和超时状态；
- stdout/stderr 日志引用；
- 建议 owner 和待处理状态。

结果写入 `.xcodeagent/reports/test-report.json`。报告包含版本、生成时间、checks、summary、revision requests 和 `deterministic-quality-gate` 元数据；不包含 Test Agent 的 `agent_note` 或 `reviewed_by`。

`main_quality_gate` 是历史节点名，不代表 Main DeepAgent，也不会调用任何模型。

## 5. `repair_planning`

质量门禁通过时，本节点直接跳过 RepairPlanner 并返回 `launch_project`。只有门禁失败且 `integration_repair_enabled` 未关闭时，才调用只读 RepairPlanner。

RepairPlanner 接收：

- TestReport 和 revision requests；
- 当前 BuildTaskPlan 和 execution scope；
- 当前 build execution slice 中的精确授权路径；
- 当前修复轮次与所选用户技能。

RepairPlanner 只能选择：

- `repair`：生成受限 SmallTask；
- `requires_user_confirmation`：扩大范围或产品决策需要确认；
- `terminal_failure`：证据不足或不可自动处理。

最终 RepairTask 的 `allowed_paths`、`target_files`、`change_scope` 和 `unit_id` 由确定性服务根据当前执行切片编译，不能由模型扩大。修复计划写入 `.xcodeagent` 任务产物目录。

默认最多执行 3 轮真实修复。只有 SmallTask 实际派发并完成一轮时才增加 `repair_iteration`；测试、规划和等待确认不消耗预算。

## 6. 状态和兼容性

### 6.1 核心输入

| 字段 | 用途 |
| --- | --- |
| `workspace` / `workspace_path` / `project_id` | 解析命令工作区和产物根目录 |
| `integration_repair_enabled` | 控制失败后是否在本子图调用 RepairPlanner |
| `repair_iteration` / `max_repair_iterations` | 限制修复闭环 |
| `repair_task_plan` / `request` | 恢复范围确认 |
| `build_task_plan` / `build_execution_scope` / `build_execution_slice` | 提供修复上下文和精确授权范围 |
| `selected_skill_names` | 失败时注入 RepairPlanner 的用户技能快照 |
| `small_task_code_change_sets` | 合并历史修复变更审计 |

`project_plan`、`build_results` 和 `build_summary.failed/pending` 不参与本节点门禁。API 契约已经在进入 Build 前校验。

### 6.2 核心输出

- `test_results` / `test_events`
- `test_report` / `test_report_path`
- `quality_gate_passed` / `needs_revision`
- `revision_requests`
- `repair_task_plan` / `repair_task_plan_path` / `repair_tasks`
- `small_task_tasks` / `small_task_results`
- `repair_iteration` / `max_repair_iterations`
- `integration_next_action` / `small_task_route`
- `code_changes` / `code_change_sets`

旧 checkpoint 可能包含 `integration_contract_check_enabled` 或 `test_agent_review`。`ProjectState` 为 `total=False` 的字典状态，新实现会忽略这些历史键，不要求迁移；新运行不再产生它们。

## 7. 错误边界

### 7.1 业务失败

命令失败、缺少必需工具、超时或非零退出码都形成 `passed = false` 的 TestResult，节点仍会生成 TestReport 和修复决策。

### 7.2 技术异常

日志目录不可写、应用配置非法、报告持久化失败或 RepairPlanner 技术异常仍会向上抛出，由 Workflow AG-UI runtime 转换为 `workflow.run.failed`。成功路径没有模型调用，因此模型服务不可用不会阻止已经通过的真实命令形成质量门禁结果。

## 8. 架构边界

- 对应 learn-coding-agent 的“收集事实—执行—验证”循环：真实命令是验证事实源；
- 对应 OpenCode 的角色分离：只在需要规划修复时调用只读 Agent；
- 对应 Deep Agents 的按需能力：成功路径不创建无必要的审阅 Agent；
- Graph State 只保存紧凑结构化结果和日志引用，避免把完整工具输出或仓库内容放入 128k 上下文。

## 9. 回归测试重点

- 成功子图事件顺序为工程检查、质量门禁、修复规划跳过，且 RepairPlanner 不被调用；
- API contract 服务仍在 ProjectPlan 与 `prepare_build_tasks` 测试中受到保护，但 Testing 不产生 `api_contract` check；
- Python 项目标记不会触发任何后端命令；
- Maven、Static、包管理器缺失、命令超时和日志引用保持原行为；
- 失败命令仍能生成 revision request、受限 RepairTask 和复测路由；
- Agent registry、技能和 AGENTS.md memory 只覆盖剩余六个 Deep Agent。
