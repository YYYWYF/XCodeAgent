# Build Task Plan Confirm / Abandon / Regenerate 生命周期

## 当前生产状态和目标动作

生产 Workflow 已默认绑定 async Planning/Confirm adapter：生成成功只写
`build-task-plan.pending.json`，Confirm 才提升到 `build-task-plan.json`。确认卡的目标动作集合为：

```text
confirm
abandon
regenerate
```

`confirm`、`abandon` 与 `regenerate` 均已接入当前生产链路。Regenerate 通过 AG-UI 结构化动作恢复 `prepare_build_tasks`，不得退回普通自然语言触发。

## 接口

```python
result = confirm_pending_build_task_plan(
    {"workspace": workspace_path},
    planning_run_id=planning_run_id,
    draft_digest=draft_digest,
    current_inputs=current_sequential_planning_inputs,
)
```

内部服务位于 `Backend/app/services/build_task_plan_lifecycle.py`，返回冻结的
`ConfirmPromotionResult(status, confirmed_plan, pending_cleanup_error, errors)`。
只从当前工作区的固定 Formal/Pending 路径读取文件，不接受客户端 Plan 正文、
文件路径覆盖、checkpoint 或客户端计算的输入指纹。

调用方必须从当前正式输入重新构造 `SequentialPlanningInputs`，并保证本次同步
调用期间正式输入稳定。使用 T6.4 的同一个完整输入摘要算法，覆盖 project_plan、
base_confirmed_plan、skeleton_plan、build_context、build_execution_scope、
workspace_snapshot 和 reuse_facts。传入的基线摘要还必须与实际 Formal 文件相同。

## 验证和提交

首次确认按以下次序拒绝失效输入：

1. `planning_run_id` 与 `draft_digest` 必须有效，且精确匹配当前 Pending identity。
2. Pending 必须通过 DraftIdentity schema 和 canonical self digest 校验。
3. 当前 Formal 摘要必须等于 `base_confirmed_plan_digest`。存在但损坏或不合格的
   Formal 不能被当作空基线。
4. 当前完整输入指纹与 Scope 必须匹配。Pending 正文若携带 Scope，也必须一致。
5. DAG 必须 ready、validation 有效且无错误、没有 blocked batch，保留全部 baseline
   Task ID。复用实际 DAG compiler 对精确任务合同检查语义、依赖和调度，并核验
   已保存的 nodes/edges/topological_order。检查不回写重编译结果，也不补齐或修复任务。
6. 构造 ConfirmedPlan，通过共享 `write_json_atomic` 原子替换 Formal。
7. 删除仍匹配本请求且摘要自洽的 Pending。

正式文件保存 `confirmation_status=confirmed`、一次生成的 `confirmed_at` 和
`confirmed_from={planning_run_id, draft_digest}`，移除草稿专用的 `draft_identity`。
任务正文、任务图和其他产物内容保持原稿不变；Scope 来自已验证的 DraftIdentity。

业务结果包括 `confirmed`、`already_confirmed`、`stale_draft`、`stale_base`、
`stale_inputs` 和 `invalid_dag`。拒绝时保留 Formal/Pending 的原始字节。
Formal 写入失败抛原始 `OSError`，不删除 Pending。

Formal atomic replace 是提交点。提交成功但删除失败仍返回 `confirmed`，并携带
`pending_cleanup_error`。重复请求优先匹配 Formal 的 `confirmed_from`，返回
`already_confirmed`，不修改 Formal 或重新生成时间。它只尝试清理同身份且未被
篡改的剩余 Pending；不同身份的新 Pending 必须保留。已成功提交的重复确认不再
要求旧基线或旧输入继续新鲜。

## 范围与限制

Pending writer 和 Confirm 共用进程内同步锁，保证同进程的重复确认只提交一次，
且本服务清理 Pending 时 writer 不会插入新文件。不提供跨进程、外部编辑器或
其他正式输入写入者的事务锁；原子替换也不等同于两个文件的联合事务。

生产 Graph 已通过 `task_planning_adapter.py` 使用独立 Pending writer 和 Confirm authority；
`tasks.py` 只保留 legacy 实现以及被 adapter 复用的前置校验、确认投影和 Build 兼容 helper。
Abandon 已通过 plan-control AG-UI 流接入：它只收口匹配 Pending 对应的 Workflow execution，
不取消 active Scheduler、不删除聊天记录，也不修改 Formal。

## Regenerate 当前合同

Regenerate 请求必须携带：

```text
action = regenerate
planning_run_id
draft_digest
```

处理顺序固定为：

1. 精确验证当前 Pending DraftIdentity。
2. 删除匹配 Pending；该删除是不可回滚提交点。
3. 回到 `prepare_build_tasks`，重新加载当前 ConfirmedPlan 和最新正式输入。
4. 分配新的 `planning_run_id`，创建全新 PlanningRun；不得继承旧 Candidate、Local Retry 或 Global Repair 状态。
5. 完成 Unit generation、Scope Assembly 和 Global Validation 后写入新的 Pending，并再次等待确认。
6. 任一后续步骤失败时保留失败事实，但不恢复旧 Pending；Formal 始终保持不变。

Regenerate 是 AG-UI 结构化动作，并与 Confirm 一样绑定精确 DraftIdentity。前端、request normalization、Graph resume、lifecycle 和 Planning refresh 投影已经按一个端到端合同接入。

## 应用级互斥和刷新边界

同一应用不允许不同页面或其他 Scope 同时处于 DAG generating 或 awaiting confirmation。
开始新 PlanningRun 前必须确认当前应用既没有 active PlanningRun，也没有待确认 PendingPlan；不能依赖工作区唯一文件的覆盖顺序解决并发。

取消只提供 Workflow/PlanningRun 级能力，不提供 Unit 级用户取消。Pending 阶段使用 Abandon，不使用 Cancel。

当前刷新能力只恢复权威状态投影，不保证刷新后请求继续运行。后台脱离执行、SSE 重连/事件重放和 Candidate 断点续跑属于后续架构能力，本轮不实现。

## 测试

在 `Backend` 目录使用现有 unittest runner：

```sh
.venv/bin/python -m unittest tests.test_build_task_plan_lifecycle tests.test_build_task_plan_regenerate tests.test_pending_build_task_plan_documents
.venv/bin/python -m unittest tests.test_build_task_plan_recovery tests.test_agent_file_documents
.venv/bin/python -m unittest tests.test_build_task_planner tests.test_build_unit_skeleton tests.test_prepare_build_tasks_guard tests.test_build_dag_v3_contract tests.test_page_build_context_resolver
.venv/bin/python -m unittest tests.test_application_planning_interrupts tests.test_application_planning_structured_actions tests.test_development_continuation tests.test_development_continuation_stream
```

后三组分别是完整 R-PERSIST、R-PLAN 和 R-WORKFLOW。新增测试包括八项任务要求、
无基线/已有基线、缺失/非法文件、严格验证次序、伪造有效 DAG、旧请求不删新草稿、
同进程重复确认，以及真实 T6.4 → T7.2 → T7.3 集成。

### 本次执行结果（2026-09-06）

- 新增 16 项 Confirm 测试及既有 13 项 Pending 测试：29/29 通过。
- R-PERSIST：17/17 通过；R-PLAN：116/116 通过。
- R-WORKFLOW：16 项中 11 项通过、2 failures、3 errors。最终三组联合执行
  149 项，144 项通过，仍为相同的 5 项失败。
- 改动 Python 文件的 `py_compile` 与 `git diff --check` 通过。
- 无前端修改，未运行 frontend build 或 Electron UI 验证。

失败测试均在未修改的 HEAD `c9170ab2f7e91be1955e08fc8df2f6c139c22931`
临时副本中以相同 Python 3.13.9 虚拟环境和原环境配置复现；未复制或输出环境密钥。
HEAD 的完整 R-WORKFLOW 也为 16 项、相同的 2 failures / 3 errors：

- `test_confirm_resumes_exact_pending_review`：requirement_spec 配合
  requirement_document_confirmation 被拒绝 confirm。
- `test_same_artifact_can_be_revised_twice_before_confirmation`：相同模式组合被拒绝 revise。
- `test_identical_resumes_are_serialized_and_only_one_reaches_downstream`：等待下游进入超时。
- `test_ag_ui_runtime_uses_command_resume`：实际 remains requires_user_input，期望 completed。
- `test_answer_is_rejected_on_confirmation_card`：product_plan 在 schema 层已被拒绝，
  与测试期望的 action=answer 错误不一致。

这些是现有需求确认流程的问题，本 Task 未修改相关生产行为或测试断言。

`curl -sS http://127.0.0.1:8000/health` 失败（连接不上）。启动脚本
`./scripts/start-backend.sh` 依赖的 python3.12 不在当前环境；使用现有
`Backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
可完成应用初始化，但沙箱禁止绑定端口。解除沙箱限制的启动申请被自动审批拒绝，
返回原因为审批服务账户用量限制。未修改启动脚本或绕过该限制，健康检查尚未完成。
因此本次不宣称全部验收通过，也不进入下一 Task。
