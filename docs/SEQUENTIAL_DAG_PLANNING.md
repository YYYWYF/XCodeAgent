# T6.4/T9.2/T9.3/T9.4/T9.5 Bounded Parallel Planning Orchestrator

## 接口与边界

`app.services.dag_planning_orchestrator.plan_dag_sequential` 将当前 T2.3 Requirements、
单 Unit Session/Local、T6.2 Barrier、T4.2 append-only Assembly、完整编译检查、
T4.1 归因和 T6.3 Global repair 接到一个最多三个 model session 并发的内部服务。

```python
result = await plan_dag_sequential(
    SequentialPlanningInputs(
        project_plan=formal_runtime_projection,
        base_confirmed_plan=confirmed_plan_or_none,
        skeleton_plan=unit_skeleton,
        build_context=build_context,
        build_execution_scope=scope,
        workspace_snapshot=workspace_snapshot,
        reuse_facts=reuse_facts,
    ),
    workspace_state={"workspace": workspace_path},
    planning_run_id=planning_run_id,
    workflow_run_id=workflow_run_id,
    thread_id=thread_id,
    policy=unit_generation_policy,
)
# result: ValidatedAssembledPlan
# result.assembly.assembled_plan: frozen cumulative DAG
# result.planning_run: frozen final in-memory Run
# result.generation_requirements: frozen T2.3 result
```

调用方负责提供已确认正式输入的当前运行时投影、当前 Scope/骨架以及可信 ReuseFacts。
输入 DTO 在调用入口深度冻结，服务核验 ConfirmedPlan 状态和完整 retained 身份覆盖，
重新计算 Requirements。可选 `generation_requirements` 必须与当前计算结果精确相同。
`build_context.required_unit_ids` 必须显式提供；空数组表示真正的空集合。
输入指纹和基线摘要是规范化内存 JSON 的 SHA-256，不读 Formal file，也不表示磁盘字节摘要。

可注入 `generate_once` 进行模型传输测试，默认执行真实单次 Unit Session；Local Validator、
Assembly、Global 编译门禁和归因始终使用真实服务。`publish` 是既有 Controller 的轻量
投影钩子；没有新增 HTTP、手写 SSE 或 AG-UI 产品协议。

## 状态链路

一个调用创建一个 Controller，首个模型调用前冻结所有 Unit Context。Context 包含当前
Unit 的正式合同 inline 切片、平台工作区快照、相关 Endpoint owner 和同 Unit retained 摘要；
不含任何当前 Candidate 正文。Global repair 复用这些冻结 Context，仅更新轮次/Attempt 和反馈。

模型 Unit 以 `UnitAttemptJob` 进入 FIFO Queue，最多三个 worker 并发执行；一次内容失败只把
当前 Unit 的下一 Attempt 追加到队尾，三次内容失败才耗尽当前轮。Unit Graph dependency
不作为生成顺序或入队门禁。
确定性 `frontend:auth-guard` 由既有 builder 生成，再经 Controller Candidate 事件接纳，
不进入模型 Session/Local retry，模型计数为零。shell/structural/reuse Unit 不生成。

任一 active model Unit 出现基础设施 fatal 时，Controller 先提交 `RunFailed`，将全部未完成
Unit 置为 `aborted`；Scheduler 随即停止新 dispatch、丢弃队列中尚未派发的 Job，并
best-effort 取消其他 active worker。已取消 sibling 若仍返回结果，Controller 的终态门禁会
拒绝其 Candidate 提交，Scheduler 保留并传播最初的 fatal，不让晚到拒绝异常覆盖根因。
fatal 不进入 Local requeue，也不会到达 Barrier、Global repair 或 Pending persistence。

Workflow registry 对活动任务发出 `task.cancel()` 后，Scheduler 先冻结派发并丢弃
所有 queued Job，再经 Controller 原子持久化 `PlanningRun.cancelled`，最后 best-effort
取消 active worker。吞掉 cancellation 的 provider 若返回晚到结果，T9.4 Attempt gate
将其作为无写入 no-op；Scheduler 不再 Local requeue，不进入 Barrier/Global。DAG
orchestrator 对 Assembly/Global 等 Scheduler 外 await 边界做幂等取消收口，但始终将
`CancelledError` 向上传播，不伪装成正常结果。

全部 Unit 到达轮次终态后进入 Global completeness；缺失时整批走 T6.3 repair。
Candidate 齐全才提交 `AssemblyStarted`，使用当前唯一 valid Candidate 和全部 confirmed
Tasks 执行真实 append-only Assembly。累计 DAG 的 Unit/task 图、跨 Unit 依赖和调度字段
允许重编译；历史 Task 身份、正文、执行状态及工程/业务验收合同保持不被替换。

Assembly 结构化失败保留完整来源序列，含重复 ID 的多个 provenance 记录，经 T4.1 归因。
成功组装后提交 `GlobalValidationStarted` 并核验真实编译的 status、task graph validation
和 blocked batches。任何已归因的一批问题只消耗一轮 G=2，先原子 supersede 所有 affected，
再把完整 affected 批次交给同一个有限并发 Scheduler；unaffected Candidate 仍保留在
Controller 中。

有些现有编译规则只有字符串错误。它们完整保存在 `GLOBAL_COMPILED_PLAN_INVALID` 的
diagnostic details 中，作为平台阻断，不通过解析文本猜测 retry Unit；未宣称所有旧字符串
规则已经具备自动修复归因能力。

集成中发现旧模板门禁会拒绝 T2.5C 的确定性 resources writer。本任务在新 Assembly 已有的
`_compile_auth_capability_dependencies` 边界内增加精确例外：auth-guard Unit、frontend owner、
deterministic 策略、授权资源 executor、完整指纹 Task ID/capability 和 resources.ts-only 路径
必须一致。普通模型 Candidate 的 Local 平台字段禁写规则保留；旧入口不启用该例外。
识别保留的不同 R 平台任务，不修改它们的指纹或切换 Build writer。

仅无问题且图有效时返回 `ValidatedAssembledPlan`，Run 停在 `active/validating`。
输出没有 `confirmation_status`/`confirmed_at`，不得把它当作 Pending 或 Build authority。
内容/Global/基础设施失败抛 `DagPlanningError(issues, snapshot)`，没有失败 Plan 返回值；
前置输入失败的 snapshot 为 None。Controller 持久化/发布及取消保留原异常语义。
Workflow Cancel 与 Pending Abandon 仍是两条独立路径：前者取消活动 task 并关闭
PlanningRun，后者只删除精确身份匹配的 PendingPlan。

## 与现有 LangGraph 入口的关系

现有 `tasks.py::prepare_build_tasks` 仍包含旧整批生成、正式路径 pending 写入和确认门禁，
其 workflow 路由依赖该确认流程。T6.4 排除 Pending/Confirm，因此本次不切换这个生产入口，
也不增加能够绕过确认直接进入 Build 的新路由。后续接入时，`tasks.py` 应仅作为服务的
LangGraph adapter；业务编排归本服务所有。

唯一允许的文件写入是 Controller 的 `.xcodeagent/plans/planning-run.json`。
不写 Pending、ConfirmedPlan、TechnicalPlan 或其他正式产物，不接 FrozenContractReader
或 Frontend。T9.4/T9.5 只完成 Backend Attempt 拒收与 Scheduler cancellation correctness，
不修改前端 Cancel UI。

## Frozen Contract Catalog

PlanningRun 只冻结一次正式 Store。每个 Unit 在 dispatch 前由当前 Scope、generation
requirements 和 Store 独立编译 expected formal binding manifest，再把调用方声明的
`formal_source_refs` 展开为 requirement/kind/ref/selector 原子做精确集合比较。缺少
Page、required API、Entity 或所需 authorization slice，以及未知、跨目标、wrong-kind、
额外 selector/source，均作为不可重试输入错误 fail closed；不会生成部分 catalog。

`formal_source_refs`、每条绑定的 `requirement_ids` 和 `selectors` 都在
`SequentialPlanningInputs` 边界规范化排序，因此仅组装顺序变化不会改变
`input_fingerprint`。最终 `UnitGenerationContext.contract_catalog` 仍只包含
`ref_id`、`kind` 和排序后的 `selectors`，不内联合同正文；retry 复用同一 Context。

## 验证

在 `Backend` 中使用现有 unittest runner：

```sh
.venv/bin/python -m unittest tests.test_dag_planning_orchestrator tests.test_sequential_auth_boundary
.venv/bin/python -m unittest tests.test_unit_generation_scheduler tests.test_unit_generation_scheduler_fatal tests.test_unit_generation_scheduler_cancellation
.venv/bin/python -m unittest tests.test_build_task_planner tests.test_build_unit_skeleton tests.test_prepare_build_tasks_guard tests.test_build_dag_v3_contract tests.test_page_build_context_resolver
.venv/bin/python -m unittest tests.test_engineering_acceptance tests.test_business_acceptance tests.test_build_task_planner
.venv/bin/python -m unittest tests.test_llm_provider tests.test_model_transport_retry tests.test_model_output tests.test_main_agent_boundaries
```

后三行分别是完整 R-PLAN、R-ACCEPT、R-MODEL。集成测试使用固定模型传输响应，实际执行
Prompt/session/parser/Local、Requirements、Controller、Assembly 和 Global；验证 A=1/B=2/C=1、
Local retry 回队尾、1/2/3/5 Unit 的最大并发、deterministic/model 混合与完整 Barrier，
基础设施 fatal 的停派/active sibling 取消/晚到成功丢弃/no Pending、Workflow Cancel 下
active=3/queued=2 的停派、worker 取消、晚到拒收、cancelled 持久化及 Global 停止、真实 retained ID 冲突只修 B、
Shared retain+append、无规划 Unit、正式文件字节不变，并补充完整数据库 Scope、确定性 auth、
空图、过期 Requirements 和无责任证据的编译失败。
