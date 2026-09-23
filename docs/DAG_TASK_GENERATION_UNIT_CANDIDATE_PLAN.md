# DAG Unit Candidate 任务生成优化

# 最终技术方案设计稿

## 1. 背景与目标

当前 DAG Task Planning 的主要问题是：

| 议题 | 当前进度 | 还需要明确的内容 |
| --- | --- | --- |
| 1. UnitGenerationContext | 输入范围、来源与切片主线已讨论；必需合同提供途径、冻结输入和 UI 代码不进入规划已确认。 | 公共／专属输入字段表最后统一对齐，尤其生成范围、资源清单与正式来源的表达；不重新讨论输入范围。冻结材料的工具签名、读取限额及上下文管理归第 10 项，持久化引用归第 8 项。 |
| 2. 各类 Unit 生成与复用 | Endpoint、static、Page、adapter 方向已确认；权限资源归平台投影；shell 前置检查／复用边界已明确。 | 文档仍有一个具体尾项：shell 无可复用任务且确需贡献时，原有规划职责如何落成具体任务与输入。只沿用现有职责，不增加自动修复。bootstrap 具体数据源配置判断继续延期；同 Unit 追加／替换归第 7 项。 |
| 3. UnitCandidate 与模型响应 | 已确认，可以收口。 | 状态枚举及存储归第 8 项，具体校验接入沿第 4、5 项落实；不重新讨论模型响应与整 Unit 重生成边界。 |
| 4. 现有校验盘点与分层 | 已完成代码盘点并讨论分层；Local／Global 与输入／平台原因的区分已由后续议题进一步明确。 | 分层表与第 5 项已确认归因规则统一复核；原始响应严格检查、平台投影路径保护、环路定位等属于已识别的实施适配，不再另开通用校验设计。 |
| 5. 结构化错误与归因 | 已确认，可以收口。 | 无独立新议题。Task 替换相关归因要引用第 7 项最终规则，错误与状态的保存归第 8 项。 |
| 6. 重试次数与失败处理 | 已确认，可以收口：Local 每轮 3 次总尝试、Global 2 轮修复、production DAG 的 SDK 基础设施重试 2 次（UnitGenerationPolicy 默认仍为 0）；Global 与 Local 反馈显式分区；SDK 重试耗尽后的基础设施故障结束 Run，由上层人工发起新 Run。 | 无独立新议题。具体轮次／失败结果存储归第 8 项，调用中断、收尾时限和事件格式归第 10 项。 |
| 7. Scope Assembly | 串行合并、历史只读、Candidate 仅本轮贡献已确定，细则尚未展开。 | 明确保留／新增／显式替换的 Task 集合；共享 Unit 保留旧 Task 并追加新 Task；ID 碰撞与合法替换的区分；替换后的依赖引用、跨 Unit／保留任务依赖及验收编译。不能照搬当前整个 Unit 替换。 |
| 8. PlanningRun / Unit / Candidate 状态与存储 | 身份与生命周期职责已区分，具体结构尚未定稿。 | 状态及转换；Unit 本轮耗尽与 Run 最终失败分别表达；生成轮次和局部次数；同 Unit 同时复用与生成的状态；内存／持久化分工；刷新后的进度和失败详情。基础设施故障按上层新 Run 方向处理，不额外设计 PlanningRun 内人工暂停／继续。 |
| 9. 草稿确认与正式 DAG 提升 | 草稿与正式文件隔离、确认同一份 DAG 后原子提升已确定。 | 最终路径与草稿身份绑定、确认时精确定位、防止陈旧确认、原子替换和写入失败处理；取消／重新生成对草稿的处理；确认界面与 Build 读取与门禁契约。 |
| 10. Unit Scheduler 与进度交互 | 有限并发只用于 Candidate 生成，沿用 AG-UI 已确定。 | 并发数与模型调用方式；请求／Unit 会话超时、调用／读取上限和上下文管理；取消及迟到结果隔离；Unit 重试与 Global 缺项／校验事件；基础设施错误的上层重新生成动作、进度与失败展示。 |

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
5. PlanningRun 成功写 Pending 后即失去执行权威；Pending 成为待确认权威。允许保留轻量 PlanningRun 投影用于刷新恢复、身份校验和清理，但不得据此继续生成。
6. 未经用户确认不得修改正式 `build-task-plan.json`。
7. 同一应用任一时刻最多存在一个 active PlanningRun 或一个 PendingPlan；不同 Scope 不得并行生成或等待确认。
8. Abandon 结束本次 Workflow execution，但保留聊天记录和已有 ConfirmedPlan。
9. Regenerate 删除旧 Pending 后创建全新 PlanningRun；后续失败不恢复旧 Pending。

---

# 4. 总体流程

生产入口由 `graph/nodes/task_planning_adapter.py` 统一组装服务端正式输入。输入只来自已确认的 ProductPlan、TechnicalPlan、运行时 PageImplementationContract、TechnicalPlan API Contract、当前有效的 Endpoint API Design，以及按页面／应用裁剪的 authorization slice；Endpoint 设计通过 `api_contract_id + endpoint_id + artifact_revision` 绑定来源。`build_context.endpoint_designs` 会在进入 PlanningRun 前转换为 Frozen Store 中的 `endpoint_api_design`，Unit 只通过 allowlisted `contract_catalog` 和 FrozenContractReader 按需读取，不接收完整合同正文。

EntitySourceBinding 仍可作为独立旧流程使用，但不再是 DAG Planning 的正式输入、就绪门禁或 Build Context 来源。页面 Scope 使用其全部 `requiredEndpointIds` 对应的已确认 Endpoint API Design；Endpoint Scope 只使用自身复合身份。缺失、过期、双文件不一致或缺少 `artifactRevision` 的设计都在 Pre-generation Gate 阻断，不能由模型补全。

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

- 属于应用级共享能力；
- Candidate 可以由某个 Scope 触发生成，但只包含本轮新增或替换的任务；
- 但正式生效必须等待当前 Scope Commit；
- 生效后进入应用累计 DAG，供后续 Scope 复用。

本设计采用以下外部前提：

> 同一项目不会同时存在两个 Scope Planning Run。

该约束由 PlanningRun 上游生命周期负责实现，不属于本轮任务生成改造。基于该前提，本轮暂时不解决 Shared Unit 多 Scope 并发版本问题。

但后续仍需要单独分析：

- 共享能力不足时如何重新打开 Unit；
- 共享权限资源属于 Build 后平台投影，不作为 UnitCandidate 待定输入或复用议题；
- `frontend:api-client` 生命周期是否需要进一步拆分。

### shell 当前讨论边界：前置检查后复用或生成

- 先沿用现有工作区与模板就绪检查；检查发现异常时，按前置条件不满足处理并停止任务生成。本轮不增加菜单或页面入口的自动补齐，也不增加其他工作区自动修复。
- 检查通过后，判断上一份 confirmed DAG 中的 shell 任务能否复用；已选定保留的任务不因尚未执行而重生成，原执行状态保持不变。
- 无可复用任务、需要本轮贡献时，保留按原有任务规划路径生成 shell Candidate 的可能性，不将 shell 预先限定为永不生成任务的 Unit。
- 新 shell 任务的具体职责、所需输入和生成条件尚未明确，后续继续分析；不能把前置检查异常转换成 shell 修复任务，也不为保证非空 Candidate 而虚构检查任务或框架修改职责。

### 权限资源由 Build 后的平台投影统一写入

本节原资源注入 Task 设计已被 BOOTSTRAP_PLAN.md 取代。UnitCandidate 仍用于业务实现隔离，但不构造权限资源注入任务，不为该任务增加 Page 前置依赖。

规划冻结 TemplateState 的 template_context，并从确认的 authorization_manifest 编译只读权限切片。通用 routes、可选资源目录与 AuthConstants 均在所有 Build 任务成功后由平台重放；Candidate、Scope Assembly 和普通 Agent 不能写这些共享文件。资源映射可以复用既有服务；重放与验收使用同一确认快照。

### 已确认延期：bootstrap 的数据源配置判断

`backend:bootstrap` 的具体数据源配置判断尚未设计，本轮暂不讨论或实现，包括具体连接配置是否可复用、是否需要新增配置，以及不同数据库连接的配置处理。不将这些判断作为本轮按 Unit 拆分任务生成的前置要求，也不将此前关于这些判断的建议视为已确认规则；现有实现不因此被认定为已支持多数据源配置。

---

# 5.3 `frontend:api-client`

仍然属于共享 Capability Unit。

当前 Scope 只要包含正式后端 Endpoint，就必须为其计算前端 API 调用职责；该判断与
Endpoint 后端内部是否使用 database、external_api 或纯业务逻辑无关。物理数据来源只影响
`backend:bootstrap` 及 Endpoint 后端来源分支，不能用于跳过前端 API Client。

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

不能用“所需 Unit 减去已有任务的 Unit”直接计算待生成集合；共享 Unit 可同时包含保留任务和新贡献。权限资源投影不进入 Candidate 或模型调用列表。当前 _replaceable_unit_ids 仅作为计算起点；bootstrap 数据源配置判断仍延期。

当前：

```text
Scope
    ↓
多个 planning_unit_ids
    ↓
一个 combined prompt
    ↓
一个大 tasks[]
```

目标：

```text
Scope
    ↓
planning_unit_ids
    ↓
多个 UnitGenerationContext
    ↓
多个 Unit Generation Request
```

例如：

```text
frontend:api-client
        ↓
Model Request A

backend:bootstrap
        ↓
Model Request B

backend:endpoint:user:list
        ↓
Model Request C

page:user-list
        ↓
Model Request D
```

这些调用由统一：

```text
Unit Generation Scheduler
```

管理。

默认采用：

```text
bounded concurrency
```

而不是无限并发。

具体并发数暂不在当前设计层决定。

---

# 13. Generation Strategy

每个 Unit 明确：

```text
not_required
structural_only
prerequisite_only
reuse_only
deterministic
model
```

说明：

```text
not_required
→ 当前 Scope 没有该 Unit 的适用职责，不表示复用了历史 Task

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
├── contract_catalog
│   └── [{ ref_id, kind, selectors[] }]
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

当前 `Unit.source_refs` 仍然可以继续作为：

```text
traceability metadata
```

但不要直接等同于 `UnitGenerationContext`。

## 输入清单收敛稿（输入边界已明确，字段表待最终对齐）

本节把生成前输入集中列出，供一次性审阅。表中的目标结构属于建议，不表示当前代码已经提供完整的单 Unit Context；其中 shell 前置检查、权限资源的平台投影边界、保留任务不以执行成功为前提、bootstrap 数据源配置判断延期等边界沿用本次讨论已确认的结论。生成方式是平台控制参数，不是实体的数据来源。

### 公共输入、来源与必填条件

| 输入组 | 建议内容 | 来源与必填条件 |
| --- | --- | --- |
| 身份 | `planning_run_id`、`scope_id`、`unit_id`、`unit_kind`、`base_confirmed_plan_digest` | Run／Scope 由平台创建，Unit 来自现有 Unit Skeleton；所有 Context 必填。首次没有 confirmed DAG 时，基线摘要为 null，并明确使用空基线，不拿失败或未确认计划替代。 |
| 本轮生成范围 | `generation_scope`：本轮负责的正式目标引用、需要新增的职责、不得重复生成的保留职责 | 由平台生成前计算，所有待生成 Unit 必填。Endpoint 使用 `(api_contract_id, endpoint_id)`，Page 使用 `page_id`，共享 Unit 使用本轮所需职责；Unit 只生成该范围内的任务。此字段是对概念模型的补充建议。 |
| 正式合同 | `formal_contracts`：适用的合同正文／有界结构化切片，以及对应正式来源引用；较大合同的受控读取方向见下文已确认规则 | 来自已确认正式产物，经现有运行时合同编译与绑定摘要逻辑组装。按下表条件必填；直接提供内容，或提供实际可查询读取的冻结输入引用，不能只给路径并假定模型已有文件工具。无关合同不下发。 |
| 工作区事实 | `workspace_context`：同一份 WorkspaceSnapshot 的身份及 Unit 相关切片、template_context、适用的预置文件清单和架构事实 | 快照来自 `inspect_workspace`，模板绑定来自 TemplateState，预置清单来自 `prebuilt_files_for_plan`。提供真实路径、目录、入口、已有文件等规划事实，不将路径存在等同于代码功能已满足。 |
| 依赖与保留事实 | `dependency_context`：直接依赖 Unit、相关 Unit Graph 边、必要的保留任务摘要、已有职责和 Endpoint owner 约束 | 来自 Unit Skeleton／Unit Graph 及上一份 confirmed DAG。相关集合必填，无相关记录时为空。保留任务摘要只含 ID、Unit、职责、交付物、能力、文件范围及必要正式输入引用，不要求成功执行记录，不下发历史任务全集。 |
| 约束 | `constraints`：owner、文件职责边界、适用权限切片、已有确定性唯一归属规则 | 来自现有规划规则、模板边界、权限 Overlay 和保留任务索引。仅传适用于该 Unit 的约束；不存在权限场景时明确不适用，不伪造权限事实。 |
| 生成控制 | `generation_policy`：固定规则或模型生成、适用 Task 字段契约及阶段规则 | 由平台选择，属于控制参数。具体重试次数与反馈结构在重试议题确定，不在正式合同输入中混入执行状态或重试决策。 |

### 各类 Unit 的专属正式输入与切片

| Unit | 需要的专属输入 | 切片与来源 |
| --- | --- | --- |
| `frontend:shell` | 前端模板／工作区就绪事实、平台选定的本轮 shell 职责、保留 shell 任务摘要 | 沿用现有前置检查与前端快照。异常作为前置条件不满足；不新增菜单、页面入口自动补齐。无可复用任务时，仅为原有规划职责提供输入，不在本节扩张 shell 职责。 |
| `frontend:api-client` | 本轮需要生成模块的 Endpoint 引用及 API Contract；固定响应适配约定；保留适配器任务及 Endpoint owner 信息 | 按 `(api_contract_id, endpoint_id)` 选择 Endpoint，保留所属契约的 schemas。同一 Contract 的本轮缺失 Endpoint 聚合为一个 Contract-level Task，并共同写入唯一 canonical `frontend/src/apis/<biz>Api.ts`；后续 PlanningRun 追加新的稳定 Task ID，不能改写 retained Task。不提供后端 Candidate 或数据库实现绑定；接口请求、响应和空响应等约定来自正式 API 合同。 |
| `frontend:auth-guard` | 有效能力及确认的权限事实 | 不构造资源注入 Candidate；共享资源由 Build 后平台投影写入，Page 只消费本页权限切片。 |
| `backend:bootstrap` | 当前 Scope 所需的后端数据来源类型及正式实体绑定引用、既有基础能力规则、后端工程路径事实、保留 bootstrap 任务职责 | 复用现有 resolver、实体摘要和后端快照裁剪。具体连接配置的复用／新增判断继续延期；本节不据此设计多数据源配置或新的缺口判定算法。 |
| `backend:endpoint:<contractId>:<endpointId>` | 当前 Endpoint 的完整实施语义、所属 API Contract 的 schema、相关实体字段及确认的数据来源绑定、当前 Endpoint 权限切片 | 固定为一个接口，包含该接口相关实体，整个 Unit 的阶段任务使用同一份输入。数据库保留表／字段映射；外部 API 只保留通过该接口引用匹配到的操作、请求响应结构和字段映射。复用 `_endpoint_context`、`entity_design_summaries` 和 `unit_authorization_slice`，不读取 bootstrap Candidate 或其他 Endpoint Candidate。 |
| static data Unit | 本轮相关 static 实体字段、确认的静态绑定摘要及其正式来源引用、对页面暴露的数据接口合同、前端模块路径事实 | 沿用现有 static 实体过滤和规划摘要；当前摘要包含种子行数与字段取值项数，不把它称为完整静态数据。具体记录供执行阶段按绑定的正式来源读取，不要求任务规划携带整批数据。沿用当前解析出的 Unit ID，本节不要求改成按 sourceId 拆分。 |
| `page:<pageId>` | 当前 PageImplementationContract 中已编译的交互行为、接口绑定、权限、导航与验收要求，消费的 API／static 接口合同，以及页面相关工作区事实 | 页面合同只保留本页；导航目标只提供 ID、路径等必要事实。消费接口保留请求／响应 schema 与正式字段绑定；不提供后端数据库／上游外部服务绑定或其他 Unit Candidate。复用页面合同编译、`_page_context`、`_scoped_pages` 与权限切片。`uiDesignRef` 可作为来源和执行引用随合同保留，但 UI Design 代码正文不列为任务生成输入，由前端执行 Agent 读取以还原页面。UI 已正式跳过时沿用现有跳过分支。 |

### 统一的切片、缺失与重试边界

- **确认进度：** 合同按目标切片并保留所属 API Contract 的完整 schemas、按适用性处理必需输入缺失、同一 Run 内重试冻结输入，以及较大必要合同的受控查询与分片读取方向均已确认。UI Design 代码仍属于执行材料。工具签名、调用上限与上下文管理细节留到生成调用机制中确定，不再以必要输入较长为由直接结束生成。
- 公共输入表示共同来源与共同结构，不表示向每个 Unit 发送完整项目。正式合同按 Unit 目标裁剪，工作区按前端／后端及所需路径裁剪；权限按现有 Page／Action／Endpoint 切片，完整资源点清单由平台投影持有，不进入普通 Task 的写入职责。
- **已确认：** API Contract 第一版沿用 `_scoped_api_contract`：裁剪 endpoints，保留所属合同 schemas，避免引用断裂。不增加递归 schema 最小化工程。
- **已确认：** 任务规划必需的语义内容必须有明确的提供途径：基础信息直接提供，较大必要合同可通过受控只读工具查询和分片读取。不能仅提供 TechnicalPlan 文件路径和 Endpoint ID，却不提供内容或实际读取途径。正式来源引用用于定位和追溯，不等于合同内容；`uiDesignRef.path / sha256` 属于可交给后续执行的设计引用，不代表规划模型必须读取该 UI 代码文件。当前无工具 ChatModel 调用仍需相应接入改造。
- **范围澄清：** UI Design 代码正文用于执行阶段的视觉还原和组件实现。Page 任务生成读取 PageImplementationContract 已编译的行为、接口、权限、导航和验收信息，不从 UI 源码重新推导任务职责。此前把长 UI 代码列为规划模型按需读取材料的建议撤回，不据此扩大 UnitGenerationContext 或引入工具调用循环。
- **已确认：** 必需的正式合同未确认、Endpoint／实体绑定无法解析、模板就绪检查失败，按现有生成前门禁停止，不让模型补造。与当前 Unit 无关的合同不要求填写；无保留任务可正常提供空集合。
- 保留职责摘要用于生成去重，不是业务代码验收。两个 Page 都消费同一 Endpoint 时，可以都取得该 Endpoint 的正式接口合同，但不会因此都取得“生成 API 模块”的职责。
- **已确认：** 同一 PlanningRun 的 Unit 生成重试保持基线、正式合同切片、工作区快照、生成范围、保留事实和规则不变。只追加本 Unit 的校验反馈／重试元信息，不读入其他 Unit Candidate。若要采用新的正式输入或重新扫描后的事实，应结束当前输入版本并开启新 Run，不将新的事实悄悄混入重试。

### 已确认：长合同的受控查询与分片读取

- 基础目标、生成范围、关键约束及合同目录直接提供给模型；较大的必要合同可通过只读工具按目标查询、按结构片段或分页读取。文件较长不直接作为终止 Unit 生成的理由，也不静默截断必需字段。
- UnitGenerationContext 可包含冻结输入引用及可读取目录。平台负责确保引用可解析、内容属于已确认的本轮输入，并限制工具只能访问当前 Unit 获准使用的内容。仅记录源文件摘要后仍读取可变化的实时文件，不等于冻结输入；应读取本轮冻结内容。
- 对结构化合同，优先按 Endpoint、schema、实体或操作标识定位具体内容；分片结果应明确来源、片段位置和后续读取位置，超出单次返回上限时明确可继续读取，不把局部结果伪装成完整合同。API Contract 的完整 schema 集合仍在可读取输入中保留，不因按需访问而新增递归 schema 裁剪。
- 分片读取发生在同一个 Unit 生成会话内，最终仍产出一个完整 UnitCandidate；不拆成 Task 级独立生成，不读取其他 Unit Candidate，也不把 UI Design 代码重新纳入规划输入。
- 分片解决按需取用问题，不代表可以把所有读取结果持续累积到上下文。需要限制单次返回和累计上下文占用，保留核心合同约束及必要字段事实，较早原文可凭冻结引用再次读取；不能仅把整份长文分多次全部追加。具体工具签名、工具调用上限与上下文管理接入在生成调用机制中明确，不引入额外语义审核模型。
- 当前 `task_preparer.py` 的任务规划仍是无工具 ChatModel。项目现有只读 Agent、`create_workspace_backend`、`create_workspace_permissions` 可提供接入参考，但尚未具备上述单 Unit 冻结合同访问边界；不能直接把整个实时工作区交给规划模型并称为已实现。

### 可复用实现与必要新增

- `build_context_resolver.py`：复用页面／Endpoint 定向解析、正式实体绑定检查和 `prebuilt_files` 提供逻辑。
- `entity_definitions.py::entity_design_summaries`：复用有界实体摘要及按 Endpoint 引用裁剪外部 API 操作。
- `graph/nodes/tasks.py::_executable_details / _scoped_api_contract / _scoped_pages`：复用合同正文组织、所属 schema 保留及导航目标裁剪。
- `agents/main/task_preparer_prompt.py::compact_workspace_snapshot`：复用前端／后端路径事实裁剪；该快照不承担依赖是否安装、代码能力是否实现的判断。
- `authorization_overlay.py` 与 `authorization_frontend_projection.py`：复用权限切片与资源映射；资源文件写入仍归 Build 后平台投影。
- 新增的是把上述来源组装成单 Unit Context、显式携带本轮生成范围，并按既定逐类规则提供保留职责约束。当前 Scope Context、`Unit.source_refs` 和单 Unit 的完整生成输入不能直接画等号。

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
DEVAGENTSTUDIO_DAG_UNIT_MAX_TOKENS=4096
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
    = 2 (production Unit policy; UnitGenerationPolicy default = 0)
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
SDK infrastructure retry = 2 (production; policy default = 0)
```

第一版 Local=3、Global=2 是固定策略，不开放 Settings 构造参数或环境变量覆盖；
`dag_unit_local_max_attempts`、`dag_global_repair_limit` 仅暴露只读固定值。
`UnitGenerationPolicy.local_max_attempts` 同样只接受 3；Global 额度归后续 Run Controller 管理，不放入 Unit Policy。
Unit 生成并发由平台拥有：`DEVAGENTSTUDIO_DAG_UNIT_CONCURRENCY` 只配置 Worker Pool 的期望并发，Scheduler 仍硬限制最多 3 个 model worker。Unit Graph 依赖、模型输出和 Candidate 都不得声明 Unit Worker、跨 Unit 调度或最终执行批次；确定性 Unit 在模型 Worker Pool 外由平台串行提交。Task Candidate 当前仍包含单任务级 `can_run_in_parallel` / `parallel_reason` 字段，但它们不是实际调度批次；Scope 编译器还会结合依赖和文件冲突生成平台执行批次。token budget 保持 DAG 独立配置。

以下保护参数由 production Planning adapter 显式构造，不进入业务 Context：

```text
Unit session timeout
contract read count
contract accumulated size
model turn limit
```

当前生产值由 `production_unit_generation_policy()` 集中给出：单次请求 120 秒、Unit Session 600 秒、最多 8 个模型 turn、最多 24 次合同读取、累计 2,000,000 字节且单次最多 200,000 字节。Policy 严格校验三项读取预算，Builder、Scheduler 和 Worker 不得另设隐式默认值。

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
├── identity: CandidateIdentity
│   ├── planning_run_id
│   ├── unit_id
│   └── generation_round
├── origin
│   ├── generated
│   └── recovered
├── generated_from: AttemptIdentity | null
├── recovered_from: CandidateRecoverySource | null
├── input_fingerprint
├── status
│   ├── valid
│   ├── invalid
│   └── superseded
├── tasks[]
├── validation_issues[]
└── generation_metadata
```

`UnitCandidate.tasks[]` 只表达该 Unit 在当前 PlanningRun 中的新增或替换任务。模型不得回传上一份 confirmed DAG 中已经复用的历史任务；历史任务由平台在 Scope Assembly 时合并。

Candidate 当前身份与其生产来源是两个独立概念：`identity` 只表示 Candidate 当前属于哪个
PlanningRun、Unit 和 generation round；`generated_from` 才记录产生它的真实 Attempt。
`origin=generated` 时两者必须一致，且 `recovered_from` 必须为空。`origin=recovered` 时
Candidate 属于当前新 Run，但不携带当前 Run 的 Attempt；`recovered_from` 只保存来源 Run 和
来源 Candidate 的 provenance，不保存 ownerSessionId 或 workflowRunId。

正常模型生成和 deterministic 生成都分配 `generated_from`；deterministic 仍不消耗 model
attempt budget。对于当前 latest Candidate，`model + origin=generated` 必须绑定当前轮真实
发生过的 model Attempt：`attempt_in_round > 0` 且
`generated_from.attempt_in_round == Unit.attempt_in_round`；这条约束不适用于 deterministic。
领域模型允许 recovered model Unit 以 `candidate_ready`、`attempt_in_round=0`、
`total_attempts=0` 表示当前 Run 尚未调用模型。Global Validation/Repair 只消费 Candidate
的当前身份、Task 和校验状态，不按 `generated`/`recovered` 分支。输入指纹绑定本轮冻结输入，
不承担跨 Run 缓存或失效判断；生成元信息记录生成方式和调用诊断。状态枚举与持久化细节在
状态与存储议题确定。

## 已确认：模型 Task 字段与平台补充字段

| 模型返回字段 | 必填规则 |
| --- | --- |
| `id` | 必填，在当前 Candidate 内唯一，遵守对应 Unit 的 ID 规则；Backend Endpoint 沿用阶段 ID 格式，`frontend:api-client` 则按 `unit_id + api_contract_id + 本轮 Endpoint ID 集合` 的稳定哈希后缀生成，不能复用 retained Task ID。 |
| `unit_id`、`owner` | 必填，必须与平台指定的当前 Unit 及 owner 范围一致，不能通过归一化修改错误归属。 |
| `title`、`description` | 必填，使用中文说明具体实施职责。 |
| `dependencies` | 必填数组，可为空；仅引用本 Candidate 内的 Task ID。跨 Unit 及历史保留任务依赖由平台组装。 |
| `change_scope` | 按现有任务规则明确文件路径、操作及该文件的修改职责；代码任务不得用空文件范围规避职责声明。 |
| `deliverables` | 按适用 Unit 规则提供交付物，沿用 `id / kind / target_id / paths / provides` 结构与现有类型清单，不自行发明交付物类型。 |
| `impact_scope`、`can_run_in_parallel`、`parallel_reason` | 可选，沿用现有含义与平台默认处理；实际并行由平台决定。 |

平台负责补充或编译：

- 新任务的 `status=pending`、适用 `task_type`；模型不得声明已经完成、验收通过或自行决定替换历史任务。
- 从明确文件范围整理 `target_files / allowed_paths`，不得由此放宽任务的业务写入范围。
- 从冻结输入注入正式 `source_refs` 和权限事实。
- 验收规则，以及 Scope Assembly 阶段的跨 Unit 和保留任务依赖。
- Candidate 身份、尝试次数、状态、校验问题和生成诊断。

平台资源投影独立于 Candidate；Candidate 继续遵循平台身份、归属、局部校验和 Assembly 契约。

## 已确认：失败处理与归一化边界

| 情况 | 处理 |
| --- | --- |
| 平台判断本轮全部复用 | 不启动该 Unit 的生成，不创建空 Candidate。 |
| 已指定生成职责，模型返回空任务包 | 本次 Unit 生成失败，反馈缺失职责，按有限重试重新生成完整本轮 Candidate。 |
| 返回其他 Unit 或错误 owner | 本次 Unit 生成失败，反馈错误归属，按有限重试重新生成完整本轮 Candidate；不删除越界任务或修改归属后接受余下任务。 |
| 重复返回已保留的历史任务职责 | 本次 Unit 生成失败，反馈重复职责，按有限重试重新生成完整本轮 Candidate。 |
| JSON 截断或无法完整解析 | 本次 Unit 生成失败，不接收可解析的前半份任务；按有限重试重新生成完整本轮 Candidate。 |
| Candidate 内 ID 重复或依赖引用不明 | 本次 Unit 生成失败，反馈冲突 ID 或无法解析的依赖，按有限重试重新生成完整本轮 Candidate；不靠随意改名、删除依赖或单独重生成一项 Task 掩盖问题。 |

上述模型内容失败均以当前 Unit 的完整本轮 Candidate 为重新生成边界。上一尝试的局部正确任务不拼接进下一尝试；其他已成功 Unit 的 Candidate 和 confirmed DAG 中的历史保留任务不重生成。沿用本轮冻结输入，仅追加当前 Unit 的错误反馈；具体计数与耗尽处理在重试议题统一确定。固定规则生成出现平台构造错误时，应按系统错误定位，不通过模型反复重试修复平台代码。

归一化只做确定性的路径格式整理、列表去重、可推导字段补充及正式来源注入。列表去重不包括删除冲突 Task 或合并重复 Task ID。不能填造业务语义、把错误 owner 改正确、删除其他 Unit 的任务后声称 Candidate 合格；原始结构与归属错误必须在会掩盖它们的默认填充之前识别。

平台处理顺序为：

```text
Task.id
→ 模型

CandidateAttempt.candidate_id
→ 平台
```

---

# 九、Planning Run 数据结构

建议正式引入一个运行时概念：

```text
PlanningRun
```

它与最终：

```text
build-task-plan.json
```

不是一回事。

建议：

```text
PlanningRun
├── planning_run_id
├── scope
├── status
├── phase
├── frozen_input_revision
├── base_confirmed_plan_ref
├── base_confirmed_plan_digest
├── required_unit_ids
├── planning_unit_ids
├── retained_task_ids
├── reusable_capabilities
├── unit_states
├── global_issues
├── started_at
└── completed_at
```

重要原则：

> `PlanningRun` 描述“本次生成过程”。

而持久化产物明确分为：

```text
build-task-plan.pending.json
= 当前唯一 PlanningRun 已通过全局校验、等待确认的 DAG 草稿

build-task-plan.json
= 最近一次经过用户确认的正式累计 DAG
```

文件名是建议契约，详细设计可以调整，但“草稿与正式文件物理隔离”的边界不应改变。Build 执行和下一次 PlanningRun 的 baseline 只能读取正式文件；确认界面读取草稿。不要混用两者状态。

`retained_unit_ids` 不足以准确表达复用。更准确地说：当前 `UnitCandidate` 只包含本轮任务；Scope Assembly 后，累计 DAG 的 `build_units[unit_id]` 才可能同时登记历史保留 Task 和本轮新增 Task。PlanningRun 应记录 Task / capability 粒度的复用事实，同时让 UnitCandidate 继续作为本轮生成和重试边界。

这也是一项需要明确实现的改动：当前 `_merge_prepared_scope_tasks` 对 `replaceable_unit_ids` 采用“同 Unit 旧任务整体替换、其他 Unit 整体保留”的合并方式，尚不支持在一个被重新打开的共享 Unit 内保留旧 Task、只追加本轮缺失 Task。若采用本计划，Assembly 必须按确定性的 Task / capability ownership 规则完成同 Unit 增量合并；不能误写成当前已经具备的能力。

### 已知实现前置条件：草稿与正式 DAG 分离

当前工作区使用唯一的 `build-task-plan.json`。在一次 PlanningRun 的生成和全局校验过程中，旧 confirmed DAG 已加载到内存，并不会提前被覆盖；全局校验通过后，新的 pending DAG 才写回同一路径。

目标设计改为：全局校验通过后只写 `build-task-plan.pending.json`，不修改正式 `build-task-plan.json`。如果用户重新生成，废弃当前草稿并继续从正式文件建立新 PlanningRun；如果用户确认，则校验草稿的 `planning_run_id`、输入指纹和基线摘要，再原子替换正式文件。

这不是跨 PlanningRun 复用失败 Candidate，也不是历史兼容机制；它只是保护当前有效的 confirmed 权威状态。首次规划不存在正式文件时，以空 DAG 作为 baseline。

---

# 十、关键状态设计

## PlanningRun Status

第一版建议尽量简单：

```text
preparing
generating_units
assembling
validating
ready_for_confirmation
failed
cancelled
```

生命周期：

```text
preparing
    ↓
generating_units
    ↓
assembling
    ↓
validating
   /       \
success    failure
  ↓           ↓
ready         按错误归因及 Global 剩余额度处理
```

可修复失败且 Global 仍有额度时回到 `generating_units`；不可重试错误或 Global 修复耗尽时才进入 `failed`。校验先检查本轮必需 Candidate 是否齐全，再组装并校验完整 DAG；以上状态枚举仍在第 8 项最终确定。

---

## Unit Generation Status

建议：

```text
pending
generating
validating
retrying
candidate_ready
failed
reused
```

生命周期：

```text
pending
   ↓
generating
   ↓
validating
   ↓
┌───────────────┐
│ valid         │
↓               │
candidate_ready │
                │
invalid         │
↓               │
retrying ───────┘
```

当前生成轮达到最大局部尝试次数：

```text
retrying
   ↓
failed（本轮 Unit 生成失败）
```

此处 `failed` 不立即终止 PlanningRun。其余 Unit 继续，待本轮全部收尾后由 Global 检查有效 Candidate 是否齐全；若失败来自模型内容且 Global 有修复额度，该 Unit 可再次进入 `generating` 并获得完整局部额度。Unit 本轮失败与整个 Run 最终失败须分别表达，具体状态字段在第 8 项确定。

---

## Candidate 生命周期

Candidate 只属于当前 Planning Run。

```text
Candidate
    ↓
candidate_ready
    ↓
参与 Scope Assembly
```

如果 Scope 成功：

```text
candidate
    ↓
Scope Assembly + Global Validation
    ↓
写入 pending DAG 草稿
    ↓
用户确认
    ↓
Scope Commit：原子替换正式 DAG
```

如果整个 Planning Run 最终失败：

```text
candidate
    ↓
随 Run 结束
    ↓
本 Run 不恢复；后续 Retry 若采用 Recovery Candidate，必须创建新 Run 并保留 source provenance
```

Task 1 已提供 `origin=recovered` 的领域合同能力；Task 2 新增独立的
`PlanningRecoverySnapshot` 与 `.devagentstudio/runtime/planning-recovery/<source_workflow_run_id>.json`。
Task 2 只在最终 failed PlanningRun 的 `UNIT_GENERATION_INFRASTRUCTURE_FAILURE` 收口后写入当前
`candidate_ready` Candidate 正文，仍不读取 Recovery、不创建新 Run 的 recovered Candidate，也不做
Retry 注入。Task 3 才通过明确的 `resumeExecutionRunId` 消费它；禁止按 workspace/session/scope
猜测最近结果。任何 Candidate 在进入 Scope Assembly 和 Global Validation 前都不能进入
PendingPlan/FormalPlan。

用户点击：

```text
重新生成
```

意味着：

```text
创建新的 PlanningRun
重新读取上一份 confirmed DAG 作为只读基线
重新计算 required_unit_ids / reuse facts / planning_unit_ids
```

“重新生成”不会继承失败或被放弃 Run 的 Candidate，也不会把上一份 pending DAG 当作基线。它从上一份 confirmed DAG 重新开始，但其中仍然有效的历史 Task 可以继续被确定性复用。

---

# 十一、重试模型

当前项目：

```text
初始调用 + 最大 2 次 retry
= 最多调用 3 次
```

该数值可以作为 Unit 每轮生成的初始建议；具体默认次数仍在重试议题确定，不能理解为单个 Unit 在整个 PlanningRun 内只能生成三次。

但 Retry Scope 从：

```text
整个 planning scope 的全部 Candidate
```

改成：

```text
单 Unit
```

例如：

```text
Unit A
attempt 1 → valid

Unit B
attempt 1 → invalid
attempt 2 → invalid
attempt 3 → valid

Unit C
attempt 1 → valid
```

A、C 不因为 B 失败而重新生成。

## 已确认：Global 与 Unit Local 的重试预算独立

采用两层有界循环。Global 发现可归因、可由模型修复的冲突或必需 Candidate 缺项后，仅触发选定本轮 Unit 的完整 Candidate 重生成；每次 Global 触发都为这些 Unit 开启新的一轮生成，并重新给予完整的 Unit Local 尝试额度。初始生成或上一轮 Global 修复中已经使用的局部次数，不扣减新一轮的局部额度。

| 计数口径 | 作用域与重置规则 |
| --- | --- |
| Unit 每轮最大生成尝试数 `U` | 包含该轮首次生成及局部内容失败后的重试。初始生成、每次 Global 触发的重生成分别计数，每轮从第 1 次开始。 |
| Global 最大修复轮数 `G` | 在整个 PlanningRun 内累计。一次全局失败完成归因后，对一批选定 Unit 发起重生成，算一轮修复；不会因为其中某个 Unit 局部失败、成功或换了冲突对象而重置。首次全局校验不占修复轮数。 |
| 累计生成诊断 | 保留每个 Unit 的生成轮次、各轮局部尝试及累计调用记录，不因局部额度重置而丢失；最终状态字段在第 8 项明确。 |

每一轮 Global 修复按以下顺序进行：

```text
本轮 Unit 均成功或局部耗尽
  → Global 完整性检查：本轮必需 Candidate 是否齐全
  → 齐全时执行 Scope Assembly + 完整 DAG 校验
  → 有可修复、可归因的缺项或 DAG 问题
  → 检查 Global 剩余额度，累计一轮修复
  → 仅为选定 Unit 开启新生成轮，各自最多尝试 U 次
  → 等待这些 Unit 成功或局部耗尽，保留其他有效 Candidate 和历史 Tasks
  → 汇总最新有效 Candidate 及失败信息，再进入 Global 完整性检查
```

失败轮次的 Candidate 不参与组装或草稿提交；不拼接同 Unit 不同尝试中的局部正确 Tasks。某个旧 Candidate 已被 Global 判定需重生成，新的生成轮失败后也不能退回该旧 Candidate 充当有效结果。Unit 的正式输入、生成范围、保留事实继续冻结，只追加针对该 Unit 的局部／全局问题反馈，不把其他 Candidate 正文加入生成输入。

例如仅用于说明计数，取 `U=3`、`G=2`：初始 Unit 生成最多尝试 3 次；第一次 Global 修复选中的 Unit 各自又可尝试 3 次；若再次全局失败，第二次修复选中的 Unit 各自仍可尝试 3 次。同一 Unit 即使连续局部耗尽，也可按此规则进入下一轮 Global 修复。两轮修复后仍执行 Global 检查，若存在必需 Candidate 缺项或完整 DAG 校验仍失败，则结束 Run，不开启第三轮修复。

因此，一个每轮都被选中重生成的 Unit，内容生成尝试上限为 `(G + 1) × U`，上述例子为 9 次。`U` 说的是总尝试数，若配置使用“最大重试次数 R”，则 `U = R + 1`。基础设施重试另行有界计数，不包含在该内容生成次数公式里，其具体策略仍在第 6 项确定。

**已确认：Local 耗尽只标记当前 Unit 本轮生成失败。** 保存失败原因及最后的局部校验问题，不提供有效 Candidate，其他 Unit 继续生成。Global 的进入条件是本轮 Unit 均已成功或局部耗尽，不要求全部 Candidate 已 ready。

Global 完整性检查读取冻结的 `planning_unit_ids / generation_scope`、有效 Candidates 和各 Unit 本轮生成结果。只有本轮明确有生成职责的 Unit 缺少有效 Candidate，才形成缺项问题；全部复用的 Unit、无需任务的结构 Unit 均不属于缺项。缺项源于模型内容失败时，Global 在剩余修复额度内重生成该 Unit，并带上之前的局部错误反馈。正式输入缺失、平台构造或编译错误不能被包装成可重试缺项。

Candidate 不齐全时先处理缺项，不将缺失 Unit 引发的下游依赖缺口扩大为下游 Unit 的生成错误；补齐后才执行完整组装和 DAG 校验。无论缺项还是职责冲突，每次由 Global 发起新一轮生成都消耗同一份 Global 修复额度，因此最多仍为 `(G + 1) × U` 次内容生成尝试。最终额度耗尽或出现不可重试错误时才终止 Run；其他进行中调用如何收尾留在第 6、10 项。

---

# 十二、Infrastructure Retry 和 Generation Retry 分开

需要区分两类失败。

## 模型基础设施失败

例如：

```text
timeout
429
5xx
connection error
```

属于：

```text
Model Invocation Retry
```

## Candidate 内容失败

例如：

```text
deliverable 缺失
owner 错误
路径越界
Unit 内依赖错误
缺少必要 Task
```

属于：

```text
Unit Regeneration
```

概念上最好至少区分：

```text
infra_attempt
generation_attempt
```

不要全部混成一个 `retry_count`。

## 第 6 项：次数、反馈与失败收尾（已确认）

本项的两层预算、默认数值、反馈分区和运行结束方式已确认，可以收口。默认数值是第一版的有界策略，不声称已经根据模型恢复率调优。具体字段存储在第 8 项、调用与取消实现及事件格式在第 10 项确定。

### 收口口径

| 项目 | 本项收口规则 |
| --- | --- |
| Local 额度 | 每个 Unit 每轮最多 3 次完整内容生成尝试，包含首次生成；JSON／内容校验失败进入本轮下一次尝试。首次通过即停止，不为用满额度而继续生成。 |
| Global 额度 | 每个 PlanningRun 最多 2 轮修复；初次检查不占额度。一次检查中的问题先聚合，同一批选定 Unit 合计消耗一轮；各 Unit 获得新的完整 Local 额度。两轮之后仍做最后一次 Global 检查，通过可保存草稿，仍有阻断问题则失败。 |
| 局部耗尽 | 只返回该 Unit 本轮无有效 Candidate 的失败结果和原因，其他 Unit 继续。本轮收尾后由 Global 判断缺项并在剩余额度内补生成；其他有效 Candidate 与历史 Tasks 保持不变。 |
| 基础设施调用失败 | DAG 规划链路不增加外层基础设施 retry；production Unit policy 将 SDK max_retries 设置为 2（DTO 默认仍为 0）。SDK 支持范围内的基础设施重试仍无法完成调用后立即结束当前 PlanningRun 并上报上层，不消耗 Global 额度尝试恢复。失败收口后，Task 2 仅按 source Workflow execution ID 写独立 Recovery Snapshot；用户重新生成时仍创建新 Run，且 Regenerate 不读取 Recovery。 |
| 重试反馈 | 冻结输入之外显式分区：Global 反馈是本 Unit 对本轮全局问题必须达成的修复目标，在整轮 Local 尝试中持续保留；最新 Local 错误是最近一次生成暴露的具体问题，按尝试更新。两类不能混成无来源的错误列表，最终须同时满足。输出始终是该 Unit 完整本轮 Candidate，其他 Unit 的 Candidate 正文不进入输入。 |
| 最终失败或用户取消 | 停止派发、停止自动重试，尝试取消进行中调用，拒收迟到结果；不组装或提交失败 Candidate，不覆盖正式 DAG。通过现有 AG-UI 向上层输出已结束的状态及原因，不保留 PlanningRun 内人工暂停／继续。 |

缺少正式输入、平台编译错误及无法归因的问题按不可重试失败处理，不使用模型内容重生成额度；平台投影在 Build 执行阶段处理。

本项约定的结果交接为：Unit 生成向调度器交付有效 Candidate，或局部耗尽及结构化问题；不可重试故障触发 Run 失败收尾。Global 输出通过、选定 Unit 的下一轮修复或最终失败。运行结果的具体 DTO／状态字段留在第 8 项，不在此另建第二套状态模型。

反向检验：按上述额度，一个每轮都被选中的 Unit 最多经历 `(2 + 1) × 3 = 9` 次内容生成会话；一次 Global 修复不能因多个问题重复入队而变成多轮，也不能在后台基础设施重试之外再套一层重试。用户显式创建新 Run 是上层新操作，不计入旧 Run 的九次范围，也不能因此带入旧 Candidate。

### 当前实现与可复用逻辑

- `Backend/app/agents/main/task_preparer.py::prepare_build_tasks_with_main_agent` 使用 `build_task_plan_max_retries`，当前代码默认 2 次重试，即 3 次总尝试；原始候选错误、编译失败、最终组装校验失败仍共用一个 Scope 级循环。可复用配置及错误反馈入口，但需拆成已确认的 Unit Local 与 Global 两层控制。
- `Backend/app/agents/model_factory.py::create_chat_model` 将 `model_max_retries` 传给模型 SDK；`Backend/app/config.py` 的代码默认值为 2。这是独立的 SDK infrastructure retry，实际配置可覆盖默认值；不能在外层再悄悄套相同重试循环。
- `Backend/app/agents/main/task_preparer_prompt.py::_task_plan_retry_feedback` 已把校验错误注入下一次完整任务生成请求，当前最多插入 20 条字符串。可复用反馈入口，改为根据结构化问题投射当前 Unit 的反馈；该固定截取不能直接当作最终完整反馈契约。

### 已确认的默认预算

| 预算 | 默认值与边界 |
| --- | --- |
| Unit 每轮内容生成 | 最多 3 次总尝试，即首次生成加 2 次局部重试。每次 Global 选中该 Unit 时恢复完整额度。 |
| Global 修复 | 最多 2 轮；首次 Global 检查不计入修复轮数，每轮选定多个 Unit 也只计一轮。耗尽后仍缺项或完整 DAG 不通过，Run 失败。 |
| 单次模型请求的基础设施重试 | DAG 规划链路不增加外层基础设施 retry；production Unit policy 显式启用模型 SDK max_retries=2，DTO 默认仍为 0，不修改其他功能的模型配置。SDK 支持范围内的重试仍无法完成调用后结束当前 PlanningRun 并向上层报告；用户选择重新生成任务时创建新 Run，不是当前 Run 内部重试。 |

基础设施调用失败单独记录，不通过 Local 或 Global 内容修复额度继续自动调用。用户从上层重新发起任务准备时创建新 Run，新 Run 拥有自己的次数预算，旧 Run 记录不清除。成功取得模型响应后出现因输出长度限制导致的 JSON 截断、结构错误或候选校验失败，属于内容失败，消耗当前 Local 尝试。传输中断而无法取得完整响应，按调用失败处理，不接收部分 Tasks。

### 基础设施错误与上层重新发起

常见调用失败需按异常类型及服务端错误码判断：连接短暂中断、服务端暂时故障、短时限流可能通过稍后重试恢复；超时可能是短暂拥塞，也可能是请求持续超过服务能力；凭据无效、权限不足、额度耗尽、模型／请求配置错误通常需先处理原因，重复相同请求不能修复。不能只凭 HTTP 429 就判断短时限流，也不能保证所有 5xx 或超时都会恢复。当前没有本项目按错误类型统计的自动重试恢复率，不能断言自动重试效果普遍很小。

`Backend/app/config.py` 当前请求超时配置默认 120 秒，SDK 全局默认 max_retries=2；DAG Unit 通过 `UnitGenerationPolicy` 显式传入 production SDK max_retries=2，连续超时可能累积数分钟等待；该请求超时不等于整个 Unit 会话的总耗时上限。DAG 链路不增加另一层基础设施 retry。

错误发生后将当前 PlanningRun 标记为 failed，停止新调用与自动重试，按失败收尾规则处理正在进行的调用及迟到结果。通过现有 AG-UI 失败流程向上层报告故障 Unit、原因及是否需要先处理配置，明确结束当前生成进度；不交给 Global 作为内容缺项自动修复，也不在 PlanningRun 内保留等待用户决定的运行状态。

- **用户选择重新生成任务：** 上层重新进入 `prepare_build_tasks` 的任务准备入口及必要输入准备，创建新的 `planning_run_id`；重新读取已确认正式合同和 confirmed DAG，建立本轮工作区快照、复用事实与生成范围。Regenerate 不读取 Recovery；Task 3 未来只有带明确 `resumeExecutionRunId` 的 Retry 才能尝试读取对应 Snapshot。新 Run 不能把 checkpoint 中上次候选计划当作 confirmed 基线；Local／Global 预算从新 Run 开始计数。这不是从需求、UI 或技术规划阶段重新生成上游产物。
- **用户选择取消／稍后处理：** 失败 PlanningRun 已经结束，无需再让它等待；上层关闭本次失败处理或等待用户稍后主动发起。正式 DAG 始终不变；用户在调用尚未失败时主动取消，则按取消分支结束运行。
- **上层等待与内部 Run 分离：** 可以由工作流／界面等待用户选择，但这不表示失败 PlanningRun 仍活跃。AG-UI 工作流执行身份与 `planning_run_id` 分属不同层，不要求更换整个应用、会话或重新执行所有上游节点。用户操作的身份绑定在第 9、10 项衔接。
- **错误契约：** 使用平台结构化失败结果和明确的新 Run 发起动作，不仅抛一个未处理异常后让界面停留在“生成中”。基础设施错误的 `ValidationIssue.retryable=false` 表示不能在该 PlanningRun 内通过 Candidate 重生成修复，不禁止用户在上层主动开启新 Run。

现有 `Backend/app/graph/workflow.py` 已支持 `resume_from=prepare_build_tasks`，`route_prepare_build_tasks` 可将失败结果交给统一失败处理；`Backend/app/graph/nodes/tasks.py::_build_task_plan_generation_failed_result` 已提供节点失败出口，可以复用这些入口。当前 `_existing_build_task_plan` 仍优先接受 checkpoint 中通过图校验的计划，不能直接等同于新方案只从正式 confirmed DAG 建立基线；重新读取基线和隔离新 Run 候选是本方案实施要求。

该方向取消上一版“同一 PlanningRun 暂停后手动重试失败 Unit”的建议。失败 Run 仍是终态；Task 2 保存的 Recovery Snapshot 只是 retry optimization state，不是 PlanningRun、PendingPlan 或 FormalPlan，也不改变锁、Pending lifecycle 或正式 DAG。Task 3 未来可在新 Run 中按显式 `resumeExecutionRunId` 重新验证并复用 Candidate。实际模型恢复率尚无数据，不能保证立刻新建 Run 就能解决同一基础设施问题。

`Backend/app/protocols/workflow/runtime.py` 已通过现有 AG-UI 入口处理 `cancel_run_id`，可复用取消入口；当前 `_invoke_live_main_agent` 仍使用同步 `invoke`，不能把已有取消入口等同于可立即中断底层模型请求。实施需让取消能独立被处理：停止派发并拒收取消后的迟到结果，底层请求支持中断时再中断。具体调用与收尾时限在第 10 项确定，不新增自定义传输接口。

同一 Unit 在所有 Global 修复轮均被选中时，按上述数值最多发生 9 次内容生成会话。必要合同查询可能让一个会话包含多次模型请求，因此 9 不是 HTTP 请求总数上限；会话工具轮数、超时、取消及累计调用限制在第 10 项明确。

### 给模型的重试反馈

- 固定携带当前 Unit 的目标、冻结 generation_scope 和约束。平台分别组织 Global 反馈与 Local 反馈，并在最终提示词中使用明确的分区标题和用途说明，不能仅依靠平台内部的 `level` 字段区分，再将消息摊平成一个错误列表。
- **Global 分区：本轮全局修复目标。** 只提供本 Unit 对本轮全局问题应完成的修复目标、对应规则和必要的正式归属事实，标明所属 Global 修复轮次。该目标在本轮全部 Local 尝试中持续有效，不能被最后一次 Local 错误覆盖；通过 Local 不代表这个目标已经通过 Global 校验。
- **Local 分区：最近一次生成的具体问题。** 标明对应生成尝试，提供最新 `code`、Task／字段定位、预期与实际；同一问题去重并随尝试更新。上一轮局部耗尽作为本轮 Global 缺项原因时，保留真实来源轮次，不能把旧问题伪装成新尝试的结果。
- 初始生成尚无 Global 修复目标、或没有上一尝试 Local 错误时，明确该分区为空／无，不编造反馈。模型需围绕 Global 目标解决 Local 问题，最终同时满足正式合同、生成范围和全部适用硬性校验；“Global 是最终修复目标”不表示可以绕过 Local 规则。若平台反馈与冻结正式约束相矛盾，由平台报告归因／反馈构造错误，不让模型选择忽略哪一方。
- 请求仍要求输出完整本轮 Candidate，不要求局部补丁，不拼接不同尝试的 Tasks，不带其他 Unit Candidate 正文。错误过多时先按规则与目标聚合；需要进一步读取的详细反馈如何受控提供，沿用第 10 项调用机制设计，不简单丢弃剩余错误后声称已提供完整反馈。

模型可见的示例结构如下，示例职责与字段错误仅用于说明分区：

```text
【本轮全局修复目标｜Global 修复第 1 轮｜本轮持续有效】
本 Unit 应复用既定的 user.list API 实现，不再重复声明该接口的实现职责。
这是本轮重生成必须达成的目标；修复下方 Local 问题时仍必须保留该目标。

【最近一次生成的问题｜Local 尝试第 2 次】
Task page:user-list::implementation 的 frontend.page 交付物缺少 paths。
请按本页正式入口事实补全路径声明。

【本次输出要求】
同时完成 Global 修复目标和 Local 问题修复，遵守冻结合同及生成范围，
输出当前 Unit 的完整 tasks JSON；不能只修复 Local 问题而恢复重复 API 职责。
```

### 正在进行的调用如何收尾

- **单个 Unit 因内容错误耗尽 Local：** 结束该 Unit 本轮生成并保存失败问题；其他 Unit 照常完成，随后 Global 汇总，沿用已确认流程。
- **整个 Run 最终失败或用户取消：** 停止派发新调用和自动重试；建议对进行中的调用尝试取消。无法取消的底层调用，其迟到结果不得再次被接收为有效 Candidate、触发下一轮或写入草稿；具体取消与收尾时限留在第 10 项。
- 本轮成功 Candidate 只在当前 Run 有效；Run 最终失败后不跨 Run 复用。正式 DAG 始终保持原样。

---

# 十三、Validation Issue 必须结构化

这是整个局部重试机制能否成立的核心基础。

当前：

```text
errors: [
  "Task xxx deliverables invalid..."
]
```

不足以支撑可靠调度。

## 第 5 项：结构化错误与全局归因（已确认）

### 现有实现可以复用什么

- `Backend/app/services/build_task_planner.py::_frontend_endpoint_implementation_owner_records` 已提取 `api_contract_id / endpoint_id / owner_task_id / owner_unit_id`；`frontend_endpoint_ownership_errors` 已按 Endpoint 聚合冲突方；`retained_frontend_endpoint_owner_conflict_errors` 已区分保留 owner 与当前 Candidate。可保留判定逻辑，在产生错误的位置直接输出结构化身份，不从错误文案反解析。
- 同文件 `_topological_order` 已发现缺失依赖和无法拓扑排序的节点；`Backend/app/services/build_unit_compiler.py::_apply_unit_task_dependencies` 已记录依赖改写、缺失 Unit 依赖和无效 Task 引用。可复用图与依赖信息，但需区分模型声明边、平台编译边和保留依赖，不能按错误消息中的 ID 一律重生成。
- 当前 `_topological_order` 的 `blocked` 集合可能同时包含环内节点及受阻下游；它不是精确环成员集合。定位环路原因需要补充实际环内节点／边的识别，不能把全部受阻 Unit 都选作重生成对象。
- `Backend/app/agents/main/task_preparer.py::_build_task_plan_validation_errors / _merge_candidate_validation_errors` 当前读取、合并并按字符串去重错误。新链路应按结构化错误聚合，消息只用于展示／反馈，不再承担调度判断。这是当前合同改造，不新增双写或旧字符串兼容通道。

### ValidationIssue 字段与上下游契约

已确认结构如下；在原草案基础上新增 `retry_unit_ids`，明确区分涉及谁与重生成谁：

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

**已确认：字段由平台判定，不调用模型分类。** 例如模型返回错误 owner，输出固定 `invalid_task_owner`，检查层为 `unit`、原因是 `generation`；在 Unit 编译后发现平台漏注入必需来源，检查层同样可以是 `unit`，但原因是 `platform`。Task ID、错误 owner 等变量放入 details，不拼进 code。原因无法可靠确定时报告归因失败并停止，不猜测为 generation 后反复重生成。

### 已确认：环路节点与受阻下游分别处理

以 Task 图 `A → B`、`B → A`、`B → C` 为例，箭头表示前一个任务完成后，后一个任务才能执行。A、B 互相等待，实际环成员为 A、B；C 只是在等待 B，不属于环路。当前拓扑排序的 `blocked = 全部任务 - 已排入顺序的任务` 会得到 A、B、C，不能将这个集合直接作为需要重生成的任务／Unit 集合。

定位实际环内节点及边之后，还需依据来源判断：同 Unit 模型声明的内部环路应在 Local 处理；跨 Unit 边由平台编译，平台编译错误不能交给任务模型修复。只有存在明确可由 Candidate 重生成修复的错误声明，才选择对应本轮 Unit；C 所属 Unit 不因被阻塞就自动重生成。无法确定错误来源时停止并报告。这是确定性图检查和来源归因，不增加模型判断环节。

Run 身份、校验轮次和 Candidate 尝试身份由运行上下文绑定，不必在每个 Issue 上重复一套状态字段。需要区分冲突任务时，`details.task_refs` 记录 `task_id / unit_id / origin`，origin 区分 `retained / candidate`；本轮候选身份关联当前尝试。文件路径只作为诊断事实，不能凭文件重叠判断职责重复。

Local 向调度器提供有效 Candidate，或本轮失败结果及原始问题；Global 接收冻结生成范围、有效 Candidates、保留事实及 Unit 本轮结果。缺项错误要保留失败 Unit 的最后局部问题作为原因，不能只给模型一句“请补任务”。该运行结果的具体存储结构仍在第 8 项确定。

### 归因与重生成选择

| 问题 | 归因证据与需要重生成的 Unit |
| --- | --- |
| 本轮必需 Candidate 缺失 | 根据 `planning_unit_ids / generation_scope` 及该 Unit 的局部耗尽结果，选择缺失 Unit；模型内容错误可重试。若原因是正式输入或平台错误则不可重试，不能把尚未收尾／漏调度当作模型未生成。 |
| 单 Candidate 的字段、owner、范围、交付物或内部依赖错误 | 选择生成该 Candidate 的 Unit；多条问题按 Unit 聚合，整包重生成一次，不逐 Task 重试。 |
| Candidate 与保留任务职责冲突 | 按明确的 Endpoint／已确定共享职责身份定位，选择违规 Candidate 的 Unit；保留 Task 不修改、不重生成。若保留任务之间已经冲突，则为基线问题。 |
| 多个 Candidate 重复声明同一职责 | 根据冻结 `generation_scope` 和正式归属规则保留合法声明，选择违反归属的 Unit。多个 Unit 均违规时选择全部违规 Unit；没有足够依据决定合法 owner 时停止并报告归因失败，不按完成先后、任意 Unit 排序或新增模型裁决。 |
| Task ID 碰撞 | 在构建 ID 索引前保存各份任务来源，按已确定 ID／生成范围规则定位违规 Candidate。保留任务不改名；不能确定违规方时停止。显式替换身份与依赖改写规则仍在第 7 项确定，不能在本项假定所有同 ID 都是非法。 |
| 缺失依赖 | 原始 Candidate 的无效内部引用归对应 Unit；其他 Unit 的 Candidate 缺失只重生成缺失方，不扩散到下游。有效输入及 Candidate 已齐全而平台仍编译出悬空引用，则按平台错误处理。 |
| DAG 环路 | 根据实际环内边及其来源判断原因。明确违反正式依赖规则的 Candidate 声明归对应 Unit；平台 Unit 图／组装边错误由平台处理。不能仅凭参与环路或处于下游就重生成，无法确定可修复方时停止。 |
| 正式合同、平台来源注入、验收编译、平台投影编译或持久化错误 | 按实际原因归 input／platform／persistence 等类别，不通过 Candidate 模型重生成修复。 |

上述已确认规则不新增通用语义覆盖判断。缺项检查依据明确生成范围；职责冲突依据现有或已确认的稳定身份与归属规则。

### Global 向 Scheduler 输出什么

Global 校验器输出结构化问题；平台归因逻辑补齐 `retry_unit_ids / retryable`，Scheduler 使用其结果及独立预算决定是否发起修复。具体字段属于同一条处理链路，不由模型自报。

1. 存在不可通过 Unit 重生成修复的阻断问题，终止 Run 并给出原因；不能同时继续无效的模型修复。
2. 所有阻断问题可修复时，取各问题 `retry_unit_ids` 的并集；同一 Unit 一轮只入队一次，多个 Unit 的同批修复合计消耗一轮 Global 额度。
3. Global 额度不足时停止；有额度时累计一轮修复，选中 Unit 恢复完整 Local 额度，未选中 Candidate 保留。
4. 反馈只包含当前 Unit 的问题、正式目标／归属约束及必要的冲突身份摘要，不注入其他 Candidate 正文；本轮结果再次汇总后重新进入 Global 完整性检查。

反向检验：一个 Issue 可能涉及 A、B，但只有 B 违规；把 `unit_ids` 直接当重试集合会误重生成 A。另一方面，某条错误虽然能定位 Unit，但若原因是平台漏注入 source_refs，也不能仅因定位成功就标记 retryable。

例如：

```json
{
  "code": "missing_page_deliverable",
  "level": "unit",
  "unit_ids": ["page:user-list"],
  "task_ids": ["page:user-list::implementation"],
  "retry_unit_ids": ["page:user-list"],
  "retryable": true,
  "category": "generation",
  "message": "缺少 frontend.page deliverable",
  "details": {}
}
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

## 第 4 项：现有检查盘点与分层（已讨论，结合第 5 项规则落实）

第 3 项的模型响应、平台补充、失败处理和归一化边界已确认，可以收口。检查归属在本项讨论；错误字段与冲突归因、重试计数、状态存储分别留在第 5、6、8 项，不反向阻塞第 3 项。

### 分层的输入与输出

| 层级 | 输入与检查对象 | 输出及后续处理建议 |
| --- | --- | --- |
| Pre-generation | 当前范围适用的正式合同、工作区／模板就绪事实、Unit Skeleton、选定保留任务事实 | 前置条件通过后冻结 Unit 输入；正式输入或保留基线有问题则阻断，不调用模型修复这些输入。 |
| Unit Local | 一个 Unit 的原始响应及归一化后的本轮 Candidate；可读取冻结合同、范围约束、保留职责摘要 | 通过后得到可组装 Candidate；模型内容失败则整体重生成该 Unit 的本轮 Candidate。不能读取其他 Unit 本轮 Candidate。 |
| Global | 先读取冻结生成范围、有效 Candidates 和各 Unit 本轮成功／失败信息做完整性检查；齐全后检查 Scope Assembly 结果，包括保留 Tasks 和平台编译依赖 | 缺项或 DAG 问题按原因归因，在 Global 剩余额度内重生成相关 Unit；完整 DAG 校验通过后才允许保存草稿。具体归因规则在第 5 项确定。 |
| System / Persistence | 平台构造、编译、模型调用设施和文件读写的运行结果 | 区分平台故障与模型内容错误；平台构造错误不靠模型重生成修复。基础设施重试在第 6 项，持久化与确认在第 8、9 项确定。 |

下面按现有函数中的可执行检查列出规则组。代码位置以仓库相对路径及函数定位；`tasks.py` 指 `Backend/app/graph/nodes/tasks.py`，其余文件均在 `Backend/app/services/`。这是生成链路的检查盘点，不把执行阶段读取源码、运行测试或执行验收断言混入 DAG 生成。

### 生成前：正式输入与工作区

| 编号 | 当前代码位置 | 当前检查内容 | 建议归属 |
| --- | --- | --- | --- |
| P1 | `tasks.py::_build_prerequisite_errors` | RequirementSpec、ProductPlan 已确认；UiManifest 已确认或明确跳过；TechnicalPlan 存在、类型正确且已确认；运行时计划是 TechnicalPlan 投影；workspace 存在。 | Pre-generation，沿用适用性门禁。 |
| P2 | `tasks.py::_formal_artifact_hash_errors` | 已有 `basedOn` 正式产物的直接上游哈希是否匹配。 | Pre-generation；保留已有产物门禁，不扩展为 Task 输入变化检测或失效传播。 |
| P3 | `template_state.py::load_template_state / assert_template_context_matches` | Bootstrap 已就绪；TemplateState 结构有效，冻结绑定与当前 revision 和 effective capabilities 一致。业务页面由 Build 创建，平台投影后验收。 | Pre-generation；输入失效直接阻断，不生成模板修复任务。 |
| P4 | `build_context_resolver.py::resolve_target_build_context / _page_context / _endpoint_context / _page_implementation_contract` | 目标类型受支持，Page／Endpoint／所属 API 合同可解析，页面实施合同存在，Endpoint 绑定实体非空且具有来源类型。 | Pre-generation，按当前目标提供必需输入。 |
| P5 | `build_context_resolver.py::_endpoint_entity_designs / _assert_endpoint_entities_designed`；`entity_design.py::entity_design_validation_errors` | 相关实体绑定已确认且有效；数据库字段绑定有目标表、实体字段合法、表列非空，已有表操作检查继续保留；static 种子／字段值落在实体字段内且类型、枚举合法。 | Pre-generation；复用绑定校验，不新增 bootstrap 数据源配置复用判断。 |
| P6 | `entity_design.py::_external_api_design_errors / _external_api_operation_errors / _external_api_connection_errors / entity_design_endpoint_binding_errors` | 上游连接、配置键、Header、HTTP 操作、路径参数和响应映射满足既有绑定规则；operation ID／名称和 Endpoint 关联有效；当前 Endpoint 恰有一个上游操作；实体字段、载荷／分页／错误路径可解析。 | Pre-generation；读取正式绑定事实，不由任务模型补写上游设计。 |
| P7 | `api_contract_validation.py::_validate_api_contract_definitions / _validate_contract_schemas / _index_and_validate_endpoints` | 合同 ID、schema 集合、Endpoint 集合存在；声明的实体引用有效；schema 引用可解析；Endpoint ID 非空且无重复。 | Pre-generation；从 `tasks.py::_scoped_contract_errors` 进入，沿用当前范围切片。 |
| P8 | `api_contract_validation.py::_validate_endpoint` | 方法在既有集合内，路径以 `/` 开始，路径占位参数已声明；非 DELETE 接口有响应 schema；已声明的请求／响应 schema 可解析。 | Pre-generation；不新增“POST 必须有请求体”等规则。 |
| P9 | `page_dependencies.py::validate_project_plan_dependencies`；`api_contract_validation.py::_validate_page_api_dependencies / _validate_page_bindings` | 页面 ID／路径非空且唯一，菜单路径不重复且不与直接页面冲突；Endpoint／导航目标有效；响应字段绑定引用已声明接口且字段存在。 | Pre-generation，保留相关目标与引用所需事实。 |
| P10 | `authorization_overlay.py::compile_authorization_overlay / _binding_items / _endpoint_http_identity / _auth_constants_projection` | 权限绑定结构、稳定目标与 resourceKey 存在；受控 Endpoint 和 HTTP 路径有效；生成的资源常量名不冲突。 | 正式输入不合法为 Pre-generation；合法输入被平台错误切片／编译则为 System。 |
| P11 | `build_unit_skeleton.py::_unit_graph` | API Endpoint 对应 Unit 存在，Page 引用的 Endpoint Unit 存在。 | 生成模型调用前检查；输入引用错误为 Pre-generation，合法输入未被平台构造出 Unit 为 System。当前此函数没有完整 Unit 图环路检查，不能描述成已有能力。 |
| P12 | `tasks.py::_retained_frontend_endpoint_owner_constraints`；`build_task_planner.py::frontend_endpoint_ownership_errors` | 选定保留 Tasks 之间，是否已经重复拥有同一正式 Endpoint 的前端实现职责。 | Pre-generation；不能让新 Candidate 重生成来修复保留基线自身冲突。 |

### 生成后：Candidate 及组装结果

| 编号 | 当前代码位置 | 当前检查内容 | 建议归属与适用输入 |
| --- | --- | --- | --- |
| V1 | `build_task_planner.py::build_task_candidate_contract_errors` | 禁止模型提交平台权限字段、AuthConstants 写入和普通规划中的 `kind=repair`。 | Unit Local，在归一化前检查原始响应。 |
| V2 | 同上 | 适用任务的 deliverables 非空；各项是对象，含 ID、受支持 kind、target_id、非空 paths／provides；不接受单数 `path` 替代 `paths`。当前四个共享 Unit 有缺省交付物豁免，不能说已有规则要求所有 Task 均非空。 | Unit Local；共享能力已确认的具体声明规则按下文契约衔接。 |
| V3 | `build_task_planner.py::_task_semantic_errors` | Task 在当前规划范围；Unit 与 owner 对应；普通 Build 不允许数据库变更任务；非 database owner 不得声明 database_scope；backend owner 不得使用 database task_type。 | Unit Local；从当前 UnitGenerationContext 取得允许 Unit／owner，不能用整个 Scope 范围放过错误 Unit。 |
| V4 | `build_task_planner.py::_database_task_semantic_errors` | 数据库任务类型受支持、database_scope 非空、不修改代码文件、高风险操作有审批要求。 | 保留既有适用检查；本轮普通 Unit 生成先按 V3 排除数据库变更任务，不扩展数据库任务生成。 |
| V5 | `build_task_planner.py::_template_boundary_errors / _authorization_coverage_errors` | 普通任务不得修改共享 routes、resources、AuthConstants 或模板基础设施。 | Unit Local；资源注入不享有 Task 例外，平台在全部任务成功后统一投影。 |
| V6 | `business_acceptance.py::business_acceptance_contract_errors` | 适用任务有交付物，交付物 ID 在 Task 内不重复，kind 受支持且与 owner／Unit 域匹配。 | Unit Local。 |
| V7 | 同上 | 交付物路径非空（现有 shared_capability 例外）、相对且无 `..`、落在 Task 文件范围；同一 Task 内不同 `frontend.api_module` deliverable 可以共同引用同一个 canonical API module，不能因此放宽路径安全、scope 或 Endpoint ownership。 | Unit Local。文件共享不等于职责共享；多个历史增量 Task 也可以继续修改同一 canonical module。 |
| V8 | `business_acceptance.py::_page_deliverable_errors` | 含 frontend.page 交付物的 Task 恰好声明一个此类交付物；覆盖指定页面入口；精确 page_key 存在时 change_scope／allowed_paths 也包含入口。 | Unit Local，读取本页入口事实。当前函数逐 Task 检查，不能宣称已有整个 Page Unit 的交付物唯一性检查。 |
| V9 | `business_acceptance.py::_endpoint_deliverable_errors`；`build_unit_compiler.py::_task_entity_ids` | Controller target 属于当前 Endpoint；多实体 Endpoint Task 可按固定 ID 确定实体归属。 | Unit Local，读取正式 Endpoint／实体集合。当前单实体分支仍有宽松回退；第 3 项的严格 ID 契约需显式检查。 |
| V10 | `business_acceptance.py::business_acceptance_contract_errors / _expected_field_errors` | 来源实体／Endpoint／Page 不越出 Unit；业务检查 ID 非空且无重复，关联已有 deliverable，kind／verifier 合法，正式来源 artifact／target／pointer／hash 完整，target_paths 在范围内，采用确定性且必需的 Build 阶段检查，各 kind 的 expected 必要字段存在。 | 编译后 Unit Local 检查。若模型声明错误目标导致失败，重生成 Candidate；若平台漏注入来源或错误编译 verifier 等字段，则为 System。 |
| V11 | `engineering_acceptance.py::engineering_acceptance_contract_errors` | 工程检查非空，检查 ID 非空且无重复；适用代码任务具有文件操作检查。 | 编译后 Unit Local；模型缺少文件范围与平台编译错误需区分。这里校验验收契约，不在生成阶段执行代码验收。 |
| V12 | `build_task_planner.py::_topological_order / _build_task_graph` | Task 依赖存在、任务图可拓扑排序且无环。 | 同 Candidate 引用及环路在 Unit Local；Assembly 后完整图依赖及环路在 Global。不能把尚未组装的跨 Unit 依赖当成局部缺失。 |
| V13 | `build_task_planner.py::frontend_endpoint_ownership_errors / retained_frontend_endpoint_owner_conflict_errors` | 普通前端任务不得重复拥有同一 `(api_contract_id, endpoint_id)`；Candidate 不得重声明已保留的 Endpoint 实现职责。 | Candidate 内重复及与冻结保留事实冲突在 Unit Local；多个 Candidate 组合后的唯一性在 Global 复核。只使用明确身份规则。 |
| V14 | `build_task_planner.py::_required_bootstrap_task_errors / _authorization_coverage_errors` | planning 范围要求 bootstrap 时有其 Task；权限范围内有 Page Task；已要求实现且有权限绑定的后端 Endpoint 有 Controller 交付物。 | 当前贡献范围内能判断的缺项在 Unit Local；需要合并保留 Tasks 才能判断的完整性在 Global。仅检查适用职责，不要求每个 Unit 每轮生成任务。 |
| V15 | `build_unit_compiler.py::_apply_unit_task_dependencies` | 当前编译跨 Unit 依赖，并记录 `missing_unit_dependencies / invalid_dependencies`；缺失 Task 引用由任务图继续检查。 | 跨 Unit／保留依赖编译及闭合性放 Assembly／Global。`missing_unit_dependencies` 当前只是记录，不能当作已有通用必需 Unit 完整性硬门禁；按已确认生成范围补齐检查，区分保留任务已满足依赖、无需 Task 的结构 Unit，不能把没有 Candidate 当作缺项。 |

当前错误承载主要是 `list[str]`、`ValueError` 及 `task_graph.validation.errors`，不是已经完成归因的结构化问题。不能仅按异常类型或“发生在编译后”判断是否应该重试模型。

### 为已确认契约必须衔接的检查

- **第 3 项原始响应检查需要收紧。** 当前 `_normalize_agent_tasks` 会补 ID／owner、对重复 ID 加后缀、跳过部分无效任务，`merge_exact_duplicate_tasks` 还会合并重复任务。新 Unit 入口须在这些行为掩盖错误前检查原始字段、错误 Unit／owner、空包、重复 ID、未知或跨 Candidate 依赖；不完整 JSON 不进入部分任务编译。此处是已确认契约的实施差异，不是已有完整严格 schema 校验。
- **共享职责只检查已确定的明确身份。** adapter 和本轮 generation_scope 可以据已确认规则校验；不能把 API owner 唯一性直接推广为所有 capability 的通用唯一性／覆盖检查。
- **Endpoint 阶段规则不能冒称已有硬校验。** 当前固定实体 ID 检查不证明四阶段 Task 及依赖完整。若将已确认的逐实体阶段规则实现为确定性检查，应在 Unit Local 对照正式来源类型和本轮职责检查；共享／保留任务组合的依赖仍由 Assembly 处理。
- **完整 DAG 的 ID 冲突必须在构建 ID 索引前暴露。** Candidate 内重复已经属于 Unit Local；与保留任务或其他 Candidate 冲突需在 Assembly／Global 检查，不能被字典覆盖或自动改名掩盖。具体替换和依赖改写仍在第 7 项讨论。

反向检验：不能把所有校验都放 Local，否则发现不了多个 Candidate 组合后的冲突；也不能把所有错误都留给 Global，否则错误 owner、局部环路等本可提前定位的问题会拖到整轮末尾。Local 允许读取公共合同和保留事实，但这些只读输入不使 Unit 依赖其他 Candidate 的生成结果。

确认动作的 schema／草稿身份／状态门禁见 `tasks.py::_build_task_plan_gate_errors`，留在第 9 项；执行调度的文件互斥和源码验收不在本项重设计。

## Unit Local Validation

只判断：

> 当前 Unit Candidate 是否满足既有可确定性检查的结构、契约与自洽性要求。

### 已确认：不新增通用业务语义覆盖校验

- 本轮保留现有校验能力，重新划分其作用域和重试边界，不新增“任务推理或描述是否完整覆盖自然语言业务要求”的通用校验器。
- Task schema、Unit / owner、deliverable 目标、路径、依赖和明确的强规则可以确定性检查；不能将这些检查通过等同于全部业务语义已经覆盖。
- 平台从正式 Contract 编译业务验收要求是在为 Task 附加执行要求，不是证明模型的任务描述已经完整落实了这些要求。现有业务验收编译和检查继续保留。
- 模型自报的覆盖标签、完整性声明或概括性描述，不作为语义完整性的可靠证明；“无法确定语义是否覆盖”本身不新增为自动重生成理由。
- 语义层面的规划质量继续由正式 Contract 输入、模型规划、用户确认和后续验收共同承担，不引入额外的语义审核模型调用。

对于共享 Unit，“本地”不等于完全忽略历史。校验对象仍然只是本轮 Candidate，但可以读取上一份 confirmed DAG 投影出的 owner / capability / retained Task 摘要，以判断本轮增量是否重复或越界；历史 Task 本身不参与重生成。

例如待分析归属：

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

production DAG SDK infrastructure retry：

```text
2
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

## Plan root metadata ownership

Scope Assembly 的 append-only 语义只针对 confirmed Tasks 及其累计 DAG 内容：历史 Task
必须保留自身的 status、acceptance contract 和 execution history。Plan root 则不随 baseline
继承：`build_execution_scope` 与 `template_context` 属于当前 PlanningRun，Assembly 后必须
重新绑定当前冻结值。`confirmation_status`、`confirmed_at`、`confirmed_from`、
`draft_identity` 属于 Pending/Confirm lifecycle，不得从 ConfirmedPlan 进入新的 planning
draft；`last_update` 属于旧 Build runtime，也不得进入新的 Planning draft。

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
not_required
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
.devagentstudio/plans/planning-run.json
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

Task 2 另外保存失败时的 retry optimization state：

```text
.devagentstudio/runtime/planning-recovery/<source_workflow_run_id>.json
```

该文件是独立的 `planning-recovery.v1` 合同，包含 source Workflow/PlanningRun 身份、输入指纹、
confirmed baseline digest、Build scope、原始 infrastructure `ValidationIssue` 以及按
`UnitRunState.latest_candidate_id` 精确提取的完整 `CandidateAttempt` 正文。它只接受
`UNIT_GENERATION_INFRASTRUCTURE_FAILURE`，使用排除 `snapshot_digest` 自身后的 canonical JSON
SHA-256 做完整性校验；损坏或摘要不匹配必须报 invalid。没有当前 `candidate_ready` Candidate
时不创建空文件。路径只能按显式 source workflow execution ID 定位，不能查找 latest，也不按
workspace/session/scope 回退。

Task 2 只写 Snapshot；Task 3 才消费它并创建属于新 Run、保留 source provenance 的 recovered
Candidate。Recovery 写入失败不能覆盖原始 PlanningRun failure；Regenerate 仍只启动 fresh
PlanningRun，Scheduler 保持 recovery-unaware，Task 4 才处理 cleanup/GC。

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

Unit 并行性完全由平台调度：Scheduler 为 pending model Units 建立 FIFO `UnitAttemptJob` 队列，按配置启动不超过 3 个 Worker；Local Retry 重新排到队尾。模型只生成当前 Unit 的 `tasks`，不得输出 `coordination`、Worker 数量、跨 Unit 顺序或最终执行批次。Task 内的 `can_run_in_parallel` / `parallel_reason` 只是当前 Candidate 合同字段，最终批次仍由 Scope 编译器结合依赖和文件冲突生成。deterministic Unit 不占模型并发槽，按平台规则在 Worker Pool 外串行完成。

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
AttemptIdentity
├── planning_run_id
├── unit_id
├── generation_round
├── attempt_in_round
└── attempt_id

UnitAttemptJob
├── identity: AttemptIdentity
├── context: UnitGenerationContext
└── policy: UnitGenerationPolicy

UnitGenerationAttemptResult
├── identity: AttemptIdentity
├── input_fingerprint
├── raw_response
├── tasks[]
├── validation_issues[]
└── generation_metadata
```

平台在 dispatch 前通过 `AttemptIdentity.allocate()` 分配独立 `attempt-<uuid>`；
Worker 必须回传原身份，反序列化缺少 `attempt_id` 时拒绝输入，不补发 ID。
`candidate_id` 使用独立的 `candidate-<uuid>`，Task ID 仍由模型提供，三者不可互相代替。
Job 构造时必须校验 identity 与 Context 的 Run/Unit 一致；Candidate 的 `identity` 保存
`CandidateIdentity`，其生成来源单独保存为 `generated_from: AttemptIdentity`，recovered 来源
则保存 `recovered_from`，不再把 Attempt 计数和 ID 混入 Candidate 当前身份。两者都不保留
第二套可冲突的平铺身份字段。

Context、Job、Result、Candidate 及其 `ValidationIssue` 为不可变快照；Issue 的 ID 序列为 tuple，
details 递归只读。JSON 导出仍使用数组和对象，导出副本可编辑但不改变原快照；复制更新重新验证契约。
Issue 的消息和详情仍不参与去重身份或重试路由。

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
.devagentstudio/drafts/plans/build-task-plan.pending.json
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

Pending root 另外携带 Pending-only 的 `planning_provenance`：

```json
{
  "schema_version": "planning-provenance.v2",
  "review_task_ids": ["当前 Scope 需要展示给用户确认的 Task ID"],
  "platform_task_ids": ["当前 Scope 内由平台确定性生成且不需用户确认的 Task ID"],
  "new_task_ids": ["本次相对于 confirmed baseline 新进入累计 registry 的 Task ID"],
  "reused_task_ids": ["当前 Scope 复用的 retained Task ID"]
}
```

上述四组 ID 由 Scope Assembly 在组装阶段确定，并按 `task_graph.topological_order` 保持稳定顺序；
其中 `new_task_ids` 由最终 assembled `task_registry` 减去 Assembly 的 `retained_task_ids` 得出，
`review_task_ids` 与 `platform_task_ids` 的并集必须准确覆盖 `new_task_ids` 与 `reused_task_ids` 的并集，
且 `platform_task_ids` 只能来自 `new_task_ids`。确认投影直接按 `review_task_ids` 从 Pending
`task_registry` 读取任务，新增任务标记 `new`，复用任务标记 `reused`，平台内部任务不展示，
不在 Confirmation 阶段重新推导依赖闭包。该 provenance 在 Confirm 提升 Formal
前删除，不能成为 Build execution authority。

Pending 成功写入后：

```text
PlanningRun.status = awaiting_confirmation
PendingPlan 成为唯一待确认权威
PlanningRun 仅保留轻量投影，不再继续执行
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
persist abandoned terminal marker
↓
delete matching Pending
↓
delete matching PlanningRun projection
↓
end current Workflow execution and release lifecycle/resource/session input locks
↓
Formal unchanged
```

这里的“结束”只指当前 Workflow execution，不删除或关闭聊天会话，也不清理聊天记录。

Regenerate：

```text
receive structured action=regenerate + exact DraftIdentity
↓
verify and delete matching Pending (commit point)
↓
load current ConfirmedPlan
↓
return to prepare_build_tasks
↓
create new PlanningRun with a new planning_run_id
↓
generate + assemble + Global Validate
↓
success: write new Pending and await confirmation
failure: keep failure state; do not restore old Pending
```

旧 Candidate / Pending 不恢复。

`regenerate` 是 AG-UI 结构化 Planning result 动作，不是普通自然语言请求，也不复用 Unit Local Retry 或 Global Repair 的内部动作。生产接入必须同时更新 request normalization、Graph resume routing、lifecycle、前端类型与确认卡。

## 38.1 同一应用全局互斥

PlanningRun 和 PendingPlan 使用工作区唯一存储路径，因此互斥边界是 application/workspace，而不是 page 或 Unit：

```text
no active PlanningRun
AND
no PendingPlan awaiting confirmation
```

满足上述条件后才能开始新的 DAG generation。不同 page、endpoint、data_source 或 application Scope 不得同时处于 generating 或 awaiting_confirmation；新请求必须被拒绝或引导用户先 Cancel/Abandon 当前运行，不能采用 last-writer-wins 覆盖。

---

# 39. Cancel

Cancel 只针对 active PlanningRun。

前端只在当前服务端权威 active Workflow/PlanningRun 的运行卡上显示“取消运行”；历史 running 快照没有取消权。当前浏览会话不是 owner 时输入区保持只读，但仍可打开 owner 对话查看运行。刷新后若本地 SSE 句柄已丢失，前端使用 `cancelRunId` 调用同一 `/workflow/run` 控制路径，并在后端完成取消后重新读取 lifecycle；不能只把本地 UI 改成 stopped。

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

待确认阶段的 UI 固定提供三个身份绑定动作：`confirm`、`abandon`、`regenerate`。此时不显示 active-run Cancel；`abandon` 才是结束待确认 Workflow execution 的动作，`regenerate` 明示先丢弃旧 Pending 且失败不恢复。

取消粒度固定为 Workflow/PlanningRun 级：用户不能单独取消某个 Unit。Unit 级 `aborted` 只是整轮取消传播后的内部结果，不是产品动作。

# 39.1 页面刷新与运行保持

第一版只恢复 PlanningRun/Pending/ConfirmedPlan 的服务端权威状态投影，不保证页面刷新后原 DAG 请求继续执行，也不从轻量 `planning-run.json` 恢复 Candidate 或 Scheduler。`planningRefresh` 是 GET 时计算、不会推进持久化 lifecycle revision 的弱投影：解析顺序以唯一 Pending 文件和权威 Abandon 标记为先，再看进程内 active run、PlanningRun 终态和 ConfirmedPlan；终态与 DraftIdentity 校验必须否决迟到结果。没有 Pending 时绝不能从聊天历史、旧确认卡或旧 execution 复活待确认状态。页面刷新、应用切换、Electron 退出或其他传输断开均允许使 active Workflow/PlanningRun 结束。

如果未来要求普通页面刷新后继续运行，必须先把 Workflow execution 从 SSE 响应协程中解耦，并设计后台任务所有权、事件重放/重新订阅、运行终止判定和跨进程恢复；该能力明确延期，不属于本轮 Regenerate 接入。

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
Recovery Snapshot consumption / cross-run Candidate injection（Task 3）
Task input hash
自动失效传播
Confirmed Task replacement
Shared Unit multi-Run versioning
General Task-level cross-unit dependency precision
Build Scheduler general redesign
Semantic review model
frontend:data 全面重构
backend:bootstrap 多数据源专项
页面刷新后的后台脱离执行、事件重放或 Candidate 断点续跑
Unit 级用户取消
```

---

# 43. 最终架构原则

> 每个 PlanningRun 以上一份 confirmed DAG 为唯一只读历史基线。平台根据 Scope、Unit Skeleton、正式输入及确定性 ReuseFacts 计算本轮 Unit generation requirements。`frontend:shell` 仅作为模板前置能力存在，不生成 Task；`frontend:auth-guard` 根据当前 authorization resource fingerprint 判断 reuse、workspace satisfied 或 deterministic Candidate，并通过确定性 executor 物化 `resources.ts`。其他需生成 Unit 只依赖冻结的 UnitGenerationContext 独立产生 Candidate。Candidate 的当前 `CandidateIdentity` 与 `generated_from`/`recovered_from` provenance 分离；Global Validation/Repair 不区分两种 origin。Unit 是 Local generation / validation / retry 边界；Local 每轮最多 3 次，Global 最多 2 轮，只重新打开明确归因的 Unit。有效 Candidate 与所有 confirmed Tasks 通过 append-only Scope Assembly 组成累计 DAG。完整 DAG 通过 Global Validation 后只写 PendingPlan，用户确认精确 DraftIdentity 后才原子提升为正式 DAG。production DAG 的模型 SDK infrastructure retry 为 2；`UnitGenerationPolicy` DTO 默认值为 0。并发、取消和 supersede 通过 PlanningRun 状态与 AttemptIdentity 保证结果隔离。Build 永远只消费 confirmed DAG。
