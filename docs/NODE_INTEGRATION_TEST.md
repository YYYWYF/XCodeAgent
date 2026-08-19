# `integration_test` 节点详细分析

## 1. 节点定位

`integration_test` 是主 Graph 中的 Testing Subgraph 包装节点。当前内部拓扑为：

```text
collect_unit_test_targets
  -> build_project_checks
  -> unit_test_confirmation
  -> (skip_unit_tests | generate_or_update_unit_tests -> validate_generated_unit_tests -> actual_project_checks)
  -> frontend_performance_confirmation
  -> (skip_frontend_performance | frontend_performance_test)
  -> main_quality_gate
  -> repair_planning
```

节点只承担三类职责：

1. 执行真实前端和 Maven 工程命令并保存结构化证据；
2. 根据命令结果执行确定性质量门禁并生成返修请求；
3. 门禁失败时调用只读 RepairPlanner，生成受限 SmallTask 修复任务。

正式 Workflow 在真实差异清空前保存本轮源码和变更集，收集业务源码后尽力生成/同步单元测试。快速修改流程显式关闭该阶段。测试生成 Agent 只能写 `frontend/tests/*.test.ts(x)` 与 `backend/src/test/java/**/*.java`；生成文件总数最多 5 个，映射缓存位于工作区 `.xcodeagent/cache/unit-test-mappings.json`。

API 契约一致性由 ProjectPlan 确认和 `prepare_build_tasks` 前置门禁负责，Testing Subgraph 不重复校验。测试生成失败但没有产生测试文件时只保留 warning 并以 `passed/skipped` 放行；已经存在或已经生成的对应测试必须执行，测试编译、用例和业务代码失败仍创建 revision request。不探测 Python 工程或执行 pytest。

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
| `frontend_unit_tests` | 按对应测试文件 | 有 `frontend/tests/*.test.ts(x)` 时优先执行 `test:unit`，否则执行 `test`；无对应文件则 passed/skipped |
| `frontend_test_generation` / `backend_test_generation` | 按目标 | 生成/校验阶段按受影响端记录；没有测试文件时 passed/skipped |
| `frontend_performance` | false（advisory） | 单测确认后由 `frontend_performance_runner` 启动用户 `frontend` 工程并对解析出的 `preview_url` 执行 LHCI；用户可跳过，失败不阻断 |

未找到前端 `package.json` 时生成 required failure；可选 `tsc` script 缺失时生成 passed/skipped 结果。测试文件不存在时不调用 Jest，避免当前 Jest “No tests found”造成假失败；E2E 不执行。

### 3.2 后端发现与检查

`.xcodeagent/application.json` 的 `datasource.type == static` 时完全跳过后端检查。其他应用只按以下顺序寻找 Maven 工程：

1. 根目录 `pom.xml`
2. `backend/pom.xml`
3. `Backend/pom.xml`

发现 Maven 工程后优先使用当前平台的 `mvnw` 或 `mvnw.cmd`，否则使用全局 `mvn`，并拆分执行：

```text
<maven-command> -B -Dmaven.test.skip=true clean install
<maven-command> -B -DfailIfNoTests=true test  # 仅存在对应 *Test.java 且构建成功时
```

未找到 Maven 工程时生成可选、passed/skipped 的 `backend_build` 和 `backend_unit_tests`。没有对应 `src/test/java/**/*Test.java` 时不调用 Maven test。`pyproject.toml`、`pytest.ini`、`setup.cfg` 或 Python 测试目录不会改变判断，也不会触发解释器探测或 `python -m pytest`。

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

检查开始和终止状态通过 `integration_test.checks` custom stream 增量发送。事件包含稳定 ID、名称、状态、required、advisory、简短 evidence，以及 `frontend_performance` 的得分/指标/报告路径；完整日志不进入 AG-UI payload。

### 3.4 前端性能测试（advisory）

单元测试阶段结束后先进入 `frontend_performance_confirmation`，通过 AG-UI 展示与单元测试同款的“是否跳过前端性能测试”按钮；选择继续执行后才启动 Lighthouse。

执行器 `Backend/app/services/frontend_performance_runner.py` 复用 `launch_frontend_project(root, skip_install=True)` 启动用户工程 `frontend/` 下的 `dev|start` 脚本，并解析日志拿到真实 `preview_url`；随后在 `.xcodeagent/runtime/tests/frontend_performance/` 下运行：

```text
npx --yes --package @lhci/cli@0.7.2 lhci autorun --config=<绝对路径>
```

LHCI 配置固定使用 `collect.url=[preview_url]`、`numberOfRuns=1`、`upload.target=filesystem`，并保持 Lighthouse 7.3 可用的移动端模拟采集链路，同时把网络/CPU 限速调至接近本地无限制（`simulate` + 高吞吐 + 1x CPU），避免 dev server 未打包模块在 1.6Mbps 模拟限速下被放大成数十秒假指标。每次运行前会清理上一次的 LHCI 产物，运行失败不会复用旧报告。不审计静态目录、不重复启动服务器。测试结束后只有本次由性能测试启动的服务器会被停止（复用的既有预览不停止）。

检查 ID 为 `frontend_performance`，结果为 `required=False`、`blocking=False`、`advisory=True`，携带 `performance_scores`（0–100）、`performance_metrics`（FCP/LCP/TBT/CLS/SI）和 `report_path`。无论得分高低或执行失败，该检查都不阻断质量门禁、不进入 RepairPlanner；前端通过指标卡展示得分并提供打开完整 HTML 报告的入口。

## 4. `main_quality_gate`

源码：`Backend/app/services/test_validation.py`。

质量门禁完全确定性执行，但只统计 `blocking` 的失败项（默认 `True`；`frontend_performance` 显式 `False`）：

```text
quality_gate_passed = all(result["passed"] or not result.get("blocking", True) for result in test_results)
needs_revision = any(not result["passed"] and result.get("blocking", True) for result in test_results)
```

每个阻断失败检查会转换为一个 `revision_requests[]` 项，包含：

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
| `unit_test_generation_enabled` | 正式 Workflow 默认开启；快速修改流程关闭 |
| `frontend_performance_test_enabled` | 正式 Workflow 默认开启；快速修改流程关闭 |
| `frontend_performance_decision` | 用户对前端性能测试的 skip/run 确认，恢复时由请求协议写回 |
| `test_generation_input_code_changes` / `test_generation_input_code_change_sets` | 清空子图差异状态前固化的真实业务差异 |
| `unit_test_generation_context` / `unit_test_generation` | 受影响源码、生成状态、测试文件、warning 和校验结果 |
| `unit_test_affected_layers` | 控制只执行本次受影响端的单元测试 |
| `unit_test_mapping_path` / `unit_test_code_change_sets` / `unit_test_generation_code_change_sets` | 映射缓存路径和实际测试文件变更集；后者是生成阶段的稳定别名 |

`project_plan`、`build_results` 和 `build_summary.failed/pending` 不参与本节点门禁。API 契约已经在进入 Build 前校验。

### 6.2 核心输出

- `test_results` / `test_events`
- `frontend_performance`（advisory 检查：`blocking=False`，含得分、指标、报告路径）
- `unit_test_generation` / `unit_test_generation_context` / `unit_test_mapping_path`
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

命令失败、缺少必需工具、超时或非零退出码都形成 `passed = false` 的 TestResult；只有 `blocking=True` 的失败项会生成修复决策，`frontend_performance` 等 advisory 失败只保留报告与证据。

### 7.2 技术异常

日志目录不可写、应用配置非法、报告持久化失败或 RepairPlanner 技术异常仍会向上抛出，由 Workflow AG-UI runtime 转换为 `workflow.run.failed`。成功路径没有模型调用，因此模型服务不可用不会阻止已经通过的真实命令形成质量门禁结果。

## 8. 架构边界

- 对应 learn-coding-agent 的“收集事实—执行—验证”循环：真实命令是验证事实源；
- 对应 OpenCode 的角色分离：测试生成 Agent 只写测试目录，RepairPlanner 仍只读且只在失败时调用；
- 对应 Deep Agents 的按需能力：没有业务目标时不调用生成 Agent，生成异常且没有测试文件时保持可恢复放行；
- Graph State 只保存紧凑结构化结果和日志引用，避免把完整工具输出或仓库内容放入 128k 上下文。

## 9. 回归测试重点

- 成功子图事件顺序包含目标收集、测试生成/校验、工程检查、质量门禁和修复规划跳过，且 RepairPlanner 不被调用；
- 单元测试完成后必须进入前端性能确认；`run/skip/未回答` 分别路由到执行、跳过和暂停恢复，快速修改与前置条件缺失自动跳过；
- 性能测试使用 `launch_frontend_project(skip_install=True)` 解析真实 `preview_url` 执行 LHCI，报告写入 `.xcodeagent/runtime/tests/frontend_performance/`，复用中的预览服务不会被停止；
- `frontend_performance` 失败不产生 revision request、不阻断质量门禁，但 summary 仍计入；
- API contract 服务仍在 ProjectPlan 与 `prepare_build_tasks` 测试中受到保护，但 Testing 不产生 `api_contract` check；
- Python 项目标记不会触发任何后端命令；
- Maven 两段命令、零测试跳过、Static、包管理器缺失、命令超时和日志引用保持原行为；
- 前端-only、后端-only、混合变更与 CSS-only 目标筛选、最多 5 个测试文件、映射缓存命中和快速修改关闭生成均有回归覆盖；
- 失败命令仍能生成 revision request、受限 RepairTask 和复测路由；
- Agent registry 额外注册只写测试目录的 TestGeneration Agent；技能和 AGENTS.md memory 仍以只读挂载提供。
