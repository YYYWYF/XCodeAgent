# 测试阶段兜底重启验收（2026-10-15）

结论：测试检查或局部修复失败后，兜底统一从 `integration_test` 重新开始；不重新派发旧 SmallTask。显式失败重试清空本轮任务、确认交接和检查缓存，并重置修复次数；暂停续接和内部自动修复循环保持原规则。

## 实现边界

- `domain/workflow_recovery_boundary.py` 集中定义失败恢复入口，解析、事务和 SQLite claim 校验共用；真实失败节点保留。claim 还校验真实失败位置未变化，不能因为两个节点都映射到测试入口而接受漂移。
- 必须存在同 Run 的测试入口 checkpoint；缺少时拒绝恢复，不退回独立 SmallTask 重试。
- `integration_test_reentry.py` 只在首次、经验证的 FAILURE_RETRY / BUSINESS_RETRY 中重开预算，保留已落盘代码及 Diff 归属。正常循环和 INTERRUPTED_CONTINUE 不补充预算。
- 主图先合并重入初始化，再合并真实节点结果；初始化不会覆盖新报告，新状态也不会遗漏在 checkpoint 外。
- 前端只说明服务端签发的恢复入口，不修改动作 ID、节点或预算。

## Electron 实测

使用当前正在运行的源码 Electron（DevAgent Studio，`localhost:5173/`），通过原生界面点击底部重试；没有使用浏览器页面代替 Electron。独立副本位于 `/Users/yifei/Coding/xcodeagent_test/test-stage-restart-20261015`，原项目与原历史未改动。

失败准备采用受控注入：在独立 Python 进程调用生产主图，仅替换初次 `integration_test` 的输出，交给真实 `small_task_repair` 一个没有源码范围的任务。真实 preflight 返回业务失败，初始修复次数设为 3。补齐正常 AG-UI runtime 所承担的节点观测和 lifecycle 失败记录，形成真实 SQLite checkpoint 和恢复来源。Electron 点击后运行的是未打补丁的实际后端、扫描器和工程命令，不替换恢复结果或检查成功状态。

最终来源 `electron-test-restart-seed3-20261015`，真实失败节点 `small_task_repair`；恢复 Run `recovery-6db3c93a6901`，恢复入口 `integration_test`。选择跳过性能测试后的续接 Run 为 `f0717be4-d5be-431b-858e-34dfd51192d1`。

| 核对项 | 实际结果 |
| --- | --- |
| 失败入口 | 底部显示真实“任务没有可验证的真实代码文件范围”原因，并说明从测试阶段重新开始、重新计算本轮修复次数。 |
| 明暗主题 | 实际切换浅色和深色查看同一错误入口、文案及按钮；结束时恢复浅色。截图见本次会话工具记录。 |
| 点击重试后的起点 | 首个业务执行步骤为“集成测试与质量门禁”，先执行前端依赖安装，再执行前端构建；没有直接执行原 SmallTask。 |
| 真实工程命令 | `pnpm install` 及 `pnpm run build` 通过；构建实际执行 `tsc -b && vite build`。 |
| 测试选择门 | 构建后停在“是否跳过前端性能测试”，没有绕过选择。 |
| 继续后的正式门 | 在 Electron 选择跳过可选性能测试后，报告通过，停在 `review_phase_confirmation`；“全量审查 / Diff 审查”等待用户选择，没有执行代码审查或验收。 |
| 旧任务 | 持久会话中的最新 Workflow 投影 `smallTaskTasks=[]`、`repairTaskPlan={}`。 |
| 代码保留 | 对测试副本 `frontend/src` 的 66 个文件逐个比较 SHA-256，前后完全一致。 |
| 预算及防重复 | 后端真实 Graph/SQLite 回归验证 3→0、同一失败只有一个恢复 attempt、旧动作不能再 fork；此项不以界面显示替代断言。 |

最终矩阵为 2 项通过、4 项跳过、0 项失败。静态应用无后端业务实体/API，后端构建、启动和静态检查按现有规则跳过；性能测试为本次显式选择跳过。

前两轮夹具调整：首次副本复用的依赖软链接触发 pnpm 重装交互提示，改为独立依赖目录，并验证离线 frozen install；第二次夹具漏传 `technical_plan`，导致静态应用额外执行后端检查，已暂停/结束后补齐上下文。最终轮未保留这些夹具问题。实际暂停及结束回执也已核对；本报告不将夹具阻塞计为最终测试通过。

## 自动验证和限制

- Backend：12 个定向 unittest 模块共 70 项，覆盖测试重启、业务/异常失败、缺少测试入口、预算重置、确认门、旧任务清理、变更保留、防重复交接，以及通用恢复、审查、对话恢复与 SmallTask 输出纠错。
- Frontend：`pnpm test:connection-recovery` 88 项、`pnpm test:execution-recovery` 39 项、`pnpm build` 通过。
- 变更 Python 文件定向 `py_compile`、`git diff --check`、Backend `/health` 通过。
- 扩展测试曾发现原 `test_integration_repair_progress` 用嵌套 StateBackend 时缺少 `files` channel，尚未调用模型就失败。夹具改用不依赖该 channel 的磁盘技能/记忆后端后，真实 Agent 的 24 轮模型上限用例通过；未修改生产 Agent 或依赖版本。
- 本次是受控业务失败后的真实 Electron 恢复验收，不是全新应用从需求开始的全旅程验收；未完成真实模型代码修复全链路、Lighthouse、Windows 或冷重启恢复全矩阵。
