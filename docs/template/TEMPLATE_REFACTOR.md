# XCodeAgent 模板重构总体方案

> 本文定义 XCodeAgent 模板体系重构后的**总体架构与公共契约**。  
> 首次模板初始化的详细实施见 [`BOOTSTRAP_PLAN.md`](./BOOTSTRAP_PLAN.md)；  
> 后续 TechnicalPlan Revision 中模板能力收敛的详细实施见 [`TEMPLATE_RECONCILE_PLAN.md`](./TEMPLATE_RECONCILE_PLAN.md)。

---

# 第一章 方案设计

## 1.1 背景与目标

旧模板体系以 frontend/backend 模板仓库和 `main/auth` Git 分支表达模板差异，Electron 负责 clone 和分支选择，XCodeAgent Backend 再通过后处理补页面、菜单、路由等内容。随着登录、权限、审计、追踪、缓存、文件存储等固定技术能力增加，这种模式会产生以下问题：

1. Git 分支无法自然表达多个 Capability 的组合关系；
2. Electron 承担模板下载与初始化职责，边界过重；
3. 模板固定能力、业务骨架、业务实现和平台投影混在同一流程；
4. `templateVariant=main|auth` 成为多个模块的隐式事实源；
5. 首次模板生成和后续模板能力变化缺少统一状态协议；
6. Template Engine 与 XCodeAgent 对 TemplateState、Operation、Validation 的理解容易发生漂移；
7. 模板文件、结构化依赖、Agent 修改、用户修改和 Platform Projection 的所有权边界不清晰。

重构后的核心目标是：

> Template Engine 负责描述并计算“模板应该是什么”；XCodeAgent 负责把 Engine 输出的目标状态与操作协议安全协调到真实 Workspace，并在其上继续业务开发。

总体架构：

```text
Template Source
    = Capability / File / Dependency / Extension / Migration 定义

Template Engine Core
    = Desired State Planner
    = Source + Current TemplateState + RequestedConfig
      → CorePlanResult

Template Engine Service
    = Stateless HTTP Adapter + Package Builder

XCodeAgent
    = Actual Workspace Reconciler
    = Desired Capability 编译、Workspace Lock、Preflight、事务 Apply、Validation、恢复、Build 编排

Agent
    = 基于已经准备好的模板能力实现业务代码

Platform Projection
    = 基于确认的业务事实写入平台托管区域
```

---

## 1.2 跨仓库协议权威

Template Engine 仓库中的以下内容是模板协议的唯一权威：

```text
Engine REFACTOR.md
Engine OpenAPI
TemplateState Schema
ChangeSet Wire Schema
Operation Schema
ValidationPlan Schema
Contract / Acceptance Tests
```

XCodeAgent 不得维护比 Engine 更窄的“兼容模型”。

新增或调整以下内容时，必须先升级 Engine 契约，再同步 XCodeAgent：

```text
Capability
TemplateState 字段
Managed Model
Operation
Package 字段
ValidationPlan
错误码
```

XCodeAgent 的模型应是 Engine 协议的消费模型，而不是第二套独立定义。

---

## 1.3 总体职责边界

| 能力 | Template Source / Engine | XCodeAgent |
| --- | --- | --- |
| Capability 定义 | 负责 | 不负责 |
| Capability 依赖解析 | 负责 | 不负责 |
| 模板 Full File | 负责 | 不负责 |
| JSON/Maven Structured Node | 负责建模和 Diff | 负责安全 Apply |
| npm/Maven 模板依赖 | 负责目标状态 | 负责真实 Workspace Apply / Validation |
| Provider/Route/Interceptor 扩展点 | 负责 | 不负责 |
| Template Migration 定义与物化 | 负责 | 不执行真实业务 DB Migration |
| TemplateState Schema 与内容 | 定义 | 校验、持久化、读取 |
| Capability Diff / Refresh Diff | 负责 | 不负责 |
| ChangeSet / Operation 顺序 | 负责 | 原样执行，不重排 |
| validationPlan | 负责生成 | 在真实 Workspace 执行 blocking validation |
| TechnicalPlan 生命周期 | 不负责 | 负责 |
| Desired Capability 编译 | 不负责 | 负责 |
| 真实 Workspace 状态 | 不感知 | 负责 |
| Workspace Lock / TOCTOU 防护 | 不负责 | 负责 |
| ChangeSet 事务 Apply / Rollback | 不负责 | 负责 |
| 崩溃恢复 / Pending Apply | 不负责 | 负责 |
| Build DAG / Agent Build | 不负责 | 负责 |
| Platform Projection | 不负责 | 负责 |

正式原则：

```text
Template Engine = Desired State Planner
XCodeAgent      = Actual Workspace Reconciler
```

---

## 1.4 Template Capability 模型

Template Source 中每个固定技术能力以 Capability 表达，例如：

```text
login
authorization
tracking
audit
redis
file-storage
```

一个 Capability 可以声明：

```text
requires
provides
files
dependencies
extensions
migrations
configSchema
```

例如：

```text
authorization
    ↓ requires
login
```

调用方只表达 Desired Capability，依赖解析完全归 Engine：

```text
XCodeAgent 不得实现 authorization → login 的第二套依赖逻辑。
```

Engine V1 中 login / authorization 的 Config Schema 为空对象，因此 XCodeAgent V1 不开放通用 Capability Config Change；只有 Engine 契约正式支持 Config Binding 后才能启用。

---

## 1.5 TemplateState：模板领域唯一持久化事实

Workspace 中模板领域唯一持久化元数据固定为：

```text
.xcodeagent/template-state.json
```

这里的“唯一”指 **Template Engine 领域事实**。XCodeAgent 仍可维护独立的：

```text
.xcodeagent/runtime/template-runtime-state.json
```

用于 managed baseline、Bootstrap commit intent 与 Revision Finalization/WAL；该文件属于 XCodeAgent Runtime，不属于 Engine TemplateState，永远不作为 `currentTemplateState` 发送给 Engine。

所有权：

```text
Template Engine Owns Schema + Content
XCodeAgent Owns Persistence + Consumption
```

XCodeAgent 必须消费 Engine 完整 TemplateState，不得简化成四字段模型。

目标字段至少包括：

```text
templateRevision
requested
effective
capabilities
managed.files
managed.nodes
migrations
```

其中：

```text
managed.files
= Engine 管理的 Full File / Generated File

managed.nodes
= JSON_NODE / MAVEN_DEPENDENCY 等结构化托管节点

migrations
= Engine 已物化到 Workspace 的模板 Migration 记录
```

XCodeAgent 可以：

- 按 Engine OpenAPI / Schema 完整校验；
- 原子持久化；
- 将完整 JSON 内容作为 `/v1/plan`、`/v1/update` 的 `currentTemplateState`；
- 读取 effective capability 作为 Build/Projection capability gate；
- 计算完整 TemplateState 的 canonical digest 绑定 Build Run。

XCodeAgent 不可以：

- 自行新增/删除字段；
- 修改 Engine 返回的 requested/effective/capabilities/managed/migrations；
- 自行推进 templateRevision；
- 自行伪造 nextTemplateState。

不再引入：

```text
template-generation-manifest.json
templateVariant
template_variant
```

作为模板事实源。

---

## 1.6 Desired / Requested / Effective 状态模型

模板能力区分：

```text
Desired
Requested
Effective
```

### Desired

最新正式 TechnicalPlan 希望应用具备的模板能力：

```json
{
  "template_capabilities": {
    "authorization": {
      "enabled": true,
      "config": {}
    }
  }
}
```

### Requested

XCodeAgent 从已确认 TechnicalPlan 确定性编译为 Engine RequestedConfig：

```json
{
  "capabilities": {
    "authorization": {
      "enabled": true,
      "config": {}
    }
  }
}
```

### Effective

Engine 解析依赖后得到真实生效能力，例如：

```text
effective.authorization = true
effective.login = true
```

关系：

```text
TechnicalPlan.template_capabilities
        ↓
      Desired
        ↓
 RequestedConfig
        ↓
TemplateState.requested
        ↓
Engine Resolve
        ↓
TemplateState.effective + capabilities + managed + migrations
```

---

## 1.7 TechnicalPlan 是 Desired Capability 唯一权威源

正式目标模型：

```text
application.json
    = 创建阶段 Initial Intent

TechnicalPlan.template_capabilities
    = 当前正式 Desired Template Capability

TemplateState
    = 当前已 Apply 的 Engine Template State
```

首次创建：

```text
application.json
→ Requirement / ProductPlan
→ TechnicalPlan.template_capabilities
→ RequestedConfig
→ /generate
```

正式 Revision：

```text
Requirement Change
→ New TechnicalPlan
→ New template_capabilities
→ RequestedConfig
→ /plan + /update
```

因此 application.json 不得成为后续 Revision 的永久 Capability Authority。

### Markdown Round-trip

TechnicalPlan 的 Markdown Renderer、人工编辑同步和 Parser 必须完整支持 `template_capabilities`：

```text
TechnicalPlan JSON
→ Markdown
→ 人工修改
→ Parse
→ TechnicalPlan JSON
```

必须通过 round-trip 测试，确保字段不丢失、不降级。

### Authorization 一致性门禁

`authorization_manifest` 表示业务权限设计；`template_capabilities.authorization` 表示权限技术基础设施 Desired State。

TechnicalPlan Confirm Gate 至少要求：

```text
authorization_manifest.enabled == true
→ template_capabilities.authorization.enabled == true
```

V1 建议进一步要求二者一致，避免“模板有权限基础设施但业务方案声明无权限”的半状态。

---

## 1.8 Template Mutation 两种模式

### Bootstrap

```text
无 Workspace TemplateState
→ /v1/plan(current=null)         # Bootstrap 专用：获取目标状态与 validationPlan
→ /v1/generate
→ Full Package
→ Validation
→ Git baseline + TemplateMetadataCommit
```

详见 `BOOTSTRAP_PLAN.md`。

### Reconcile

```text
已有 Workspace + TemplateState
→ /v1/update                     # 权威计算入口
   ├─ 204 → Workspace Health Check
   └─ ZIP → Package Validate → Update Policy Gate → Transactional Apply
→ Validation
→ TemplateMetadataCommit
```

`/v1/plan` 在 Reconcile 中是**可选 Preview/Dry-run API**，不参与正常执行 binding；Plan 结果不会传给 Update，`/v1/update` 会独立重新计算。

定义：

```text
Bootstrap ≠ Reconcile
```

同时固定：

```text
/v1/plan   = advisory preview
/v1/update = authoritative reconcile result
```

如果未来要求 Preview 与 Apply 强绑定，应由 Engine 增加 `planToken/sourceRevision`，不能由 XCodeAgent 自己用 digest 模拟服务端会话状态。

## 1.9 Capability Reconcile 与 Template Refresh

Engine 定义：

```text
current.templateRevision == source.templateRevision
→ Same Revision Diff

current.templateRevision != source.templateRevision
→ Template Refresh
```

XCodeAgent V1 暂不开放 Template Refresh / Revision Upgrade。

Reconcile 不再依赖 `/v1/plan` 预判，而是在 `/v1/update` 返回 200 Update Package 后、首个 Workspace Apply 前检查：

```text
package.nextTemplateState.templateRevision
==
currentTemplateState.templateRevision
```

不相等则：

```text
TEMPLATE_REVISION_UPGRADE_NOT_SUPPORTED
```

即：

```text
Engine 可以计算 Refresh
XCodeAgent V1 可以拒绝执行 Refresh
```

收到 ZIP 不等于已 Apply；XCodeAgent 必须完成 Update Policy Gate 后才能触碰 Workspace。

## 1.10 ChangeSet 与 Operation 协议

XCodeAgent 必须支持 Engine V1 完整操作集：

```text
ADD_FILE
UPDATE_FILE
DELETE_FILE

UPSERT_JSON_NODE
DELETE_JSON_NODE

UPSERT_MAVEN_DEPENDENCY
DELETE_MAVEN_DEPENDENCY
```

Operation 由 Engine 排序，XCodeAgent 必须：

```text
严格按 change-set.json operations 原顺序执行
```

禁止：

```text
按 Operation Type 分组
重新排序 DELETE/ADD/UPDATE
由 Agent 再决定调用顺序
```

推荐实现：

```text
WorkspaceOperationApplier
        ↓
OperationHandlerRegistry
        ├── AddFileHandler
        ├── UpdateFileHandler
        ├── DeleteFileHandler
        ├── UpsertJsonNodeHandler
        ├── DeleteJsonNodeHandler
        ├── UpsertMavenDependencyHandler
        └── DeleteMavenDependencyHandler
```

这些 Handler 是 Backend 确定性执行器，不是 Agent Tool。

---

## 1.11 validationPlan / risks / diagnostics

Engine `ChangeSetBody` 中的：

```text
requested
effective
operations
validationPlan
risks
```

以及 Core diagnostics 都属于跨仓库协议。

XCodeAgent 不得只消费 operations。

### validationPlan

所有 `blocking=true` 的 Validation 必须在真实 Workspace 上执行，并位于 TemplateState Commit 之前：

```text
Apply Operations
→ Post Validation
→ Engine Blocking Validation
→ Commit next TemplateState
```

命令必须按参数数组执行，不经过 Shell。

### risks / diagnostics

- diagnostics 用于阻断性或可诊断错误展示；
- risks 必须进入 Reconcile 执行记录和用户可观测状态；
- XCodeAgent 不修改 Engine 返回内容。

---

## 1.12 Workspace Ownership

V1 不再只按“文件属于谁”划分 Ownership，而是同时区分**整文件、结构化节点、Marker Region**三种所有权。固定分类：

```text
ENGINE_EXCLUSIVE
SHARED_STRUCTURED_HOST
PLATFORM_OVERLAY
BUSINESS_AGENT
```

### ENGINE_EXCLUSIVE

Engine 对整个文件拥有唯一写权；Agent/Platform 不得修改。

XCodeAgent 为这类文件维护运行时冲突证据：

```text
.xcodeagent/runtime/template-runtime-state.json
  .managedBaseline.engineExclusiveFiles
```

只记录：

```text
ENGINE_EXCLUSIVE path → sha256(actual final bytes) + mode
```

Preflight：

```text
Workspace actual sha256
==
current committed baseline sha256
```

否则：

```text
TEMPLATE_MANAGED_FILE_CONFLICT
```

### SHARED_STRUCTURED_HOST

`package.json`、`pom.xml` 属于**共享结构化宿主**，不能按 Engine Exclusive 整文件保护。

V1 固定 Registry 至少包含：

| Host | Engine Ownership | Business Ownership |
|---|---|---|
| `frontend/package.json` | `TemplateState.managed.nodes` 中声明的 JSON Pointer | 其他未被 Engine 管理的 JSON Node / dependency |
| `backend/pom.xml` | `TemplateState.managed.nodes` 中声明的 Maven Dependency stable key | 其他未被 Engine 管理的 dependency / plugin / property |

规则：

1. Shared Structured Host **不进入 whole-file baseline**；
2. 当前 Engine-owned Node 的基线就是 committed `TemplateState.managed.nodes`；
3. Preflight 只验证 Engine-owned Node 当前值仍等于 Current TemplateState；
4. 业务/Agent 对**未被 Engine 声明管理的节点**做合法增删改，不构成模板冲突；
5. Agent/业务若修改与 Engine Managed Node 相同的 JSON Pointer 或 Maven stable key，则报 `TEMPLATE_MANAGED_NODE_CONFLICT`；
6. 同 Template Revision 的 Capability Reconcile 对已有 Shared Host 只允许结构化 Node Operation；若 Engine 返回 `UPDATE_FILE` / `DELETE_FILE` 覆盖整个 Shared Host，则 XCodeAgent V1 报 `SHARED_HOST_WHOLE_FILE_OPERATION_UNSUPPORTED`；
7. `ADD_FILE` 创建 Shared Host 只允许发生在首次 Bootstrap；
8. Template Refresh 当前禁止，因此 V1 不解决 Shared Host 的跨 Revision whole-file merge。未来 Refresh 必须新增 Base Node/Region Ownership 或 3-way merge 协议后才能开放。

因此：

```text
package.json / pom.xml
≠ ENGINE_EXCLUSIVE

它们是：
SHARED_STRUCTURED_HOST
```

这保证业务正常新增依赖不会让下一次 Capability Reconcile 永久冲突，同时 Engine 对自己声明的 Node 仍有可验证的确定性所有权。

### TemplateState + Runtime State 逻辑原子提交

V1 Template 子系统只保留两个权威 JSON：

```text
.xcodeagent/template-state.json
.xcodeagent/runtime/template-runtime-state.json
```

其中：

```text
template-state.json
= 完整 Engine TemplateState

template-runtime-state.json
= XCodeAgent 私有运行状态
  ├─ managedBaseline.engineExclusiveFiles
  └─ finalization | null
```

Shared Structured Host 的 Engine Managed Node 基线仍由 `TemplateState.managed.nodes` 表达，不进入 whole-file baseline。

`template-state.json` 与 runtime `managedBaseline` 必须作为一个 `TemplateMetadataCommit` 逻辑事务组提交。`template-runtime-state.json.finalization` 自身承担 WAL/commit intent：

```text
stage target template-state.json
stage target template-runtime-state.json
→ persist runtime finalization.phase = COMMITTING_METADATA + old/target digests
→ atomic replace runtime state（durable commit intent）
→ atomic replace template-state.json
→ atomic replace runtime state（target baseline + RECONCILED）
→ fsync metadata directories
→ Target Workspace Contract recheck
```

如果 crash 发生在两个 metadata 文件切换之间，Recovery 复验 Workspace 后只安全 roll-forward 或阻断；不能看到一个文件已更新就认为事务完成。

TemplateState 仍是模板领域唯一 applied truth；runtime managedBaseline 只是 XCodeAgent 的冲突证据。

现有 `.xcodeagent/checkpoints/checkpoints.sqlite` 继续只服务 LangGraph Checkpoint，不承载 Reconcile/WAL/Lease 自定义表。

### PLATFORM_OVERLAY

Engine 提供 Host/Scaffold，XCodeAgent Platform Projection 只拥有 marker region。V1 固定使用 `MARKER_REPLAY`。

| Host | V1 Ownership | Reconcile 策略 |
|---|---|---|
| `frontend/src/constants/routes.tsx` | Engine Host + Platform Marker Region | 提取 Region → Engine Operation → Marker Replay |
| `frontend/src/constants/resources.ts` | Engine Host + Platform Marker Region | **跨仓库契约门禁：Template Source 先提供稳定 marker host；XCodeAgent 再把 whole-file projection 改成 marker projection** |
| `backend/.../AuthConstants.java` | Engine Host + Platform Marker Region | 提取 Region → Engine Operation → Marker Replay |

`resources.ts` 不允许只改单侧。正式启用 Reconcile 前必须同时满足：

```text
Template Source / Engine Package
  contains fixed resources.ts marker pair

AND

XCodeAgent authorization_frontend_projection
  only writes marker region
```

两侧任一未满足：

```text
PLATFORM_OVERLAY_CONTRACT_NOT_READY
```

不得进入 Apply。

若 Registry 中出现 Whole-file Platform Projection 与 Engine Whole-file Ownership 重叠：

```text
PLATFORM_OVERLAY_UNSUPPORTED
```

V1 直接阻断。Platform Overlay Host 不进入 whole-file Engine Exclusive baseline，其健康性由 Host/marker/region contract 校验。

### BUSINESS_AGENT

业务文件由 Deterministic Skeleton / Agent Build 管理；Engine ChangeSet 不得越界修改。

## 1.13 Workspace Mutation Lease、Fencing 与 Ownership Guard

公共原则：Mutation Lease 必须落到 Backend 最低公共写入边界，而不是只在 Finalizer 上层持锁。

V1 采用：

```text
WorkspaceMutationManager
WorkspaceMutationContext
WorkspaceMutationFS
WorkspaceOwnershipPolicy
```

同一 Workspace 的 server-owned 写入要求运行在支持 POSIX `flock` 的共享文件系统语义下。外部 runtime registry 保存：

```text
<XCODEAGENT_RUNTIME>/workspace-mutation/<workspaceId>.lock
<XCODEAGENT_RUNTIME>/workspace-mutation/<workspaceId>.json
```

Acquire 固定：

```text
flock exclusive
→ epoch = previous + 1
→ atomic persist owner metadata + fsync
→ 保持 lock fd 存活
```

Crash 后内核释放 flock，下一 owner 获取后 epoch 递增；不使用 TTL 判断 stale lease。每次真实写入都执行 epoch fencing，旧 Context 报 `WORKSPACE_MUTATION_FENCED`。

入口覆盖：

```text
Bootstrap / Reconcile / Recovery / Rollback
Skeleton
Build Run
Projection
Direct Workspace Mutation
Application Delete
Attach cleanup/repair/recovery
```

实际写入覆盖：Agent File Tool、delete tool、workspace API、Platform Writer 等都必须经过 MutationFS/等价 Guard。

Ownership Guard 同时约束 caller：

- Agent 不得修改/删除 `ENGINE_EXCLUSIVE`；
- Agent 不得 whole-file 修改 `PLATFORM_OVERLAY` Host；
- Agent 可改 `SHARED_STRUCTURED_HOST` 的业务节点，但 before/after 中所有 Engine Managed Node 必须保持不变；
- Skeleton 只写 Skeleton Manifest 声明的业务文件；
- Projection 只写 Registry 声明的 marker region。

因此 Build Agent 的“last write wins”不能成为模板保护的旁路。

外部 IDE 不受 Lease 控制，所以仍需要 Preflight、Operation recheck、Metadata Recovery recheck，以及 Build 前后的 Managed Workspace Health Check。

## 1.14 Persistent Revision Finalization Journal / WAL / Recovery

V1 不新建 Runtime SQLite，也不复用 LangGraph `checkpoints.sqlite` 作为 Reconcile 数据库。

固定两个 Template 持久化 JSON：

```text
.xcodeagent/template-state.json
.xcodeagent/runtime/template-runtime-state.json
```

`template-runtime-state.json` 最小结构：

```json
{
  "schemaVersion": "template-runtime-state.v1",
  "managedBaseline": {
    "engineExclusiveFiles": {}
  },
  "bootstrapCommit": null,
  "finalization": null
}
```

Bootstrap Metadata Commit 期间仅临时使用 `bootstrapCommit`；Bootstrap 完成后清为 `null`。

Revision 期间 `finalization` 保存：

```text
changeId / binding
phase
Update Package digest/path
Operation WAL
Validation snapshot
Metadata Commit intent
Skeleton WAL
failure evidence
```

不再维护：

```text
active.json
per-change journal JSON
runtime/history journal archive
```

大 payload / backup 仍可放：

```text
.xcodeagent/runtime/template-finalization/staging/<changeId>/...
.xcodeagent/runtime/template-finalization/backup/<changeId>/...
```

这些只是 recovery artifact，不是状态真相源。

### Finalizer 入口顺序

```text
Resolve workspaceId/changeId
→ Acquire Mutation Lease
→ Load lifecycle + template-runtime-state.json
→ Recover finalization
→ RELOAD lifecycle + template-state.json + template-runtime-state.json
→ Load confirmed TechnicalPlan
→ Compile RequestedConfig
→ /v1/update
```

Recovery 可能改变 TemplateState/runtime baseline，因此严禁使用 Recovery 前缓存的 `currentTemplateState`。

### Operation WAL

每个 Operation 固定：

```text
Prepare backup/staged after + fsync
→ persist finalization.currentOperationWal=PREPARED + fsync runtime state
→ recheck before
→ atomic Apply + fsync target/parent
→ verify after
→ persist APPLIED + nextOperationIndex + fsync runtime state
```

Rollback 本身也使用 reverse WAL。Structured Operation backup/restore 完整 Host file。

### Metadata Commit / Rollback Boundary

`COMMITTING_METADATA` 之前失败可 rollback old Workspace；一旦 durable 进入 `COMMITTING_METADATA`，Recovery 不再自动 rollback，只能 Target Workspace Contract recheck 后 roll-forward，无法证明安全则 `METADATA_RECOVERY_BLOCKED`。

`RECONCILED` 后 Skeleton/Continuation 失败保留新 TemplateState，不回滚模板。

### Skeleton / Lifecycle

Skeleton 先生成稳定 `SkeletonManifest`，逐项记录 PREPARED/APPLIED 和 next index。Crash 后验证已完成项并继续，不重新执行 Engine ChangeSet。

权威划分：

- lifecycle：`changeId`、formal branch、confirmed binding、continuation hash/generation/consumed；
- `template-runtime-state.json.finalization`：Engine Apply、WAL、Metadata Commit、Skeleton 细粒度进度；
- identity/continuation 冲突时 lifecycle 优先，mutation phase 冲突时 finalization 优先。

`COMPLETED` 且 lifecycle 已进入 continuation-ready/building 后，将 `finalization` 幂等清为 `null`。完整 Recovery Matrix 与人工验收见 `TEMPLATE_RECONCILE_PLAN.md`。

## 1.15 NO_CHANGE 不等于 Workspace Healthy

Reconcile 主流程以 `/v1/update` 的权威结果分支：

```text
/v1/update → 204
→ Managed Workspace Health Check
→ 成功后进入 Skeleton / Revision Finalizer

/v1/update → 200 ZIP
→ Package Validation
→ Update Policy Gate
→ Transactional Apply
```

`/v1/plan` 不参与正常 NO_CHANGE 判定。

Engine Core 契约要求 `NO_CHANGE` 仅在：

```text
normalized Target TemplateState == Current TemplateState
AND
operations == []
```

时成立，因此 `/v1/update → 204` 不需要推进 TemplateState。它只说明模板目标状态没有变化，XCodeAgent 仍必须检查真实 Workspace：

```text
managed.files 宿主存在且为普通文件
ENGINE_EXCLUSIVE actual digest == runtime managedBaseline
managed.nodes 宿主存在且可解析、Managed Node 值匹配
migrations 对应文件存在
Platform marker 完整
```

因此：

```text
204 NO_CHANGE ≠ Workspace Healthy
```

可选 `/v1/plan` Preview 即使曾返回 NO_CHANGE，也不能替代本次 `/v1/update` 的 204。

## 1.16 Structured Operation 与 Validation 副作用事务边界

JSON Node / Maven Dependency 与 File Operation 使用同一个 WAL；Structured Host 必须先生成完整 after bytes 到 staging，再原子 replace，不能在真实 Host 上原地 patch。

V1 文件操作只支持 UTF-8 普通文本：

- ADD 默认 `0644`；
- UPDATE 保留原 mode；
- rollback 恢复 bytes + mode；
- symlink/device/socket/directory operation 拒绝；
- 大小写不敏感 Workspace 做 normalized path collision 检测。

Engine Blocking Validation 在独立 process group 中运行，Journal/Snapshot 记录 `validationRunId + pid/pgid + commandDigest + startedAt`。Crash Recovery 先终止仍属于该 Validation Run 的 orphan process group，再扫描副作用和 rollback。

`ValidationMutationSnapshot`：

```text
persistent file set + sha256
allowed persistent side effect before image
exact excluded runtime paths for current changeId/leaseId
```

允许：声明的 ephemeral output、`pnpm-lock.yaml` 等公共 policy 中明确的 side effect；其他源码/配置或非当前事务 `.xcodeagent/**` 变化均报 `VALIDATION_SIDE_EFFECT_CONFLICT`。

## 1.17 Build Binding 与 Managed Workspace Health

BuildContext 固定使用语义明确字段：

```json
{
  "template_context": {
    "state_path": ".xcodeagent/template-state.json",
    "template_state_jcs_sha256": "...",
    "template_revision": "...",
    "effective_capabilities": {}
  }
}
```

`template_state_jcs_sha256 = canonical_json_sha256_v1(parse(template-state.json))`，即 RFC 8785 JCS + SHA-256，不是文件 raw bytes hash。

继续区分：

```text
artifact_file_sha256       # TechnicalPlan 等正式文件 raw bytes
canonical_json_sha256_v1   # RequestedConfig / TemplateState / ChangeSet
```

Build 启动前必须：

```text
State JCS digest == BuildContext
+ Managed Workspace Health Check
```

Build Agent 写入受 Ownership Guard 约束；Build 完成、Projection 前再次 Managed Workspace Health Check。

因此 Build 不能只依赖 `state_sha256`，也不能只证明 State 没变而忽略真实 Workspace 漂移。

## 1.18 Template Capability、Skeleton、Agent、Projection 边界

```text
Template Capability
= Engine 固定技术能力

Deterministic Skeleton
= TechnicalPlan 可确定推导的业务骨架

Agent Build
= 非确定性业务实现

Platform Projection
= 平台托管的业务事实投影
```

顺序：

```text
Template Mutation Ready
        ↓
Deterministic Skeleton
        ↓
Workspace Inspection
        ↓
Build DAG
        ↓
Agent Build
        ↓
Platform Projection
        ↓
Validation
```

Skeleton 不得继续使用“存在则覆盖、异常吞掉”的逻辑；它也必须遵守 Workspace Mutation Lease、Conflict Policy 和 Atomic Write。

---

## 1.19 Capability Remove 的产品语义

Engine 可以计算 Capability Remove，但：

```text
删除模板 Managed File / Node
≠
回滚运行态业务数据
≠
回滚数据库 Schema
≠
删除业务代码中的权限引用
```

例如 authorization 移除后可能残留：

```text
resource / role / role_resource / role_member
业务 Controller 注解
Platform resource 常量
历史运行态配置
```

因此 XCodeAgent V1 初始发布建议：

```text
支持 Capability Add
支持 NO_CHANGE
暂不开放通用 Capability Remove
```

Remove Gate 不能比较 `Current Effective` 与 `RequestedConfig`，因为 Effective 可能包含 Engine 自动引入的 required dependency。必须比较 Engine 已解析依赖后的两个 Effective State：

```text
removedEffective =
    enabled(currentTemplateState.effective)
    - enabled(plan.nextTemplateState.effective)

removedEffective 非空
→ CAPABILITY_REMOVE_NOT_SUPPORTED
```

例如 authorization 仍启用时，即使 RequestedConfig 不显式声明 login，Plan Next Effective 仍应包含 login，因此不会把自动依赖误判为 Remove。

后续只有 Capability 定义完整 `decommission contract` 后才开放 Remove。

---

## 1.20 Legacy Workspace 切换边界

新版 Reconcile V1 只对**由新版 Bootstrap 生成完整 Engine V1 TemplateState** 的 Workspace 开启。

兼容性检查至少要求当前 State 具备：

```text
capabilities
managed.files
managed.nodes
migrations
```

旧四字段 TemplateState：

```text
templateRevision
managedFiles
requested
effective
```

不得在首次 Reconcile 时自动猜测/升级，因为缺少结构化 Managed Node、Migration 与可靠 ownership 信息。

检测到旧 State：

```text
LEGACY_TEMPLATE_STATE_UNSUPPORTED
```

行为：

- 不进入新版 Template Reconcile；
- 现有 Workspace 可继续走明确保留的 legacy 非模板变更路径；
- 涉及 Capability 变化的 Formal Revision 必须阻断并提示需要显式迁移；
- V1 不提供自动原地迁移，后续若支持必须设计独立 `TemplateState Migration` 流程与 EDD。

因此新版 Reconcile 的切换条件是“**新 Bootstrap State**”，不是应用创建日期或代码版本。

---

# 第二章 实施计划与人工门禁

公共架构只保留实施顺序；Bootstrap 和 Reconcile 的具体人工命令分别见 `BOOTSTRAP_PLAN.md` 与 `TEMPLATE_RECONCILE_PLAN.md`。

## 2.1 实施顺序

| Step | 目标 | 人工可见证据 | 未通过时 |
|---|---|---|---|
| 00 | 实际 Engine/OpenAPI/Package 达到目标契约 | `/plan`、`/update`、7 Operation、完整 State、resources marker | Feature Flag 关闭 |
| 01 | XCodeAgent 完整 Model + JCS digest | Java/Python golden vector 一致 | 不进入 Planning 改造 |
| 02 | TechnicalPlan `template_capabilities` + Markdown round-trip | JSON→Markdown→JSON 等价 | 不确认 Capability Revision |
| 03 | Ownership Registry + Agent/Workspace Write Guard | Agent 改 Engine Exclusive 被拒；Shared Host 业务 node 可改 | 不进入 Build 接入 |
| 04 | Mutation Lease + `flock` + epoch fencing | 两 worker 互斥、kill 后 epoch+1、旧 context fenced | 不进入事务 Apply |
| 05 | Journal/Recovery Matrix | 每个 phase 有唯一恢复结果；Recovery 后 State reload | 不调用 `/plan` |
| 06 | Package Validator + 7 Handler + Operation/Rollback WAL | PREPARED/after-write fault injection 均恢复 old Workspace | 不进入 Metadata Commit |
| 07 | Validation + Metadata Commit | side-effect、partial metadata rename、Workspace divergence 可人工复验 | 不进入 Skeleton |
| 08 | Skeleton WAL + Lifecycle/Retry/Continuation | Skeleton crash resume、token reissue、同 changeId Retry | 不 continuation |
| 09 | Build Binding + Managed Health + Projection | `template_state_jcs_sha256`、Agent Guard、marker replay | 不启用正式 Build |
| 10 | Legacy Gate + Feature Flag Cutover | 新 Workspace E2E PASS；旧四字段 State 明确阻断 | 不默认启用 |

## 2.2 每一步的人工验收要求

所有 Step 必须提供：

```text
validation/<feature>/verify-step-XX-*.sh
```

脚本要求：

1. 失败返回非 0；
2. 输出关键 phase / digest / error code；
3. 保留可 `jq/grep/git diff` 检查的 Fixture/临时 Workspace；
4. 不只打印 `PASS`，必须让人看到“输入是什么、系统做了什么、最终状态是什么”；
5. fault injection Step 必须明确 kill point。

## 2.3 发布范围

V1 正式支持：Capability Add、Same Capability NO_CHANGE、7 类 Operation、Blocking Validation、Persistent WAL/Recovery、Skeleton/Continuation Recovery、Ownership/Overlay 保护、Build State + Workspace Health Binding。

继续关闭：Template Revision Refresh、通用 Capability Remove、Capability Custom Config、自动 Merge 人工模板修改、真实数据库 Migration Rollback。

## 2.4 Definition of Done

- [ ] Engine 实际部署版本通过 Consumer Contract Test；
- [ ] Recovery 先于 State Load，Recovery 后 State/Baseline 强制 reload；
- [ ] Operation 与 Rollback 都有 durable WAL；
- [ ] Verified Update Package 可跨 crash 恢复；
- [ ] Journal 所有 phase 有唯一 Recovery 动作；
- [ ] Mutation Lease 使用真实互斥 + epoch fencing；
- [ ] Agent Build 受 Ownership Guard；
- [ ] Validation orphan process 可恢复清理；
- [ ] `COMMITTING_METADATA` 后不再使用 pre-commit rollback 语义；
- [ ] Skeleton 有逐项可恢复进度；
- [ ] Lifecycle/Journal 权威关系闭合；
- [ ] Build 使用 `template_state_jcs_sha256` + Managed Workspace Health；
- [ ] `resources.ts` Engine marker host 与 XCodeAgent marker writer 同步发布；
- [ ] 每个实施 Step 都存在人工可执行验收入口；
- [ ] Reconcile 整体案例和 fault-injection 变体通过。
