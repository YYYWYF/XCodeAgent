# 二次修改正式修订模板重试 Electron 实测（2026-10-08）

## 测试范围与结论

直接操作已运行的 DevAgent Studio Electron，未使用浏览器页面替代，未手工修改 Graph checkpoint、失败身份或正式确认状态。

工作区：`/Users/yifei/Coding/xcodeagent_test/ui-recovery-fix-20261006`。
正式修订：`chg_135eacb091fb49cd831fb055aadf6546`。
规划 thread：`10eeb51a-c84a-40e5-803a-2d2e1ded93bd`。
来源开发 thread：`b708de03-92e1-4954-aada-ccb026d0e675`。

本轮已验证该正式修订的模板失败、重复失败、成功重试和回到原开发会话。最终停在 DAG 确认门，没有点击“确认并进入 Build”。不代表所有二次修改节点均已完成失败/停服验收。

## 真实执行证据

| 阶段 | 执行及结果 |
| --- | --- |
| 地址未配置时点击底部重试 | `recovery-9450e94df1fc`，first/current node 均为 `template_reconcile`，failed；底部仍显示 Template Engine 地址未配置，重试入口保留。 |
| 临时接入真实本地引擎后重试 | `recovery-121324d7063d`，同一模板节点；引擎 `/v1/update` 返回 200，但真实项目启动校验因前端依赖安装失败而 failed。 |
| 准备测试依赖后再次重试 | `recovery-d0b0bf005f38`，first/current node 均为 `template_reconcile`，completed。 |
| 显式对账并完成原续接 | continuation 于 `2026-10-08T08:33:02.352107Z` 消费；同一修订进入 building；开发 Run `7192206c-182d-4102-bd83-338633c656b2` 从 `development_readiness_gate` 到 `prepare_build_tasks`，状态 awaiting_user。Electron 显示原开发会话前置产物更新回执、工作区扫描和任务计划待确认，旧底部错误消失。 |

技术规划在所有重试前后保持相同摘要，没有重新生成或重新确认：

- Markdown：`4dc15f9e4079810e568e6dc98cbe86546144aa880db3015cc9e713f32c9391f3`。
- 内部 JSON：`bc0103acd52c88d25ccc98a95b53e482279e0c0b5dd2d815db1d6dcac1083b07`。

运行日志：`/tmp/xcodeagent-electron-retry-20261008-backend.log`；Native Recovery 与 lineage 证据位于测试工作区 `.devagentstudio/recovery/execution-recovery.sqlite`。Electron AX 与实际页面观察见本轮工具记录。

## 实测发现并修正的问题

1. 模板运行校验异常来自 `workspace_bootstrap.models`，也会被模块名 model 误归为模型调用失败。已在通用模块判断前按模板领域异常类型分类。
2. 旧技术规划 lifecycle 覆盖真实模板运行节点，进度误显示“正在生成技术规划”。模板节点现在优先于旧阶段投影。
3. Native Recovery 的入场 lifecycle 覆盖终帧中节点新提交的 lifecycle，导致 continuation_ready 回退为 template_reconcile_failed，阻止原流程交接。规划终帧现保留节点最新快照，工作台生命周期控制保持原路径。
4. 模板启动遗留的预览错误会抢走当前规划重试。当前规划失败/恢复入口现在优先；显式重试发现当前节点已完成且原 continuation 已签发时，仅续接该事务。

## 测试环境准备

使用本地真实 Template Engine 服务包，临时监听 `127.0.0.1:18080`，仅在测试后端进程环境传入地址，不修改 `.env`。
测试工程原 pnpm-workspace.yaml 含未决 esbuild 占位配置；将 esbuild 设为允许构建并补单包 packages，使用 pnpm 9.15.9 完成 frozen 安装，真实 esbuild postinstall 成功。测试后端通过临时 PATH 使用 pnpm 9，未绕过模板 Validation 或项目启动 Ready 校验。

本轮验证使用既有正式规划与确认，不提交或推送主仓库。测试依赖配置保留在测试工程；临时引擎与后端地址在测试收尾恢复。

## 补丁与验证

生产补丁：`Backend/app/services/execution_failure_classifier.py`、`Backend/app/protocols/workflow/runtime.py`；`Frontend/src/renderer/src/service/{recoveryFailureMessage,applicationPlanningWorkflowState,applicationPlanningRuntime}.ts`、`Frontend/src/renderer/src/components/AiChatPanel/AiChatPanel.tsx`。
更新了对应 classifier、真实 Recovery journey、规划运行态、Runtime 与错误卡回归，以及 CODEBASE_INDEX。

通过：

- `pnpm test:execution-recovery`：38 项。
- `node --test-name-pattern='技术规划|当前规划中断|当前模板失败|模板重试' scripts/run-workflow-preview-tests.mjs`：5 项。
- `pnpm test:application-planning-runtime`：包括新增加的 completed 原续接、不重发模板请求用例。
- `pnpm test:connection-recovery`：83 项。
- Backend `.venv/bin/python -m unittest tests.test_technical_planning_recovery_journey tests.test_execution_failure_classifier`：10 项；真实 AG-UI 终帧检查节点最新 lifecycle。
- `pnpm build`、`git diff --check`、后端 `/health`（收尾恢复后 status=ok）。

扩展检查 `.venv/bin/python -m unittest tests.test_workflow_ag_ui tests.test_execution_recovery_protocol` 共 52 项，有 4 项原有失败：acceptance 两项启动进度、regenerate 旧 pending 清理，以及 workbench 首节点前 lifecycle 广播。在仅于内存中移除本次 planning 终帧条件修改后，同一套测试仍恰为这 4 项失败，0 errors；没有改写或回滚工作区文件。

此前完整 `pnpm test:workflow-preview` 的两项 API 门禁显示失败仍是独立问题。本轮没有全量注入各二次修改节点的故障，也没有继续执行 Build；保持原正式确认门。
