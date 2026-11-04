# 规划历史只读与正式返回验证

日期：2026-11-03。本记录对应独立线程改动之后新增的历史只读规则；此前“历史需求可以独立提问”的行为已取消。

补充更正：本轮原先确认了正式规划可返回开发，但随后在同一 Electron 会话发现已消费 continuation 被再次发送，产生 `continuation changeId 不匹配`。因此下述“返回开发通过”不能代表重新进入和失败重试也通过；该缺口的修复及单独验收见 [续接去重验证](REVISION_CONTINUATION_VALIDATION_2026_11_03.md)。

## 实现

- `Frontend/src/renderer/src/components/AiChatPanel/planningStageAccess.ts`：统一按当前 lifecycle 业务阶段及会话归属判断权限。初始需求展示会话通过明确的 `entryKey` 绑定后台线程，阶段交接后的会话直接绑定其 Graph thread；两者都不依赖浏览位置授予权限。
- `AiChatPanel.tsx`：历史阶段不显示 Composer、执行/恢复底栏，不提交确认、草稿保存或节点调试；当前确认投影不注入旧会话。当前流程的事件按权威线程投递。开发阶段原有自由对话和正式修改入口保留。
- `components/PlanningHistoryNotice.tsx`：底部显示只读说明及前往当前计划/需求/开发阶段的入口，复用已有工作台明暗主题 token。
- `components/SessionSidebar/{index,FreeChatHistory}.tsx`：需求和计划阶段可以浏览会话目录，不能由用户新建规划会话；历史视图也不能借临时对话重新发起聊天。
- `components/MessageList/index.tsx`：历史操作禁用传递到模板卡；历史空态不提供新迭代需求输入卡。
- `components/ChatComposer/productConversation.ts`、`hooks/useWorkflowConversation.ts`、`service/agUiAgent.ts`、`service/technicalPlanningConversation.ts`、`service/applicationPlanningRuntime.ts`、`hooks/useApplicationPlanningRuntimes.ts`、`typings/workflow.ts`：移除完成态产品自由对话的客户端入口和历史提问的线程转发。
- `Backend/app/protocols/{application_page_planning,technical_planning_conversation}.py`：通过标准 AG-UI 失败生命周期拒绝历史产品对话，不调用模型或写 checkpoint；正式修订启动、当前阶段对话与原生确认流程保留。
- `Backend/app/graph/{application_planning_revision,application_planning_interrupts,state}.py`、`Backend/app/protocols/workflow/projection.py`：首次正式 START 与后续对话分开；用内部 `design_interaction_context` 暂存并恢复原审阅语义、业务请求和需求 revision，只读问题不重生成，也不破坏后续联合确认。该上下文不公开给客户端。
- `WorkbenchTopBar.tsx` 不再为回看需求/计划展示“可编辑上游产物”的旧切换弹窗；`Welcome/UiDesignConfirmationPanel.tsx` 的历史反馈输入框同步禁用。
- `technicalPlanningConversation.ts`、Runtime/Manager/AppEntryPage 使用独立答复投递，回复仅进入历史，不替换 Canonical Workflow。稳定审阅快照重投不能覆盖已展示的只读回复。
- 更新 `Backend/tests/test_planning_stage_thread_journey.py`、`Frontend/tests/workbenchPhaseNavigation.test.tsx`、`Frontend/tests/designConversation.test.ts` 及相关契约文档。

## 行为边界

| 场景 | 预期 |
| --- | --- |
| 进入计划后手动回需求 | 只看历史，提示在当前计划阶段提出调整 |
| 进入开发或后续测试/审查/验收后回需求/计划 | 始终只读，提示在开发阶段提出需求 |
| 后续阶段回开发 | 保留开发对话和正式修改入口，沿用原任务占用规则 |
| 系统正式返回需求/计划 | 重新开放当前业务阶段的会话及确认；同阶段旧会话只读 |
| 初次模板就绪、尚未进入开发 | 保留计划卡的“进入开发阶段”动作 |

## 已通过的自动化

Backend：

```sh
.venv/bin/python -m unittest \
  tests.test_planning_stage_thread_journey tests.test_planning_stage_handoff \
  tests.test_technical_planning_conversation tests.test_application_page_planning \
  tests.test_application_design_conversation tests.test_design_revision_recovery \
  tests.test_revision_continuation_handoff tests.test_application_lifecycle \
  tests.test_planning_execution_end tests.test_application_planning_structured_actions \
  tests.test_requirement_document_special_interactions tests.test_product_planning_retry
# 141 tests, OK
```

Frontend：

```sh
pnpm test:workbench-phase                 # 14 项，通过
pnpm test:application-planning-runtime    # 通过
pnpm test:stage-sessions                  # 通过
pnpm test:connection-recovery             # 108 项，通过
pnpm test:development-artifacts           # 44 项，通过
pnpm test:workflow-preview                # 99 项，通过
pnpm test:design-runtime                  # 通过
pnpm test:ui-design-adjust-settlement     # 6 项，通过
pnpm build                               # 通过
```

另通过修改 Python 文件的 `py_compile`、`git diff --check` 和 Backend `/health`（`status=ok`）。开发中的未使用变量/引用编译错误已修复，最后完整构建通过。

`pnpm test:design-conversation` 仍有此前已在原始 HEAD 复现的阶段断言失败：当前 `Frontend/tests/designConversation.test.ts:1091` 预期 `template_preparation`，实际 `technical_planning`。本次只更新了该文件中已取消的完成态产品对话契约断言，没有修改该阶段推断规则。此套件不计为通过。

## 真实 Electron 实测结果

直接操作已运行的 macOS Electron，使用真实 Backend、模型与原生确认按钮；没有用浏览器或写盘伪造状态。开始时 Mac 锁定，工具返回：

> The Mac is locked and automatic unlock could not unlock it. Ask the user to unlock the Mac manually before continuing.

用户解锁后已完成以下实测。主应用为 `/Users/yifei/Coding/xcodeagent_test/stage-thread-electron-20261103`（“独立阶段线程验证1103”）。

1. **计划后回需求只读：通过。** 无主输入框，历史确认、修改、UI 生成/跳过和反馈文本框均禁用；会话目录可浏览，新建对话禁用，历史视图临时对话也禁用，底部引导返回当前计划。回看不再弹出声称可以编辑上游的切换提示。
2. **开发后回需求/计划只读：通过。** 两个阶段均提示在开发阶段提出新增或调整需求，“前往开发阶段”恢复开发输入入口。
3. **开发→需求正式修订：通过。** 从原开发 thread `1af93e82-38f6-46a9-b5c9-3aa82148c351` 提出页面正文修改，经“确认并返回设计阶段”进入 DESIGN thread `397dd05f-f064-482d-866f-43c9d0b48269`，changeId 为 `chg_69996fed17064feb9c79f310bcb500be`。新需求会话可以继续修订，ProductPlan 草稿包含 `HISTORY-READONLY-1103`。
4. **需求问答后联合确认：通过。** 正式返回后的只读问题收到当前状态回复，没有再次生成；当前联合确认卡仍可操作。点击确认成功进入 UI 审阅，再明确跳过 UI，停在进入计划阶段门禁，没有自动确认下游。
5. **需求→计划→原开发续接：通过。** 系统创建 PLAN thread `23304d98-7dae-455d-80c5-46def4c6b727`，真实生成到技术确认门。计划问答回复可见；回复前后持久化 lifecycle、TechnicalPlan JSON/Markdown、ProductPlan JSON 和 RequirementSpec JSON 五份文件的 SHA256 均未改变。点击确认后回到原开发会话，并完成 application 目标的接口前置检查，停在开发目标选择，没有启动 Build。
6. **开发→计划专项修订：通过。** 再从原开发会话提出仅修改 TechnicalPlan 架构说明，经“确认并进入计划阶段”进入 PLAN thread `baac8d96-4451-43c6-91e1-262935715166`，changeId 为 `chg_ef4a3dd022f14774b6fad40458e54c9e`。TechnicalPlan 包含 `HISTORY-PLAN-1103`/NoticeContent 约定；RequirementSpec、ProductPlan JSON 保持上一轮摘要。只读问题正常回复仍待确认，确认后再次返回原开发会话。
7. **同阶段旧会话只读：通过。** 当前计划专项修订期间，打开上一轮 PLAN 会话仍可看见此前问答，但没有主输入框，底部引导返回当前流程；返回后当前技术确认与输入恢复可用。旧会话没有接入新确认门。
8. **后续阶段回看：通过测试阶段浏览场景。** 在已完成开发的隔离应用 `/Users/yifei/Coding/xcodeagent_test/retry-confirm-fixed-20261019` 中进入可回访测试阶段，再回需求/计划都保持只读；回到开发输入内容时发送按钮可用，随后清空草稿，未发送新请求。审查/验收处于未到达禁用状态，未通过修改持久化状态绕过它们；其运行阶段权限由自动化矩阵覆盖。
9. **主题：通过。** 实际检查浅色、深色历史只读提示、返回按钮和禁用状态，随后恢复原浅色主题。

实测发现并修复了三类遗漏：旧阶段切换文案/历史 UI 反馈输入未锁定；活动 formal revision 把后续问题当成首次 START，且只读回复返回时丢失审阅 clarification；技术问答正文被确认卡合并隐藏。上述场景均在修复后重新实测，相关自动化同步补齐。

主测试应用收口时 lifecycle revision 为 62，状态 `ready_for_workbench/completed`，`activeFormalRevision=null`、`activeExecutions={}`。没有留下生成任务，也未执行开发 Build。模板初始化沿原流程在测试项目创建本地提交，因未配置远端凭证未推送；没有为测试配置凭证或执行发布。

未执行 Windows 和完整测试/审查/验收执行链。本次源码改动未提交或推送。
