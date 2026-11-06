# 技术规划模块生成修复与专项验证（2026-11-06）

本次范围为 TechnicalPlan 分模块生成、片段持久化与异常恢复，以及 Electron 的查看、修订、重新确认和模板交接。测试没有覆盖后续业务代码开发、业务集成测试或整应用发布。

## 修复结果

| 问题 | 当前行为与主要所有者 |
| --- | --- |
| 单页重复 Endpoint 依赖 | `agents/main/technical_plan_generation_prompts.py` 明确要求复用同一接口时合并依赖；局部校验继续拒绝重复，不静默猜测。 |
| 模型断线或进程退出丢失已完成模块 | `services/technical_plan_fragments.py` 即时保存已验证正文与原子目录，生成器在下一次调用前等待保存完成；同输入沿既有业务节点恢复，变化输入失效，旧输入迟到写入拒绝。 |
| Graph 与片段正文重复存储 | Graph 只保留输入摘要；正文和唯一当前目录属于内部候选，公开 AG-UI 不暴露该摘要或候选目录，正式文档完整提交后清理目录。 |
| Build 的有效重试提示被通用失败文案覆盖 | `services/workflow_failure_message.py` 在真实收尾错误、任务错误和显式错误之后读取本轮重试提示。 |
| 技术失败审阅提交、begin 尚未交接时无法重试 | `services/business_node_retry.py` 与 `protocols/execution_recovery.py` 共用精确的 REPAIR/INPUT_COMMITTED、同线程、原 owner 校验，等待原执行真实退出后以 CAS 交接；其他 owner 仍拒绝。 |
| 首次返回 UI 设计时解包错误 | `nodes/ui_confirmation.py` 正确解包 `(ui_designs, product_plan)` 并保留原审阅门。 |
| 文档已生成但右侧为空 | `AiChatPanel` 与确认卡共用 `useWorkflowDocuments.ts`，按当前引用读取展示副本；隔离工作区与迟到响应，显示读取失败与加载状态。 |
| 确认后不触发模板交接 | `protocols/workflow/presentation.py` 从当前计划投影 `state.technicalPlanConfirmed`，前端联合阶段、终态、生命周期与正式确认回执交接；不依赖内嵌正文。Runtime 回归验证只交接一次，false 或缺失确认事实时不推进。 |

原四项失败中的 UI skip、跳过后本地效果推断、`summary.planControl` 是过期测试预期，已分别改为当前 UI 必须确认、从已确认 UI 读取绑定、从唯一 `state.planControl` 读取回执。没有恢复跳过 UI、旧 result 读取或历史兼容分支。扩展前端用例同步了当前单份 state 和产品文档不展示 UI 状态的合同。

## 自动验证

后端以下 30 个相关模块合计 **380 项通过**：

```sh
.venv/bin/python -m unittest \
  tests.test_technical_plan_modular_generation tests.test_technical_plan_modular_workflow \
  tests.test_technical_plan_fragment_persistence tests.test_technical_plan_fragment_editor \
  tests.test_technical_plan_fragment_workflow tests.test_technical_plan_editor_tools \
  tests.test_technical_plan_json_recovery tests.test_technical_plan_repair_feedback \
  tests.test_product_planning_retry tests.test_product_technical_planning \
  tests.test_authorization_manifest tests.test_workflow_projection \
  tests.test_workflow_failure_message tests.test_workflow_ag_ui \
  tests.test_application_planning_recovery_projection tests.test_technical_planning_native_recovery \
  tests.test_technical_planning_transaction_recovery tests.test_technical_planning_recovery_journey \
  tests.test_technical_planning_stage_retry tests.test_technical_planning_repair_baseline \
  tests.test_technical_planning_conversation tests.test_stopped_technical_planning_revision \
  tests.test_planning_stage_thread_journey tests.test_planning_stage_handoff \
  tests.test_application_planning_structured_actions tests.test_application_planning_interrupts \
  tests.test_business_node_retry tests.test_execution_recovery_protocol \
  tests.test_ui_design_manifest tests.test_ui_design_adjust_base
```

覆盖成功组装与单份文档提交、局部解析失败复用、连接异常早于节点 checkpoint、子进程立即退出、原子替换失败、损坏正文拒绝、输入变化与迟到写入、取消、正式修订、恢复事务、前置确认门及公开投影。

前端从 `Frontend` 执行以下专项命令，均通过：

```sh
pnpm test:workflow-documents
pnpm test:workflow-preview
pnpm test:application-planning-runtime
pnpm test:application-planning-recovery
pnpm test:execution-recovery
pnpm test:stage-sessions
pnpm test:workbench-phase
pnpm test:planning-artifact-status
pnpm test:requirement-doc-pages
pnpm test:technical-plan-authorization
pnpm test:design-conversation
pnpm build
```

另外执行所有变更 Python 文件的 `py_compile`、`git diff --check`，以及加载当前源码后端的 `curl -fsS http://127.0.0.1:8000/health`。构建包含 TypeScript 与 Electron/Vite 生产打包；构建本身不替代下面的 Electron 界面验证。

## 真实模型进程强杀与恢复

使用配置模型 `deepseek-v4.1-flash`，隔离后端位于 8001，正式 Electron 后端位于 8000。四页已确认上游由测试夹具准备；模型、节点和校验器没有替换。官方 `@ag-ui/client` `HttpAgent` 执行原应用规划接口。

当共享目录与 `order_contract` 已持久化、下个请求开始时，对隔离后端进程发送 SIGKILL。原客户端观察到连接终止，随后重启隔离后端，由只读恢复查询取得后端签发的动作，再通过 `/execution-recovery/execute` 执行。

恢复实际耗时 **31.724 秒**。逐次模型输出恰好为剩余 `customer_contract` 与四个页面，共 **5 次响应**；共享目录与已完成订单 Contract 没有重生成。落盘后的订单 Contract 与强杀前片段完全一致。最终为 2 实体、2 Contract、8 Endpoint、4 页面；完整生产校验返回空错误。源 Run 为 interrupted，新 Run 为 awaiting_user、入口为 `technical_planning_generate`，checkpoint 停在 `technical_planning_review` 且有原生 interrupt。正式 Markdown/JSON 已完整落盘，候选目录清理，没有触发模板或消费确认。

证据目录：`/Users/yifei/Coding/xcodeagent_test/technical-modular-crash-20261106/`，包含 `manifest-at-crash.json`、SDK 事件与 token、逐次恢复模型响应、`verification.json` 和正式产物。隔离后端已停止，8000 后端继续运行。

## Electron 实际交互

在已运行 Electron 创建应用“分模块技术规划完整验收1106”，实际经过需求/ProductPlan 生成、联合确认与 DESIGN→PLAN 交接，在 PLAN 会话点击入口发起真实模型技术规划。四页 UI 的自动模型生成因业务展示容器缺少静态归属被生产校验拒绝；为隔离本次技术规划范围，改用受控 TSX 上游，四页均通过同一生产校验，并在 Electron 实际点击 UI 确认，没有伪造已确认 checkpoint。

界面与读盘核对结果：

- 技术规划首版正常显示待确认卡，右侧展示架构、实体、API 和页面绑定；开发入口在确认前不可用。
- 浅色、深色截图均检查了确认卡与 API 文档；最终恢复浅色。
- 点击“修改”提出仅修改订单查询接口说明，真实模型沿现有局部编辑器完成。初稿与修订稿结构比较仅有 `/api_contracts/0/endpoints/0/summary` 一处差异；修订版重新等待确认，旧卡失效。
- 点击“确认保存”后，内部 JSON 为 confirmed，Graph completed、无后续节点，生产全量校验无错误。
- 修复按引用正文的交接判断后，Runtime 回归证明自动交接一次及防误推进；Electron 实际通过模板重试完成下载/物化/本地基线，最终 lifecycle 为 `ready_for_workbench/completed`，显示“应用模板已就绪”和“进入开发阶段”。没有点击进入业务开发。
- 模板重试曾因测试证据文件放在应用根目录而触发 Git 干净门禁；将证据移动到独立同级目录后重试通过。应用仓库地址为空，没有远端推送。

应用工作区：`/Users/yifei/Coding/xcodeagent_test/technical-modular-electron-complete-20261106/`。证据独立位于 `/Users/yifei/Coding/xcodeagent_test/technical-modular-electron-evidence-20261106/`，包含原 UI 模型失败、受控 UI 校验、初稿、修订待确认稿、精确差异、确认后的文档和最终 `verification.json`。

## 验证边界

本次证明技术规划模块生成、恢复、修订、确认和模板就绪的上述场景。**上游真实 UI 模型生成没有通过**，此处使用的是已验证的受控输入；没有把它记为端到端 UI 生成成功。未覆盖 Windows、其他模型、断电或磁盘硬件故障、多后端同时生成同一工作区，以及确认后的业务开发、应用测试和发布。没有提交或推送本仓库变更。
