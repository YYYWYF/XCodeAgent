# XCodeAgent Template Capability Reconcile 实施方案

> 本文描述**已有 Workspace 在正式 TechnicalPlan Revision 后的模板能力收敛**。
> 公共协议见 [`TEMPLATE_REFACTOR.md`](./TEMPLATE_REFACTOR.md)；首次初始化见 [`BOOTSTRAP_PLAN.md`](./BOOTSTRAP_PLAN.md)。

本文只包含 **XCodeAgent Backend / Workspace / AG-UI** 的实施计划。Template Engine 被视为
已经按 `TEMPLATE_REFACTOR.md` 交付的外部依赖；本文不安排、不验证、也不描述 Engine 的
接口、模板源、marker、OpenAPI、Java 实现或发布工作。XCodeAgent 仅对已收到的响应和
Package 做本地安全校验。

---

# 第一章 方案设计

## 1.1 目标

首次 Bootstrap 完成后，正式 TechnicalPlan Revision 可能新增固定技术能力，例如 authorization。

Reconcile 不重新下载完整模板、不重新初始化 Git，也不让 Agent 补模板固定代码。

**V1 固定主流程以 `/v1/update` 为唯一权威计算入口；`/v1/plan` 不再是必经步骤。Recovery 必须发生在重新读取 Current TemplateState 之前：**

```text
New TechnicalPlan Confirmed
        ↓
Resolve workspaceId + active changeId
        ↓
Acquire Revision Finalization Run Gate
        ↓
Load lifecycle + template-runtime-state.json
        ↓
Recover / Resolve existing Finalization State
        ↓
RELOAD lifecycle + template-state.json + template-runtime-state.json
        ↓
Load confirmed TechnicalPlan + verify binding
        ↓
Compile RequestedConfig
        ↓
POST /v1/update
        │
        ├─ 204 NO_CHANGE
        │      ↓
        │   Managed Workspace Health Check
        │
        └─ 200 Update Package
               ↓
          Package Validation
               ↓
          Update Policy Gate
          ├─ Template Revision Gate
          ├─ Capability Remove Gate
          ├─ Risk Gate
          ├─ Operation/Ownership Gate
          └─ Config Gate
               ↓
          Persist minimal reconcileAttempt
               ↓
          Apply with Git rollback boundary
               ↓
          Post Validation
               ↓
          Blocking Validation + Side-effect Check
               ↓
          TemplateMetadataCommit
          (template-state.json + runtime managedBaseline)
        ↓
Attempt → RECONCILED
        ↓
Deterministic Skeleton
        ↓
Ensure/Reissue Revision Continuation
        ↓
Release Revision Finalization Run Gate
        ↓
Consume continuation / enter building
        ↓
inspect_workspace
        ↓
prepare_build_tasks
        ↓
Build Preflight: State JCS digest + Managed Workspace Health
        ↓
Agent Build
        ↓
Managed Workspace Health Recheck
        ↓
Platform Projection / Final Validation
        ↓
```

关键顺序约束：

1. **不能先 Load TemplateState 再 Recovery。** Recovery 可能执行 Git 回退或发现 metadata 中断；Recovery 完成后必须重新读取两个 Template JSON。
2. Revision Finalization Run Gate 必须先于 Recovery 与任何 Reconcile Workspace 写入取得；同一 `changeId` 只允许一个 Finalizer 实例执行。
3. `/v1/update` 返回的 `204` 或 ZIP 才是本次 Reconcile 的权威结果；可选 `/v1/plan` 的历史结果不得替代 Update Package Policy Gate。
4. Build 不仅绑定 TemplateState digest，还必须在 Build 前后做 Managed Workspace Health Check，防止用户/IDE 在 Reconcile 完成后篡改 Engine-owned 内容。
5. `template-state.json` 与 `template-runtime-state.json.managedBaseline` 作为一个 crash-consistent `TemplateMetadataCommit` 逻辑事务组提交。

核心原则：

> Template Engine 通过 `/v1/update` 决定本次权威 Desired State、ChangeSet 和 Operation 顺序；XCodeAgent 只在收到结果后执行本地 Policy Gate，并以 Git 回退、最小 Attempt 与人工可验收证据安全应用到真实 Workspace。

## 1.2 V1 范围

正式支持：

```text
Capability Add
Same Capability NO_CHANGE
7 类 Engine Operation
原顺序 Apply
Blocking Validation
TemplateRuntimeState 最小 Attempt
Crash Recovery
Git 回退 / 强阻断
Revision Gate
Platform Overlay 保护
```

暂不支持：

```text
Template Revision Refresh / Upgrade
Capability Custom Config Change
通用 Capability Remove
自动 Merge 人工模板修改
数据库 Schema/Data 自动回滚
```

如果 `/v1/update` 的权威结果触发上述未支持语义，必须在首个 Apply 前由 Update Policy Gate 阻断。

---

## 1.3 触发条件与 Revision Gate

Reconcile 只发生在：

```text
Workspace 已 Bootstrap
.xcodeagent/template-state.json 存在
TechnicalPlan 正式 Revision 已确认
```

它不重新进入：

```text
GENERATING_APPLICATION_TEMPLATE_FILES
```

TechnicalPlan 已确认但 Reconcile 未完成时：

```text
不得 continuation
不得 Build
不得 Projection
```

Reconcile 是正式 Revision continuation 的硬门禁。

---

## 1.4 TechnicalPlan Desired Capability

唯一 Desired Authority：

```text
New TechnicalPlan.template_capabilities
```

必须完成：

```text
TechnicalPlan JSON Schema
Markdown Renderer
Markdown Parser / Sync
Round-trip Test
```

application.json 不能阻塞后续 Capability Revision。

Authorization Confirm Gate：

```text
authorization_manifest.enabled
与
template_capabilities.authorization.enabled
```

V1 要求一致。

Engine V1 login/authorization config 为空对象，XCodeAgent 拒绝非空 Config。

---

## 1.5 Recovery 后读取 Current TemplateState

Template Service 不读取 XCodeAgent Workspace。

Finalizer 固定入口顺序：

```text
resolve workspaceId/changeId
→ acquire Revision Finalization Run Gate
→ load lifecycle + template-runtime-state.json
→ recover/resolve finalization
→ reload lifecycle
→ reload .xcodeagent/template-state.json
→ reload .xcodeagent/runtime/template-runtime-state.json
→ validate applied state + managedBaseline + recovered phase
→ compile RequestedConfig
```

只有完成 Recovery 后，XCodeAgent 才读取当前：

```text
.xcodeagent/template-state.json
```

并按完整 Engine Schema 反序列化/校验：

```text
templateRevision
requested
effective
capabilities
managed.files
managed.nodes
migrations
```

然后把**恢复后的完整 JSON Object**作为 `/v1/update.currentTemplateState`。不是传文件路径，也不得缓存 Recovery 之前读取到的旧 State。

同时读取：

```text
.xcodeagent/runtime/template-runtime-state.json.managedBaseline
```

用于 Engine Exclusive 文件冲突检查。该 runtime 文件是 XCodeAgent 私有状态，**绝不发送给 Template Engine**。

如果 Recovery 改写了 TemplateState 或 TemplateRuntimeState，Finalizer 必须丢弃之前的内存对象和 digest，重新计算：

```text
currentTemplateStateJcsSha256
currentManagedBaselineSha256
```

否则报：

```text
RECOVERED_STATE_RELOAD_REQUIRED
```

并阻断 `/v1/update`。

## 1.6 `/v1/plan` 的定位：可选 Preview，不参与主流程绑定

`/v1/plan` 与 `/v1/update` 是相互独立的 Stateless HTTP Adapter。对 XCodeAgent 而言，
两者请求只包含：

```text
currentTemplateState
+
requestedConfig
```

**`/v1/plan` 的结果不会传给 `/v1/update`，XCodeAgent 也不保存可供 `/v1/update`
消费的 planId / plan snapshot。**

因此 V1 固定：

```text
正常 Revision Reconcile
→ 不调用 /v1/plan
→ 直接 /v1/update
```

`/v1/plan` 只用于：

```text
人工预览 / Dry-run
管理后台查看 CHANGE / NO_CHANGE
调试与 EDD
未来显式用户审批前的变化展示
```

如果显式调用了 `/v1/plan`，其结果仅是 advisory preview：

- 不持久化为执行 binding；
- 不生成 `planDigest`；
- 不把 plan body 作为 `/v1/update` 参数；
- `/v1/update` 仍重新计算一次；
- 真正 Apply 前只信任本次 `/v1/update` 返回的 204 或 Update Package。

如果未来要求“用户批准的 Preview 必须与实际 Update 完全相同”，需要新增外部
`planToken/sourceRevision` 绑定契约；这不是 V1。

## 1.7 `/v1/update`：Reconcile 权威入口

请求：

```json
{
  "currentTemplateState": { "...": "完整 TemplateState JSON Object" },
  "requestedConfig": { "capabilities": {} }
}
```

Engine Service 内部重新执行：

```text
Core.plan(currentTemplateState, requestedConfig)
        ↓
NO_CHANGE → HTTP 204
CHANGE    → build Update Package → HTTP 200 application/zip
```

### 204 NO_CHANGE

```text
/v1/update → 204
→ 不创建 Operation record 或 Workspace mutation
→ Managed Workspace Health Check
→ 成功后进入 Deterministic Skeleton / Revision Finalizer
```

`204` 的前提必须由 Engine Contract 保证：`NO_CHANGE` 仅在“规范化 Target TemplateState == Current TemplateState 且 Operations 为空”时成立。因此 204 不需要推进本地 TemplateState，但它仍不表示 Workspace 健康。

### 200 Update Package

```text
/v1/update → ZIP
→ Package Validator
→ Update Policy Gate
→ Persist verified package
→ Transactional Apply
```

收到 ZIP 本身不产生任何 Workspace 修改；所有 Gate 必须在首个 Apply 之前完成。

## 1.8 Update Package Policy Gate

Gate 的输入只来自：

```text
Recovered Current TemplateState
RequestedConfig
Verified Update Package.change-set.json
Verified Update Package.next-template-state.json
Local Ownership Registry
```

### Template Revision Gate

```text
package.nextTemplateState.templateRevision
==
currentTemplateState.templateRevision
```

不相等表示 Engine 进入 Refresh：

```text
TEMPLATE_REVISION_UPGRADE_NOT_SUPPORTED
```

V1 拒绝继续。

### Capability Remove Gate

不能使用 `Current Effective - RequestedConfig` 判断 Remove，因为 RequestedConfig 不包含 Engine 自动引入的 required dependency。

必须比较 Engine 已完成依赖解析后的权威 Target Effective：

```text
removedEffective =
    enabled(currentTemplateState.effective)
    - enabled(package.nextTemplateState.effective)
```

如果非空：

```text
CAPABILITY_REMOVE_NOT_SUPPORTED
```

例如 authorization 仍启用而 RequestedConfig 未显式声明 login 时，Target Effective 仍包含 login，不得误判为移除。

### Config Gate

V1 Login / Authorization Config Schema 固定为空对象。出现非空 Capability Config：

```text
CAPABILITY_CONFIG_CHANGE_NOT_SUPPORTED
```

### Risks / Diagnostics

`change-set.json.risks` 是实际 Apply 的风险事实：

```text
LOW / MEDIUM risk → 记录/展示，不阻断
HIGH risk         → TEMPLATE_UPDATE_HIGH_RISK，阻断自动 Apply
```

Engine `diagnostics` 当前属于 `/v1/plan` 响应，不在 V1 Update Package 中，因此：

- 主流程不得依赖 diagnostics 做安全门禁；
- 可选 Preview 时 diagnostics 只用于展示/调试；
- 如果未来 diagnostics 需要阻断 Update，必须先升级 Update Package Contract 使其成为权威执行输入。

### Operation / Ownership Gate

必须确认：

```text
Operation 类型均为 V1 支持的 7 类
Operation path 合法
Shared Structured Host 没有不支持的 whole-file UPDATE/DELETE
Platform Overlay marker contract ready
Engine Operation 没有越界到 BUSINESS_AGENT ownership
```

任一失败都在 Apply 前阻断。

## 1.9 Update Package Contract

```text
update-package.zip
├── change-set.json
├── next-template-state.json
└── payload/
    └── ADD_FILE / UPDATE_FILE 对应 path
```

`change-set.json` 是完整 Engine `ChangeSetBody`，包含：

```text
requested
effective
operations
validationPlan
risks
```

`payload/` 只对应 ADD_FILE / UPDATE_FILE；DELETE 和结构化 Node Operation 不制造伪 Payload。

Update Package 是本次执行的唯一权威计划快照。XCodeAgent 必须计算并持久化：

```text
changeSetJcsSha256
nextTemplateStateJcsSha256
updatePackageSha256
```

用于 Recovery 与人工验收。

## 1.10 `/v1/plan` 与 `/v1/update` 一致性的处理原则

V1 **不做 Plan/Package Alignment**，因为正常 Reconcile 不需要先调用 `/v1/plan`。

如果人工 Preview 先调用了 `/v1/plan`，之后 `/v1/update` 返回不同结果：

```text
以 /v1/update 为权威
→ 重新执行 Update Policy Gate
→ 不沿用旧 Preview 的任何允许/阻断结论
```

这消除了：

```text
Plan Snapshot 持久化
planJcsSha256
Plan → Update 一致性比较
两次请求之间 Source 变化导致的 mismatch 状态机
```

如果业务未来要求 Preview 与 Apply 强绑定，则另行增加外部 `planToken/sourceRevision`
契约；XCodeAgent V1 不用 digest 猜测绑定。

## 1.11 完整 Operation 集

XCodeAgent 必须支持 Engine V1：

```text
ADD_FILE(path, content)
UPDATE_FILE(path, content)
DELETE_FILE(path)

UPSERT_JSON_NODE(path, pointer, value)
DELETE_JSON_NODE(path, pointer)

UPSERT_MAVEN_DEPENDENCY(path, key, value)
DELETE_MAVEN_DEPENDENCY(path, key)
```

实现：

```text
WorkspaceOperationApplier
        ↓
OperationHandlerRegistry
```

Handler 是平台内部确定性执行器，不注册成 Agent Tool。

---

## 1.12 Operation 顺序

唯一合法执行方式：

```python
for index, operation in enumerate(change_set.operations):
    handler_registry.apply(operation)
```

禁止：

```text
按文件操作重新分组
先 ADD 后 UPDATE 后 DELETE
Agent 再决定顺序
```

Engine 已决定语义顺序，XCodeAgent 不得重排。

---

## 1.13 Package Validator

Apply 前至少校验：

```text
change-set.json exactly one
next-template-state.json exactly one
payload only ADD_FILE / UPDATE_FILE
payload bytes == operation content
path normalized relative POSIX
no absolute / .. / symlink / special file
no .git mutation
ChangeSet 不直接写 .xcodeagent/template-state.json
operation type in Engine V1 allow-list
next TemplateState 完整 Schema 合法
```

并验证 Operation 与 Current/Next Managed State 的过渡一致性。

V1 文件系统边界同时冻结：

- 只处理 UTF-8 普通文本文件；
- `ADD_FILE` 默认 mode=`0644`；`UPDATE_FILE` 保留原 mode；rollback 恢复原 mode；
- 拒绝 symlink / socket / device / directory operation；
- 对 normalized path 做大小写碰撞检测；在大小写不敏感 Workspace 上若两个 Operation 归一到同一实际路径则 `WORKSPACE_PATH_COLLISION`；
- 不试图保留 mtime 作为语义状态。

---

## 1.14 Managed Workspace Health Check

用于 NO_CHANGE 和 Apply Preflight。

根据 Current TemplateState：

```text
managed.files
→ target 存在且为普通文件

managed.nodes
→ host 存在、为普通文件、可按 nodeType 解析

migrations
→ 对应物化文件存在

Platform Overlay
→ marker / region 完整
```

失败时：

```text
MANAGED_WORKSPACE_UNHEALTHY
```

不得 continuation。

---

## 1.15 Ownership / Conflict Model

V1 固定四类 Ownership：

```text
ENGINE_EXCLUSIVE
SHARED_STRUCTURED_HOST
PLATFORM_OVERLAY
BUSINESS_AGENT
```

### Engine Exclusive

只有 Engine 真正拥有**整个文件**的 Host 才进入 XCodeAgent 私有 runtime baseline：

```text
.xcodeagent/runtime/template-runtime-state.json
  .managedBaseline.engineExclusiveFiles[path]
    → sha256(actual final bytes) + mode
```

Preflight：

```text
current Workspace bytes sha256
== current committed baseline[path]
```

否则 `TEMPLATE_MANAGED_FILE_CONFLICT`。

### Shared Structured Host

`frontend/package.json`、`backend/pom.xml` 不属于 Engine Exclusive，而是 Shared Structured Host：

```text
Engine owns: Current TemplateState.managed.nodes
Business owns: nodes not declared by Engine
```

Preflight：

```text
JSON_NODE:
  current value at RFC6901 pointer == current state managed node value

MAVEN_DEPENDENCY:
  current dependency at stable key == current state managed node value
```

只要业务新增/修改的是未被 Engine 管理的其他节点，就不构成冲突。相同 Pointer/Stable Key 被业务改写则报：

```text
TEMPLATE_MANAGED_NODE_CONFLICT
```

Same-Revision Capability Reconcile 对已有 Shared Host 只允许：

```text
UPSERT_JSON_NODE / DELETE_JSON_NODE
UPSERT_MAVEN_DEPENDENCY / DELETE_MAVEN_DEPENDENCY
```

若 `/v1/update` 对已有 Shared Host 返回 `UPDATE_FILE` / `DELETE_FILE`：

```text
SHARED_HOST_WHOLE_FILE_OPERATION_UNSUPPORTED
```

V1 阻断。首次 Bootstrap 的 `ADD_FILE` 除外。Template Refresh 当前本来就禁止，未来开放 Refresh 前必须先解决 Shared Host 的跨 Revision merge。

### Platform Overlay

V1 Overlay Registry：

| Host | Strategy | Release Gate |
|---|---|---|
| `frontend/src/constants/routes.tsx` | `MARKER_REPLAY` | XCodeAgent 校验收到的 Host marker 并回放 Platform Region |
| `frontend/src/constants/resources.ts` | `MARKER_REPLAY` | XCodeAgent 校验收到的 Host marker，并将现有 whole-file projection 改为 region-only projection |
| `backend/.../AuthConstants.java` | `MARKER_REPLAY` | XCodeAgent 校验收到的 Host marker 并回放 Platform Region |

`resources.ts` marker 被视为既定输入契约。XCodeAgent 只验证收到的 Host 中 marker pair
唯一、完整，并验证自身 Projection Writer 仅修改 Platform Region；任一不满足：

```text
PLATFORM_OVERLAY_CONTRACT_NOT_READY
```

`MARKER_REPLAY`：

```text
Apply 前提取 Platform-owned marker region
→ 校验 marker pair 唯一且完整
→ Engine Whole-file Operation
→ 校验新 Host 仍含 compatible marker
→ 确定性回放 Platform Region
→ Post Validation
```

Platform Overlay Host 不进入 whole-file Engine Exclusive baseline。

### Business / Agent

Engine Operation 不得越界修改业务文件。Agent Build、Skeleton、Projection、Delete 和通用
workspace API 不属于本方案的模板 Ownership Guard 范围；它们各自遵守现有 DAG、Projection
和生命周期边界。

## 1.16 Revision Finalization 防重入与 TOCTOU

V1 前提是：用户能返回技术规划阶段，表示没有仍在执行的 Workspace 写任务。Build 只会在
Finalization 成功、Continuation 被消费后开始；因此 Reconcile 不需要全局 Workspace Lease、
跨流程锁或 epoch fencing。

Reconcile 只需要由 application lifecycle 的 CAS 建立 **Revision Finalization Run Gate**：

```text
TechnicalPlan confirmed
→ CAS active formal revision.status = template_reconciling
→ Finalizer 以 changeId 执行
→ success: continuation_ready
→ failure: template_reconcile_failed / skeleton_failed
```

规则：

- 同一 `changeId` 的重复请求读取已有 runtime finalization，恢复或返回当前进度，不得再发起第二次 `/v1/update`；
- lifecycle 表明存在 Build 或其他写任务时，拒绝进入技术规划/Finalizer；
- Attach/Retry 先恢复该 `changeId` 的 runtime finalization，完成或失败收敛后才允许新请求；
- Blocking Validation 的 orphan process 仍必须在 Recovery 前按 Journal 中的 process identity 终止；这是同一 Finalizer 的 crash cleanup，不是全局并发控制。

外部 IDE 仍不能被 lifecycle gate 阻断，因此 Reconcile 保留局部 TOCTOU 检查：

```text
Preflight
+ 每个 Operation 写入前 recheck
+ Metadata Commit 前 final recheck
+ Crash Recovery 在无法安全 Git 回退时强阻断，不自动写 Workspace
```

生命周期的 `template_finalizing` CAS 仅防同一 Revision 重复进入；digest/contract recheck 解决外部进程改写。V1 不引入 Workspace Lease。

## 1.17 最小持久化 ReconcileAttempt

V1 不引入新的 SQLite Runtime DB，也不把 Template Reconcile 状态写入现有 LangGraph
`checkpoints.sqlite`。Template 子系统只维护两个权威 JSON：

```text
.xcodeagent/template-state.json
.xcodeagent/runtime/template-runtime-state.json
```

`template-state.json` 只保存已经成功应用的完整 TemplateState；
`template-runtime-state.json` 保存长期 `managedBaseline` 和短期 `reconcileAttempt`。
不保存逐 Operation WAL、完整 backup、Update Package 副本或 Skeleton resume 进度。

```json
{
  "schemaVersion": "template-runtime-state.v1",
  "managedBaseline": {"engineExclusiveFiles": {}},
  "bootstrapCommit": null,
  "reconcileAttempt": null
}
```

进入真实 Workspace 写入前，`reconcileAttempt` 只持久化最小恢复信息：

```text
changeId
technicalPlanSha256
requestedConfigJcsSha256
preReconcileHead
addedPaths[]                 # 仅本轮 ADD_FILE 的精确路径
phase = APPLYING | VALIDATING | FAILED_CLEAN | COMMITTING_METADATA | RECONCILED
validationRunId/pid/pgid | null
failureCode / failureDetails | null
```

前置条件固定为：Git `HEAD` 存在、工作区干净，并记录 `preReconcileHead`。忽略的
`.xcodeagent/**` 不属于 Git 回退范围，必须保持 old metadata，直到全部 Apply、Validation
和 Post Validation 成功。

`FAILED_CLEAN` 的含义是：已确认 Git HEAD 仍等于 `preReconcileHead`、工作树已 reset 到
该提交、`addedPaths[]` 已逐个删除、old TemplateState 与 old managedBaseline 仍成立。只有
此状态允许同一 `changeId` Retry。

`COMMITTING_METADATA` 后 TemplateState 已可能成为 target，不再将其视为可自动回退的
失败；后续 Skeleton/Continuation 失败只重试后续步骤。

成功后清除 `reconcileAttempt`。历史审计继续由既有 lifecycle / run artifacts / event logs
承担；V1 不建立运行历史、Operation WAL 或 backup 目录。

## 1.18 Apply、失败回退与 Retry

`/v1/update` 返回 200 ZIP 且完成 Package Validation、Policy Gate、Git clean preflight 后进入
Apply。XCodeAgent 按 Engine 给定顺序执行 Operation，但不做逐项持久化、逐项恢复或中断续跑。

正常捕获的异常统一处理：

```text
停止后续 Operation
→ git reset --hard <preReconcileHead>
→ 逐个删除 reconcileAttempt.addedPaths[]
→ 清理本轮允许的 validation side effects / ephemeral output
→ 验证 HEAD、工作树、old TemplateState、old managedBaseline
→ reconcileAttempt.phase = FAILED_CLEAN
→ active formal revision = template_reconcile_failed
```

这里的 `git reset --hard <preReconcileHead>` 只恢复本次 Reconcile 前的工作树，不删除历史
提交；禁止使用 `git clean` 清理未知 untracked 文件。`ADD_FILE` 产生的未跟踪路径只能依据
`addedPaths[]` 精确删除。

若 reset 失败、HEAD 已不等于 `preReconcileHead`、存在新的用户 commit、`addedPaths[]`
无法精确删除，或 old metadata 不成立：

```text
RECONCILE_RECOVERY_REQUIRED
```

不得自动 Retry 或覆盖用户修改。

进程崩溃后发现 `APPLYING` / `VALIDATING`，Recovery 同样只执行上述 Git 回退流程；不读取
旧 HTTP response、不要求保存 Update Package、不从第 N 个 Operation 继续。回退成功后进入
`FAILED_CLEAN`，用户可 Retry；回退无法证明安全时进入 `RECONCILE_RECOVERY_REQUIRED`。

### TemplateMetadataCommit

V1 的两个模板持久化文件是一个逻辑提交单元：

```text
.xcodeagent/template-state.json
.xcodeagent/runtime/template-runtime-state.json
```

其中 runtime state 在 commit 后同时包含：

```text
managedBaseline = target baseline
reconcileAttempt.phase = RECONCILED
```

固定提交顺序：

```text
1. stage target template-state.json + fsync
2. stage target template-runtime-state.json + fsync
   - managedBaseline = target
   - reconcileAttempt.phase = COMMITTING_METADATA
3. atomic replace template-runtime-state.json
4. atomic replace template-state.json
5. 再 atomic replace template-runtime-state.json
   - reconcileAttempt.phase = RECONCILED
6. fsync 两个 parent directory
7. 重新读取两个 JSON，验证 target digest
```

`COMMITTING_METADATA` 是最小记录仍然无法完全自动恢复的边界：若进程在两个 JSON 的替换之间崩溃，系统不猜测应该 roll-forward 还是回退，而是标记 `RECONCILE_RECOVERY_REQUIRED`，阻断 Retry，要求人工核对并修复这对元数据。V1 不保存旧/目标双份 JSON digest，也不实现自动 roll-forward。

TemplateState 仍是模板领域唯一 applied truth；runtime managedBaseline 只是 XCodeAgent 的冲突证据。

## 1.19 File 与 Structured Operation Apply 约束

### ADD_FILE

```text
Preflight: target 不存在
Apply 前：先把规范化 path 追加并 fsync 到 reconcileAttempt.addedPaths[]
Rollback: 仅删除 reconcileAttempt.addedPaths[] 中的路径
```

### UPDATE_FILE

```text
Preflight: target 存在、受 Git 跟踪 + ownership/digest/marker 检查
Apply: atomic replace
Rollback: git reset 恢复 preReconcileHead 版本
```

### DELETE_FILE

```text
Preflight: target 存在、受 Git 跟踪 + ownership/digest/marker 检查
Apply: 删除
Rollback: git reset 恢复 preReconcileHead 版本
```

---

## 1.20 Structured Operation

JSON Node / Maven Dependency 的宿主必须受 Git 跟踪；修改前完整解析、修改后 atomic replace。失败时由 `git reset --hard <preReconcileHead>` 统一恢复，不维护逆向 patch、宿主备份或逐操作回滚记录。未受 Git 跟踪的既有宿主一律在 preflight 阻断，不能假设可安全回退。

JSON 必须遵守 Engine 定义的 RFC 6901 Pointer 与规范化格式；Maven 必须使用与 Engine 协议一致的稳定 Key 语义。

---

## 1.21 Blocking Validation 与 Side-effect Policy

执行 Engine `validationPlan` 中所有 `blocking=true` 项。

每个 Validation Command 必须在独立 process group 中启动；最小 Attempt 仅记录：

```text
validationRunId
pid / pgid
commandDigest
startedAt
```

Backend crash 后 Recovery 先确认/终止仍存活的该 process group，再执行同一 Git 回退，避免 orphan validation process 在 Recovery 期间继续修改 Workspace。PID 复用时必须结合 `validationRunId/startedAt` 校验，不能仅按数字 PID kill。

Validation 结束后以 `git status --porcelain` 检查副作用：除本次 `reconcileAttempt` 本身以及明确可删除的 `target/`、`dist/`、`coverage/` 外，不得留下工作区变更；出现其他变更即失败并走 Git 回退。V1 不保存副作用快照、before image 或白名单持久副作用；确需保留的验证产物必须由后续独立需求设计。

## 1.22 Post Validation

Metadata Commit 前至少验证：

```text
所有 Operation 已执行
全部 Operation 的后置校验均已通过
```

### ENGINE_EXCLUSIVE File Operation

```text
ADD_FILE / UPDATE_FILE
→ Workspace 实际 UTF-8 bytes == Operation.content UTF-8 bytes

DELETE_FILE
→ target 不存在
```

### SHARED_STRUCTURED_HOST

不比较宿主整文件 bytes。Metadata Commit 前重新解析宿主并验证：

```text
所有 target TemplateState.managed.nodes 最终值成立
本次 Structured Operation 的目标 Pointer / Maven stable key 成立
未被 Engine 管理的业务 Node 可以保留
```

Shared Host 若出现 whole-file Operation，则前面已经以 `SHARED_HOST_WHOLE_FILE_OPERATION_UNSUPPORTED` 阻断。

### PLATFORM_OVERLAY File Operation

Marker Replay 后最终文件**有意不等于原始 Operation.content**。必须计算：

```text
expectedComposedBytes =
    OverlayComposer.compose(
        engineHost = Operation.content,
        preservedPlatformRegion
    )
```

然后验证：

```text
Workspace actual bytes == expectedComposedBytes
marker pair 唯一、完整
preserved Platform Region byte/semantic equality
```

不能为了满足 raw Operation byte equality 而丢掉 Platform Region。

### Structured Operation

```text
UPSERT_JSON_NODE / DELETE_JSON_NODE
→ 重新解析 Host，RFC6901 Pointer 结果与 Operation 一致

UPSERT_MAVEN_DEPENDENCY / DELETE_MAVEN_DEPENDENCY
→ 重新解析 Maven Model，稳定 Key 的最终值与 Operation 一致
```

同时验证：

```text
next state managed.files / managed.nodes / migrations 完整
Platform Overlay marker/region 完整
frontend/backend 关键入口存在
Validation Side-effect Policy 未被突破
Blocking Validation 全部成功
```

在持有 Revision Finalization Run Gate 的情况下，Metadata Commit 前做最后一次 touched-path / host recheck，尽可能捕获外部编辑器在 Apply 后制造的漂移。

全部验证成功后，从此刻真实 Workspace 的 `ENGINE_EXCLUSIVE` Host 计算 target baseline，进入 `TemplateMetadataCommit`。

## 1.23 Rollback Boundary / Failure States

Rollback 边界固定，不能在不同章节使用不同语义。

| Finalization Phase | 失败后的模板处理 |
|---|---|
| `PLANNED / PREFLIGHTED` | 无真实 Workspace mutation，直接标记失败 |
| `APPLYING / VALIDATING` | `git reset --hard <preReconcileHead>`，删除 `addedPaths[]`，成功则 `FAILED_CLEAN` |
| `COMMITTING_METADATA` | 不自动猜测或补齐双文件，进入 `RECONCILE_RECOVERY_REQUIRED` |
| `RECONCILED` 及以后 | 新 TemplateState 已成为事实；Skeleton/Continuation 失败不得回滚模板 |

Metadata Commit 前 rollback 成功：

```text
TemplateRuntimeState.reconcileAttempt → FAILED_CLEAN
active formal revision → template_reconcile_failed
```

Git 回退失败、HEAD 已变化、`addedPaths[]` 不完整，或元数据提交中断，均为 `RECONCILE_RECOVERY_REQUIRED` 强阻断：

```text
禁止 Build
禁止 Projection
禁止新的 Reconcile
禁止 Skeleton
```

必须由人工核对并修复后解除；V1 不做自动 roll-forward、逆操作回放或重新下载 Package。

Skeleton 失败：

```text
active formal revision → skeleton_failed
```

Retry 只重新执行确定性的 Skeleton `CREATE_IF_ABSENT / VERIFY_EXACT` 检查，不重新调用 `/v1/update`。

## 1.24 Crash Recovery 完整矩阵

Recovery 触发点：Backend 启动恢复、Workspace Attach、正式 Revision Retry、Finalizer 入口发现 `template-runtime-state.json.reconcileAttempt != null`。

**固定入口顺序：**

```text
Resolve workspaceId + lifecycle changeId
→ Acquire Revision Finalization Run Gate
→ Load lifecycle + template-runtime-state.json
→ 校验 Attempt/Lifecycle 的 changeId binding
→ Recover Attempt
→ RELOAD lifecycle + template-state.json + template-runtime-state.json
→ 才允许进入新的 /v1/update
```

### Finalization Phase Recovery Matrix

| Finalization 状态 | Recovery 行为 | Recovery 后 |
|---|---|---|
| `reconcileAttempt=null` 且 lifecycle 非 finalizing | 可创建新的 Attempt | `PLANNED` |
| `APPLYING / VALIDATING` | 终止 orphan validation process，再执行 Git 回退 | `FAILED_CLEAN` 或 `RECONCILE_RECOVERY_REQUIRED` |
| `FAILED_CLEAN` | 不自动修改；用户可同 changeId Retry | 保持 |
| `COMMITTING_METADATA` | 不自动覆盖或补齐元数据 | `RECONCILE_RECOVERY_REQUIRED` |
| `RECONCILED` | 校验 target TemplateState/runtime baseline 后启动 Skeleton | `RECONCILED` |
| Skeleton 失败 | 保留已完成模板结果；可重试 Skeleton | `skeleton_failed` |
| 其他 binding 不一致或未知状态 | 不自动修改 | `RECONCILE_RECOVERY_REQUIRED` |

`COMMITTING_METADATA` 或 lifecycle 与 Attempt 的 `changeId` 不一致时，只落稳定 failure code 并阻断。恢复入口不得写 Workspace、不得重放 Operation，也不得自动改写任一模板 JSON；人工修复后才可再次进入 preflight。

## 1.25 NO_CHANGE 与 Recovery 语义

`/v1/update` 返回 `204` 时：

```text
不创建 Operation 记录或 Workspace mutation
→ Managed Workspace Health Check
→ Deterministic Skeleton
→ Ensure/Reissue Continuation
```

NO_CHANGE 仍要求：

```text
Engine Exclusive baseline healthy
Shared Host managed nodes healthy
Platform Overlay contract healthy
TemplateState 完整 Schema + JCS digest valid
```

如果已有未清理的 `reconcileAttempt`，必须先按 1.24 判断为可 Retry 的 `FAILED_CLEAN` 或强阻断，之后才允许重新调用 `/v1/update`。

正常 NO_CHANGE 路径必须额外验收：

```text
/v1/plan callCount = 0
/v1/update status = 204
Workspace mutation count = 0（Skeleton 除外）
```

可选 `/v1/plan` Preview 即使曾返回 NO_CHANGE，也不能替代这次 `/v1/update` 的 204 权威结果。

## 1.26 Deterministic Skeleton

Reconcile 或 NO_CHANGE Health Check 成功后才进入 Skeleton。

Skeleton 不再直接“遍历并写文件”，而是先从 confirmed TechnicalPlan 编译稳定 `SkeletonManifest`：

```text
SkeletonManifest
  changeId
  technicalPlanSha256
  items[]
    index
    path
    expectedSha256
    mode = CREATE_IF_ABSENT | VERIFY_EXACT
```

规则：

1. Skeleton 只拥有 BUSINESS_AGENT 类业务骨架文件；不得写 Engine Exclusive、Platform Overlay 或 Engine Managed Node。
2. 已存在且内容相同 → 视为该 item 已完成；
3. 已存在且内容不同 → `SKELETON_FILE_CONFLICT`，不得覆盖；
4. 新建必须 atomic write + fsync；
5. 不保存 Skeleton WAL 或逐项进度；重试时从 Manifest 首项重跑上述幂等检查；
6. Metadata 已 `RECONCILED` 后 Skeleton 不做模板 rollback。

状态：

```text
RECONCILED → Skeleton → continuation_ready
```

失败：

```text
active formal revision → skeleton_failed
```

Retry 同 `changeId` 重新跑 Skeleton Manifest，不重新 Apply Engine ChangeSet。

## 1.27 Revision Finalizer、Attempt/Lifecycle 权威关系与 Continuation

统一入口：

```text
technical_plan_revision_finalize.py
```

所有 Design Stage / Workbench TechnicalPlan Revision 都必须走同一 finalizer。

### Lifecycle 与 TemplateRuntimeState.reconcileAttempt 的边界

固定权威划分：

| 事实 | 权威来源 |
|---|---|
| 当前 active `changeId`、formal branch、planning thread | application lifecycle |
| TechnicalPlan confirmed binding | application lifecycle + canonical file SHA |
| 正在进行的 Reconcile 的最小回退信息 | TemplateRuntimeState.reconcileAttempt |
| continuation token hash / generation / consumed 状态 | application lifecycle |
| 可展示失败状态 | lifecycle；Attempt 只提供 failureCode 与回退依据 |

发生跨文件崩溃时：

- **identity / continuation 冲突时 lifecycle 优先**；Attempt 不得创建新的 `changeId` 或伪造 consumed continuation；
- Attempt 仅用于判断 Git 回退或强阻断，不承担精细流程续跑。

### Lifecycle 最小状态

建议收敛为：

```text
template_finalizing / template_reconcile_failed / skeleton_failed / continuation_ready / building / stopped / failed
```

`template_finalizing` 覆盖 Reconcile 的内部阶段，避免 lifecycle 复制 Attempt 状态机。

### Attempt/Lifecycle 一致性

`reconcileAttempt.changeId` 与 active revision 不一致、lifecycle 不在 `template_finalizing`，或 Attempt 缺失了 `preReconcileHead` 时，一律进入 `RECONCILE_RECOVERY_REQUIRED`。不做跨文件状态修复矩阵；正常失败只将 lifecycle 标记为 `template_reconcile_failed`，并保留 `FAILED_CLEAN` Attempt 供用户重试。

### Reconcile Retry

TechnicalPlan 已 confirmed 但 Reconcile 失败时：

```text
TechnicalPlan 保持 confirmed
changeId 保留
reconcileAttempt = FAILED_CLEAN
lifecycle = template_reconcile_failed
```

只有以下 binding 全部一致才允许同 changeId Retry：

```text
changeId
technicalPlanSha256
requestedConfigJcsSha256
old/current TemplateState relation
```

### Continuation 可恢复签发

`issue_revision_continuation()` 必须收敛为 `ensure_or_reissue_revision_continuation()`：

```text
binding = changeId + technicalPlanSha256 + targetTemplateStateJcsSha256
```

规则：

1. lifecycle 无 continuation → 生成 token，CAS 写 hash + binding + generation，再返回；
2. `continuation_ready`、未消费、binding 相同 → 允许 rotate token，原子替换 hash，`generation + 1`；
3. 已 consumed / `building` → 不重新签发；
4. binding 不一致 → 阻断。

如果 crash 发生在 lifecycle 写 hash 后、raw token 返回前，Recovery 可以安全 reissue，不依赖无法还原的旧 token。

## 1.28 Retry AG-UI Contract

`template_reconcile_failed` / `skeleton_failed` 必须有正式产品动作，不能只存在服务端 Retry 语义。

V1 固定 Action：

```text
action = retry_template_revision_finalization
```

执行上下文：

- 复用当前 `active_formal_revision.changeId`；
- 复用该 Revision 的 `planningThreadId`，不创建新 thread；
- 客户端不得重新上传 TechnicalPlan、RequestedConfig 或 TemplateState；服务端从 active revision + TemplateRuntimeState.reconcileAttempt 读取并重新校验 binding；
- 只允许状态为 `template_reconcile_failed`、`skeleton_failed` 或可恢复的 `failed/recovery_required`。

最小输入：

```json
{
  "changeId": "chg_..."
}
```

服务端必须验证：

```text
changeId == active formal revision
technicalPlan file sha256 == reconcileAttempt.technicalPlanSha256
requestedConfig canonical digest == reconcileAttempt.requestedConfigJcsSha256
reconcileAttempt.phase == FAILED_CLEAN
```

事件语义至少包括：

```text
revision_finalization_retry_started
revision_finalization_phase_changed
revision_finalization_failed
revision_continuation_ready
```

失败事件返回稳定 `failureCode` 与可展示 message；成功最终仍进入原有 continuation，而不是创建第二条 Build 路径。

具体 HTTP endpoint 可以复用现有 AG-UI action transport，但 action 名、输入 Schema、thread/changeId 绑定和事件名必须在 API/Frontend/Backend Contract Test 中冻结。

## 1.29 Digest Contract

必须区分：

```text
artifact_file_sha256
= SHA-256(canonical artifact file raw bytes)

canonical_json_sha256_v1
= SHA-256(RFC 8785 JCS UTF-8 bytes)
```

固定字段命名：

```text
technicalPlanSha256                 # file bytes
requestedConfigJcsSha256            # JCS
oldTemplateStateJcsSha256           # JCS
targetTemplateStateJcsSha256        # JCS
changeSetJcsSha256                   # JCS of authoritative change-set.json
nextTemplateStateJcsSha256           # JCS of authoritative next-template-state.json
```

JCS 固定要求：

- RFC 8785；
- Array 顺序原样保留，尤其 `operations`；
- Unicode / number / null 按 JCS；
- NaN / Infinity 非法；
- UTF-8，无 BOM，无额外 whitespace。

XCodeAgent 使用基于 `TEMPLATE_REFACTOR.md` 的本地 JCS golden vectors 验证自身实现。

现有 `canonical_sha256(path)` 实际是 raw file bytes hash，保留给 artifact file binding；不得再用于 TemplateState/RequestedConfig semantic digest。

## 1.30 Build Binding 与 Managed Workspace Health

BuildContext 不再使用含义模糊的 `state_sha256`，固定字段：

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

`template_state_jcs_sha256` 固定等于：

```text
canonical_json_sha256_v1(parse(.xcodeagent/template-state.json))
```

不是文件 raw bytes hash。

`prepare_build_tasks` 持久化该值后，Build 启动前必须：

```text
1. 重新读取并 parse TemplateState
2. 重新计算 template_state_jcs_sha256
3. 与 BuildContext 比较
4. Managed Workspace Health Check
   - Engine Exclusive baseline
   - Shared Host managed nodes
   - Platform Overlay markers
```

任一失败：

```text
BUILD_TEMPLATE_STATE_DRIFT
或
BUILD_TEMPLATE_MANAGED_WORKSPACE_DRIFT
```

Agent Build 完成、进入 Platform Projection 前再执行一次 Managed Workspace Health Check；若发现
模板受管内容漂移，阻断后续 Projection 并提示用户处理。

因此 Build 的保护由两层组成：

```text
semantic state binding
+
actual workspace ownership health
```

不能只校验 State digest。

## 1.31 Platform Projection

统一顺序：

```text
Template Reconcile
→ Skeleton
→ Build DAG
→ Agent Build
→ Route Projection
→ Authorization Projection
→ Validation
```

Platform Projection 不属于 Engine ChangeSet，不由 Agent Tool 完成。

Authorization capability 新增后，Engine 提供权限固定基础设施；业务 resourceKey、RouteGuard 绑定、AuthConstants 业务常量仍由 Platform Projection 负责。

---

## 1.32 Capability Remove 暂停开放

Engine 能计算 Remove，但 XCodeAgent V1 不开放通用 Remove。

原因：

```text
模板删除
≠
业务引用撤销
≠
数据库表/数据撤销
≠
运行态配置撤销
```

Authorization Remove 可能残留：

```text
resource / role / role_resource / role_member
Controller annotation
AuthConstants business constants
resources.ts projection
runtime role/resource data
```

未来需引入 Capability-specific `decommission contract` 后再开放。

---

## 1.33 Legacy Workspace Compatibility Gate

新版 Reconcile V1 只对新版 Bootstrap 产生的完整 Engine V1 TemplateState 开启。

兼容性检查要求至少存在：

```text
capabilities
managed.files
managed.nodes
migrations
```

旧四字段 State：

```text
templateRevision
managedFiles
requested
effective
```

不做自动原地升级。检测到后：

```text
LEGACY_TEMPLATE_STATE_UNSUPPORTED
```

策略：

- 不进入新版 Reconcile；
- legacy Workspace 可继续明确保留的非模板变更路径；
- 一旦 Formal Revision 要改变 Template Capability，必须阻断并提示先执行未来的显式 TemplateState Migration；
- V1 不通过扫描 Workspace 猜测 `managed.nodes/migrations/ownership`。

因此切换条件是“当前 Workspace 是否拥有新版完整 TemplateState”，而不是应用创建日期。

---

# 第二章 分步实施计划与运行验证

本章只定义**实施顺序、代码落点和可观察结果**，不要求新增逐 Step 验收脚本、fixture 目录或 shell 验证入口。功能完成后由用户启动工程，通过正式 AG-UI 流程、Workspace 文件和日志完成整体联调验证；每一步的“人工验收”仅保留为实现者应满足的观察点。

Reconcile 专属服务统一放入：

```text
Backend/app/services/template_reconcile/
```

建议按职责拆分 `models`、`engine_client`、`policy_gate`、`preflight`、`overlay`、
`runtime_state`、`applier`、`recovery`、`health` 和 `service`。`template_state.py`、
`canonical_json.py` 以及 `workspace_bootstrap/` 仍是 Bootstrap、Build、Projection 共用能力，保持原位置；不要为了目录统一而制造循环依赖或重复实现。

---

## 2.1 Step 00：契约前提与本地 Fixture

### 实施目标

Step 00 以当前 Engine OpenAPI 与 Core 代码为唯一输入契约，固化为 XCodeAgent 本地 fixture，
供后续 DTO、Package Validator 和 Handler 测试复用。`TEMPLATE_REFACTOR.md` 中尚未落入
当前 OpenAPI/代码的目标字段不在本步骤实现范围。

### 必须完成

当前 Engine 契约仅包含四字段 TemplateState 与三类文件 Operation。因此准备最小、完整、非法
三类本地 fixture：

```text
四字段 TemplateState
ChangeSetBody.operations
ADD_FILE / UPDATE_FILE / DELETE_FILE
Update Package: change-set.json / next-template-state.json / payload
原始 Operation 顺序
完整 nextTemplateState（四字段）
```

当前 Engine 尚未提供 `resources.ts` marker host、`validationPlan`、risks、diagnostics、
JSON/Maven Structured Operation 或完整 V1 Managed Model；这些能力不得由 XCodeAgent fixture
自行伪造。待 Engine 先升级 OpenAPI 与代码后，再扩充 fixture 和后续 Step 的实现边界。

### 人工验收

运行：

不新增 Step 00 专用脚本；实现完成后随工程整体启动验证。

人工检查 fixture 至少覆盖：

```text
四字段 TemplateState
3 种 File Operation
NO_CHANGE / 204
非法 path / 非法 State
```

### 通过标准

- 后续 Step 可完全离线地使用 fixture 开发和测试 XCodeAgent 消费逻辑；
- 本地 fixture 覆盖当前 Engine OpenAPI/代码规定的输入边界。

外部依赖不满足该既定契约不在本实施计划的处理范围内；XCodeAgent 只对收到的非法输入
fail closed。

---

## 2.2 Step 01：XCodeAgent 协议模型与 Digest 冻结

### 实施目标

让 XCodeAgent 只消费当前 Engine 权威模型，并冻结当前可验证的文件 SHA-256 语义。

### 当前 Engine 下的代码落点

```text
Backend/app/services/template_reconcile/models.py
Backend/app/services/template_reconcile/digests.py
Backend/tests/test_template_reconcile_models.py
```

### 必须完成

- 四字段 TemplateState DTO；
- 3 类 File Operation DTO；
- ChangeSetBody.operations 与 PlanResponse DTO；
- `artifact_file_sha256`；
- 当前 Engine 不定义 JCS/语义摘要，XCodeAgent 不自行宣称与 Engine 摘要兼容；
- 当前 Engine 不输出 validationPlan、risks 或 diagnostics 的细化 Schema，XCodeAgent 仅保留 OpenAPI 允许的原始 diagnostics 数组。

### 人工验收

不新增 Step 01 专用脚本；以 DTO 消费真实 Engine 响应的运行结果验证。

### 通过标准

- 当前 Engine 的合法 TemplateState、CHANGE/NO_CHANGE PlanResponse 与 3 类 File Operation 可被严格消费；
- OpenAPI 未定义的字段、未知 Operation 和缺少 `content` 的 ADD/UPDATE 均 fail closed；
- `artifact_file_sha256` 始终表示原始文件 bytes SHA-256，不误称为 JSON 语义摘要。

---

## 2.3 Step 02：TechnicalPlan Desired Capability 与 Markdown Round-trip

### 实施目标

冻结 `template_capabilities` 唯一 Desired Authority。

### 必须完成

```text
TechnicalPlan JSON Schema
TechnicalPlan Markdown Renderer
Markdown Parser / Sync
authorization_manifest consistency gate
RequestedConfig compiler
```

### 人工验收

准备一个 TechnicalPlan：

```json
{
  "template_capabilities": {
    "authorization": {"enabled": true, "config": {}}
  },
  "authorization_manifest": {"enabled": true}
}
```

运行：

不新增 Step 02 专用脚本；通过正式 TechnicalPlan 确认流程验证。

人工检查：

```text
JSON → Markdown → JSON
```

前后 `template_capabilities` 完全等价；再把 manifest 改成 `false`，确认 TechnicalPlan Confirm 被拒绝。

### 通过标准

- application.json 只作为 Initial Intent；
- Revision 可以从 auth=false 改为 auth=true；
- 非空 capability config 被 V1 Gate 拒绝；
- Markdown 往返不丢字段。

---

## 2.4 Step 03：Template Apply Ownership Registry 与冲突提示

### 实施目标

只为 Reconcile 的 Update Package Apply 定义“该 Operation 能否修改当前 Workspace”。
它不承担后续 Agent Build、Skeleton、Projection、delete 或通用 workspace API 的写权限管理。

### 建议代码落点

```text
Backend/app/services/template_reconcile/preflight.py
```

### 必须完成

当前 Engine 只提供 `TemplateState.managedFiles`，因此本步骤只实现两类：

```text
ENGINE_EXCLUSIVE
BUSINESS_AGENT
```

Registry 由 Current / Next TemplateState 的 `managedFiles` 共同推导：

```text
ENGINE_EXCLUSIVE → Current 或 Next managedFiles 中出现的路径
BUSINESS_AGENT   → 其余路径
```

`ADD_FILE` 必须只新增 Next managedFiles 路径；`UPDATE_FILE` 必须同时属于 Current 与 Next
managedFiles；`DELETE_FILE` 必须只属于 Current managedFiles。任何 Engine Operation 触及业务路径
或与 Current/Next State 关系不一致，都在 Apply 前以稳定错误码阻断。

Shared Structured Host、Platform Overlay、marker replay、节点级冲突检查不在当前 Engine
契约中；待 Engine 先提供结构化 Operation、Managed Model 与 marker host 后，才能新增。

Apply 前只做本地预检：冲突时返回稳定错误码和受影响 path/node，停止本次 Reconcile，等待
用户修复后 Retry；不尝试 Agent 自动 merge 或扩展写保护范围。

### 人工验收

不新增 Step 03 专用脚本；通过 Reconcile 冲突提示的真实流程验证。

当前契约下至少验证：

1. ADD/UPDATE/DELETE 与 Current/Next managedFiles 关系正确时通过；
2. Engine Operation 触及业务路径时返回 `TEMPLATE_OPERATION_OWNERSHIP_CONFLICT`；
3. Operation 与 Current/Next managedFiles 关系不一致时返回同一错误码。

人工再检查：

```bash
git diff -- frontend/package.json backend/pom.xml
```

确认冲突信息精确指出受影响 path。

### 通过标准

Reconcile 不会覆盖已漂移的模板受管内容，并能向用户及时报告冲突位置。

---

## 2.5 Step 04：Revision Finalization 防重入与恢复串行

### 实施目标

利用现有 application lifecycle 的 revision CAS，保证同一已确认 TechnicalPlan Revision 不会
重复进入 Reconcile。V1 不建设 Workspace Lease、全局写锁、Agent Tool 写保护或 active Build
扫描；用户能回到技术规划阶段即表示没有进行中的 Workspace 写任务。

### 必须完成

- `template_reconciling` / `template_reconcile_failed` 状态 CAS；
- 同一 `changeId` 已处于 `template_reconciling` 时返回“已取得”的结果，不再次进入 Apply；
- changeId 不一致、已签发 continuation 或不允许的 lifecycle 状态在写 Workspace 前拒绝；
- Retry 的 `FAILED_CLEAN` 与 Validation 子进程恢复属于 Step 05，不在本步骤实现。

### 人工验收

不新增 Step 04 专用脚本；通过同一 Revision 的重复触发和 active Build 拒绝场景验证。

必须人工看到以下过程：

```text
request-A(changeId=chg_1) → template_reconciling → acquired=true
duplicate request-B(changeId=chg_1) → template_reconciling → acquired=false
other changeId / 已签发 continuation → preflight reject
Reconcile 失败 → template_reconcile_failed
```

通过标准：技术规划与 Reconcile 的生命周期串行约束可证明，且无重复 Apply。

### 通过标准

不得把该轻量 CAS 扩展为 Agent Tool、delete、workspace API 的通用写权限系统。

---

## 2.6 Step 05：Template Runtime State、Recovery 入口顺序与状态矩阵

### 实施目标

先把两个 JSON 的持久化与恢复模型做对，再接真实 Engine Apply。

### 建议代码落点

```text
Backend/app/services/template_state.py
Backend/app/services/template_reconcile/runtime_state.py
Backend/app/services/template_reconcile/recovery.py
```

### 必须完成

- `.xcodeagent/template-state.json` 只保存完整 Engine TemplateState；
- `.xcodeagent/runtime/template-runtime-state.json` 只保存最小 `reconcileAttempt`。当前
  `TemplateState.managedFiles` 已是 Engine 给出的完整文件基线，不另存第二份
  `managedBaseline`；
- 使用统一 `AtomicJsonStore`：tmp write → flush/fsync → atomic replace → parent dir fsync；
- 不向 `checkpoints.sqlite` 新增 Reconcile/WAL/Lease 表；
- lifecycle `changeId` 必须与 `template-runtime-state.reconcileAttempt.changeId` 一致；
- Finalizer 固定 `Acquire Revision Finalization Run Gate → Recovery → Reload 两个 JSON → /v1/update`；
- Attempt 仅使用 `APPLYING / VALIDATING / FAILED_CLEAN / COMMITTING_METADATA / RECONCILED`；
- binding 不一致与 `COMMITTING_METADATA` 一律强阻断。

### 人工验收

运行：

不新增 Step 05 专用脚本；通过工程启动、Workspace Attach 与 Retry 流程验证。

先人工查看两个状态文件：

```bash
jq . <workspace>/.xcodeagent/template-state.json
jq . <workspace>/.xcodeagent/runtime/template-runtime-state.json
```

再查看现有 LangGraph DB：

```bash
sqlite3 <workspace>/.xcodeagent/checkpoints/checkpoints.sqlite '.tables'
```

人工确认没有为 Template Reconcile 新建 `revision_finalization / operation_wal / workspace_lease` 等自定义表。

Fixture 至少覆盖每个 Attempt phase，并打印：

```text
inputPhase
inputLifecycleStatus
recoveryAction
outputPhase
outputLifecycleStatus
stateDigestBeforeRecovery
stateDigestAfterRecovery
stateDigestUsedForUpdate
```

人工重点核对 P0 场景：

```text
Recovery 在 APPLYING/VALIDATING 时回到 preReconcileHead
→ 删除 addedPaths[] 后确认旧工作区
→ Finalizer reload template-state.json + template-runtime-state.json
→ 仅 FAILED_CLEAN 可重试
```

再 fault inject：

```text
reconcileAttempt=COMMITTING_METADATA
进程在两个 JSON 替换之间 crash
```

重启后必须进入 RECONCILE_RECOVERY_REQUIRED，不得重新 Apply ChangeSet 或自动补齐元数据。

### 通过标准

- 运行时模板事务只有两个权威 JSON；其中 Runtime State 不复制
  TemplateState 的 `managedFiles`；
- 所有 Attempt 状态都有唯一 Recovery 动作；
- `checkpoints.sqlite` 仍只承担 LangGraph checkpoint；
- binding conflict 稳定阻断；
- Recovery 后两个 JSON 必须重新读取；
- 不存在 runtime/history Finalization 搬移双写窗口。

## 2.7 Step 06：Update/Package Validator 与 Git 回退

### 实施目标

实现 `/v1/update` 权威入口、Update Package Policy Gate，以及当前 Engine 已提供的
`ADD_FILE / UPDATE_FILE / DELETE_FILE` 的 crash-recovery Apply。

### 必须完成

```text
TemplateEngineClient.update             # 主流程必需
TemplateEngineClient.plan               # 可选 Preview/调试，不作为执行依赖
Update Package Validator
Update Policy Gate
3 个文件操作 Handler
最小 reconcileAttempt 写入与 Git 回退
```

主流程必须证明：

```text
正常 Revision Finalizer 调用 /v1/plan 次数 = 0
/v1/update 204 → NO_CHANGE + Health Check
/v1/update 200 → Package Validate + Policy Gate + durable package + Apply
```

Update Policy Gate 至少验证：

```text
nextTemplateState.templateRevision 未变化
Current Effective → Target Effective 没有 Remove
Operation 类型/路径/Ownership 合法
```

### 人工验收

不新增 Step 06 专用脚本；通过正式 Reconcile 与失败后的 Retry 流程验证。

先运行三类接口案例：

**Case 0A：NO_CHANGE**

```text
Core normalized TargetState == CurrentState
Operations = []
/v1/update → 204
/v1/plan callCount = 0
TemplateState file digest unchanged
Workspace Health Check = PASS
```

**Case 0B：CHANGE**

```text
/v1/update → 200 ZIP
PACKAGE_VALIDATION=PASS
UPDATE_POLICY_GATE=PASS
updatePackageSha256=...
```

**Case 0C：可选 Preview**

```text
/v1/plan → CHANGE
随后 /v1/update 独立重新计算
请求体中不存在 planId / plan body / plan digest
最终仍只按 Update Package 执行 Policy Gate
```

然后至少对同一个 UPDATE_FILE 做两次 fault injection：

**Case A：写入前 kill**

预期：

```text
Recovery → `git reset --hard preReconcileHead` → old Workspace
```

**Case B：真实文件写入后 kill**

预期：

```text
Recovery → Git 回退 + 删除已持久化的 addedPaths[] → old Workspace
```

### 通过标准

- 正常 Reconcile 不依赖 `/v1/plan`；
- 204 和 200 两条权威分支人工可观察；
- 204 场景可证明 TargetState == CurrentState，且不会做 TemplateMetadataCommit；
- 可选 Plan 结果不会传给 Update；
- preReconcileHead、addedPaths 与 phase 可从 TemplateRuntimeState 检查；
- Recovery 不依赖原 HTTP response、Package 副本或逐操作进度；
- 三类文件 Operation 不重排；
- 任意 Apply boundary kill 都能以 Git 回到 old Workspace；
- unknown third-state 进入 `RECOVERY_WORKSPACE_DIVERGED`，不会覆盖未知内容。

## 2.8 Step 07：Validation、Post Validation、Metadata Commit 与 Rollback Boundary

### 实施目标

闭合 Apply 成功后的文件一致性验证与 `template-state.json` 元数据提交。

### 必须完成

- Operation Post Validation；
- `TemplateMetadataCommit`；
- `COMMITTING_METADATA` 强阻断。

### 人工验收

不新增 Step 07 专用脚本；通过工程运行时的 Validation、失败与 Recovery 状态验证。

必须人工验证：

1. Validation 修改普通源码 → 失败并 Git 回退；
2. kill Backend 后 Validation 子进程仍存活 → Recovery 先终止对应 process group，再 Git 回退；
3. Validation 只产生 ephemeral output → 清理后工作区无变更；
4. kill 在 template-state.json 与 template-runtime-state.json 的 metadata replace 之间 → `RECONCILE_RECOVERY_REQUIRED`，不自动补齐 pair；
5. Skeleton 失败时 target TemplateState 保留，不回 old State。

### 通过标准

文档中的 Rollback Boundary 与代码只有一套语义：`COMMITTING_METADATA` 前统一 Git 回退；进入该阶段后只阻断并要求人工处理。

---

## 2.9 Step 08：Skeleton、Retry 与 Continuation Reissue

### 实施目标

保证 Engine Reconcile 完成后不会因为 Skeleton/Continuation crash 再次执行模板 Apply。

### 必须完成

```text
retry_template_revision_finalization
ensure_or_reissue_revision_continuation
continuation generation
```

当前版本不新增 Skeleton Manifest：既有后端 Skeleton 注入本身是幂等的。成功的
Template Reconcile 会以同一 `changeId + technicalPlanSha256` 保留 `RECONCILED` Attempt；
若后续 Skeleton 或 continuation 失败，重试直接复用该结果，不再请求 `/v1/update`。

### 人工验收

不新增 Step 08 专用脚本；通过 Skeleton、Continuation 与 Retry 的正式流程验证。

至少检查：

**Skeleton crash**

```text
第 1 个 skeleton item 已写
第 2 个 item 前 kill
→ restart
→ 不调用 /v1/update
→ 从 Manifest 首项重跑
→ item1 exact，item2 仅在不存在时创建
```

**Token response lost**

```text
lifecycle 已持久化 token hash generation=3
raw token 未返回
→ retry
→ generation=4
→ 新 token 可消费
→ 旧 token 无效
```

**Attempt 强阻断**：Attempt/lifecycle binding 不一致时不得自动修复或重试。

### 通过标准

Retry 复用同一 changeId/thread，不生成第二条 Build 路径。

---

## 2.10 Step 09：Build Binding、健康检查与 Platform Projection

### 实施目标

让后续 Build 使用已完成 Reconcile 的 TemplateState，并在 Build 前后尽早发现模板受管内容漂移。

### 必须完成

BuildContext 延续已有 `template_context`（revision + effective capabilities）绑定。

Build 在 `inspect_workspace` 前读取 TemplateState；Build 前后执行 TemplateState binding + Managed
Workspace Health Check。后者仅用于发现并报告 drift，不把 Step 03 的 Reconcile Apply
Ownership Registry 扩展成 Agent 运行时写入 Guard。

当前 Engine 未提供节点、marker 或 platform overlay 契约；本步骤仅对
`managedFiles` 做普通文件健康检查，不实现上述 projection。

### 人工验收

不新增 Step 09 专用脚本；通过启动工程后的 Build 与 Projection 流程验证。

至少检查：

1. Build Task Plan 保留 `template_context`；
2. 修改 TemplateState revision 或 effective capabilities → Build 被阻断；
3. 修改 TemplateState 语义 → Build 被阻断；
4. Build 前人工修改 Engine Exclusive file → Managed Health 阻断；
5. Build 后模板受管内容发生漂移 → Managed Health 阻断后续 Projection，并返回受影响 path/node；
6. Projection 后 marker 外 Host 内容保持原值，marker 内内容符合 TechnicalPlan。

### 通过标准

Build 能绑定正确的 TemplateState，并及时暴露模板受管内容漂移。

---

## 2.11 Step 10：Feature Flag Cutover、Legacy Gate 与操作手册

### 实施目标

只对真正满足新版契约的 Workspace 开启 Reconcile。

### 必须完成

- `TemplateStateCompatibilityChecker`（当前四字段 State 即为唯一支持版本）；
- Update Package 临时文件清理；
- legacy/non-current State → `TEMPLATE_STATE_INVALID`；
- Reconcile feature flag；
- Retry/Recovery UI 状态展示；
- manual recovery runbook；
- 全量 EDD/故障注入进入 CI。

### 人工验收

不新增 E2E 脚本；按第三章场景启动工程进行整体联调。

另外选一个旧四字段 Workspace：

```text
触发 capability-changing Revision
→ 必须明确阻断
→ 不自动扫描/猜测迁移
```

### 通过标准

Feature Flag 默认关闭；确认当前 Engine 与 XCodeAgent 的联调通过后才打开。

---

## 2.12 EDD / CI 必测场景

以下场景不再作为“实施计划替代品”，而作为上述步骤的自动回归门禁：

```text
A. authorization Add + Engine 自动引入 login
A1. Normal Reconcile /v1/plan callCount = 0
A2. Optional /v1/plan result is not forwarded/bound to /v1/update
B. NO_CHANGE + Workspace 缺 Managed File
C. JSON Node
D. Maven Dependency
E. Operation 原顺序
F. Revision mismatch / Refresh reject
G. Platform Overlay marker replay
H. Engine Exclusive conflict
I. Apply 写入前 crash
J. Apply 写入后 crash
K. Git 回退失败 / HEAD 已变化强阻断
L. Blocking Validation failure
M. Metadata pair partial commit
N. COMMITTING_METADATA Workspace Divergence
O. TechnicalPlan Markdown round-trip
P. Authorization semantic gate
Q. Skeleton partial crash 后从 Manifest 首项幂等重跑
R. Continuation token response loss/reissue
S. Formal Revision retry same changeId
T. Validation unexpected source mutation
U. Legacy four-field state
V. Shared Host business dependency preserved
W. Duplicate Finalizer / active Build rejection
X. COMMITTING_METADATA 中断强阻断
Y. Attempt/Lifecycle binding 冲突强阻断
Z. Build State JCS drift + Managed Workspace drift
AA. Build 后 Managed Workspace drift reported
AB. resources.ts cross-repo marker release gate
AC. 本地 canonical JSON SHA golden vectors
AD. Recovery 不依赖 Update Package / HTTP 临时数据
AE. Validation orphan process terminated before Recovery
AF. File mode rollback / path collision rejection
AG. checkpoints.sqlite remains LangGraph-only; no Reconcile custom tables
AH. Template subsystem persists only template-state.json + template-runtime-state.json
```

---

## 2.13 Definition of Done

- [ ] Finalizer 固定 `Acquire Revision Finalization Run Gate → Recovery → Reload template-state/runtime-state → /v1/update`；
- [ ] Recovery 后不会使用旧内存 TemplateState；
- [ ] 正常 Reconcile 只以 `/v1/update` 为权威入口，`/v1/plan` 调用次数为 0；
- [ ] 可选 `/v1/plan` 结果不传给、不绑定 `/v1/update`；
- [ ] Template 子系统只保留 `template-state.json + template-runtime-state.json` 两个权威状态 JSON；
- [ ] `checkpoints.sqlite` 继续仅由 LangGraph Checkpointer 使用；
- [ ] Attempt 在首个变更前持久化 `preReconcileHead`，每个 ADD 前持久化该 path；
- [ ] Recovery 不依赖 HTTP response、临时目录、Update Package 副本或逐操作进度；
- [ ] APPLYING/VALIDATING 失败统一 `git reset --hard preReconcileHead` + 精确删除 addedPaths[]；
- [ ] Validation subprocess 使用独立 process group，Crash Recovery 能先终止 orphan validation；
- [ ] File mode / special file / case-collision V1 规则已冻结并验收；
- [ ] Template Runtime State 不使用 `active.json` 或 runtime/history Finalization 双写；
- [ ] 所有 Attempt Phase 都有唯一 Recovery 行为；
- [ ] Rollback Boundary 在所有章节/代码一致；
- [ ] `RECONCILE_RECOVERY_REQUIRED` 覆盖 metadata 中断及无法证明安全回退的场景；
- [ ] Skeleton 重试基于幂等 Manifest，不保存逐项 WAL；
- [ ] Ownership Registry 仅在 Reconcile Apply 前检查 Engine Exclusive、Shared Host 和 Overlay 冲突，并返回精确 path/node；
- [ ] 同一 changeId 的 Finalizer 防重入；active Build/write task 时拒绝进入 Finalizer；
- [ ] Attempt/Lifecycle binding 不一致稳定阻断，不做自动 pair repair；
- [ ] BuildContext 使用 `template_state_jcs_sha256`，不是模糊 `state_sha256`；
- [ ] Build 前后都执行 Managed Workspace Health Check；
- [ ] 本地契约 Fixture 覆盖 `TEMPLATE_REFACTOR.md` 规定的完整/非法输入边界；
- [ ] 每个实施 Step 有明确可观察输出；整体功能通过启动工程后的正式流程联调验证；
- [ ] 第三章完整 E2E 案例可从新 Bootstrap Workspace 一次执行通过。

## 2.14 实施断点回检结果

本轮按“能否直接交给实现人员且不需要二次猜测”回检，以下断点已在方案中闭合：

| 断点 | 定版处理 | 对应实施 Step |
|---|---|---|
| Recovery 与 State Load 顺序 | 固定 `Acquire Revision Finalization Run Gate → Recovery → RELOAD 两个 Template JSON → /v1/update` | Step 05 |
| `/v1/plan` 与 `/v1/update` 重复计算 | `/v1/plan` 降级为可选 Preview；主流程只调用 `/v1/update`，实际 ZIP/204 为权威 | Step 06 |
| Reconcile 持久化与 LangGraph SQLite 混用 | TemplateState + TemplateRuntimeState 两个 JSON；`checkpoints.sqlite` 不承载 Reconcile/WAL/Lease | Step 05 |
| Apply crash | preReconcileHead + addedPaths[]；统一 Git 回退，不依赖 WAL/backup | Step 06 |
| Attempt Recovery Matrix | 最小 Attempt 的每个 phase 有唯一动作；不依赖 checkpoints.sqlite | Step 05 |
| Rollback Boundary | `COMMITTING_METADATA` 前统一 Git 回退；之后强阻断，不自动 roll-forward | Step 07 |
| 模板 Apply 覆盖业务或已漂移受管内容 | Reconcile Preflight 按 Engine Exclusive / Shared Host / Overlay 返回精确冲突 | Step 03 |
| 重复 Finalizer / active Build | lifecycle CAS 保证同一 changeId 幂等恢复；写任务存在时拒绝进入 Finalizer | Step 04 |
| Attempt/Lifecycle 双事实 | lifecycle 管 identity/continuation；Attempt 仅保存回退依据；不一致直接阻断 | Step 08 |
| Build `state_sha256` 歧义 | 改为 `template_state_jcs_sha256`；Build 前后再做 Managed Workspace Health | Step 09 |
| Build 后模板受管内容漂移 | Build 前后 Managed Workspace Health Check 阻断后续 Projection 并报告 path/node | Step 09 |
| Crash 后 Update Package 丢失 | Recovery 不依赖 Package；安全回退后重新发起 `/v1/update` | Step 06 |
| Skeleton 中途 crash | SkeletonManifest 从首项幂等重跑 | Step 08 |
| Validation orphan 子进程 | 独立 process group + validationRunId/pgid，Recovery 先终止再扫描 | Step 07 |
| Finalization 归档双写窗口 | V1 不建立 Finalization 历史或归档；Attempt 仅保留当前/失败事实 | Step 05 |
| 文件 mode / 特殊文件 / 大小写碰撞 | V1 固定 text-file mode 规则、special-file reject、path collision gate | Step 06 |

回检后仍保留为**明确 V1 非目标**而不是实施断点的事项：

```text
Template Revision Refresh / Upgrade
通用 Capability Remove
Capability Custom Config Binding
自动 Merge 人工模板修改
真实数据库 Migration Rollback
跨多主机且不支持可靠 POSIX flock 的分布式 Workspace 写入
```

这些事项均有明确阻断/Feature Gate，不要求当前实现人员自行补设计。

# 第三章 整体验证案例

## 3.1 场景：Login-only Workspace 正式 Revision 新增 Authorization

这个案例用于人工从头验证“Planning → Reconcile → Recovery-safe Commit → Skeleton → Continuation → Build”的完整闭环。

### 前置条件

```text
1. Update Package 输入符合 `TEMPLATE_REFACTOR.md` 的完整 V1 契约；
2. Update Package 的 resources.ts Host 包含既定 marker；
3. Workspace 由新版 Bootstrap 创建；
4. Current Desired 只有 login=true；
5. Reconcile Feature Flag 已开启；
6. Git 工作区除本案例明确修改外保持干净。
```

先记录：

```bash
WORKSPACE=/path/to/workspace
jq . "$WORKSPACE/.xcodeagent/template-state.json"
jq '.managedBaseline,.finalization' "$WORKSPACE/.xcodeagent/runtime/template-runtime-state.json"
sqlite3 "$WORKSPACE/.xcodeagent/checkpoints/checkpoints.sqlite" '.tables'
git -C "$WORKSPACE" status --short
```

### 步骤 1：制造一个合法业务 Shared Host 修改

在 `frontend/package.json` 增加一个**非 Engine managed** 的业务 dependency，例如测试 fixture 使用的 `dayjs`。

记录：

```bash
jq '.dependencies.dayjs' "$WORKSPACE/frontend/package.json"
```

预期：这不构成模板冲突。

### 步骤 2：确认新的 TechnicalPlan

TechnicalPlan 设置：

```json
{
  "template_capabilities": {
    "authorization": {"enabled": true, "config": {}}
  },
  "authorization_manifest": {
    "enabled": true
  }
}
```

人工记录：

```bash
sha256sum "$WORKSPACE/.xcodeagent/plans/technical-plan.json"
```

并确认 Markdown round-trip 后字段仍存在。

### 步骤 3：触发 Revision Finalizer

从现有正式 Revision UI/AG-UI 触发 TechnicalPlan Confirm；观察事件：

```text
revision_finalization_phase_changed: template_finalizing
...
revision_continuation_ready
```

同时在另一个终端观察 TemplateRuntimeState：

```bash
watch -n 0.5 'jq ".reconcileAttempt.phase,.reconcileAttempt.addedPaths" \
  "$WORKSPACE/.xcodeagent/runtime/template-runtime-state.json" 2>/dev/null || true'
```

预期阶段按以下方向单调推进：

```text
APPLYING
→ VALIDATING
→ COMMITTING_METADATA
→ RECONCILED
→ continuation_ready
```

### 步骤 4：核对 TemplateState 与 Shared Host

```bash
jq '.effective,.managed.nodes,.capabilities' \
  "$WORKSPACE/.xcodeagent/template-state.json"

jq '.dependencies.dayjs' "$WORKSPACE/frontend/package.json"
```

预期：

```text
effective 同时包含 login + authorization
Engine-managed nodes 正确
业务 dayjs 仍存在
```

### 步骤 5：核对 Platform Overlay

```bash
grep -n 'XCODEAGENT' "$WORKSPACE/frontend/src/constants/resources.ts"
grep -n 'XCODEAGENT' "$WORKSPACE/frontend/src/constants/routes.tsx"
grep -n 'XCODEAGENT' "$WORKSPACE/backend/src/main/java/com/cmbchina/backend/auth/domain/constant/AuthConstants.java"
```

预期：marker pair 唯一；marker 外 Engine Host 保持模板内容；marker 内资源/路由/常量与 TechnicalPlan 一致。

### 步骤 6：核对 Build Binding

生成 Build DAG 后：

```bash
jq '.template_context' "$WORKSPACE/.xcodeagent/build-task-plan.json"
```

必须看到：

```text
template_state_jcs_sha256
```

而不是旧的 `state_sha256`。

运行 Build 前置健康检查，预期：

```text
STATE_DIGEST=PASS
ENGINE_EXCLUSIVE_HEALTH=PASS
SHARED_NODE_HEALTH=PASS
OVERLAY_HEALTH=PASS
```

### 步骤 7：验证 Build 后模板健康检查

在专用验收 Task 中修改一个 Engine Exclusive 文件，再执行 Build 后的 Managed Workspace
Health Check。

预期：

```text
BUILD_TEMPLATE_MANAGED_WORKSPACE_DRIFT
```

输出必须包含受影响 path；后续 Platform Projection / Final Validation 不得继续。

### 步骤 8：完成 Build 与最终健康检查

执行项目既有后端/前端测试与 build。完成后再次运行 Managed Workspace Health Check。

预期模板受管内容没有漂移，然后才允许 Platform Projection / 最终 Validation 完成。

### 步骤 9：验证 NO_CHANGE

保持同一个 Desired Capability 再创建一次不改变模板能力的正式 Revision。

预期：

```text
/v1/update → 204 NO_CHANGE
/v1/plan 调用次数 = 0
Managed Workspace Health Check = PASS
Skeleton/Continuation 可继续
```

### 步骤 10：做一次故障恢复变体

复制一份专用测试 Workspace，再执行相同 auth Revision，并使用 fault injection 在：

```text
UPDATE_FILE 已 replace+fsync
但进程尚未写入下一阶段
```

时 kill Backend。

重启/Attach：

```text
Recovery 从 template-runtime-state.json 读取 preReconcileHead 与 addedPaths[]
→ git reset --hard 回到 preReconcileHead
→ 精确删除本次 ADD_FILE 新建路径
→ old TemplateState / old runtime managedBaseline 保持
→ reconcileAttempt=FAILED_CLEAN
```

随后执行：

```text
retry_template_revision_finalization(changeId)
```

预期同一 changeId 成功完成 Reconcile、Skeleton、Continuation，不产生第二条 Build 路径。

## 3.2 整体验收通过标准

只有以下全部成立才认为 Template Capability Reconcile 可发布：

```text
本地契约 Fixture PASS
Planning Round-trip PASS
Template Apply Conflict Check PASS
Finalization Gate / Retry PASS
Git Rollback/Crash Recovery PASS
Metadata 中断强阻断 PASS
Skeleton 幂等 Retry PASS
Continuation Reissue PASS
Build State + Managed Health PASS
Platform Marker Replay PASS
NO_CHANGE PASS
Legacy Gate PASS
```

任一项不能靠“日志看起来没报错”判定通过，必须有上述可见文件、digest、phase、错误码或命令输出作为人工证据。
