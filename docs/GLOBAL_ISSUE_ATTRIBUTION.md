# T4.1 Global Issue Attribution

## 范围与当前调用关系

`services/global_issue_attribution.py::attribute_global_issues` 是新增的内部纯函数，
输出冻结 `GlobalRepairDecision(retryable, retry_unit_ids, issues)`。
`services/global_planning_validation.py` 提供归因所需的身份投影及完整性门禁，
**不是完整 DAG Validator**。当前直接调用方是新增单元测试；生产流程尚未接入。

已有 `graph/nodes/tasks.py -> prepare_build_tasks_with_main_agent` 仍走旧字符串校验、
候选 finalizer 和整批重生成；这与新基线 G3/G9 的差异不在 T4.1 内修正。
本任务不适配旧错误字符串，不执行 Global retry、Assembly、Candidate 状态变更、
Round/Attempt 分配、模型裁判或任何文件持久化，也不新增产品 HTTP 入口。

## 输入契约

```python
attribute_global_issues(
    issues,                      # Sequence[ValidationIssue]
    task_provenance=...,          # Sequence[TaskProvenance]
    reuse_facts=...,              # ReuseFacts
    candidate_ownership=...,      # Sequence[CandidateOwnership]
    planning_unit_ids=...,        # Sequence[str]
)
```

- `CandidateOwnership(candidate_id, unit_id, task_ids)` 是平台掌握的当前有效 Candidate
  身份投影。可通过 `CandidateOwnership.from_candidate(candidate)` 从已有
  `CandidateAttempt` 只读提取；invalid/superseded、空 Task 集合或带校验问题的记录被拒绝。
  Unit 取自 `Candidate.identity.unit_id`，不信任模型 Task 正文的自报归属。
- 调用方先选定当前 Run 的有效 Candidate；这里不选择 Attempt、不校验晚到响应或恢复旧记录。
  每个 planning Unit 必须恰有一个 Candidate，范围外或多份 Candidate 均阻断。
- `TaskProvenance(task_id, unit_id, source, candidate_id=None)` 的 `source` 为
  `retained/candidate/platform`。仅 Candidate 来源必须带 Candidate ID。
  所有 retained/Candidate Task 都必须有完整精确的来源记录。相同 Task ID 的不同来源
  **必须保留多条记录**；以 Task ID 为键覆盖会被完整性门禁拒绝。
- `ReuseFacts` 的前置问题阻断归因；retained ID、Endpoint owner 必须与正式身份一致。
  retained 与本轮 Candidate 可以属于同一 Unit，因为 Candidate 只代表新增贡献。
- `ValidationIssue` 来自受信的确定性 Global 校验规则。一个冲突 Issue 必须列出该冲突的
  **完整 Task 参与者**。`unit_ids` 仅用于诊断，不提供责任证据。
  原有 `retry_unit_ids` 如非空，必须是在规则命中处已证明的责任声明；本层再次核验这些目标
  属于问题涉及的当前 Candidate。模型、UI 或字符串解析结果不得直接提供该声明。
  `message/details` 永不参与路由；Endpoint 身份等诊断字段不会被启发式解析。

## 确定性规则

| Issue code | 归因行为 |
| --- | --- |
| `GLOBAL_TASK_ID_COLLISION` | 必须指向一个相同 Task ID 的多个来源；一个 retained 与 Candidate 冲突时只选择 Candidate；多个 Candidate 无明确责任时阻断 |
| `GLOBAL_ENDPOINT_OWNERSHIP_CONFLICT` | 一个 retained owner 与 Candidate 冲突时保留 retained；同 Unit Candidate 的多个 Task 冲突时只重生成该 Unit；不同 Candidate Unit 冲突需规则明确指定目标并留下唯一 owner Unit |
| `GLOBAL_AUTH_CAPABILITY_PROVIDER_CONFLICT` | 使用同样的 retained 优先、唯一 owner 规则；不计算资源指纹或运行 auth writer |
| `GLOBAL_DEPENDENCY_CYCLE` | 必须有确定性规则显式目标；仅有环成员、单个 Candidate 参与等信息不足以证明责任 |
| `GLOBAL_CROSS_UNIT_DEPENDENCY_INVALID` | 必须有确定性规则显式目标，参与 Task 的来源本身不能证明哪条边违规 |
| `GLOBAL_REQUIRED_UNIT_INCOMPLETE`、`GLOBAL_CONTRACT_VIOLATION` | 必须有确定性规则显式目标及对应 Candidate Task 来源；缺失整个 Candidate 先由完整性门禁阻断 |
| 未知 code、非 `global` 层级、非 `generation` 类别 | 不自动重试 |

平台 Task 参与、来源不完整、目标指向 retained-only/无关 Unit、多个 retained 冲突、
规则声明与 ownership 推论冲突，均不可重试。同一 code + Task 集合存在相互矛盾的责任声明时
阻断，不能聚合成“重试所有参与者”。归因规则只返回已有 ID，不补 ID、改 owner、rename 或丢 Task。

多 Candidate 的规则声明属于本层的受信输入边界：本层能核验身份与目标一致性，
不能从一个笼统冲突重新证明业务 owner 或边的正确性。新增实际 Validator 必须在规则处提供
证明充分的声明；没有该证据就保持 `retryable=False, retry_unit_ids=()`。

## 聚合与副作用

先对全部 Issues 归因，再稳定去重并将目标排序取 Unit 并集。
无 Issue 时 `retryable=False, retry_unit_ids=(), issues=()`，表示没有 repair 要做。
任一 Issue 不可修复时，总 `retryable=False` 且总目标为空；逐项成功归因仍保留用于诊断，
调用方不得忽略总开关执行部分 Global repair。非法输入门禁则清空所有原始目标。

该函数不接受计数器、不分配任何 ID；重复调用不消耗 Global round。
输入和输出使用冻结契约，JSON 导出是独立副本。一个 Unit 多个问题只出现一个总目标。

## T5 状态入口

T5 通过 `GlobalRepairStarted(decision=decision, at=...)` 将完整结果交给 Controller，
再原样传给 `begin_global_repair(run, decision, at=...)`。Event 和纯状态入口都重新验证
`GlobalRepairDecision` 的聚合一致性；裸 `issues` 入口已移除，不提供兼容别名。

总开关关闭或总目标为空时，整个事件被拒绝，不递增 revision/Global round，不 supersede、
持久化或发布。总开关通过后仅使用 `decision.retry_unit_ids` 重开 Unit；逐项分组只提供反馈，
不从可修复子集重新计算路由。任一 missing-candidate、platform 或 non-retryable blocker
都必须保留在 T4.1 的完整结果中。

Decision 是受信服务契约，不是能够证明来源的凭证：类型验证无法识别上游在调用 T4.1
前已遗漏的问题，也无法阻止调用方另行构造一个内容自洽但不完整的 Decision。后续编排必须
收集完整 Global Issue 集合并直接转交 T4.1 返回值，不能过滤 blocker 后重新构造决策。
本次不提前实现 T6 编排、完整 DAG Validator 或跨阶段的决策存储机制。

`tests/test_global_repair_controller.py` 覆盖真实 T4.1 → Event → Controller 链路中的
完整成功、混合 blocker、missing Candidate、裸子集和未验证 Decision 反例。

## T6.3 串行 Global 循环

`services/global_repair_orchestrator.py::run_global_repair_loop` 接收同一个
`PlanningRunController`、`validate_global(snapshot)` 和 `regenerate_unit(unit_state)`
两个异步内部适配器。一轮生成结束后调用；每个 Run 只能有一个编排调用方。

循环先通过 T6.2 Barrier。Candidate 不齐全时，完整缺失集合已经具有 Unit 责任证据，
通过 `aggregate_global_repair_decision` 一次性聚合；此时不调用要求 Candidate 齐全的
T4.1 Task 来源归因器。齐全时才调用 `validate_global`，它必须收集本次完整 Global
Issues 并返回 T4.1 的完整决策；T6.3 测试用 mock 规则配合真实归因器实现该适配器。
公共聚合函数只供受信且已归因的 Issue 使用，不能替代 Task provenance 核验。

可修复决策原样提交 `GlobalRepairStarted`，先原子 supersede 全部 affected Candidate、
重开 generation round 并重置 Local 预算，再按去重后的目标顺序 await Unit runner。
模型 Unit runner 调用 `run_unit_generation_round`，将收到的 `unit_state.current_issues`
作为 `global_feedback`；deterministic Unit 由受信适配器执行确定性生成及 Controller
事件提交，不进入模型 Local retry。Unit runner 必须结束当前 Unit 的整轮再返回。
它不接收其他 Unit Candidate 正文；每轮完成后重新通过完整性 Barrier。

无 Issues 时返回空决策，Run 保持 `active/global_check`，供调用方接续后续阶段。
有 blocker 或 G=2 耗尽时提交 `RunFailed`，本次完整 Issues 存入 `failure.details.issues`，
同时返回失败决策；Local 基础设施错误由现有 Local 编排终止 Run 后原样上抛。
旧 superseded Candidate 在任何失败下都不会恢复。初次检查不消耗 G，一个 Unit 在一批
多个问题中只重开一次，无关 Unit 和 Candidate 保持原样。

本入口是内部编排能力；不实现 Scope Assembly 主体、完整 Global Validator、Pending、
并发或生产 Planning 切换，不新增 HTTP/AG-UI 产品入口。

## 验证

在 `Backend` 下使用现有 unittest runner：

```sh
.venv/bin/python -m unittest tests.test_global_issue_attribution tests.test_global_planning_validation
.venv/bin/python -m unittest tests.test_global_repair_orchestrator tests.test_global_repair_controller tests.test_unit_generation_orchestrator tests.test_candidate_completeness_barrier
.venv/bin/python -m unittest tests.test_build_task_planner tests.test_build_unit_skeleton tests.test_prepare_build_tasks_guard tests.test_build_dag_v3_contract tests.test_page_build_context_resolver
```

最后一行是用户全局基线定义的完整 R-PLAN；测试覆盖 T4.1 七类验收及完整性、歧义、
错误分类、矛盾证据、冻结序列化和无副作用反例，以及 T6.3 七项验收、预算重置、
缺失聚合、混合 blocker 与基础设施失败。不表示后续 Task 已实现或已接入生产。
