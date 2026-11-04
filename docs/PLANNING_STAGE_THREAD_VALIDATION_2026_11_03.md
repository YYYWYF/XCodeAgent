# 需求与技术阶段独立线程验证记录

验证日期：2026-11-03。环境：macOS、当前开发 Backend、已运行的 Electron。未提交或推送。

## 当前行为

- DESIGN 与 PLAN 分别使用自己的 AG-UI、LangGraph checkpoint 和模型 `threadId`。
- 首次进入 PLAN、从 PLAN 实际返回上游修改、正式修订入场，通过既有 `/application-page-planning/run` 的 `planningStageHandoff` 交接服务端产物及未消费的原生确认门。交接本身不生成产物、不自动确认。
- 手动回 DESIGN 只提问，使用独立产品会话身份，只读当前规划；技术执行归属和技术确认门不变。
- 上游实际修订后旧 PLAN 只保留历史，旧线程不能再提交确认或继续写入。它不是两个可同时推进的业务分叉。
- 阶段内停止、继续、失败恢复和冷恢复使用该阶段当前线程。正式修订保留影响确认身份及来源开发会话，TechnicalPlan 确认后的 continuation 仍返回来源开发会话。
- `initialization.iterationId` 是稳定迭代身份，阶段交接不再触发新迭代或分支作用域重建。不增加历史数据迁移。

## 文件范围

后端：

- `Backend/app/protocols/planning_stage_handoff.py`：输入校验、锁、checkpoint 交接、确认门重建、幂等及 lifecycle CAS。
- `Backend/app/protocols/product_stage_conversation.py`：已完成应用的独立只读产品问答。
- `Backend/app/protocols/application_page_planning.py`、`technical_planning_conversation.py`、`workflow/runtime.py`：AG-UI 路由、模型会话身份和旧线程写入拦截。
- `Backend/app/domain/application_lifecycle.py`、`graph/state.py`、`services/application_lifecycle.py`、`application_revision_lifecycle.py`、`revision_continuation_handoff.py`：阶段归属与稳定迭代身份。
- 新增 `Backend/tests/test_planning_stage_handoff.py`、`test_planning_stage_thread_journey.py`；更新 `test_application_page_planning.py`、`test_technical_planning_conversation.py`、`test_design_revision_recovery.py`。

前端目录以下均相对 `Frontend/src/renderer/src/`：

- `service/planningStageHandoff.ts`、`activeApplicationPlanning.ts`、`applicationPlanningRuntime.ts`、`technicalPlanningConversation.ts`、`hooks/useApplicationPlanningRuntimes.ts`：标准 AG-UI 交接、Canonical State 替换、目标 Runtime 接管、取消收尾。
- `components/AiChatPanel/AiChatPanel.tsx`、其 `hooks/revisionSession.ts` 与 `hooks/useWorkflowConversation.ts`：独立 StageSession、历史只读问答、正式修订来源、@上游修改入口、失效卡片隔离。
- `components/LeftPanel/LeftPanel.tsx`、`pages/WorkbenchPage.tsx`、`pages/AppEntryPage.tsx`：交接参数及 lifecycle 投影。
- `context/WorkbenchPhaseContext.tsx`、`service/applicationBranches.ts`、`components/WorkbenchTopBar.tsx`、`typings/workflow.ts`：稳定迭代作用域。
- 更新 `Frontend/tests/applicationPlanningRuntime.test.ts`、`formalRevisionHandoffRecovery.test.ts`、`applicationBranchView.test.ts`。

文档：`CODEBASE_INDEX.md`、`PRODUCT_UI_TECHNICAL_PLANNING.md`、`DESIGN_CONVERSATION_AGENT.md`、`APPLICATION_LIFECYCLE.md`、`APPLICATION_REVISION_WORKFLOW.md` 及本记录。

## 自动化结果

后端在 `Backend` 运行以下命令，**170 项通过**：

```sh
.venv/bin/python -m unittest \
  tests.test_planning_stage_handoff tests.test_planning_stage_thread_journey \
  tests.test_application_page_planning tests.test_application_planning_structured_actions \
  tests.test_technical_planning_conversation tests.test_technical_planning_stage_retry \
  tests.test_planning_execution_end tests.test_application_lifecycle \
  tests.test_model_request_headers tests.test_generated_document_inclusion \
  tests.test_application_design_conversation tests.test_design_revision_recovery \
  tests.test_revision_continuation_handoff tests.test_requirement_document_special_interactions \
  tests.test_product_planning_retry
```

新增流程测试使用真实 LangGraph/SQLite/checkpoint 与 AG-UI，产物模型使用受控替身，覆盖：

- 交接不调用生成模型、不消费确认，幂等与旧线程写入拦截；来源 Run、版本及目标线程冲突拒绝。
- 产品问答不改变原 checkpoint、lifecycle 或产物。
- 上游修改使技术状态失效，保留需求/产品联合确认，拒绝提前进入 PLAN。
- 独立 PLAN 线程生成、目标模型 thread 身份、冷恢复与确认。
- DESIGN/PLAN 两类正式修订启动、待批准影响基线、正式修订来源及 continuation 相关回归。
- 稳定 iterationId 在创建、交接及生命周期更新中保留。

前端在 `Frontend` 运行，以下命令通过：

```sh
pnpm test:application-planning-runtime
pnpm test:stage-sessions
pnpm test:application-planning-recovery
pnpm test:connection-recovery
pnpm test:workbench-phase
pnpm test:branch-view
pnpm test:iteration-origin
pnpm test:workflow-preview
pnpm test:design-runtime
pnpm test:design-mention
pnpm build
```

Runtime 回归包含交接后的旧来源事件隔离、先结束原 transport 再执行上游修改、禁止缺少目标时回退同线程、正式修改入场不确定时保留目标会话，以及用户停止后等待服务端确认、不把原请求取消错误作为新的网络故障。

另已通过改动 Python 文件的语法/导入检查、`git diff --check` 和 `curl -sS http://127.0.0.1:8000/health`（`status=ok`）。构建的 AG-UI 静态/动态导入提示不阻断构建。

### 已知既有失败

`pnpm test:design-conversation` 仍失败：`Frontend/tests/designConversation.test.ts:1105` 预期 `template_preparation`，实际 `technical_planning`。已用干净 HEAD 的临时源码副本和同一依赖复现相同失败；本次未修改该测试文件或对应阶段推断规则。此命令不能计为通过；本次没有扩展到修改模板阶段推断语义。

## Electron 实测

直接操作已运行的 Electron，测试工作区为 `/Users/yifei/Coding/xcodeagent_test/stage-thread-electron-20261103`，应用名“独立阶段线程验证1103”。未以 Vite 浏览器页替代。

1. 实际模型生成需求/产品文档，用户联合确认并明确跳过 UI，再进入 PLAN。DESIGN thread 为 `573b07b1-7eac-4377-85ed-6c3117301492`，首次 PLAN thread 为 `e56b6aab-1c06-4669-8760-5c58d5c8f87c`；技术生成到达“确认保存”。
2. 手动切回 DESIGN，仅询问当前状态。产品会话收到只读回复；磁盘 lifecycle revision、技术 thread/run 以及 TechnicalPlan JSON SHA256 均保持不变。
3. 通过 `@需求设计` 修改单页公告正文。实际交接到 DESIGN thread `e3557556-ca14-4cbf-8f02-63d84caa09bf`，ProductPlan 草稿包含新正文，停在需求/产品联合确认门，没有自动确认。
4. 浏览旧 PLAN，旧技术卡显示失效、操作禁用，提示完成上游确认后重新进入计划阶段。当前需求确认卡只出现在所属 DESIGN 会话。
5. 联合确认、明确跳过 UI 后再次进入 PLAN，创建 thread `23501733-96d2-4486-be03-81bc501d2b62`。停止生成、点击继续后仍使用该 PLAN thread；再次停止后正常显示“已停止生成 / 继续生成”，没有误报网络错误。
6. 检查深色主题下停止/恢复卡与输入区的可读性，随后恢复原浅色主题。测试应用最终停在 PLAN 的已停止状态，没有继续触发模板或开发。

## 证据边界

- 自动化中的模型替身验证线程、确认门、持久化和路由合同；Electron 实测补充真实模型与界面链路。
- 稳定 iterationId 由自动化验证；此次 Electron 测试应用在开发中途加入该字段前已创建，没有把它计作新建完整合同的实测证据。
- 未在 Windows 运行；未对真实模板生成、完整开发 Build、验收链及正式修订返回开发进行整条 Electron 端到端验证，相关入口与 continuation 仅有本次列出的自动化覆盖。
- 未执行整个仓库的全部测试。不能将以上结果视为所有安装、平台或开发流程均已通过。
