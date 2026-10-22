# 已完成页面/API 的修订变更分析

## 当前行为

正式修订确认上游产物、模板对账完成后，`continue_revision_build` 沿原 AG-UI Workflow 进入 `api_design_readiness_gate` 的只分析分支。

- 根据当前正式目录及 Lifecycle 首次完成事实，仅分析已完成页面/API；未完成、开发中、新增目标不进入分析。
- `completed_development_analysis.py` 复用 DAG 的 `resolve_reuse_facts`、任务输入新鲜度及 `resolve_generation_requirements`，只计算职责覆盖缺口，不调用模型、不创建 PlanningRun、不写 Pending/Formal DAG、不执行 Build。
- 页面自身合同变化先确定性标记；页面依赖的 API 只作为输入，不能使未完成 API 成为任务生成目标。
- `changeStatus=changed` 显示“有变更”；输入不完整或读取失败显示 `unavailable`（待检查），不伪装成无变更。
- 分析结果与首次完成时间、Run、Thread、分支归属分开保存。重复检查相同结果不增加 Lifecycle revision。
- 收口沿现有 `development_target_selection` 释放本轮修订与执行占用，显示“你想开发哪个页面或 API？”卡片；点击后仍走原逐页面/API开发路径。
- 有变更或待检查目标不计入当前完成比例，阻止进入测试；原目标经过 Build 与单测门后清除当前变更状态，首次完成证据保留。

主要文件：后端 `domain/development_artifacts.py`、`services/completed_development_analysis.py`、`services/development_artifacts.py`、`graph/nodes/api_design.py`、`graph/state.py`、`protocols/workflow/request.py`、`protocols/application_lifecycle.py`；前端 `developmentArtifacts.ts`、Workflow typings、QuickTaskGuide、ApplicationOutline 状态圆点、NextDevelopmentGuide 及 AiChatPanel 选择卡。

## 2026-10-21 验证

- Backend `.venv/bin/python -m unittest tests.test_completed_development_analysis tests.test_development_artifacts tests.test_workflow_request tests.test_development_target_selection tests.test_task_input_freshness tests.test_unit_generation_requirements tests.test_revision_continuation_handoff tests.test_api_design_node tests.test_application_api_design_continuation tests.test_application_lifecycle`：173 项通过。
- Frontend `pnpm test:development-artifacts`：39 项通过；`pnpm build`：类型检查与 Electron/Vite 构建通过。
- 改动 Python 文件的 `py_compile`、`git diff --check`、`curl -sS http://127.0.0.1:8000/health` 通过；Health 发布新增变更状态元数据。
- 原开发状态测试有两处陈旧断言，把缺失 Build/单测证据预期为 failed；已按现有节点实际行为改为检查 in_progress 及回退 build/unit_test，未改对应节点业务逻辑。

隔离工作区：`/Users/yifei/Coding/xcodeagent_test/completed-change-analysis-g67wq6x1`。复制已有正式产物和真实首次完成证据，建立测试修订、签发正式 continuation，调用生产 `build_workflow_ag_ui_stream` 与真实 Graph（内存 Checkpointer），没有替换分析函数或调用任务模型。AG-UI 结果为检查 1 项、1 项有变更，仅执行 `api_design_readiness_gate` 后完成；Lifecycle 执行、修订与资源占用全部释放。

该真实运行结果保存到隔离工作区的新本地会话，在已经运行的 Electron 冷读取并切到开发阶段。实际截图检查浅色/深色下的选择卡、“有变更”条目、目录圆点及禁用的测试阶段；主题恢复浅色。未点击目标启动正式开发，未用浏览器页面替代 Electron。

这次验证没有重新调用模型生成上游正式产物，也没有执行选择卡后的完整开发过程；对应输入、状态清除与原逐目标入口由定向测试覆盖。现有业务工作区未修改。
