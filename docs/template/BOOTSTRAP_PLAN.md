# XCodeAgent Workspace Bootstrap 实施方案

> 本文只描述**首次创建应用时的模板初始化**。  
> 公共 TemplateState、Capability、Operation、Validation、Ownership 和 Build Binding 契约见 [`TEMPLATE_REFACTOR.md`](./TEMPLATE_REFACTOR.md)。
> 已有 Workspace 的后续模板能力收敛见 [`TEMPLATE_RECONCILE_PLAN.md`](./TEMPLATE_RECONCILE_PLAN.md)。

---

# 第一章 方案设计

## 1.1 目标

Bootstrap 解决：

> 在 Workspace 尚未初始化时，由 XCodeAgent Backend 基于已确认 TechnicalPlan 编译 RequestedConfig，先通过 `/v1/plan` 获得 Engine 的完整目标状态和 Validation Contract，再调用 `/v1/generate` 获取完整工程包，安全物化 Workspace，执行 Blocking Validation，建立独立 Git baseline，并持久化完整 `.xcodeagent/template-state.json`。

目标链路：

```text
TechnicalPlan Confirmed
        ↓
Compile Initial RequestedConfig
        ↓
POST /v1/plan
currentTemplateState = null
        ↓
Plan Contract Gate
        ↓
POST /v1/generate
        ↓
Full Template Package
        ↓
Package / State Alignment Validation
        ↓
Materialize Staging
        ↓
Workspace Preparation
        ↓
Engine Blocking Validation
        ↓
Git Init + Baseline
        ↓
Atomic Persist TemplateState
        ↓
Readiness
        ↓
READY_FOR_WORKBENCH
```

Bootstrap 仅用于 First Creation，不得用于后续 Revision。

---

## 1.2 非目标

Bootstrap 不负责：

- `/v1/update`；
- Existing Workspace ChangeSet Apply；
- Template Refresh / Revision Upgrade；
- Agent 业务代码生成；
- 页面 placeholder / 业务菜单 / 业务 Route；
- Authorization 业务资源投影；
- 真实业务数据库 Migration 执行；
- Engine Service 项目级持久化；
- 第二份模板领域状态。

---

## 1.3 前置条件

Bootstrap 只允许在未初始化 Workspace：

```text
frontend 不存在
backend 不存在
.git 不存在
.xcodeagent/template-state.json 不存在
```

生命周期：

```text
AWAITING_TECHNICAL_PLAN_CONFIRMATION
        ↓ TechnicalPlan confirmed
GENERATING_APPLICATION_TEMPLATE_FILES
```

如果 TemplateState 已存在，不得重新 Bootstrap。

---

## 1.4 Initial RequestedConfig

唯一 Desired Authority：

```text
Confirmed TechnicalPlan.template_capabilities
```

application.json 仅提供创建阶段 Initial Intent。

XCodeAgent：

```text
不解析 Capability dependency
不自动补 authorization → login
不支持非空 capability config（Engine V1）
```

TechnicalPlan Confirm 前必须通过：

```text
template_capabilities Markdown round-trip
authorization_manifest / template_capabilities consistency
```

---

## 1.5 Bootstrap Plan Gate

Bootstrap 不直接只调用 `/v1/generate`。

先调用：

```json
{
  "currentTemplateState": null,
  "requestedConfig": {
    "capabilities": {}
  }
}
```

`/v1/plan` 首次必须返回 `CHANGE`。

XCodeAgent 保存本次执行期 Plan Snapshot：

```text
nextTemplateState
ChangeSetBody
validationPlan
risks
diagnostics
planJcsSha256
```

在首次 Materialize 前，还必须持久化一个 **Bootstrap Execution Journal**。它只记录本次
Bootstrap 尝试，不是第二份模板领域状态：

```text
.xcodeagent/runtime/bootstrap/<attemptId>.json

attemptId
phase
technicalPlanFileSha256
requestedConfigJcsSha256
planJcsSha256
packageStateJcsSha256 | null
leaseId / epoch
validationRun | null
```

`technicalPlanFileSha256` 是确认后的 TechnicalPlan Markdown 原始 bytes SHA-256；
`requestedConfigJcsSha256` 是 RFC 8785 JCS digest。开始 Materialize 前、进入
`COMMITTING_METADATA` 前都必须重新计算并比对这两个 binding。任一改变都不得提交旧
Package，且在 metadata commit 之前按 Bootstrap cleanup 结束本次尝试。

Execution Journal 是 Attach 终止 orphan validation process、判断 cleanup 边界和恢复
metadata 的唯一 Bootstrap 尝试证据；成功后可原子移入 history 或删除。它不参与
TemplateState / capability 决策。

---

## 1.6 `/v1/generate` Package Contract

完整包：

```text
template-package.zip
├── frontend/
├── backend/
└── .xcodeagent/
    └── template-state.json
```

Generate Package 内的 TemplateState 必须与 Plan：

```text
plan.nextTemplateState
```

语义完全一致；不一致时拒绝 Bootstrap。

XCodeAgent 必须按完整 Engine Schema 校验：

```text
templateRevision
requested
effective
capabilities
managed.files
managed.nodes
migrations
```

不再使用旧四字段模型。

---

## 1.7 Package 下载与安全

必须：

```text
HTTP streaming
timeout
package size limit
staging
async cancellation
```

ZIP 拒绝：

- `..`；
- 绝对路径；
- Windows drive path；
- symlink / 特殊文件；
- 加密 ZIP；
- 重复 path；
- 大小写冲突；
- Unicode 规范化冲突；
- 解压配额超限；
- `.git/**`；
- 非 allow-list 的 `.xcodeagent/**`。

禁止直接 `extractall(workspace)`。

---

## 1.8 Bootstrap Transaction

### Preparation

只在 staging：

```text
download
→ archive validation
→ extract staging
→ TemplateState validation
→ compare Plan nextTemplateState
→ workspace tree validation
```

### Materialize

```text
move frontend
move backend
```

### Blocking Validation

执行 `/v1/plan` 返回的全部：

```text
blocking=true validationPlan
```

命令：

```text
参数数组直接执行
禁止 shell 拼接
workingDirectory 必须位于 Workspace
超时/启动失败/非零退出阻断
```

Validation 可能产生 `pnpm-lock.yaml`；该文件属于 Workspace 包管理副作用，不进入 TemplateState，但必须在失败 rollback 中被清理。

Validation Command 与 Reconcile 共用公共 Runner：必须在独立 process group 中运行，并在
**启动子进程前**将下列内容 fsync 到 Bootstrap Execution Journal：

```text
validationRunId + pid/pgid + commandDigest + startedAt + MutationContext(leaseId, epoch)
```

命令退出后也必须先持久化其退出状态，才进入 Git Baseline。若 Backend crash，Attach
Recovery 先从 Journal 验证 command identity、终止仍属于该 Validation Run 的 orphan
process group，再判断 cleanup/rollback；禁止先删除 Workspace 后才寻找子进程。

### Git Baseline

Validation 成功后：

```text
git init
git config --local user.name XcodeAgent
git config --local user.email xcodeagent@local
git add frontend backend
git commit -m "chore: initialize workspace from template"
```

`.xcodeagent/` 不进入 baseline。

### Metadata Commit

全部 Materialize、Blocking Validation 与 Git baseline 成功后：

```text
Compute Engine Exclusive actual-byte baseline
→ Stage .xcodeagent/template-state.json
→ Stage .xcodeagent/runtime/template-runtime-state.json
   managedBaseline = computed target
   bootstrapCommit.phase = COMMITTING_METADATA
   finalization = null
→ Atomic replace runtime state（durable bootstrap commit intent）+ fsync
→ Atomic replace template-state.json + fsync
→ Target Workspace Contract final recheck
→ Atomic replace runtime state（bootstrapCommit=null，final baseline）+ fsync
→ verify both target digests
→ Bootstrap Completed
```

不能把 TemplateState 和 baseline 拆成两个无恢复关联的提交步骤。


## 1.9 Cleanup / Recovery Boundary

Bootstrap 只有在 **尚未持久化 `bootstrapCommit.phase=COMMITTING_METADATA`** 时才允许
自动 cleanup。此时任一步失败，清理本轮 Bootstrap 创建的：

```text
frontend/
backend/
.git/
.xcodeagent/template-state.json
.xcodeagent/runtime/template-runtime-state.json
.xcodeagent/runtime/bootstrap/<attemptId>.json
staging/
validation 产生的 lockfile / build side effects（按 Cleanup Policy）
```

保留 Requirement / ProductPlan / UiDesign / TechnicalPlan 等已确认 Artifact。

Bootstrap 失败不做原地 Resume；进入明确 Failed lifecycle。

一旦 durable commit intent 已存在，禁止以“任一步失败”为由直接删除整个 Workspace：

```text
State 未写入 / State target 已写入
→ Attach 读取 bootstrapCommit + Execution Journal
→ Target Workspace Contract 成立才补齐 metadata pair
→ Contract 不成立则 TEMPLATE_METADATA_WORKSPACE_DIVERGED / recovery_required
```

这样既不会把已提交 target State 回退成旧事实，也不会在外部编辑后自动删除或覆盖
Workspace。Bootstrap 的业务失败仍可由用户重新发起新的 Bootstrap attempt；但
`recovery_required` 必须先完成 Attach/人工恢复，不能把它伪装成普通 retry。

---

## 1.10 Git Baseline 验收

成功后：

```bash
git rev-parse HEAD
git status --porcelain
git ls-files .xcodeagent
```

预期：

```text
HEAD 存在
working tree clean
.xcodeagent 无 tracked file
```

---

## 1.11 Ownership Baseline 与 Metadata Commit

Bootstrap 完成后按 Ownership 建立不同冲突证据：

```text
ENGINE_EXCLUSIVE
→ .xcodeagent/runtime/template-runtime-state.json.managedBaseline.engineExclusiveFiles
→ path → sha256(actual final bytes) + mode

SHARED_STRUCTURED_HOST
→ 不记录 whole-file baseline
→ Engine-owned node baseline 直接由 committed TemplateState.managed.nodes 表达

PLATFORM_OVERLAY
→ 不记录 whole-file baseline
→ marker/region contract
```


TemplateRuntimeState V1：

```json
{
  "schemaVersion": "template-runtime-state.v1",
  "managedBaseline": {"engineExclusiveFiles": {}},
  "bootstrapCommit": null,
  "finalization": null
}
```

Bootstrap 不使用 `finalization`；它只在 Metadata Commit 临界区短暂写 `bootstrapCommit`，成功后清空。

固定 Shared Structured Host 至少包括：

```text
frontend/package.json
backend/pom.xml
```

这样后续业务正常新增依赖不会因为宿主文件 bytes 改变而永久触发 `TEMPLATE_MANAGED_FILE_CONFLICT`。

Bootstrap Readiness 必须同时验证：

```text
Shared Host 可解析
TemplateState.managed.nodes 在实际 Host 中全部成立
Engine Exclusive runtime managedBaseline 与 actual bytes 一致
Platform marker host contract 成立
```

Bootstrap 的最终提交仍把两个 Template JSON：

```text
.xcodeagent/template-state.json
.xcodeagent/runtime/template-runtime-state.json
```

作为同一个 `TemplateMetadataCommit` 逻辑事务组。现有 `.xcodeagent/checkpoints/checkpoints.sqlite` 仍只由 LangGraph Checkpointer 管理，不写 Bootstrap/Reconcile 自定义状态。

固定：

```text
stage target template-state.json
→ write runtime state with target managedBaseline + bootstrapCommit intent
→ atomic replace runtime state（durable intent）+ fsync
→ atomic replace template-state.json + fsync
→ Target Workspace Contract recheck（intent 仍存在）
→ atomic replace runtime state（bootstrapCommit=null）+ fsync metadata dirs
→ 两者 target digest 都成立后 Bootstrap Completed
```

`Target Workspace Contract` 是清除 intent 前的最后一道门。若它失败，必须保留 intent 并
进入 recovery_required；不得先清空 `bootstrapCommit` 再发现 Workspace 漂移。

若 crash 发生在 metadata replace 中间，Attach Recovery 在取得统一 Mutation Lease 后，必须先复验 Target Workspace Contract，再 roll-forward target metadata pair。

`resources.ts` 若属于 Platform Overlay Host，新 Bootstrap 使用的 Engine Source 必须已经包含双方约定 marker；否则该 Engine 版本不能被 XCodeAgent 标记为 Reconcile-ready。

## 1.12 TemplateMutationCoordinator / WorkspaceMutationManager

`TemplateMutationCoordinator` 负责 Bootstrap 业务编排，但真正的互斥必须下沉到公共：

```text
WorkspaceMutationManager
WorkspaceMutationContext
WorkspaceMutationFS
```

Backend 所有 Workspace 写路径共用同一个按 `workspaceId/applicationId` 管理的 Lease。Lease 身份保存在 Workspace 外部 runtime/registry，确保 Application Delete 也受同一个锁保护。

Lease Provider 是跨平台抽象，不得把 POSIX `flock` 当成产品级唯一实现：

```text
macOS / Linux → fd-backed POSIX flock
Windows       → LockFileEx（或等价的内核级排他文件锁）
```

两种实现都必须具备：同一 workspace 的跨进程排他、进程死亡后内核自动释放、原子递增
并持久化 epoch、以及旧 MutationContext 在新 epoch 下被 `WorkspaceMutationFS` fencing。
不能只持久化 lease metadata，也不能把仅进程内 mutex 当作 Lease。

Bootstrap acquire lease 后，其内部 Materialize/Git baseline/metadata commit 都携带同一 MutationContext。Build、Projection、Reconcile、Delete、Attach Recovery 也必须走同一 Manager。

实际文件写入层必须验证 MutationContext；无有效 Lease 的 server-owned Workspace mutation 返回：

```text
WORKSPACE_MUTATION_LEASE_REQUIRED
```

文件层与受管子进程必须分开建模：

```text
WorkspaceMutationFS
→ Backend 内部 write / replace / move / delete

WorkspaceMutationProcessRunner
→ git、blocking validation、Build、其他会写 Workspace 的受管子进程
```

Process Runner 在启动前校验 MutationContext，并把 leaseId/epoch 记录到 process registry；
它不能被要求“经 WorkspaceMutationFS 写文件”。Agent File Tool、delete tool、workspace
mutation API、Projection、Git 和受管 subprocess 都必须纳入 write-path inventory 与旁路测试。

SSE disconnect 不取消 Bootstrap。

## 1.13 Delete 与 Attach Recovery

Preparation 阶段可取消并清理 staging。

Commit 临界区必须等待成功或 rollback 完成后再 Delete。

Workspace Attach 若发现 `template-runtime-state.json.bootstrapCommit.phase == COMMITTING_METADATA`，必须先 Acquire 同一 Mutation Lease，并复验 Target Workspace Contract：

```text
ENGINE_EXCLUSIVE target baseline 成立
SHARED_STRUCTURED_HOST target managed.nodes 成立
PLATFORM_OVERLAY marker contract 成立
```

通过后再处理 metadata pair：

```text
TemplateState target + RuntimeBaseline target + bootstrapCommit!=null → 清 bootstrapCommit，完成 Bootstrap
TemplateState absent + RuntimeBaseline target + bootstrapCommit!=null → Target Contract 通过后 roll-forward TemplateState
TemplateState target + RuntimeBaseline old/invalid                    → recovery required
其他异常组合                                                     → recovery required
```

若 Workspace target contract 已不成立，则标记 `TEMPLATE_METADATA_WORKSPACE_DIVERGED`，不得盲目 roll-forward。

Attach 必须先读取 Bootstrap Execution Journal：若 `validationRun` 仍是运行中状态，先按
process identity 终止 orphan process group；只有确认其不能再写 Workspace 后，才进入
metadata recovery 或 cleanup。

若尚未进入 metadata commit，cleanup 只删除当前 `attemptId` 的 Execution Journal；不得
删除 Requirement / ProductPlan / UiDesign / TechnicalPlan 或其他运行时所有者的数据。

只有 Metadata Commit 尚未开始或仍处于可回滚阶段，才按下面逻辑执行 cleanup。

Workspace Attach 若发现：

```text
lifecycle = GENERATING_APPLICATION_TEMPLATE_FILES
Coordinator 无 active task
```

执行确定性 cleanup；成功后转：

```text
APPLICATION_TEMPLATE_GENERATION_FAILED
```

cleanup 未完成则保持 GENERATING，下一次 Attach 继续收尾。

---

## 1.14 Bootstrap Readiness 与提交边界

以下是 **Pre-Commit Readiness**，必须在写入 durable `COMMITTING_METADATA` intent 前完成。
其中 TemplateState / RuntimeBaseline 校验使用已 stage 的 target JSON 与当前 Workspace，
不要求正式 metadata 文件已经提交：

1. RequirementSpec CONFIRMED；
2. ProductPlan CONFIRMED；
3. UiDesign CONFIRMED/SKIPPED；
4. TechnicalPlan CONFIRMED；
5. TemplateState 完整 Schema 合法；
6. TemplateState `canonical_json_sha256_v1`（RFC 8785 JCS + SHA-256）可计算；
7. Plan nextTemplateState 与 Package State 一致；
8. RequestedConfig 与 TemplateState.requested 语义一致；
9. 请求 Capability 已体现在 effective；
10. `managed.files` 宿主存在且为普通文件；
11. `managed.nodes` 宿主存在且可解析；
12. migrations 对应文件存在；
13. frontend/backend 入口文件存在；
14. Engine Blocking Validation 全部成功；
15. Git HEAD 存在且 clean；
16. `.xcodeagent` 不进入 baseline；
17. 无 staging 残留；
18. 不依赖 templateVariant/main/auth branch。

Pre-Commit Readiness 失败必须 cleanup/rollback。

Metadata Commit 后只允许执行不改变语义的 **Post-Commit Verification**：metadata pair target
digest、Git HEAD/clean、staging 已清理、Execution Journal 可归档。该校验异常时不得 cleanup
已提交 Workspace，而是保留 Execution Journal 并标记 recovery_required 交给 Attach 处理。

---

## 1.15 与业务开发解耦

Bootstrap 只建立：

```text
Template Capability
TemplateState
Git Baseline
Managed Baseline Evidence
```

不生成：

```text
业务页面
业务菜单
业务 Route
业务 resourceKey
AuthConstants 业务常量
```

后续：

```text
READY_FOR_WORKBENCH
→ Workspace Inspection
→ Build DAG
→ Agent Build
→ Platform Projection
```

---

## 1.16 新旧 Workspace 切换规则

新版 Template Reconcile 只对本 Bootstrap 方案生成的**完整 Engine V1 TemplateState** 启用。

旧四字段 TemplateState Workspace 不在 Bootstrap 阶段自动改写，也不通过扫描现有代码推断 `capabilities / managed.nodes / migrations`。

运行时应由 `TemplateStateCompatibilityChecker` 判断：

```text
完整 Engine V1 State → new template lifecycle
旧四字段 State       → legacy lifecycle / capability revision blocked
```

旧 Workspace 的显式迁移不属于 Bootstrap V1。

---

# 第二章 分步实施计划与人工验收

Bootstrap 与 Reconcile 共用 Engine Model、Mutation Lease、Validation Runner、Digest 与 Ownership 公共组件。本章只描述首次 Workspace 初始化的实施顺序。

统一建议提供：

```text
validation/template-bootstrap/
├── verify_step_00_engine_contract.py
├── verify_step_01_client_package.py
├── verify_step_02_materialize_validation.py
├── verify_step_03_git_metadata.py
├── verify_step_04_runtime_cutover.py
└── verify_e2e.py
```

验证程序必须由 Python 在 Windows/macOS 共同执行；可额外提供 shell / PowerShell wrapper，
但 wrapper 不是唯一验收入口。

## 2.1 Step 00：Engine Plan/Generate 契约

实现并确认实际 Engine 支持完整 TemplateState、`/v1/plan`、`/v1/generate`、validationPlan 与 Generate Package State。

人工验收：

```bash
python3 validation/template-bootstrap/verify_step_00_engine_contract.py
```

手工核对：

```bash
unzip -p <generate.zip> .xcodeagent/template-state.json | jq '.capabilities,.managed,.migrations'
```

通过标准：Generate Package State 与 Plan `nextTemplateState` JCS 语义相等；实际 Engine 不是旧四字段版本。

## 2.2 Step 01：公共 Engine Client、Archive Security 与 Package Validator

实现：

```text
plan(null, requested)
generate(requested)
archive traversal/symlink guard
Package State / Plan alignment
```

人工验收：

```bash
python3 validation/template-bootstrap/verify_step_01_client_package.py
```

必须可见验证：合法 package 解压；绝对路径、`..`、symlink、重复 State、State mismatch 均稳定拒绝。

## 2.3 Step 02：Bootstrap Journal、跨平台 Lease、Materialize 与 Blocking Validation

实现：

```text
Persist Bootstrap Execution Journal + TechnicalPlan / RequestedConfig binding
Acquire Workspace Mutation Lease (platform lock + epoch fencing)
Bootstrap preflight
staging materialize
atomic move frontend/backend
persist validationRun before process start
blocking validation process group
persist validation exit evidence before Git
validation side-effect cleanup
all Workspace write-path inventory
```

人工验收：

```bash
python3 validation/template-bootstrap/verify_step_02_materialize_validation.py
```

人工观察：

- 第二 worker 同时 Bootstrap 被 Lease 阻断；
- macOS 与 Windows 上 kill 第一 worker 后均可重新取得更高 epoch；
- Validation failure 后 frontend/backend、lockfile、副作用按 policy 清理；
- Backend 在 Validation 运行中被 kill 后，Attach 从 Execution Journal 终止已登记的 orphan process，确认它不再修改 Workspace 后才 cleanup；
- Agent File Tool、delete tool、workspace mutation API、Projection、Git 和受管 subprocess 缺少有效 MutationContext 时均不能旁路写入。

## 2.4 Step 03：Git Baseline、Ownership Baseline 与 Metadata Commit

实现顺序：

```text
Validation PASS
→ git init + baseline commit
→ Compute ENGINE_EXCLUSIVE actual-byte baseline
→ verify Shared Host managed nodes
→ Pre-Commit Readiness（含 TechnicalPlan / RequestedConfig binding recheck）
→ stage TemplateState + baseline
→ persist COMMITTING_METADATA evidence
→ runtime intent replace + fsync
→ TemplateState replace + fsync
→ Target Workspace Contract recheck（intent 保留）
→ runtime final replace + fsync
→ Post-Commit Verification
→ Bootstrap Completed
```

人工验收：

```bash
python3 validation/template-bootstrap/verify_step_03_git_metadata.py
```

手工检查：

```bash
git -C <workspace> rev-parse HEAD
git -C <workspace> status --porcelain
git -C <workspace> ls-files .xcodeagent
jq . <workspace>/.xcodeagent/template-state.json
```

故障注入至少覆盖：

- kill 在 Validation 子进程启动后、exit evidence 持久化前；
- kill 在 template-runtime-state intent 与 template-state replace 之间；
- State 已 target、intent 未清除时外部修改 Engine Exclusive / managed node；
- Pre-Commit Readiness 失败必须 cleanup；Post-Commit Verification 异常必须进入 recovery_required，不能 cleanup。

## 2.5 Step 04：TemplateState Consumer、Build Binding 与 Runtime Cutover

BuildContext 使用：

```text
template_state_jcs_sha256
```

而不是 `state_sha256`。Build 前同时验证 State JCS digest 与 Managed Workspace Health。

同一次切换退出：

```text
Electron clone
templateVariant/main/auth branch
旧四字段 TemplateState consumer
```

人工验收：

```bash
python3 validation/template-bootstrap/verify_step_04_runtime_cutover.py
```

确认新建应用只通过 Backend Template Engine 路径生成；Build Task Plan 能看到 `template_state_jcs_sha256`。

## 2.6 Step 05：Legacy Gate 与 Reconcile-ready 标记

只有满足：

```text
完整 TemplateState
ENGINE_EXCLUSIVE baseline
Shared Host managed-node health
Platform marker host contract
Engine Consumer Contract PASS
```

才使 Workspace 的 `reconcile-ready` 判定为 true。

`reconcile-ready` 不是新的持久化标记或第三份模板状态；必须由
`TemplateStateCompatibilityChecker + TemplateRuntimeState baseline + Managed Workspace Health +
Engine Consumer Contract evidence` 每次推导。

旧四字段 Workspace 不自动升级。

人工验收：

```bash
python3 validation/template-bootstrap/verify_e2e.py
```

并用一个旧 Workspace 验证 `LEGACY_TEMPLATE_STATE_UNSUPPORTED`。

## 2.7 Bootstrap 整体验证案例

使用 authorization=true 创建一个全新应用：

```text
TechnicalPlan Confirmed
→ /v1/plan(null, authorization)
→ effective 自动含 login+authorization
→ /v1/generate
→ materialize
→ blocking validation
→ git baseline
→ State+Baseline metadata commit
→ readiness PASS
```

人工最终核对：

```bash
jq '.effective,.managed.nodes' <workspace>/.xcodeagent/template-state.json
git -C <workspace> status --porcelain
jq '.template_context' <workspace>/.xcodeagent/build-task-plan.json
```

预期：工作区 clean、`.xcodeagent` 未进入 Git baseline、完整 State 存在、Shared Host Engine nodes 成立、Build 使用 `template_state_jcs_sha256`。

## 2.8 Definition of Done

- [ ] 每一步都有独立人工验收脚本；
- [ ] Bootstrap 先 `/plan` 后 `/generate`；
- [ ] Generate State 与 Plan nextTemplateState JCS 等价；
- [ ] XCodeAgent 消费完整 TemplateState；
- [ ] Blocking Validation 使用可 crash-recovery 的 process group runner；
- [ ] Bootstrap Execution Journal 在 Materialize 前持久化 input binding，并在启动 Validation process 前持久化 process identity；
- [ ] Mutation Lease 在 macOS/Linux 使用 `flock`、在 Windows 使用等价内核锁，并具备 epoch fencing；
- [ ] WorkspaceMutationFS 与 WorkspaceMutationProcessRunner 覆盖全部已盘点的 Workspace 写路径；
- [ ] Git baseline 成立且 `.xcodeagent` 不 tracked；
- [ ] Managed Baseline 只覆盖 Engine Exclusive；
- [ ] `package.json` / `pom.xml` 作为 Shared Structured Host；
- [ ] resources marker host 契约成立；
- [ ] State + Baseline 通过 crash-consistent metadata commit；
- [ ] Target Workspace Contract 在清除 bootstrapCommit intent 前完成；
- [ ] Pre-Commit Readiness 失败可 cleanup；metadata commit 后异常只允许 recovery，不得误删 Workspace；
- [ ] Build 使用 `template_state_jcs_sha256` + Managed Workspace Health；
- [ ] 旧四字段 Workspace 不自动切入 Reconcile；
- [ ] `reconcile-ready` 由现有 State/runtime health 推导，不新增持久化模板事实；
- [ ] 实际 Engine Consumer Contract PASS 后才判定 Reconcile-ready。
