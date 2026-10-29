# 1008 智能体流程适配合入记录

完成时间：2026-10-29 15:16（北京时间，代码及自动化验证完成；本地提交见 Git 记录）。

来源：`origin/dev_agent_integration_1008@12d736f0` 中 xuemingfeng 的 `6327e56c`、`04fc315e`、`d5511e36`、`d7b6eca1`、`12d736f0`。接收基线：`dev_agent_integration_1028@9ec24a01`。两条远端引用在核对时已通过 `git ls-remote` 验证。

## 功能对照与实现

| 原功能 | 本次处理与当前代码入口 |
| --- | --- |
| 需求识别、适配建议、接受或拒绝后不重复询问 | 恢复 `requirements_analyzer.py` 和 `requirement_spec.py` 的 `agent_requirements`、页面 ID 同步、确认校验、概览编辑与 Markdown 同步；不改变应用配置权威 |
| 产品智能体能力、页面操作、独立页面或浮窗载体 | ProductPlan 使用当前 `product-plan.v8`；`product_plan.py` 恢复能力、入口、交互状态与验收事实；前后端 schema 元数据同步更新，不添加历史读取 |
| 候选浮窗启停 | `agent_surface_selection.py` 与 `agentSurfaceSelection.ts` 沿原应用规划 AG-UI 端点保存待确认草稿；递归排序 JSON 摘要进行 CAS 校验；保存不等于联合确认 |
| 技术规划七段配置与审阅 | 复用 1028 `runtime_agent_contracts.py`，通过 `runtime_agent_settings.py` 校验已启用产品入口与技术合同的对应关系；技术规划 Markdown/结构化阅读面板展示七模块 |
| 同级智能体产物、详情与开发入口 | Electron `agentPlanningArtifactProjection.ts` 只读投影；大纲、快捷任务、详情、完成统计与下一步任务纳入业务 Agent；`src/shared/businessAgent.ts` 共享只读合同 |
| 独立智能体开发 | Agent 目标贯通会话持久化、会话身份比较、AG-UI 请求、资源锁、生命周期、Build Context、正式职责、调度切片与结束释放；只开发所选 Agent 和 Tool 前置依赖，不扩大到绑定页面或整应用 |
| Prompt/Temperature 正式修订 | `agent_settings_revision*.py` 复用现有 Formal Revision 和通用 Revision Draft；严格请求、正式文件与 Contract 哈希、字段 Diff、明确确认/放弃；用户编辑过的 Markdown 先同步再确认；保留隐藏 Prompt 事实，定向使旧计划及已完成 Agent 状态失效 |
| 固定 UI 模板与 Mock Build | 恢复内置设计组件、生成应用固定资产、模板 manifest、设计编译别名、构建运行包和打包资源；`agent_ui_build_contract.py` 按当前 Runtime 合同派生 Mock 输入；任务候选不能覆盖平台注入的 `agent_ui` |
| Mock 验证与交付边界 | `frontend_agent_ui.py` 接入确定性业务验收；构建完成后进入可见的真实 Runtime 集成待办门禁，不把 Mock 预览作为真实会话、Launch 或 Acceptance 完成证据 |
| Plan Control 会话隔离、Runtime DAG/启动日志 | 当前分支已有对应实现，沿用现状；没有因为旧文件名不同而重复迁入 |

所有新增产品动作继续通过 `@ag-ui/client` / `@ag-ui/core` 与既有 `/application-page-planning/run` 或 `/workflow/run`；没有新增 REST 产品入口、自制 SSE 或第二套顶层 Workflow。

## 明确不恢复的旧实现

- 不复制旧 Java Gateway 派生合同、网关专属跳转门禁或旧 Git 模板初始化。当前采用显式 Direct Runtime 服务图、Template Engine、内置数据库及当前正式确认链。
- 不复制旧 Graph、按 Entity/SQL 拆任务的规则、历史文件读取或恢复兼容分支。
- `prototype/` 仅为交互原型，未将原型中的模拟知识库、长期记忆、专属评测等视为已交付生产功能。
- 真实 Runtime 模板、会话身份/凭据和生成应用端到端联调仍受原 1028 能力边界约束；本次没有声称这些链路已被真实运行验收。

## 验证记录

- 后端定向回归共 263 项：261 项通过，2 项现有失败；两项均在 `HEAD` 原始源码临时副本中单独复现相同错误：
  - `tests.test_execution_resource_locks.ExecutionResourceLockTests.test_frontend_performance_confirmation_can_resume_in_test_thread`：测试未写 ProductPlan，现有测试准入门禁拒绝读取。
  - `tests.test_execution_resource_locks.ExecutionResourceLockTests.test_api_design_completion_keeps_execution_running`：现有断言预期 `api_design_readiness_gate`，基线实际为 `api_design`。
- 回归模块：`test_agent_integration_1008`、`test_agent_requirement_spec`、`test_agent_product_plan`、`test_agent_surface_selection`、`test_frontend_agent_ui_scaffold`、`test_ui_design_agent_template`、`test_product_technical_planning`、`test_api_design_node`、`test_development_artifacts`、`test_ui_design_generator`、`test_ui_design_manifest`、`test_business_acceptance`、`test_business_acceptance_verifier`、`test_build_scheduler`、`test_execution_resource_locks`、`test_workspace_run_lease`。
- `pnpm test:development-artifacts`：47 项通过，包括新 Agent 投影、独立任务、大纲选择、七模块详情、产品只读开关、完成统计和设计编译别名。
- `pnpm build`：通过，包含 Node/Web TypeScript 检查与 Electron/Vite 构建。
- `pnpm exec node scripts/build-design-runtime.mjs`：通过；生成运行包由脚本清理行尾空白。ESLint 排除该第三方生成包，仍检查对应源码。
- 全量源码 ESLint：150 个错误；对同一批文件的 `HEAD` 原始源码复核也是 150 个，按文件、规则和消息比较，新增错误为 0。未进行无关 lint 清理。
- 本次新增或修改 Python 文件的 `py_compile`：通过；`git diff --check`（不含用户原有锁文件变更）：通过。
- `curl -sS http://127.0.0.1:8000/health`：`status=ok`，运行态已发布 `agentSettingsRevision` 与 `product-plan.v8` 元数据。
- 已检查现有 Electron 窗口；收尾时可访问窗口为 Electron 默认启动页，未在该窗口确认用户应用或改写其产物。新增交互的完整 Electron 明暗主题实测、真实模型、模板服务与生成应用会话联调未完成，不能用 SSR 或构建结果替代。

本次只适配本地接收分支，不推送或发布。开始时已有 `Frontend/pnpm-lock.yaml` 未提交修改，未纳入本次合入。
