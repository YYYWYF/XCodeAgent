# DAG Unit Candidate 任务生成优化

# 最终技术方案设计稿

## 1. 背景与目标

当前 DAG Task Planning 的主要问题是：

* 一个 Scope 中多个 `planning_unit_ids` 一次性交给模型；
* 模型返回大规模扁平 `tasks[]`；
* 单次输出长，容易截断；
* 一个局部错误会触发整个 Scope 重生成；
* 已经正确的内容重复生成；
* 用户只能看到笼统的“生成中”；
* 当前 Scope merge 仍建立在“某些 Unit 整体替换历史 Task”的旧假设上。

本次改造的核心目标：

> 将 Scope 级整批 Task Generation 重构为 **Unit 级独立 Candidate Generation + Local Validation + Local Retry + 有限并发**；Scope 继续承担完整 DAG 的 Assembly、Global Validation 与提交。

同时完善：

```text
PlanningRun
PendingPlan
ConfirmedPlan
Scheduler
Progress
Confirmation
```

完整生命周期。

---

# 2. 核心边界

## 2.1 Unit：生成隔离边界

Unit 是当前 PlanningRun 中：

```text
Generation
Local Validation
Local Retry
Failure Isolation
```

的最小边界。

不进一步拆成 Task 级生成或 Task 级 Retry。

三个概念必须严格区分：

```text
Unit Skeleton Node
= Unit Graph 中结构节点

UnitCandidate
= 当前 PlanningRun 对一个 Unit 新产生的 Task 增量

build_units[unit_id]
= Scope Assembly 后累计 DAG 中该 Unit 的全部 Task
```

因此：

> “重新生成整个 Unit”只代表重新生成**当前 PlanningRun 的 UnitCandidate**，绝不代表删除上一份 confirmed DAG 中这个 Unit 的所有历史 Task。

---

## 2.2 Scope：一致性和提交边界

Scope 是一次业务目标对应的完整 DAG 规划范围。

```text
Unit = Candidate Isolation Boundary

Scope = Consistency / Commit Boundary
```

只有：

```text
所有必需 Candidate 稳定
+
复用事实有效
+
Scope Assembly 成功
+
Global Validation 成功
```

后，才能产生 PendingPlan。

---

# 3. 正式数据生命周期

系统具有三个明确阶段：

```text
ConfirmedPlan
build-task-plan.json
        │
        │ read-only baseline
        ▼
PlanningRun
planning-run.json + runtime
        │
        │ generation success
        ▼
PendingPlan
build-task-plan.pending.json
        │
        │ user confirm
        ▼
ConfirmedPlan
build-task-plan.json
```

核心 invariant：

1. 下一 PlanningRun 只读取 ConfirmedPlan。
2. PendingPlan 永远不能成为下一 Run baseline。
3. Build 永远只读取 ConfirmedPlan。
4. LangGraph checkpoint 只是 Workflow projection，不是正式 DAG 权威。
5. PlanningRun 成功写 Pending 后即可销毁。
6. 未经用户确认不得修改正式 `build-task-plan.json`。

---

# 4. 总体流程

```text
读取正式输入 + ConfirmedPlan
        ↓
Pre-generation Gate
        ↓
Unit Skeleton
        ↓
Build Execution Scope
        ↓
required_unit_ids
        ↓
ReuseFacts
        ↓
generation_requirements_by_unit
        ↓
planning_unit_ids
        ↓
Frozen UnitGenerationContext
        ↓
PlanningRun
        ↓
Unit Scheduler
        ↓
Candidate Generation
        ↓
Local Validation
        ↓
Local Retry
        ↓
Barrier
        ↓
Global Candidate Completeness
        │
        ├── 缺项且可修复 ────────────┐
        │                           │
        ▼                           │
Scope Assembly                     │
        ↓                           │
Global Validation                  │
        │                           │
        ├── 可归因可修复 ───────────┤
        │                           │
        │                  Global Repair
        │                           ↓
        │                  affected Units only
        │                           │
        └───────────────────────────┘
        ↓
Global success
        ↓
PendingPlan
        ↓
┌──────────┬────────────┬────────────┐
│ Confirm  │ Abandon    │ Regenerate │
└────┬─────┴─────┬──────┴──────┬─────┘
     ↓           ↓             ↓
 Confirmed     Delete       New PlanningRun
```

---

# 5. Unit 分类

## 5.1 Structural Unit

```text
application:root
app:integration
```

规则：

```text
generatable = false
participation = structural_only
generation_status = not_required
```

只用于：

* Unit Graph；
* Scope structure；
* DAG 编译。

Task 不允许归属 Structural Unit。

---

# 5.2 `frontend:shell`

最新正式定义：

> `frontend:shell` 是一个 **frontend template/application shell prerequisite capability**，而不是需要生成 Task 的工作单元。

职责：

```text
证明 frontend application shell 已由模板阶段正确准备
```

它不负责：

```text
修复模板
生成页面 placeholder
更新 menu
生成 route
修改 layout
创建 provider
```

上述已有平台或模板职责不搬进 shell。

正式定义：

```text
unit_id = frontend:shell

generation_strategy = prerequisite_only

participation = prerequisite_only

generation_status = not_required

produces_task = false

provides:
    frontend.shell.ready
```

完成依据：

```text
template generation readiness
+
workspace/template prerequisite gate
```

如果 template readiness 不满足：

```text
Pre-generation Failure
```

而不是创建 shell Task 修复。

Page Unit Graph 可以继续依赖：

```text
frontend:shell
```

表达架构前置关系。

因为 shell 不产生 Task，所以不会制造无意义的 Build Task dependency。

---

# 5.3 `frontend:api-client`

仍然属于共享 Capability Unit。

允许：

```text
历史 adapter Task
历史 user API Task
+
本轮 order API Candidate
```

因此可能：

```text
participation = reuse_and_generate
```

不能因为 Unit 已有 Task 就整体复用，也不能因为本轮需要生成就删除历史任务。

---

# 5.4 `frontend:auth-guard`

正式职责：

> 将当前 confirmed authorization design 中确定的**完整资源点目录**物化到：

```text
frontend/src/constants/resources.ts
```

并为需要引用当前资源目录的 Page 提供前置 capability。

它不负责：

```text
routes.tsx
Page implementation
Backend AuthConstants
Endpoint authorization implementation
```

---

## auth-guard 是 deterministic Unit

```text
generation_strategy = deterministic
```

不调用 LLM。

平台已经可以根据 authorization manifest 确定性编译完整 resource catalog，因此没有必要让模型重新推断资源点。

---

# 6. auth-guard Resource Identity

根据当前 confirmed authorization manifest：

```text
compile resource catalog
        ↓
canonical representation
        ↓
SHA-256
        ↓
resource_catalog_fingerprint
```

形成 capability：

```text
frontend.auth.resources:<fingerprint>
```

例如：

```text
frontend.auth.resources:8a91f...
```

该 fingerprint 表达：

> 当前完整资源目录的版本身份。

---

# 7. auth-guard 生成判断

假设当前资源目录 fingerprint 为：

```text
R2
```

判断顺序：

```text
已有 confirmed Task provides R2？
        │
       Yes
        ↓
reuse confirmed Task

        No
        ↓

workspace resources.ts 已精确等于 R2？
        │
       Yes
        ↓
external capability reuse

        No
        ↓
generate deterministic Candidate
```

因此有三种情况。

---

## 7.1 Confirmed Task reuse

存在：

```text
task-auth-resources-R2

provides:
    frontend.auth.resources:R2
```

则：

```text
participation = reuse_only
```

无论该 Task 当前执行状态是：

```text
pending
failed
completed
```

Planning 都不重复生成相同职责。

执行状态由 Build 负责。

---

## 7.2 Workspace external reuse

如果：

```text
resources.ts
==
expected resources projection R2
```

即使正式 DAG 中没有对应 Task，也可以记录：

```text
external capability:
frontend.auth.resources:R2
```

无需为了“留痕”创建一个虚假 Task。

---

## 7.3 Deterministic Candidate

如果：

```text
没有 confirmed R2 provider
AND
workspace 不满足 R2
```

生成：

```text
frontend:auth-guard
└── task-sync-auth-resources-R2
```

---

# 8. auth-guard Task Contract

建议：

```text
id:
    frontend-auth-resources-<fingerprint-short>

unit_id:
    frontend:auth-guard

owner:
    frontend

task_type:
    authorization_resource_projection

execution_strategy:
    deterministic

platform_executor:
    authorization.frontend_resources

target_files:
    frontend/src/constants/resources.ts

provides:
    frontend.auth.resources:<fingerprint>

source_refs:
    authorization_manifest
    resource_catalog_fingerprint
```

acceptance：

```text
resources.ts
必须与当前 confirmed authorization resource projection 完全一致
```

---

# 9. auth-guard 执行职责

Planning：

```text
平台 deterministic Candidate
```

Build Execution：

```text
平台 deterministic executor
```

Validation：

```text
平台 deterministic validation
```

LLM 不参与。

因此 Task Contract 中：

```text
owner
```

表示代码领域；

```text
execution_strategy
```

表示实际执行机制。

不能通过新增：

```text
owner = platform
```

混淆业务 ownership 与 executor。

---

# 10. Authorization Projection 职责拆分

当前 frontend authorization projection 同时管理：

```text
resources.ts
routes.tsx
```

目标拆成：

```text
Frontend Authorization Projection
├── resources projection
└── routes projection
```

最终职责：

```text
resources.ts
→ frontend:auth-guard deterministic Task

routes.tsx
→ platform-managed projection

Backend AuthConstants
→ platform-managed projection
```

Build 启动前的 platform projection 不再提前写：

```text
resources.ts
```

否则 auth-guard DAG Task 会失去意义。

这属于本次唯一允许的小范围 Build execution extension，不扩大为通用 Build Scheduler 重构。

---

# 11. Page → auth capability dependency

历史 auth Tasks 采用 append-only 保留：

```text
auth-guard
├── task-R1
└── task-R2
```

新 Page 如果要求：

```text
frontend.auth.resources:R2
```

不得依赖 auth Unit 中全部历史 Task。

必须精确解析：

```text
required capability R2
        ↓
find provider task for R2
        ↓
Page depends_on task-R2
```

如果 workspace 已经 external-satisfied R2：

```text
不创建 Task dependency
```

因此 auth-guard 是第一版需要支持的：

> **versioned shared capability dependency**

普通 Unit 的 cross-unit dependency precision 暂时不全面重构。

---

# 12. Required / Reuse / Generation

必须保持三层区别。

```text
required_unit_ids
```

当前 Scope 构成完整 DAG 所需的 Units。

```text
reuse_facts
```

历史 confirmed Task / capability / owner 等确定性可复用事实。

```text
generation_requirements_by_unit
```

每个 Unit 当前还缺少的新增职责。

```text
planning_unit_ids
```

最终需要生成 Candidate 的 generatable Units。

不能使用：

```text
required units - units with existing tasks
```

简单计算。

---

# 13. Generation Strategy

每个 Unit 明确：

```text
structural_only
prerequisite_only
reuse_only
deterministic
model
```

说明：

```text
structural_only
→ DAG structure only

prerequisite_only
→ 有正式前置能力但无 Task，如 frontend:shell

reuse_only
→ 当前需求完全由 existing facts 满足

deterministic
→ 平台产生 Candidate

model
→ LLM Unit generation
```

---

# 14. UnitGenerationContext

```text
UnitGenerationContext
├── planning_run_id
├── build_execution_scope
├── unit_id
├── unit_kind
├── input_fingerprint
├── base_confirmed_plan_digest
│
├── generation_requirements
│
├── formal_contracts
│   ├── inline_slices
│   └── frozen_catalog_refs
│
├── workspace_context
│   ├── workspace_snapshot identity
│   ├── relevant paths
│   ├── template variant
│   └── architecture facts
│
├── dependency_context
│   ├── dependency_unit_ids
│   ├── dependency_capabilities
│   ├── retained_task_summaries
│   ├── retained owner constraints
│   └── unit_graph_slice
│
└── constraints
    ├── owner
    ├── managed files
    ├── authorization constraints
    └── strong rules
```

Context 属于冻结业务输入。

同一 PlanningRun 重试不能重新读取变化后的正式输入。

---

# 15. UnitGenerationPolicy

运行策略独立：

```text
UnitGenerationPolicy
├── local_max_attempts
├── model_max_retries
├── model_max_tokens
├── request_timeout
├── unit_session_timeout
├── model_turn_limit
└── frozen_contract_read_limits
```

不能把 Retry / timeout 等运行策略塞进 UnitGenerationContext。

---

# 16. DAG 专属配置

新增：

```text
XCODEAGENT_DAG_UNIT_MAX_TOKENS=4096
```

Settings：

```text
dag_unit_max_tokens = 4096
```

Model factory 支持：

```text
max_tokens_override
max_retries_override
timeout_seconds_override
```

DAG model generation：

```text
max_tokens_override
    = settings.dag_unit_max_tokens

max_retries_override
    = 0
```

不会修改其他 Agent 使用的：

```text
AGENT_MAX_TOKENS
MODEL_MAX_RETRIES
```

已确认：

```text
Unit concurrency = 3
Local attempts = 3
Global repair rounds = 2
SDK infrastructure retry = 0
```

以下属于实现保护参数：

```text
Unit session timeout
contract read count
contract accumulated size
model turn limit
```

必须可配置，但具体默认值在实现和压测阶段确定。

---

# 17. UnitCandidate 模型响应

模型最终只返回：

```json
{
  "tasks": []
}
```

平台负责 Candidate metadata。

模型不返回：

```text
PlanningRun metadata
Scope DAG
workspace_analysis
Candidate status
Global issue
platform compilation data
```

Task ID 继续由模型生成。

平台禁止：

```text
自动补 Task ID
自动修改 owner
重复 ID 自动 rename
静默 drop 非法 Task
exact duplicate silent merge
```

---

# 18. Candidate Dependency

Candidate Task 可以引用：

```text
1. 当前 Candidate 内 Task
2. UnitGenerationContext 显式提供的同 Unit retained Task
```

禁止引用：

```text
另一个并发 Candidate
跨 Unit Task ID
Unit ID
未知 Task
```

例如：

```text
frontend:api-client

retained:
task-response-adapter

candidate:
task-order-api
    depends_on = task-response-adapter
```

允许。

跨 Unit dependency 仍由平台编译。

---

# 19. CandidateAttempt

```text
CandidateAttempt
├── candidate_id
├── planning_run_id
├── unit_id
├── generation_round
├── attempt_in_round
├── input_fingerprint
├── status
│   ├── valid
│   ├── invalid
│   └── superseded
├── tasks[]
├── validation_issues[]
└── generation_metadata
```

其中：

```text
Task.id
→ 模型

CandidateAttempt.candidate_id
→ 平台
```

---

# 20. ValidationIssue

```text
ValidationIssue
├── code
├── level
├── category
├── unit_ids[]
├── task_ids[]
├── retry_unit_ids[]
├── retryable
├── message
└── details
```

level：

```text
pre_generation
unit
global
system
```

category：

```text
input
generation
platform
infrastructure
persistence
```

核心原则：

```text
unit_ids != retry_unit_ids
```

问题涉及某 Unit，不表示它必须重新生成。

---

# 21. Validation 分层

## Pre-generation

检查：

```text
formal artifacts
TechnicalPlan freshness
workspace/template readiness
Unit Skeleton
confirmed baseline
Frozen Context
```

失败直接阻断。

---

## Unit Local

检查：

```text
raw JSON
Task schema
ID
owner / unit
generation requirements
deliverables
change_scope
managed files
same-unit dependencies
same-unit cycles
retained ownership conflicts
strong rules
```

内容错误：

```text
retry current Unit
```

平台/input 错误：

```text
fail PlanningRun
```

---

## Global

先检查 Candidate completeness。

齐全后：

```text
Scope Assembly
ID collision
endpoint ownership
auth capability provider
cross-unit dependency
full DAG cycle
required-unit completeness
global contracts
```

---

# 22. Retry

## Local

```text
U = 3
```

表示：

> 一个 Unit、一个 generation round 中，最多三次完整 Candidate generation attempts，含首次。

---

## Global

```text
G = 2
```

第一次 Global Check 不消耗额度。

修复时：

```text
聚合 retry_unit_ids
↓
global_repair_round += 1
↓
affected Candidate superseded
↓
affected Units 开新 generation round
↓
每个 Unit 重新获得 Local=3
```

一个 Unit 极端最多：

```text
(2 + 1) × 3 = 9
```

次内容 Generation Sessions。

---

# 23. Generation Session

正确单位：

```text
1 Unit Local Attempt
=
1 Unit Generation Session
```

并不保证：

```text
1 Session = 1 HTTP Request
```

以后如果需要 FrozenContractReader：

```text
Model turn
↓
contract read
↓
Model turn
↓
contract read
↓
Model final tasks[]
```

仍然属于一个 Local attempt。

---

# 24. Infrastructure Failure

DAG SDK infrastructure retry：

```text
0
```

以下直接结束 PlanningRun：

```text
HTTP / connection error
429
5xx
authentication/config error
provider timeout
Unit session infrastructure timeout
```

处理：

```text
PlanningRun.failed
↓
stop dispatch
↓
best-effort cancel active jobs
↓
reject late results
```

不消耗 Local / Global 内容修复预算。

如果 Provider 正常响应，但：

```text
finish_reason=length
JSON incomplete
Candidate invalid
```

属于内容失败，消耗 Local attempt。

---

# 25. FrozenContractReader

大合同通过冻结的受控接口读取：

```text
read_frozen_contract_fragment(
    ref_id,
    selector,
    cursor?
)
```

必须限制：

```text
Unit allowlist
selector
read count
accumulated size
model turns
```

不开放：

```text
任意 workspace read_file
实时 formal artifact
其他 Unit Candidate
```

具体上限由实现阶段压测决定。

---

# 26. Scope Assembly

第一版是：

> **Append-only cumulative DAG**

设：

```text
B = all confirmed baseline Tasks
C = current valid Candidate Tasks
```

则：

```text
A = B ∪ C
```

第一版没有正常业务路径删除 confirmed Task。

明确延期：

```text
confirmed Task replacement
Task input invalidation propagation
historical Task automatic removal
```

---

# 27. Assembly ID 规则

registry 建立前必须检查：

```text
baseline duplicate
Candidate vs retained
Candidate vs Candidate
```

Candidate 撞 retained：

```text
Candidate invalid / retry attributable Unit
```

禁止：

```text
覆盖 retained
自动 rename
推断 replacement
```

---

# 28. Scope Assembly 编译顺序

```text
deepcopy confirmed Tasks
↓
collect current valid Candidates
↓
origin / ID validation
↓
retained + candidate
↓
compile Unit metadata
↓
compile cross-unit dependencies
↓
compile current capability dependency
↓
compile Candidate acceptance
↓
rebuild build_units
↓
rebuild task_registry
↓
rebuild task_graph
↓
execution batches
↓
Global Validation
```

retained Task 的业务与历史 acceptance contract 保留。

平台派生的：

```text
dependency graph
unit_dependencies
task_graph
execution batches
```

允许针对累计 DAG 重新计算。

---

# 29. PlanningRun

```text
PlanningRun
├── planning_run_id
├── workflow_run_id
├── thread_id
├── revision
├── status
├── phase
├── build_execution_scope
├── input_fingerprint
├── base_confirmed_plan_digest
├── required_unit_ids[]
├── planning_unit_ids[]
├── global_repair_round
├── global_repair_limit
├── global_issues[]
├── unit_states{}
├── started_at
├── updated_at
└── failure
```

status：

```text
active
failed
cancelled
```

phase：

```text
preparing
generating_units
global_check
assembling
validating
persisting_pending
```

等待确认不属于 PlanningRun。

---

# 30. UnitRunState

```text
UnitRunState
├── unit_id
├── kind
├── participation
├── generation_status
├── generation_round
├── attempt_in_round
├── total_attempts
├── retained_task_ids[]
├── reusable_capabilities[]
├── latest_candidate_id
├── candidate_task_count
├── current_issues[]
└── round_history[]
```

participation：

```text
reuse_only
generate_only
reuse_and_generate
prerequisite_only
structural_only
```

其中：

```text
frontend:shell
→ prerequisite_only

application:root / app:integration
→ structural_only
```

generation_status：

```text
not_required
pending
generating
validating
candidate_ready
round_exhausted
aborted
```

---

# 31. Candidate Supersede

Global 要求重新生成 Unit：

```text
old Candidate
valid → superseded

latest_candidate_id = null

generation_round += 1
attempt_in_round = 0
generation_status = pending
```

新一轮失败：

```text
不得恢复 superseded Candidate
```

---

# 32. PlanningRun 存储

Backend memory：

```text
Frozen Context
full Candidate
raw model result
async tasks
Semaphore
cancel state
attempt registry
```

临时文件：

```text
.xcodeagent/plans/planning-run.json
```

只保存轻量：

```text
identity
status
phase
revision
Unit states
rounds
issues
diagnostics
```

Backend 重启后：

```text
disk Run active
+
runtime missing

→ planning_run_interrupted
→ failed
```

不恢复 Scheduler。

---

# 33. 单写者

并发：

```text
Model Workers
```

串行：

```text
PlanningRunController
```

所有 state transition：

```text
validate
↓
apply
↓
revision += 1
↓
atomic persist
↓
progress snapshot
```

Worker 不直接写 `planning-run.json`。

---

# 34. Unit Scheduler

默认：

```text
concurrency = 3
```

Unit Graph dependency **不决定 Candidate generation 顺序**。

调度单位：

```text
UnitAttemptJob
```

失败后下一 Local attempt 重新进入队尾，避免一个快速失败 Unit 独占 Worker。

Generation round 的所有 Unit 到达：

```text
candidate_ready
OR
round_exhausted
```

后才通过 Barrier。

---

# 35. Attempt Identity

```text
planning_run_id
unit_id
generation_round
attempt_in_round
attempt_id
```

结果写状态前必须验证：

```text
Run still active
AND
attempt_id still expected
```

否则：

```text
discard
```

适用于：

```text
Cancel late response
Global superseded response
old generation round result
fatal Run result
```

---

# 36. PendingPlan

路径：

```text
.xcodeagent/plans/build-task-plan.pending.json
```

身份：

```text
DraftIdentity
├── planning_run_id
├── draft_digest
├── base_confirmed_plan_digest
├── input_fingerprint
├── build_execution_scope
└── created_at
```

`draft_digest` 对去除自身字段后的 canonical PendingPlan 计算。

Pending 成功写入：

```text
delete planning-run.json
```

---

# 37. Confirm

请求必须带：

```text
action = confirm
planning_run_id
draft_digest
```

Backend：

```text
load current Pending
↓
request identity
↓
Pending self digest
↓
base Confirmed digest
↓
input freshness
↓
DAG gate
↓
construct ConfirmedPlan
↓
atomic formal replace
↓
delete Pending
```

Formal 保存：

```text
confirmed_from
├── planning_run_id
└── draft_digest
```

用于重复确认和 crash recovery。

---

# 38. Abandon / Regenerate

Abandon：

```text
verify identity
↓
delete matching Pending
↓
Formal unchanged
```

Regenerate：

```text
verify Pending
↓
delete Pending
↓
load current ConfirmedPlan
↓
create new PlanningRun
```

旧 Candidate / Pending 不恢复。

---

# 39. Cancel

Cancel 只针对 active PlanningRun。

行为：

```text
stop dispatch
stop Local requeue
best-effort cancel active sessions
PlanningRun.cancelled
reject late results
```

Pending 阶段对应的是：

```text
Abandon
```

二者不得混淆。

---

# 40. Progress

继续使用：

```text
prepare_build_tasks.progress
```

发送完整 Snapshot。

```text
DagGenerationSnapshot
├── schemaVersion
├── planningRunId
├── revision
├── status
├── phase
├── globalRepairRound
├── globalRepairLimit
├── units[]
├── globalIssues[]
├── summary
└── artifacts[]
```

UI 不展示虚假百分比。

推荐：

```text
3 / 5 Unit 已就绪

frontend:api-client
✓ 保留 2，新增 1

page:user-list
⟳ 校验中 · 2/3

Global Repair
1 / 2
```

---

# 41. Build 读取契约

Build：

```text
ONLY
build-task-plan.json
AND
confirmation_status == confirmed
```

绝不能读取：

```text
PendingPlan
planning-run.json
checkpoint candidate
```

`frontend:auth-guard` 增加的 deterministic executor 是局部 Task execution capability，不改变 Build Scheduler 的整体任务依赖和 batch 模型。

---

# 42. 第一版明确延期

```text
Task-level generation / retry
Cross-run Candidate cache
Task input hash
自动失效传播
Confirmed Task replacement
Shared Unit multi-Run versioning
General Task-level cross-unit dependency precision
Build Scheduler general redesign
Semantic review model
frontend:data 全面重构
backend:bootstrap 多数据源专项
```

---

# 43. 最终架构原则

> 每个 PlanningRun 以上一份 confirmed DAG 为唯一只读历史基线。平台根据 Scope、Unit Skeleton、正式输入及确定性 ReuseFacts 计算本轮 Unit generation requirements。`frontend:shell` 仅作为模板前置能力存在，不生成 Task；`frontend:auth-guard` 根据当前 authorization resource fingerprint 判断 reuse、workspace satisfied 或 deterministic Candidate，并通过确定性 executor 物化 `resources.ts`。其他需生成 Unit 只依赖冻结的 UnitGenerationContext 独立产生 Candidate。Unit 是 Local generation / validation / retry 边界；Local 每轮最多 3 次，Global 最多 2 轮，只重新打开明确归因的 Unit。有效 Candidate 与所有 confirmed Tasks 通过 append-only Scope Assembly 组成累计 DAG。完整 DAG 通过 Global Validation 后只写 PendingPlan，用户确认精确 DraftIdentity 后才原子提升为正式 DAG。模型 infrastructure retry 为 0；并发、取消和 supersede 通过 PlanningRun 状态与 AttemptIdentity 保证结果隔离。Build 永远只消费 confirmed DAG。
