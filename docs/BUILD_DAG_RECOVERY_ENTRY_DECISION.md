# Build DAG 生成失败时两个重试入口的决策材料

> 历史决策材料：本文记录 2026-10-04 统一入口实施前的代码状态和待决策问题。实施后的当前链路以 `docs/CODEBASE_INDEX.md` 和源码为准；下方失败重试入口已移除，旧请求动作已停用。

本文供评审者判断：当 `prepare_build_tasks` 失败时，是否应优先展示节点内部的 Planning Recovery 重试，并隐藏通用的失败节点重试。文中区分已由代码确认的行为、尚未证明的前提和待决策的问题；不把两个按钮仅因都能再次进入同一节点就视为等价。

范围是工作台主 Workflow 的 `prepare_build_tasks` 失败。截图中的上方卡片显示“当前执行失败／重新执行失败步骤”，下方计划执行栏显示“计划执行失败／重试失败任务”。这里的“Build DAG 生成”发生在正式 Build 任务执行之前。以下分析基于 2026-10-04 的工作区代码，仅为静态代码审查，未运行这次故障的实际恢复。

## 当前两个入口的调用路径

| 入口 | 前端请求 | 后端选择与执行 | 对本场景的含义 |
| --- | --- | --- | --- |
| 上方 Recovery Incident | 点击前刷新当前 lifecycle；对于 `recoverable`，提交后端签发的 `incidentId`、`actionId`，执行 `retry_failed_node`。 | `FailureTargetResolver` 解析失败节点的重入计划，`WorkflowReentryExecutor.prepare_failure_retry` 复核并从节点入口状态重入。 | 可再次进入 `prepare_build_tasks`，但这个入口本身没有传入 Planning Recovery 所用的原 Workflow run ID。 |
| 下方计划执行栏 | `failed` 且 `canRetryFailedTasks` 时，发送 `workflowAction=retry_failed_tasks`、Workflow 快照和 `resumeExecutionRunId`。 | 请求适配器判断失败阶段；若为 `prepare_build_tasks`，从该阶段进入，且 Build 专用的 `retry_failed_tasks` 布尔值为 `false`。Planning adapter 仍凭 `workflow_action` 和原 run ID 读取 Planning Recovery。 | 可能复用上次 DAG 生成留下的有效 Unit Candidate，再生成尚未完成的部分。 |

前端入口和后端分支见 [`RecoveryIncidentCard.tsx`](../Frontend/src/renderer/src/components/RecoveryIncidentCard/RecoveryIncidentCard.tsx)、[`useWorkflowConversation.ts`](../Frontend/src/renderer/src/components/AiChatPanel/hooks/useWorkflowConversation.ts)、[`PlanExecutionDock/index.tsx`](../Frontend/src/renderer/src/components/AiChatPanel/components/PlanExecutionDock/index.tsx)、[`request.py`](../Backend/app/protocols/workflow/request.py) 和 [`execution_recovery.py`](../Backend/app/protocols/execution_recovery.py)。建议评审时重点看 `useWorkflowConversation.ts:959-1003,2227-2244`、`request.py:250-267,744-746,1391-1485`、`execution_recovery.py:263-310`。

“精确”有两个不同维度。上方入口对**Workflow 节点入口状态**有严格的重入校验；下方入口对**DAG 生成节点内部已完成的 Unit Candidate**有复用机会。若目标是减少已完成 Unit 的重复模型调用，下方路径可能更精确。若目标是验证失败节点的重入身份和 Graph 状态，上方路径有更严格的专用机制。不能把其中一项的精确性直接推广到另一项。

## 下方入口复用内部进度的前提

`prepare_build_tasks` 的 Planning adapter 在 `workflow_action=retry_failed_tasks` 时，把 `resume_execution_run_id` 作为 `recovery_source_workflow_run_id` 传给规划服务；此条件独立于 Build 专用的同名布尔状态。[`task_planning_adapter.py:190-209`](../Backend/app/graph/nodes/task_planning_adapter.py)

规划服务按该精确 run ID 读取 Planning Recovery Snapshot。Snapshot 不是通用 checkpoint，也不是规划权威：只在 `UNIT_GENERATION_INFRASTRUCTURE_FAILURE`、系统／基础设施类别、不可在原轮次重试、且至少存在一个可保存的有效 Candidate 等条件满足时，才会持久化。[`build_task_planning_service.py:94-156`](../Backend/app/services/build_task_planning_service.py)、[`planning_recovery_contracts.py:56-102`](../Backend/app/services/planning_recovery_contracts.py)

新 PlanningRun 会对 Snapshot 校验输入指纹、已确认计划摘要和执行范围；每个 Candidate 还要通过当前 Unit Context 和 Local Validator。通过的 Candidate 进入新 Run，剩余 Unit 正常生成。Snapshot 不存在、损坏、过期或 Candidate 未通过校验时，代码会放弃相应复用并继续全新生成。因此，下方按钮**不保证**“从上次内部进度接着做”，它保证的是“尝试按当前输入安全复用”。[`dag_planning_orchestrator.py:274-351,375-383`](../Backend/app/services/dag_planning_orchestrator.py)、[`build_task_planning_service.py:94-113,261-279`](../Backend/app/services/build_task_planning_service.py)

相关测试至少覆盖了：Planning adapter 独立传递原 run ID；有效 Candidate 的复用；输入不匹配时跳过复用；同一失败 execution 被新执行接管后拒绝再次接管。[`test_async_workflow_planning_adapter.py:152-180`](../Backend/tests/test_async_workflow_planning_adapter.py)、[`test_dag_planning_orchestrator.py:224-332`](../Backend/tests/test_dag_planning_orchestrator.py)、[`test_planning_recovery_cleanup.py:240-275`](../Backend/tests/test_planning_recovery_cleanup.py)。这些测试不等于截图中两个入口的完整端到端行为已验证。

## 上方入口的边界

上方按钮不是无条件的“兜底重试”。当前 Recovery ActionPlan 只对具有 escaped exception 证据、且能解析精确 Workflow Re-entry 计划的 `FAILED` execution 签发 `retry_failed_node`。仅由业务节点写入的 `status=failed` 若缺少该证据，会进入 `needs_attention`，不会获得相同的节点重入动作。[`execution_recovery_source_admission.py:21-32`](../Backend/app/services/execution_recovery_source_admission.py)、[`execution_recovery_action_planner.py:25-76`](../Backend/app/services/execution_recovery_action_planner.py)

上方执行会核对当前 incident/action 身份，重新解析并验证节点重入计划，再从失败节点入口语义状态执行。[`execution_recovery.py:263-310`](../Backend/app/protocols/execution_recovery.py)、[`execution_recovery_executor.py:294-344`](../Backend/app/services/execution_recovery_executor.py)。从当前调用链推断，这条路径未显式传入下方路径用于读取 Planning Recovery Snapshot 的 `recovery_source_workflow_run_id`；**是否可能通过别的持久状态间接复用 Candidate，尚需端到端证据确认**，不能仅凭按钮名称断言绝不会复用。

## 当前阶段识别的独立风险

下方的 `retry_failed_tasks` 先比较旧 DAG 范围和当前范围、检查 Build gate error，再读取 lifecycle 中的失败阶段；仍无法确认时会倒序查找客户端 Workflow 快照中的失败事件，最后才拒绝请求。[`request.py:1391-1464`](../Backend/app/protocols/workflow/request.py)

正常失败流程可能先记录 `prepare_build_tasks` 失败，随后 `handle_failure` 再把 lifecycle `phase` 写为 `handle_failure`。后者不在当前重试阶段映射中，因此一次完整流程可能需要依赖客户端事件回退。这是代码路径推论，尚未用截图对应的实际 lifecycle、DurableExecution、AG-UI 事件和 Planning Recovery 文件验证。[`lifecycle.py:407-421`](../Backend/app/protocols/workflow/lifecycle.py)、[`workflow.py:254-265`](../Backend/app/graph/workflow.py)、[`request.py:1424-1485`](../Backend/app/protocols/workflow/request.py)

已有请求测试证明“lifecycle 直接记录 `prepare_build_tasks` 且事件为空”时会回到 Prepare，也证明缺少任何失败阶段证据时会拒绝；它们没有单独证明上述 `handle_failure` 覆盖后的真实快照仍总能准确定位。[`test_workflow_request.py:1970-2006,2609-2620`](../Backend/tests/test_workflow_request.py)

## 需要评审者裁决的方案

**方案 A：保持双入口。** 用户可以自行选择节点级重入或规划内部重试。代价是两个入口在同一失败上并列，按钮名称无法解释恢复粒度，也可能引导用户选择会重复已完成工作的路径。

**方案 B：专项恢复优先。** 后端在当前 `prepare_build_tasks` 失败上确认 Planning Recovery 可用时，只展示 DAG 规划专项动作；不可用时才展示通用节点重入动作。优点是界面只有一个推荐动作。关键设计问题是“可用”的定义：仅有 Snapshot 文件并不足以保证 Candidate 最终可复用，指纹、范围和 Local Validator 仍会淘汰 Candidate。

**方案 C：一个上方入口，由后端决定恢复策略。** Recovery ActionPlan 明确区分“复用规划 Candidate 后重试”和“节点入口重入”，并绑定当前 source run、thread、scope、Snapshot 身份与执行条件。界面只呈现后端签发的当前动作；必要时显示候选动作或解释退化为全新生成的原因。此方案改动 Recovery ActionPlan、执行分派和相关测试，不能通过隐藏一个按钮完成。

待回答的问题：

1. 产品要保证的是“优先尝试复用已完成 Unit”，还是“保证实际复用后才显示专项动作”？后一保证可能需要在执行前重建当前规划上下文并验证 Candidate。
2. Snapshot 不存在、损坏或与当前输入不匹配时，是否允许专项入口退化为全新 DAG 生成？如果允许，按钮文案应明确是“重新生成计划”，不应承诺“断点续做”。
3. 对没有 escaped exception 证据的业务 `FAILED`，上方 Recovery ActionPlan 应给出何种可执行动作？不能直接解除现有 `retry_failed_node` 准入要求。
4. 要不要把原失败节点作为服务端持久事实保留，避免通用 `handle_failure` 覆盖后再依赖客户端事件推断？
5. “已停止→继续执行”与下方失败按钮共用组件和前端 handler；调整失败入口时是否明确保留停止后的继续能力？[`PlanExecutionDock/index.tsx:222-239`](../Frontend/src/renderer/src/components/AiChatPanel/components/PlanExecutionDock/index.tsx)

## 决策前建议核验的证据

用一个确实写出了 Planning Recovery Snapshot 的 `prepare_build_tasks` 基础设施失败作为样本，记录 source run、lifecycle 原始和最终 phase、DurableExecution failure/current node、AG-UI 失败事件、Snapshot 中的 Candidate 数量，并分别在隔离工作区执行两条路径。比较新 Run 是否接受 recovered Candidate、实际模型调用的 Unit 集合、生成结果和失败时的退化行为。另覆盖 Snapshot 缺失、输入指纹变化、业务 `FAILED` 无异常证据，以及新 run 已接管后重复点击。只有这些证据能证明“专项优先”在当前产品流程中安全且确实减少重复工作。

本文没有修改产品实现；工作区中已有的 Recovery Incident UI 相关未提交改动不属于本文。
