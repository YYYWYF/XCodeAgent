# 显式节点回退 Electron 端到端验证

日期：2026-11-05。macOS、已运行的 Electron、当前开发后端，主模型和 UI 模型均为 `deepseek-v4.1-flash`。所有测试输入、发送、跳过和确认均通过 Electron 界面操作，产物由真实模型生成；没有使用浏览器替代 Electron，没有写盘伪造确认或 checkpoint。

独立应用：**节点回退独立验收1105**。

工作区：`/Users/yifei/Coding/xcodeagent_test/explicit-node-electron-20261105`。

## 环境与准备

- 原 8000 后端进程未开启热重载，能力元数据仍是旧的讨论目标语义。确认当前 Electron 无正在生成的任务后，正常重启后端，核验 `/health` 为 `ok`，两类入口的 `targetStartsExecution=true`。
- 使用 Electron 新建单页纯静态公告应用，唯一页面 `/page/notice`、`pageId=notice`，标题“节点回退验收”，初始正文包含 `E2E-INITIAL-1105`，无实体、API、数据库、登录、权限或业务交互。
- 真实生成需求/产品文档后手动联合确认，明确跳过初始 UI，再手动进入计划阶段，真实生成初始 TechnicalPlan。初始计划停在“确认保存”，没有确认 TechnicalPlan，也没有触发模板生成。

## 结果

| 场景 | 实际结果 | 确认门 |
| --- | --- | --- |
| PLAN 中不带 `@` 提问“这个应用有几个页面？现在需要我确认什么？” | 正确回答只有 1 个页面、当前技术规划待确认；生命周期和全部 8 份规划文件的 SHA256 均未改变 | 原技术确认按钮仍可用 |
| `@技术规划` 在架构说明中加入 `E2E-TECH-1105` | 同一 PLAN thread 中创建新 Run，真实生成计划，正式请求正文进入生成器，产物包含标记 | `awaiting_technical_plan_confirmation/awaiting_user`；旧卡失效，新卡等待确认 |
| PLAN 中 `@UI设计` 生成公告设计稿 | 交接独立 DESIGN thread。首次实测发现无旧稿时只返回空骨架；修复后在同项目重新发送，真实生成并在 Electron 内预览，TSX 含 `E2E-UI-1105` | 整体 `ui_designs.confirmation_status=pending_user_confirmation`，lifecycle 停在 UI 确认；手动点击“下一步”后仍须点击“进入计划阶段” |
| 再进入 PLAN 后 `@需求设计` 把公告正文改为 `E2E-REQ-1105` | 再次交接新的 DESIGN thread，真实生成 RequirementSpec 和 ProductPlan 草稿，两份草稿都包含新正文；原正式文件仍保留旧正文 | `awaiting_requirement_document_confirmation/awaiting_user`，“确认并继续规划”可用，未自动进入 UI 或 PLAN |
| DESIGN 回退后不带 `@` 再提问 | 正常返回只读回复，生命周期、Run/Thread 和全部规划文件摘要不变，上一条 `@需求设计` 没有污染下一轮路由 | 原需求/产品联合确认门恢复可用；回答内容准确性见下述限制 |

首次 DESIGN thread：`f82c21a8-ce0e-4655-9a43-d01ac3840da2`。

初始 PLAN thread：`ba33c608-1935-4429-8d4a-cdfdcca67cbc`；技术修改 Run：`a075cda1-08e6-47f4-be4a-658a05f76335`。

UI 回退 DESIGN thread：`d50836ef-b175-4468-ad1e-8890c49bf4ea`。

UI 手动确认后进入的新 PLAN thread：`1ea18197-551d-4423-aff9-77786e84c7bc`。

需求回退 DESIGN thread：`a43233b5-228c-4515-9835-70608019b5c8`。以上阶段交接始终保留同一 iterationId：`f82c21a8-ce0e-4655-9a43-d01ac3840da2`。

## 实测发现与修复

初始 UI 已跳过时，`prepare_ui_revision_state` 把显式执行正文与 `ui_design_action` 一并清掉，UI 节点只创建未生成的页面骨架；已有骨架但没有源码时，多页调整又会跳过该页。因此仅“能返回 UI”不足以证明正文已执行。

本次补齐：

- 显式 UI 回退在无旧稿或页面集合变化时保留正文及生成动作。
- UI 节点准备骨架后执行该动作；没有源码时调用既有首次生成器并传入视觉指令，有源码时仍沿原增量调整。
- ProductPlan 继续作为唯一业务事实来源；生成完成仍返回原整体 UI 确认门，不自动进入下游。

改动位于 `Backend/app/graph/application_planning_revision.py`、`Backend/app/graph/nodes/ui_confirmation.py`、`Backend/app/services/ui_design_generator.py`。补充生成指令传递、无旧稿首次执行和确认门回归，并将已有 UI 模型测试替身对齐当前 `stream` 调用合同。

## 自动化与健康检查

在 `Backend` 执行：

```sh
.venv/bin/python -m unittest \
  tests.test_application_design_conversation \
  tests.test_technical_planning_conversation \
  tests.test_ui_design_generator tests.test_ui_design_adjust_base
```

结果：**71 项通过**。另通过三个产品 Python 文件的 `py_compile`、`git diff --check`、后端 `/health`。

本轮未修改前端代码，因此未重复运行此前已经通过的 `pnpm build` 和前端专项测试。Electron 测试使用当前开发前端。

## 证据与限制

哈希、生命周期、最近 Durable Execution 和标记核对保存于 `artifacts/explicit-node-electron-20261105/`；`04-after-explicit-ui.json` 保留首次 UI 空骨架问题现场，`05-after-ui-fixed.json` 与 `summary.json` 记录修复后状态和源码标记，`06/07` 记录需求回退与随后问答的摘要一致性。

主后端日志：`/tmp/xcodeagent-explicit-target-electron-backend-20261105.log`。测试结束时后端已加载本轮修复。

需求侧额外提问“现在公告正文是什么？”时，模型错误回答正文尚未填写，但右侧文档及两份草稿已有 `E2E-REQ-1105`。当前 `_requirement_summary`/`_product_summary` 只投影有限身份与名称，未传入该正文；这条回复的**内容准确性不计通过**。它的路由、零产物变更及确认门保留已通过。本次没有扩展修改问答上下文投影。

独立项目最终停在需求/产品联合确认门（lifecycle revision 26），没有正在运行的生成任务，未确认最后一轮需求，没有启动模板、开发或发布。未进行 Windows、完整开发链、节点职责不匹配拒绝或视觉主题的专项验收。验收时保留原有暂存内容；提交与推送状态以实际 Git 记录为准。
