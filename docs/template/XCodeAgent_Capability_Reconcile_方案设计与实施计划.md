# XCodeAgent Template Capability Reconcile 方案设计与实施计划

> 适用对象：XCodeAgent `template_refactor` 目标架构
>
> 基准文档：`XCodeAgent_Capability_Reconcile_方案设计与实施计划_简化事务版.md`
>
> 核心职责：消费 Template Service 返回的可执行 Modification Strategy，在当前 Workspace 上完成幂等增量修改、Capability 后置条件验收、可恢复执行和 TemplateState 最终提交。
> 明确边界：XCodeAgent 是 **Workspace Runtime 与唯一代码事实执行方**；Template Service 是 **Capability / Strategy / Candidate State 的权威规划方**。

---

# 第一章 方案设计

## 1. 目标、前提与核心原则

### 1.1 目标

Capability Reconcile 的目标是：

```text
TechnicalPlan 已达到现有模板处理条件
        ↓
按现有逻辑决定 DOWNLOAD / UPDATE / NO_ACTION
        ↓
Template Preparation 独立节点展示执行
        ↓
Template Service 计算 Capability 目标与 Modification Strategy
        ↓
XCodeAgent 在当前 Workspace 上执行 Strategy
        ↓
Validation Plan
        ↓
TemplateState Atomic Commit
        ↓
回到现有 Development Readiness 判断
```

本方案重点解决：

```text
1. 模板更新不覆盖用户已有业务代码。
2. Strategy 在当前真实 Workspace 上幂等执行。
3. 进程崩溃后能够判定并恢复未完成 Reconcile。
4. TemplateState 与 Workspace 不一致时能够通过 RECONCILE 的 Capability Postcondition Validation 发现并收敛。
5. Validation 的构建/测试副作用不污染真实 Workspace。
6. 模板下载/更新能够独立展示进度、日志与 Retry。
7. 只支持当前 V2 State、Strategy Package 与 ReconcileAttempt 合约。
```

### 1.2 本方案的强前提

本方案明确以以下产品级约束为前提：

> **模板下载 / 更新执行期间，禁止任何外部、人工或其他 XCodeAgent 任务写入真实 Workspace。**

包括：

```text
IDE 自动保存
用户人工编辑
外部脚本
其他 Agent
Build Task
Repair Task
其他 Template Reconcile
任何非当前 Template Preparation 的 Workspace Writer
```

因此 V2 不再建设：

```text
Workspace SHA Guard
Git clean worktree 前置条件
Git HEAD rollback
通用 WorkspaceTransaction
Transaction Workspace Snapshot
TransactionFileSnapshot
Persistent File Journal
Journal Context
```

注意：

```text
禁止外部写入
≠
不需要 Crash Recovery
≠
不需要 Capability Postcondition Validation
≠
不需要持久化执行状态
```

进程崩溃、State Commit 临界区、Validation 副作用仍然需要单独解决。

### 1.3 核心原则

```text
1. Current Workspace First：当前 Workspace 是代码事实源。
2. Service Plans, XCodeAgent Executes：Service 负责 Capability / Strategy 规划，XCodeAgent 负责在当前 Workspace 上执行。
3. /v1/update 采用单次最小协议，只传 currentTemplateState / requestedConfig / mode。
4. mode 只冻结 APPLY | RECONCILE，不再引入 NORMAL / HEALTH_RECONCILE 等并行语义。
5. Service 返回 Strategy Package，不返回 Workspace ChangeSet，也不读取 Workspace Snapshot。
6. 双端协议不包含 Workspace Snapshot / Workspace Context / 428 二阶段回传。
7. Strategy 必须幂等：S(S(code)) = S(code)。
8. 单 Workspace 同一时刻只允许一个 Template Preparation 写执行。
9. 持久化最小 ReconcileAttempt，但不持久化文件 before-image Journal。
10. Crash Recovery 采用 Roll-forward，而不是 Git rollback。
11. Build/Test 类 Validation 必须在隔离环境运行。
12. Validation Plan 成功后才允许提交 nextTemplateState；RECONCILE 的 Validation Plan 必须包含 Capability Postcondition Validation。
13. TemplateState 只在最终成功后原子提交。
14. Template Preparation 只拆展示、日志与重试，不改变现有 TechnicalPlan 依赖和 Development Gate。
15. 不兼容旧接口、旧 ReconcileAttempt、旧任务数据或旧 Workspace；它们必须重新初始化为当前 V2 Workspace。
```

---

## 2. 关键概念与职责边界

### 2.1 不再保留 Workspace Snapshot 协议

本方案正式冻结：**Workspace 当前代码只在 XCodeAgent 本地读取和执行，不上传 Template Service。**

因此 V2 双端协议不再建设：

```text
Workspace Snapshot
Decision Workspace Snapshot
Workspace Context
snapshotRequest / workspaceRequirements
428 WORKSPACE_*_REQUIRED
readTargets / absenceTargets
Service 侧基于当前代码生成最终 ChangeSet
```

这里需要与本地执行态概念区分：

| 概念 | 是否保留 | 用途 |
|---|---:|---|
| Workspace Snapshot / Context | 删除 | 不再作为 Service 协议输入 |
| Transaction Workspace Snapshot | 删除 | 不用于并发保护或全 Workspace rollback |
| Persistent File Journal / before-image | 删除 | 不建设 crash-safe 文件回滚日志 |
| WorkingCopyStore originalContent | 保留，内存态 | 同一路径 Strategy 串行修改、正常进程内失败恢复 |
| Immutable Strategy Package | 保留，持久化 | Crash Recovery 时重放同一份权威 Strategy |
| ReconcileAttempt | 保留，持久化 | 记录 phase、Package/State digest、Retry/Recovery 事实 |
| Validation Result | 保留 | 判断本次执行是否可以完成和提交 State |

因此：

```text
WorkingCopyStore ≠ Workspace Snapshot
ReconcileAttempt ≠ Transaction Journal
Immutable Strategy Package ≠ Workspace Backup
```

### 2.2 Template Service 职责

Template Service 负责：

```text
Capability Dependency Resolution
Capability Config Canonicalization
判断 ENABLE / CONFIG_CHANGE / REVISION_REFRESH
APPLY 模式下生成本次变化所需 Strategy
RECONCILE 模式下基于 currentTemplateState.effective 重新生成全部维护 Strategy
Addition CREATE / MAINTAIN 规则
生成 Modification Strategy
生成 candidate nextTemplateState
生成 Validation Plan
生成完整 Strategy Update Package
```

Template Service 不负责：

```text
读取当前 Workspace
接收 Workspace Snapshot / Context
执行面向当前代码的 AST / parser transformation
生成最终 UPDATE_FILE ChangeSet
```

### 2.3 XCodeAgent 职责

XCodeAgent 负责：

```text
Template Preparation 节点承载
Reconcile Run Gate
读取 / 迁移 TemplateState
调用 /v1/update
校验并持久化 Strategy Update Package
持久化 ReconcileAttempt
Crash Recovery / Roll-forward
读取当前 Workspace
执行 Modification Strategy
WorkingCopyStore
Apply
进程内 Failure Restore
Validation Sandbox
执行 Package 内的 Validation Plan（含 Capability Postcondition Validation）
TemplateState Atomic Commit
AG-UI 进度 / 日志投影
Retry
```

XCodeAgent 不负责重新实现 Service 中的 Capability Planner，也不把 Workspace 内容上传给 Service。

---

## 3. 端到端主流程

### 3.1 产品流程保持不变

Template Preparation 从技术规划节点 UI 中拆出，但原依赖关系不改变：

```text
TechnicalPlan 达到现有可执行条件
        ↓
现有模板处理判断
        ↓
Template Preparation Node
        ├─ DOWNLOAD → 既有 Bootstrap
        ├─ UPDATE   → Capability Reconcile
        └─ NO_ACTION → 按现有逻辑继续
        ↓
模板处理得到现有成功结果
        ↓
现有 Development Readiness 判断
        ↓
允许 / 不允许进入开发阶段
```

禁止新增：

```text
第二套 TemplateReady Decision
第二套 CAN_ENTER_DEVELOPMENT
第二套 TechnicalPlan Confirmed 状态
```

### 3.2 UPDATE 主流程

正常 TechnicalPlan 驱动的模板更新使用 `mode=APPLY`；需要强制健康收敛时使用 `mode=RECONCILE`。两种模式共用同一个 `/v1/update` 单次请求协议。

```text
Template Preparation UPDATE
        ↓
Enter Reconcile Run Gate
        ↓
Load Runtime State
        ↓
存在未完成 Attempt？
        ├─ YES → Recovery Coordinator
        └─ NO
        ↓
Load Current V2 TemplateState
        ↓
Compile RequestedConfig（复用现有逻辑）
        ↓
POST /v1/update
mode=APPLY | RECONCILE
        ↓
 ┌──────────────────────┬──────────────────────────┐
 │ 204 NO_CHANGE        │ 200 Strategy Package     │
 └──────────┬───────────┴────────────┬─────────────┘
            │                        ↓
            │              Validate Package Contract
            │                        ↓
            │              Persist Immutable Package
            │                        ↓
            │              Persist Attempt=PREPARED
            │                        ↓
            │              Execute Strategies against
            │              Current Workspace
            │                        ↓
            │              WorkingCopyStore
            │                        ↓
            │                     Apply
            │                        ↓
            │              Validation Executor
            │                        ↓
            │              Validation Plan
            │                ├─ Capability Postcondition Validation
            │                └─ Build / Test Validation（Sandbox）
            │                        ↓
            │              Commit nextTemplateState
            │                        ↓
            │              Attempt=SUCCEEDED
            │                        ↓
            │              Template Node=SUCCEEDED
            ↓
APPLY：表示 Desired State 无变化，可按现有流程结束模板更新
RECONCILE：只有 effective 为空、没有任何 Capability 可检查时才允许 204
```

`204` 的语义必须由 `mode` 解释，不能把 `APPLY` 的 NO_CHANGE 当成 Workspace 已健康。

### 3.3 关键不变量

在没有未完成 Attempt 时：

```text
Committed TemplateState
        ↕
当前 Workspace 已通过对应 Package 的 Capability Postcondition Validation
```

必须一致。

在存在未完成 Attempt 时：

```text
Workspace 允许暂时处于部分更新状态
        ↓
Development Gate 必须保持关闭
        ↓
下一次进入模板处理前必须先 Recovery
```

本方案不承诺进程被 `kill -9` 后立即恢复到执行前的 Workspace；而是通过 durable Attempt + immutable Package + Strategy 幂等做 Roll-forward。

---

## 4. `/v1/update` 双端最小协议（唯一冻结协议）

> **双端文档最终直接固定成下面这个最小协议。** 该协议是 Template Service 与 XCodeAgent 之间唯一的 V2 wire contract；其他文档不得重新定义 mode、Workspace Snapshot、428 二阶段请求或最终 ChangeSet 协议。

### 4.1 请求

```http
POST /v1/update
```

```json
{
  "protocolVersion": "2",
  "mode": "APPLY",
  "currentTemplateState": {},
  "requestedConfig": {}
}
```

V2 请求只包含：

```text
protocolVersion
mode
currentTemplateState
requestedConfig
```

明确禁止出现：

```text
workspace
workspaceSnapshot
workspaceContext
existingFiles
pathStates
readTargets
absenceTargets
snapshotRequest
workspaceRequirements
decisionFingerprint
planFingerprint
```

### 4.2 `mode = APPLY | RECONCILE`

#### APPLY

用于正常 Desired State 更新：

```text
currentTemplateState
+
requestedConfig
+
currentTemplateRevision
        ↓
ENABLE / CONFIG_CHANGE / REVISION_REFRESH
        ↓
生成本次需要执行的 Strategy
```

若不存在任何状态变化：

```http
204 No Content
```

此时含义仅为：

```text
Desired Capability State 无变化
```

不等价于 Workspace 已健康。

#### RECONCILE

用于对当前已生效 Capability 做健康收敛：

前置条件必须成立：

```text
semanticCanonicalize(requestedConfig)
==
semanticCanonicalize(currentTemplateState.requested)
```

若不成立，Service 必须直接拒绝：

```text
409 RECONCILE_REQUESTED_CONFIG_MISMATCH
```

不得忽略请求中的新 Desired State，也不得自动改为 `APPLY`。

```text
currentTemplateState.effective
        ↓
按 Capability Dependency Order
重新生成这些 Capability 的全部幂等维护 Strategy
        ↓
返回 Strategy Package
```

`RECONCILE` 不因为 `requested == effective` 或状态无变化而直接返回 204。

只有：

```text
currentTemplateState.effective 为空
```

即没有任何 Capability 可检查时，才允许返回 204。

### 4.3 `204 NO_CHANGE`

统一语义：

```text
APPLY + 204
= Desired State 无变化

RECONCILE + 204
= 当前没有任何 effective Capability 需要检查
```

禁止定义：

```text
204 = Workspace 一定健康
```

Capability 是否完整落在当前 Workspace，只能由 XCodeAgent 实际执行幂等 Strategy 后的 Validation Plan 得出。

### 4.4 `200 Strategy Update Package`

Service 返回的是 **Strategy Package**，不是最终 Workspace ChangeSet。

逻辑响应：

```json
{
  "protocolVersion": "2",
  "packageId": "pkg-...",
  "mode": "APPLY",
  "sourceRevision": "R3",
  "currentStateDigest": "sha256:...",
  "nextStateDigest": "sha256:...",
  "strategies": [],
  "validationPlan": [],
  "payloadManifest": {},
  "nextTemplateState": {},
  "diagnostics": []
}
```

其中：

```text
Service：State → Capability → Strategy
XCodeAgent：Strategy + Current Workspace → Code
```

Service 不返回：

```text
UPDATE_FILE 最终完整文件
Workspace ChangeSet
基于 Workspace Snapshot 计算出的代码结果
```

### 4.5 失败响应

V2 最小错误协议至少包括：

```text
400 INVALID_REQUEST
409 CAPABILITY_REMOVAL_UNSUPPORTED
409 CAPABILITY_CONFIG_MIGRATION_UNSUPPORTED
409 RECONCILE_REQUESTED_CONFIG_MISMATCH
409 TEMPLATE_STATE_PROTOCOL_MISMATCH
422 STRATEGY_PACKAGE_BUILD_FAILED
```

具体 Capability / Strategy schema 错误可继续扩展，但不得重新引入 428 Workspace Context / Snapshot 流程。

### 4.6 唯一跨端边界

最终固定：

```text
Template Service
负责“应该做什么”
        ↓
Modification Strategy

XCodeAgent
负责“在当前代码上把它真正做出来”
        ↓
Current Workspace + Strategy Executor
```

这条约定优先于旧双端方案中任何 `Workspace Snapshot → Service Transformer → ChangeSet` 描述。

---

## 5. Update Package 可执行协议

### 5.1 顶层字段

建议冻结：

```json
{
  "protocolVersion": "2",
  "packageId": "pkg-...",
  "mode": "APPLY",
  "currentStateDigest": "sha256:...",
  "nextStateDigest": "sha256:...",
  "strategies": [],
  "validationPlan": [],
  "payloadManifest": {},
  "nextTemplateState": {}
}
```

### 5.2 Strategy 唯一标识与全局顺序

每个 Strategy：

```json
{
  "strategyId": "strategy-0003",
  "index": 3,
  "schemaVersion": 1,
  "type": "ENSURE_ROUTE",
  "target": "frontend/src/routes.tsx",
  "precondition": {},
  "payloadRef": null
}
```

V2 固定：

```text
strategies[] 数组顺序
= 唯一权威全局执行顺序
```

因此：

```text
同一路径：按数组顺序
跨文件：按数组顺序
```

Executor 不得：

```text
按 path regroup 后改变全局顺序
按 type regroup
自行并行执行
根据 Handler 类型重排
```

如果未来协议增加 `dependsOn`，V2 只将其用于 Contract Validation；实际执行仍按冻结后的全局顺序。

### 5.3 Payload Integrity

`payloadManifest` 至少绑定：

```text
payloadRef
size
sha256
```

在第一次真实 Workspace Apply 之前必须完成：

```text
Package digest 校验
nextTemplateState digest 校验
每个 payload digest 校验
Strategy schema 校验
Validation schema 校验
```

任一失败：

```text
UPDATE_PACKAGE_INVALID
```

不得触碰真实 Workspace。

### 5.4 Validation Contract

Validation Item：

```json
{
  "validationId": "validation-03",
  "index": 3,
  "type": "MAVEN_TEST",
  "workingDirectory": "backend",
  "blocking": true,
  "timeoutSeconds": 600,
  "executionMode": "SANDBOX"
}
```

`type` 必须区分：

```text
CAPABILITY_POSTCONDITION
→ 验证指定 Capability 的路径、结构、依赖与行为后置条件

BUILD_TEST
→ 验证构建、测试或打包质量门禁
```

`RECONCILE` Package 的 `validationPlan[]` 必须为每个 effective Capability 包含至少一个 `CAPABILITY_POSTCONDITION` Item；该 Item 是 Capability 完整落地的唯一权威验收输入。

结果必须结构化：

```json
{
  "validationId": "validation-03",
  "status": "FAILED",
  "exitCode": 1,
  "durationMs": 15234,
  "errorCode": "VALIDATION_COMMAND_FAILED",
  "stdoutLogRef": "...",
  "stderrLogRef": "...",
  "sideEffectViolations": []
}
```

禁止只以 Exception 文本作为协议结果。

---

## 6. Reconcile Run Gate 与 Workspace 独占边界

### 6.1 Run Gate

同一 Workspace 同时最多一个：

```text
Template Preparation Writer
```

包括：

```text
DOWNLOAD
UPDATE
RECONCILE
RECOVERY
```

Run Gate 是流程互斥，不是 ACID 文件系统事务。

### 6.2 Workspace 独占

Run Gate 持有期间，上层调度必须禁止任何其他 Writer。

因此 V2 明确不实现：

```text
beforeSha
WORKSPACE_CHANGED_DURING_RECONCILE
Git clean verification
```

若未来产品不能继续保证 Workspace 完全独占，必须重新引入并发检测机制；不能默认为当前方案天然支持并发编辑。

---

## 7. Durable ReconcileAttempt 与 Immutable Package

### 7.1 为什么仍需要 Attempt

删除 Transaction Journal 后仍然需要持久化最小执行事实，以处理：

```text
进程 crash
服务重启
kill -9
机器重启
Retry
AG-UI 重连
State Commit 临界区判定
```

Attempt 不保存每个文件的 before-image，因此不是 File Journal。

### 7.2 建议持久化目录

```text
.xcodeagent/runtime/template-reconcile/
├── current.json
└── attempts/
    └── {attemptId}/
        ├── attempt.json
        ├── update-package.zip
        ├── package-manifest.json
        └── logs/
```

### 7.3 Attempt 最小字段

```json
{
  "attemptId": "...",
  "retryOf": null,
  "operationType": "UPDATE",
  "mode": "APPLY",
  "protocolVersion": "2",
  "technicalPlanSha256": "...",
  "packageId": "...",
  "sourceRevision": "R3",
  "packageDigest": "...",
  "currentStateDigest": "...",
  "nextStateDigest": "...",
  "phase": "PREPARED",
  "status": "RUNNING",
  "startedAt": "...",
  "updatedAt": "...",
  "errorCode": null,
  "errorMessage": null
}
```

### 7.4 Phase

推荐：

```text
PREPARING
PREPARED
APPLYING
VALIDATING
VALIDATING_POSTCONDITIONS
COMMITTING_STATE
SUCCEEDED
FAILED
RECOVERY_REQUIRED
```

在第一次真实 Workspace 写入前，必须满足：

```text
Update Package 已完整持久化
+
Attempt=PREPARED 已原子持久化
```

这样 crash 后才能确认权威 Package。

---

## 8. Crash Recovery 与 Roll-forward

### 8.1 Recovery 总原则

V2 不通过 Git rollback 回到 `preReconcileHead`。

统一采用：

```text
Durable Attempt
+
Immutable Update Package
+
State Digest 判定
+
幂等 Strategy
→ Roll-forward
```

### 8.2 Recovery 入口

每次新的 UPDATE / RECONCILE 开始前：

```text
Load unfinished Attempt
```

如果存在，必须先 Recovery，不允许直接生成新的 Package。

### 8.3 判定规则

读取当前 TemplateState digest。

#### 情况 A：等于 `currentStateDigest`

表示：

```text
TemplateState 尚未推进
Workspace 可能已部分 Apply
```

执行：

```text
重新加载该 Attempt 的 immutable Package
从 Strategy index 0 重新执行全部 Strategy
→ 幂等收敛
→ Validation Plan
→ Commit nextTemplateState
```

不从某个 operation index 续跑。

#### 情况 B：等于 `nextStateDigest`

表示：

```text
TemplateState 可能已经提交
但 Attempt 尚未成功落终态
```

执行：

```text
重新执行该 immutable Package 的 Validation Plan
├─ PASSED → Attempt=SUCCEEDED
└─ FAILED → RECOVERY_REQUIRED
```

#### 情况 C：两个 digest 都不匹配

返回：

```text
RECOVERY_STATE_CONFLICT
```

禁止自动继续，避免错误 Package 作用于未知 State。

### 8.4 Development Gate

存在以下任意状态：

```text
unfinished Attempt
RECOVERY_REQUIRED
FAILED 且 Workspace 健康未确认
```

必须阻断后续开发阶段。

这不是新增第二套开发判断，而是模板操作尚未获得原有“成功”结果，因此原有 Gate 自然不满足。

---

## 9. Current-Contract-Only Boundary

V2 只接受并处理：

```text
TemplateState schemaVersion=2
Strategy Update Package protocolVersion=2
ReconcileAttempt protocolVersion=2
```

以下旧数据不兼容，也不提供迁移、恢复或路由分支：

```text
V1 TemplateState
V1 ChangeSet
V1 ReconcileAttempt
旧任务运行态
旧 Workspace
```

检测到任一旧合约时，返回：

```text
TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED
```

旧 Workspace 必须重新初始化为当前 V2 Workspace 后，才能进入 Template Preparation。

---

## 10. WorkingCopyStore

WorkingCopyStore 仍是本次 Strategy 的内存执行上下文。

```text
WorkingCopyEntry
{
    path
    originalExists
    originalContent
    workingContent
    changed
    applied
}
```

职责：

```text
lazy load 当前文件
保存进程内 originalContent
承载连续 Strategy 的 workingContent
同一路径串行修改
最终统一 Apply
正常异常路径 Failure Restore
```

同一路径：

```text
一次读取
多次内存修改
最多一次最终写入
```

注意：

> WorkingCopyStore 是**进程内**恢复能力，不承担 crash recovery。

Crash 后依赖第 8 节 Roll-forward。

---

## 11. Modification Strategy Executor

统一：

```text
ModificationStrategyExecutor
StrategyHandler<T>
```

基础 Strategy：

```text
ADD_FILE
TEXT_ANCHOR_INSERT
ENSURE_IMPORT
ENSURE_NPM_DEPENDENCY
ENSURE_MAVEN_DEPENDENCY
ENSURE_REACT_PROVIDER
ENSURE_ROUTE
ENSURE_MENU_ITEM
ENSURE_SPRING_BEAN
ENSURE_INTERCEPTOR
```

Handler 必须：

```text
只操作 WorkingCopyStore
不得自行写真实 Workspace
不得自行修改 TemplateState
不得自行改变 Strategy 顺序
必须满足幂等语义
```

前端结构化修改优先：

```text
AST / parser
```

后端 Java 结构化修改优先：

```text
Java Parser / AST
```

纯文本 Anchor 只作为明确协议 Strategy 使用，不得作为不可识别结构的隐式 fallback。

---

## 12. Strategy 幂等、ADD_FILE 与冲突语义

### 12.1 幂等

所有 Strategy：

```text
S(S(code)) = S(code)
```

这是 Roll-forward Recovery 的硬前提，不再只是优化目标。

### 12.2 ADD_FILE

```text
target 不存在
→ CREATE

target 已存在 && 当前内容 == payload 期望内容
→ NO_CHANGE

target 已存在 && 当前内容 != payload 期望内容
→ ADDITION_TARGET_CONFLICT
```

不得覆盖不同内容。

### 12.3 TRANSFORM

```text
目标不存在
→ TRANSFORM_TARGET_NOT_FOUND

结构不唯一
→ TRANSFORM_TARGET_AMBIGUOUS

业务语义冲突
→ TRANSFORM_CONFLICT
```

禁止：

```text
转换失败 → 用 Service 返回完整目标文件强制覆盖
```

---

## 13. Apply 与进程内 Failure Restore

### 13.1 Apply

所有 Strategy 先在 WorkingCopyStore 完成，然后：

```text
changed entries
→ 确定顺序 Apply
→ 每个文件采用 temp + fsync + atomic replace（适用时）
→ 标记 applied
```

### 13.2 正常失败恢复

如果进程仍然存活，而：

```text
Apply 中途失败
Validation 失败
Capability Postcondition Validation 失败
State Commit 抛出明确未提交异常
```

可使用 WorkingCopyStore：

```text
originalExists=true  → restore originalContent
originalExists=false → delete 本次创建文件
```

### 13.3 Failure Restore 的边界

Failure Restore 只处理：

```text
当前进程中的 touched source files
```

它不是：

```text
Crash-safe Journal
全 Workspace rollback
Git rollback
```

若进程直接终止，WorkingCopyStore 消失，下一次通过 Roll-forward Recovery 收敛。

---

## 14. Validation 副作用边界与 Sandbox

### 14.1 Validation 分类

#### A. Read-only Validation

可直接针对真实 Workspace：

```text
FILE_EXISTS
STRUCTURE_CHECK
JSON_STRUCTURE_CHECK
CAPABILITY_POSTCONDITION
```

要求不得写文件。

#### B. Side-effect Validation

必须在 Validation Sandbox：

```text
NPM_BUILD
NPM_TEST
MAVEN_TEST
MAVEN_PACKAGE
```

因为可能产生：

```text
dist/
coverage/
target/
surefire-reports/
generated-sources/
lockfile rewrite
cache files
测试脚本副作用
```

### 14.2 Validation Sandbox

流程：

```text
Post-Apply Real Workspace
        ↓
创建 Validation Sandbox / isolated working copy
        ↓
运行 build / test / package
        ↓
收集结构化结果和日志
        ↓
销毁 Sandbox
```

禁止直接在真实 Workspace 上运行有副作用 Validation。

Sandbox 实现可选择：

```text
临时目录 copy / clone
受控 workspace sandbox
其他与真实 Workspace 隔离的文件系统机制
```

实现方式可以演进，但协议上必须保证真实 Workspace 不被 Validation 产物污染。

### 14.3 Validation 失败

```text
Validation FAILED
→ 本次 Attempt FAILED
→ 正常进程内可 Failure Restore
→ TemplateState 不推进
→ Template Preparation FAILED
```

Retry 时重新从模板操作入口进入；若发现 unfinished Attempt，则先走 Recovery。

---

## 15. Validation Plan 与 RECONCILE

### 15.1 Validation Plan 是唯一验收输入

Capability 是否完整落在当前 Workspace 中，正式定义为 `validationPlan[]` 的 Capability Postcondition Validation 职责。

```text
CapabilityDefinition
        ↓ Service 编译
Strategy Package
        ├─ strategies[]
        ├─ validationPlan[]
        └─ nextTemplateState
        ↓
XCodeAgent
```

三者职责固定为：

```text
Strategy
= 应该如何把 Workspace 收敛到目标状态

Validation Plan
= 如何证明 Workspace 已经达到目标状态

TemplateState
= Capability 的逻辑状态
```

因此不再建设独立的 `Managed Workspace Health Check`、`Health Manifest` 或 `ManagedWorkspaceHealthChecker`。Service 不接收 Workspace Snapshot，也不根据当前代码生成 Repair ChangeSet。

### 15.2 APPLY 与 Capability 验收解耦

`mode=APPLY` 的职责只是处理 Desired State 变化。

```text
APPLY → 204
```

表示：

```text
Desired State 无变化
```

它不自动触发 RECONCILE，也不代表 Workspace 已通过 Capability Postcondition Validation。

### 15.3 `mode=RECONCILE`

需要主动检查 / 修复当前 effective Capability 时，由调用方使用：

```text
POST /v1/update mode=RECONCILE
```

Service：

```text
currentTemplateState.effective
        ↓
重新生成全部幂等维护 Strategy
        ↓
Strategy Package
```

XCodeAgent：

```text
Strategy Package
+
Current Workspace
        ↓
幂等执行
        ↓
Apply
        ↓
Validation Plan
        ├─ Capability Postcondition Validation
        └─ Build / Test Validation（Sandbox）
```

如果 Capability 对应结构被删除，幂等 Strategy 应重新补齐；如果当前业务代码与 Strategy 语义冲突，则 Fail Closed，不允许覆盖猜测。

RECONCILE 返回的 `validationPlan[]` 必须覆盖每个 `effective` Capability 的 Capability Postcondition Validation；缺少任一覆盖项的 Package 视为无效。

### 15.4 RECONCILE 的 State 语义

RECONCILE 的目的不是改变 Desired Capability，而是让 Workspace 重新满足已提交 Capability 的预期。因此 Package 必须满足：

```text
nextTemplateState.requested
== currentTemplateState.requested

nextTemplateState.effective
== currentTemplateState.effective
```

比较使用 Service Canonical Config 后的语义相等，不使用原始 JSON 的字段顺序或默认值表示比较。

只有 Validation Plan 全部成功后，本次 RECONCILE 才能标记成功。

---

## 16. Candidate nextTemplateState 与 Atomic Commit

Service 返回的：

```text
nextTemplateState
```

永远只是 candidate。

只有：

```text
Package Valid
+
Strategy Execute Success
+
Apply Success
+
Validation Success
```

才能：

```text
Atomic Commit TemplateState
```

写入推荐：

```text
write temp
→ flush
→ fsync
→ atomic rename
→ fsync directory
```

### 16.1 State Commit 与 Crash 判定

State Commit 前 Attempt：

```text
COMMITTING_STATE
```

Crash 后通过：

```text
current state digest
```

判定是：

```text
仍为 currentStateDigest
```

还是：

```text
已经 nextStateDigest
```

不再依赖“猜测 metadata 是否写了一半”。

---

## 17. Template Preparation 独立节点

### 17.1 定位

Template Preparation 是：

```text
现有模板下载 / 更新执行
的独立 UI / Task / Progress / Log / Retry 投影
```

不是：

```text
新的 TechnicalPlan 阶段
新的 Capability Planner
新的 Development Lifecycle
```

### 17.2 operationType

```text
DOWNLOAD
UPDATE
```

DOWNLOAD：

```text
复用既有 Bootstrap
```

UPDATE：

```text
复用本文 Current V2 Capability Reconcile Coordinator
```

### 17.3 UI 状态

推荐：

```text
PENDING
RUNNING
RECOVERING
SUCCEEDED
FAILED
```

UPDATE phase 可映射：

```text
PRECHECK
RECOVERING
MIGRATING_STATE
REQUESTING_UPDATE
COLLECTING_WORKSPACE_FACTS
DOWNLOADING_PACKAGE
VALIDATING_PACKAGE
APPLYING
VALIDATING
VALIDATING_POSTCONDITIONS
COMMITTING_STATE
```

DOWNLOAD phase 继续复用 Bootstrap 自身阶段。

### 17.4 Progress

优先显示真实进度：

```text
当前 phase
已完成 Strategy 数 / 总 Strategy 数
当前 Validation
下载字节数（可获得时）
开始时间
```

没有稳定总量时不得伪造百分比。

---

## 18. Template Preparation AG-UI 与持久化契约

### 18.1 不新建第二套 Thread / Run

Template Preparation 仍属于：

```text
现有 application
现有 revision
现有 workflow thread
```

它不创建独立 AG-UI 生命周期。

### 18.2 持久化事实与 UI 投影分层

```text
Durable ReconcileAttempt / Bootstrap Attempt
        ↓
Template Preparation Projection
        ↓
AG-UI CustomEvent + StateSnapshot
```

持久化事实源：

```text
.xcodeagent/runtime/...
```

AG-UI Event 不是恢复权威。

服务重启 / 前端重连后：

```text
读取 durable Attempt
→ 重新生成 Template Preparation Projection
→ StateSnapshot
```

### 18.3 State Snapshot 字段

建议在现有 workflow snapshot 中增加：

```json
{
  "templatePreparation": {
    "operationType": "UPDATE",
    "attemptId": "...",
    "retryOf": null,
    "status": "RUNNING",
    "phase": "APPLYING",
    "completedOperations": 3,
    "totalOperations": 8,
    "retryable": false,
    "errorCode": null,
    "errorMessage": null,
    "startedAt": "...",
    "updatedAt": "..."
  }
}
```

### 18.4 日志事件

复用现有 workflow process event 机制，Template Preparation 产生结构化 progress/log frame，例如：

```text
[INFO] REQUESTING_UPDATE       请求模板更新
[INFO] COLLECTING_WORKSPACE_FACTS 读取 Service 请求的 Workspace 事实
[INFO] APPLYING                3/8 ENSURE_ROUTE frontend/src/routes.tsx
[INFO] VALIDATING              MAVEN_TEST
[INFO] VALIDATING              CAPABILITY_POSTCONDITION login.route
```

日志只用于：

```text
展示
诊断
审计
```

不得作为 Recovery 所需唯一事实。

---

## 19. Template Preparation Retry

### 19.1 Retry 范围

失败后只重试：

```text
当前模板下载 / 更新操作
```

不得：

```text
重新生成 TechnicalPlan
重新确认 TechnicalPlan
重跑已经成功的上游节点
绕过 Development Gate
```

### 19.2 UPDATE Retry

```text
用户点击 Retry
        ↓
仍使用原 TechnicalPlan / revision 依赖
        ↓
创建新的 Template Preparation attempt / retryOf
        ↓
Enter Run Gate
        ↓
先检查 unfinished ReconcileAttempt
        ↓
需要时 Recovery
        ↓
重新进入 Reconcile 入口
```

如果前一次 crash 后存在 unfinished Attempt：

```text
优先恢复原 immutable Package
```

而不是直接生成另一份新 Package。

如果前一次已经明确 FAILED 且已完成进程内 Restore、无 unfinished Attempt：

```text
可重新调用 /v1/update
→ 新 Package / 新 ReconcileAttempt
```

### 19.3 不做 Operation 断点续跑

无论 Retry 还是 Recovery：

```text
都不从 operationIndex N+1 开始
```

Recovery 使用同一 immutable Package 从 Strategy 0 幂等重放；新的 Retry 则重新从 Reconcile 入口执行。

### 19.4 DOWNLOAD Retry

继续复用既有 Bootstrap Recovery / Retry 语义；Template Preparation 不重新实现第二套 Bootstrap 事务模型。

---

## 20. Current Workspace First

真正的代码更新始终是：

```text
Service Strategy
+
Current Workspace
        ↓
XCodeAgent Strategy Executor
        ↓
WorkingCopyStore
        ↓
Apply
```

不能退化成：

```text
Service 返回最终完整目标文件
→ 无条件覆盖业务文件
```

只有协议显式的 `ADD_FILE` 可以使用完整 payload，而且仍受冲突前置条件约束。

---

## 21. 最终可靠性模型

V2 最终依赖：

```text
Workspace 独占写
+
Reconcile Run Gate
+
单次 /v1/update Strategy 协议
+
Immutable Update Package
+
Durable Minimal Attempt
+
Current Workspace First
+
Deterministic Global Strategy Order
+
Idempotent Strategy
+
ADD_FILE retry-safe
+
WorkingCopyStore 进程内 Failure Restore
+
Validation Sandbox
+
Capability Postcondition Validation
（由 validationPlan[] 提供）
+
Atomic State Commit
+
Roll-forward Crash Recovery
+
Current V2 Contract Boundary
```

不依赖：

```text
全量 Workspace Transaction Snapshot
Persistent File before-image Journal
Git clean worktree
Git rollback
SHA 并发 Guard
复杂 Operation checkpoint
```

---

# 第二章 实施计划

实施顺序按“先冻结当前协议 → 再建立恢复边界 → 再实现 Executor → 最后接 UI / E2E”排列。

---

## XC-0：冻结 V2 End-to-End Protocol Contract

双端首先冻结本文第 4 章的唯一最小协议：

```text
POST /v1/update
Request:
  protocolVersion
  mode = APPLY | RECONCILE
  currentTemplateState
  requestedConfig

Response:
  204 NO_CHANGE
  200 Strategy Update Package
```

明确从 V2 Contract 删除：

```text
428 WORKSPACE_SNAPSHOT_REQUIRED
428 WORKSPACE_CONTEXT_REQUIRED
snapshotRequest
workspaceRequirements
DecisionWorkspaceSnapshot
WorkspaceSnapshot
workspaceContext
decisionFingerprint
planFingerprint
Service 侧最终 ChangeSet
```

冻结 Strategy Package：

```text
packageId
protocolVersion
mode
sourceRevision
currentStateDigest
nextStateDigest
strategies
validationPlan
payloadManifest
nextTemplateState
diagnostics
```

冻结 Strategy：

```text
strategyId
index
schemaVersion
type
target
precondition
payload / payloadRef
```

冻结：

```text
strategies[] = 唯一全局执行顺序
```

冻结 Package integrity：

```text
packageDigest
currentStateDigest
nextStateDigest
payload size / sha256
```

冻结 RECONCILE：

```text
requestedConfig 必须与 currentTemplateState.requested 语义一致
不一致 → 409 RECONCILE_REQUESTED_CONFIG_MISMATCH
nextTemplateState.requested / effective 必须分别与 currentTemplateState 相同
```

### 验收

```text
1. 双端只存在一份 /v1/update 权威 Schema。
2. mode 只允许 APPLY | RECONCILE。
3. Contract Test 明确拒绝 Workspace Snapshot / Context / 428 相关字段与响应。
4. Service Contract 只返回 Strategy Package，不返回最终 Workspace ChangeSet。
5. Contract Fixture 可由 Service 和 XCodeAgent 双端共同验证。
```

---

## XC-1：实现 Current TemplateState Reader / Writer

只支持当前 V2 TemplateState，至少包含：

```text
schemaVersion
templateRevision
requested
effective
appliedAdditions
```

其中每个 `appliedAdditions` 条目仅记录 `capabilityId`、`target`、`installedRevision`；
不得再写入没有消费方的 `origin` 字段。

Writer：

```text
write temp
→ fsync
→ atomic rename
→ fsync directory
```

### 验收

```text
1. 仅 schemaVersion=2 的 State 可读取。
2. 旧 State、旧 Attempt 或旧任务数据返回 TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED。
3. V2 State 原子写入，不产生半文件。
```

---

## XC-2：实现 `/v1/update` 单次 Client

请求：

```text
protocolVersion
currentTemplateState
requestedConfig
mode = APPLY | RECONCILE
```

响应只支持：

```text
204 NO_CHANGE
200 Strategy Update Package
```

Client 不上传：

```text
Workspace
Workspace Snapshot
Workspace Context
Workspace SHA
```

### 验收

```text
1. APPLY 无状态变化时正确处理 204。
2. RECONCILE 在 effective 非空时要求 Service 返回 Strategy Package。
3. RECONCILE 的 requestedConfig 与 currentTemplateState.requested 不一致时，正确呈现 `RECONCILE_REQUESTED_CONFIG_MISMATCH`，不得重试为 APPLY。
4. Client 对 428 Workspace Context / Snapshot 响应 fail closed，视为协议不兼容。
5. Client 不包含第二阶段请求代码路径。
```

---

## XC-3：实现 Strategy Update Package Transport Contract

新增 / 固化：

```text
StrategyUpdatePackage DTO
StrategyDescriptor DTO
ValidationPlan DTO
PayloadManifest DTO
```

要求：

```text
Service Package 不包含最终 UPDATE_FILE ChangeSet
Service Package 不包含 Workspace-derived final content
所有 Strategy 均由 XCodeAgent 在 Current Workspace 上执行
```

### 验收

```text
协议 Fixture 能证明：
- Package 只描述 Strategy 与 Validation
- Workspace 内容不会进入请求
- 同一 Package 可被 XCodeAgent 本地 Executor 独立执行
```

---

## XC-4：实现 Update Package Validator 与 Immutable Package Store

新增：

```text
UpdatePackageValidator
PackageDigestVerifier
PayloadManifestVerifier
ImmutablePackageStore
```

在任何真实 Workspace 写入前完成：

```text
protocolVersion
package digest
state digests
strategy schema
strategy id / index uniqueness
全局顺序合法性
payload digest
validation schema
nextTemplateState schema
```

持久化：

```text
attempts/{attemptId}/update-package.zip
```

### 验收

```text
Package 被篡改 → 不 Apply
payload 被篡改 → 不 Apply
重复 strategyId → 不 Apply
index 非法 → 不 Apply
未知 schemaVersion → 不 Apply
```

---

## XC-5：实现 Reconcile Run Gate 与 Coordinator

新增：

```text
ReconcileRunGate
ReconcileCoordinatorV2
```

顺序：

```text
enter gate
→ recovery check
→ update protocol
→ package prepare
→ strategy execute
→ apply
→ validation plan
→ state commit
→ exit gate
```

上层同时保证：

```text
Gate 生命周期内禁止其他 Workspace Writer。
```

不新增：

```text
WorkspaceTransaction
TransactionFileSnapshot
SHA Guard
```

---

## XC-6：实现 Durable ReconcileAttempt Store

新增：

```text
ReconcileAttemptV2
ReconcileAttemptStore
```

字段至少：

```text
attemptId
retryOf
protocolVersion
operationType
mode
technicalPlanSha256
packageId
packageDigest
currentStateDigest
nextStateDigest
phase
status
startedAt
updatedAt
errorCode
errorMessage
```

所有状态更新使用 atomic JSON write。

### 关键门禁

```text
Package durable
+
Attempt PREPARED durable
之后
才允许第一次真实 Workspace Apply
```

---

## XC-7：实现 V2 Roll-forward Recovery Coordinator

新增：

```text
ReconcileRecoveryCoordinatorV2
```

启动前处理 unfinished Attempt。

规则：

```text
stateDigest == currentStateDigest
→ reload frozen package
→ 从 Strategy 0 重放

stateDigest == nextStateDigest
→ 重新执行 immutable Package 的 Validation Plan
→ PASSED 则 finalize SUCCEEDED

otherwise
→ RECOVERY_STATE_CONFLICT
```

不依赖：

```text
Git HEAD
File Journal
operation checkpoint
```

### 故障注入

至少覆盖：

```text
Apply 1/5 后 crash
Apply 4/5 后 crash
Validation 前 crash
Validation 后、Commit 前 crash
State rename 后、Attempt 成功前 crash
```

---

## XC-8：实现 WorkingCopyStore

新增：

```text
WorkingCopyStore
WorkingCopyEntry
```

结构：

```text
path
originalExists
originalContent
workingContent
changed
applied
```

要求：

```text
同 path 初始内容只读取一次
所有 Strategy 使用同一 working copy
同 path 最多一次最终写盘
```

不再保留 `beforeSha`。

---

## XC-9：实现 Strategy Executor Framework

新增：

```text
ModificationStrategyExecutor
StrategyHandler
StrategyExecutionContext
StrategyExecutionResult
```

硬约束：

```text
1. 严格按 Package strategies[] 顺序。
2. Handler 不直接写磁盘。
3. Handler 不修改 TemplateState。
4. Handler 不重排 Strategy。
5. Handler 必须幂等。
```

先实现：

```text
ADD_FILE
TEXT_ANCHOR_INSERT
ENSURE_IMPORT
```

再扩展结构化 Strategy。

---

## XC-10：实现前端结构化 Strategy

至少：

```text
ENSURE_IMPORT
ENSURE_REACT_PROVIDER
ENSURE_ROUTE
ENSURE_MENU_ITEM
ENSURE_NPM_DEPENDENCY
```

优先：

```text
AST / parser
```

验收：

```text
业务代码已经被用户修改
→ Strategy 仍可定位
→ 不覆盖业务逻辑
→ 重放无重复结构
```

---

## XC-11：实现后端结构化 Strategy

至少：

```text
ENSURE_MAVEN_DEPENDENCY
ENSURE_SPRING_BEAN
ENSURE_INTERCEPTOR
```

优先：

```text
Java Parser / AST
```

共享文件：

```text
pom.xml
Spring Configuration
Interceptor Registry
```

必须通过 WorkingCopyStore。

---

## XC-12：实现 ADD_FILE Retry-safe Precondition

规则：

```text
不存在 → CREATE
存在且内容一致 → NO_CHANGE
存在且内容不同 → ADDITION_TARGET_CONFLICT
```

验收：

```text
首次 ADD
重复 ADD
Roll-forward 重放 ADD
已有相同文件
已有冲突文件
```

---

## XC-13：实现 Apply 与进程内 Failure Restore

新增：

```text
WorkingCopyApplier
FailureRestorer
```

Apply 采用确定性顺序与原子文件替换。

Failure Restore：

```text
originalExists=true  → restore originalContent
originalExists=false → delete created file
```

明确：

```text
FailureRestorer 只处理进程内正常异常。
Crash 后不依赖它。
```

---

## XC-14：实现 Validation Executor 与 Validation Sandbox

支持：

```text
FILE_EXISTS
STRUCTURE_CHECK
JSON_STRUCTURE_CHECK
CAPABILITY_POSTCONDITION
NPM_BUILD
NPM_TEST
MAVEN_TEST
MAVEN_PACKAGE
```

分类执行：

```text
Read-only → Real Workspace
Side-effect → Validation Sandbox
```

Sandbox 结束后必须销毁。

输出结构化：

```text
ValidationResult
stdoutLogRef
stderrLogRef
exitCode
durationMs
errorCode
```

### 验收

```text
NPM_BUILD 产生 dist 不进入真实 Workspace
MAVEN_TEST 产生 target 不进入真实 Workspace
测试脚本写临时文件不污染真实 Workspace
```

---

## XC-15：实现 Capability Postcondition Validation 与 RECONCILE

新增 / 演进：

```text
CapabilityPostconditionValidator
ReconcileValidationCoordinator
```

规则：

```text
APPLY：处理 Desired State 变化，204 只表示状态无变化
RECONCILE：Service 基于 effective 返回全部幂等维护 Strategy
RECONCILE Package Apply → Validation Plan
Recovery state==nextDigest → 重新执行 immutable Package 的 Validation Plan
```

Validation Plan 不通过时：

```text
本次 RECONCILE FAILED / RECOVERY_REQUIRED
```

禁止：

```text
再发 HEALTH_RECONCILE mode
上传 Workspace Snapshot 给 Service
让 Service 根据 Workspace 生成 Repair ChangeSet
```

### 验收

覆盖：

```text
State 显示已安装但 managed 结构被删
幂等 Strategy 能恢复缺失结构
已有正确结构全部 NO_CHANGE
业务代码与 Strategy 语义冲突时 fail closed
Capability Postcondition Validation 不通过时不得成功
```

---

## XC-16：实现 Atomic TemplateState Commit 与 Commit Recovery 判定

只有：

```text
Strategy
Apply
Validation Plan
```

全部成功后提交 State。

实现：

```text
temp
fsync
atomic rename
fsync directory
```

Commit 前：

```text
Attempt phase=COMMITTING_STATE
```

Crash 后按 digest 判定 current / next，不再返回模糊的“metadata 可能提交一半”作为唯一恢复方式。

---

## XC-17：实现 Template Preparation AG-UI Projection

不新建第二套 Thread / Run endpoint。

复用现有 Workflow AG-UI：

```text
CustomEvent
StateSnapshot
process event
```

新增投影：

```text
templatePreparation
```

字段至少：

```text
operationType
attemptId
retryOf
status
phase
completedOperations
totalOperations
retryable
errorCode
errorMessage
startedAt
updatedAt
```

Template Preparation 状态由 durable Bootstrap / Reconcile Attempt 投影生成。

### 验收

```text
刷新页面后仍能读取当前模板进度
Backend 重启后 StateSnapshot 可从 durable Attempt 恢复
历史日志可查询
实时日志可订阅
```

---

## XC-18：实现 Template Preparation 独立 Retry

扩展现有 workflow action，例如：

```text
retry_template_preparation
```

Retry：

```text
不重跑 TechnicalPlan
不重新确认 TechnicalPlan
不改变 Development Gate
```

UPDATE：

```text
有 unfinished Attempt → Recovery
无 unfinished Attempt → 新 Reconcile Attempt
```

DOWNLOAD：

```text
复用既有 Bootstrap Retry
```

每次用户级 Retry 生成新的 Template Preparation attempt，并通过 `retryOf` 关联。

---

## XC-19：login Capability E2E

场景：

```text
已有业务代码 Workspace
→ TechnicalPlan 按现有逻辑要求 login
→ Template Preparation UPDATE
→ /v1/update 单次 Strategy 协议
→ Strategy Apply
→ Validation Sandbox
→ State Commit
→ 原 Development Gate
```

重复执行：

```text
无重复 import
无重复 provider
无重复 route
ADD_FILE 同内容 NO_CHANGE
RECONCILE 下通过 Strategy + Capability Postcondition Validation 确认完整落地
```

---

## XC-20：authorization Capability E2E

覆盖：

```text
login
→ authorization
```

共享：

```text
App.tsx
routes
menu
package.json
pom.xml
Spring Configuration
```

确认：

```text
全局 Strategy 顺序确定
同路径 WorkingCopy 不覆盖
重复执行幂等
Build/Test 不污染真实 Workspace
Validation Plan 成功后才推进 State
```

---

## XC-21：Crash / Failure / Retry 注入

必须覆盖：

```text
Package 下载一半失败
Package digest 失败
Strategy 内存执行失败
Apply 第 1 个文件后 crash
Apply 多文件中途 crash
ADD 已存在相同内容
ADD 已存在冲突内容
Validation Sandbox 创建失败
NPM_BUILD 失败
MAVEN_TEST 失败
Capability Postcondition Validation 失败
RECONCILE Capability Postcondition Validation 成功
RECONCILE Capability Postcondition Validation 再失败
State Commit 前 crash
State atomic rename 后 crash
Attempt finalize 前 crash
旧 State / 旧 Attempt / 旧任务数据被拒绝
重复点击 Retry
```

确认：

```text
1. 不产生两个并发 Template Writer。
2. crash 后能根据 Attempt + State digest 得到唯一恢复路径。
3. 未完成模板操作不能进入开发阶段。
4. Retry 不重跑 TechnicalPlan。
5. 不依赖 Git rollback 或 Persistent File Journal。
```

---

## XC-22：最终架构门禁

### XCodeAgent 生产代码必须保留

```text
StrategyReconcileExecutorV2
ReconcileRunGate
Single-call /v1/update Client
StrategyUpdatePackage Contract
UpdatePackageValidator
ImmutablePackageStore
Durable ReconcileAttempt
Roll-forward Recovery
WorkingCopyStore
ModificationStrategyExecutor
Retry-safe ADD_FILE
WorkingCopyApplier
In-process FailureRestorer
Validation Sandbox
Capability Postcondition Validation
RECONCILE Validation
Atomic TemplateState Commit
Template Preparation AG-UI Projection
Template Preparation Retry
```

### V2 不得继续依赖

```text
Git clean worktree
Git HEAD baseline
Git rollback
Workspace SHA concurrent guard
Transaction Workspace Snapshot
TransactionFileSnapshot
Persistent File before-image Journal
Journal Context
Operation-level resume checkpoint
```

### 产品流程门禁保持不变

必须保证：

```text
Template Preparation 只拆进度 / 日志 / Retry
不改变 TechnicalPlan → Template 的现有依赖
不改变 Template → Development 的现有门禁
不新增第二套 Template Ready / Can Enter Development 判断
```

---

# 第三章 实施阶段建议

虽然第二章已经按 XC 依赖顺序展开，为降低切换风险，实际落地建议按以下阶段组合执行。

## 阶段一：冻结当前协议边界

```text
XC-0
XC-1
XC-2
XC-3
XC-4
```

阶段目标：

> Service 与 XCodeAgent 的当前 V2 Contract 唯一；旧协议、旧运行态与旧 Workspace 明确不受支持。

## 阶段二：建立 V2 可恢复执行内核

```text
XC-5
XC-6
XC-7
XC-8
XC-9
XC-12
XC-13
XC-16
```

阶段目标：

> 在测试 Fixture 中完成“Package durable → Apply → crash → Roll-forward → State Commit”的闭环。

## 阶段三：补齐结构化 Strategy、Validation 与 Capability Postcondition

```text
XC-10
XC-11
XC-14
XC-15
```

阶段目标：

> V2 能在真实业务代码形态上完成安全修改，Build/Test 不污染 Workspace，RECONCILE 能通过幂等 Strategy + Capability Postcondition Validation 发现并修复模板漂移。

## 阶段四：接入产品体验

```text
XC-17
XC-18
```

阶段目标：

> Template Preparation 独立展示进度、日志和 Retry；产品依赖逻辑不改变。

## 阶段五：E2E

```text
XC-19
XC-20
XC-21
XC-22
```

阶段目标：

> login / authorization 与故障注入全部通过后，当前 V2 Contract 才可作为唯一生产实现。

---

# 第三章补充：实施遗漏修复计划（验收阻塞项）

本节基于实施回检新增。以下事项不是可选优化；在完成前，不得宣称当前 V2 Contract 已成为唯一生产实现，也不得进行最终验收。

## XR-1：统一 Bootstrap、Reconcile 与 Build 的 V2 TemplateState 事实源

当前必须形成：

```text
/v1/generate
→ V2 TemplateState
→ Bootstrap 原子落盘
→ V2 Reconcile
→ Build / Projection / Readiness 只读取 V2 TemplateState
```

要求：

```text
1. Bootstrap Package 只接受并落盘 TemplateStateV2。
2. Build、route projection、authorization、readiness 等全部迁移到 V2 requested/effective/appliedAdditions。
3. 旧 managedFiles State、旧 Attempt、旧 ChangeSet 只返回 TEMPLATE_RECONCILE_PROTOCOL_UNSUPPORTED；旧 Workspace 必须重新初始化。
4. 所有生产调用方完成迁移后，删除旧 template_state、health、applier、runtime_state、ChangeSet DTO/validator 的生产路径。
```

验收：

```text
rg 证明生产代码不再导入旧 State / Health / ChangeSet / Git rollback Reconcile 实现。
新 Bootstrap Workspace 可直接进入 V2 APPLY、RECONCILE 与原 Development Gate。
```

## XR-2：补齐 Package、Attempt 与 TemplateState 的完整性绑定

冻结唯一 canonical JSON 与 `stateDigest()`：

```text
sha256(zip bytes)
== Attempt.packageDigest

stateDigest(currentTemplateState)
== Package.currentStateDigest

stateDigest(nextTemplateState)
== Package.nextStateDigest
```

要求：

```text
1. Package Validator 校验 nextStateDigest 与 nextTemplateState 的实际 canonical digest。
2. Recovery 重读 immutable ZIP 时先校验 ZIP bytes digest，再解析 Package。
3. Apply 前再次校验 currentStateDigest；不得接受绑定旧 State 的 Package。
4. Attempt 持久化 protocolVersion、packageId、sourceRevision、packageDigest、mode、技术规划摘要、current/next digest；Recovery 全部复核。
5. Recovery 的固定判定顺序为：ZIP bytes digest → 解析 Package → Attempt/Package 全字段比对 → Current State digest → 调用参数与当前 TechnicalPlan SHA-256 → `recovery_action()`；任一步失败都不得执行 Strategy 或 Roll-forward。
```

## XR-3：修正 State Commit 临界区与 Roll-forward 语义

严格阶段边界：

```text
PREPARED / APPLYING / VALIDATING
→ 进程内正常异常可以 restore WorkingCopy

COMMITTING_STATE 之后
→ 禁止 restore Workspace
→ 只能 Roll-forward
```

要求：

```text
1. State atomic rename 成功后，即使 Attempt finalize 失败，也不得恢复 Workspace 文件。
2. stateDigest==current：从 immutable Package 的 Strategy 0 重放。
3. stateDigest==next：重新执行 immutable Validation Plan；成功仅 finalize Attempt。
4. 其他 digest：RECOVERY_STATE_CONFLICT，并阻断后续开发。
5. 必须提供 Apply 第 N 文件、State rename 后、Attempt finalize 前的可控 fault injection。
```

## XR-4：收紧结构化 Strategy 与 Validation Sandbox

策略分层：

```text
受管理局部代码块
→ managed marker + 唯一 anchor

语言结构（Import / Route / Provider / Spring）
→ AST selector + 明确插入位置 + AST postcondition

配置结构（package.json / pom.xml）
→ JSON/XML 语义编辑 + 冲突检测 + 稳定格式
```

要求：

```text
1. 不得以 marker/文本锚点猜测业务结构位置；找不到唯一 AST 目标、目标类型不符或语义冲突时 fail closed。
2. Maven XML 修改保持 namespace、注释和稳定格式，不得无差别重序列化业务文件。
3. Validation Plan 使用显式 DTO，不以无限制 parameters 承载任意断言或命令。
4. Sandbox 定义确定性依赖准备（独立缓存的 frozen/offline install 或不可变依赖层），不得借用真实 Workspace node_modules。
5. Validation Result 只返回 stdoutLogRef/stderrLogRef、exitCode、durationMs、errorCode；原始输出落 Attempt 私有日志目录。
```

## XR-5：补齐 RECONCILE、Template Preparation 与 Retry 产品闭环

要求：

```text
1. 提供唯一的受控 RECONCILE 触发点；请求与 TemplateState.requested 不一致时返回 RECONCILE_REQUESTED_CONFIG_MISMATCH。
2. RECONCILE 前后逐字段验证 requested/effective/config 不变；成功只能由 Strategy → Apply → Validation Plan 证明。
3. 现有 Workflow AG-UI 在 Attempt 的 phase、Validation、错误变化时实时投影 templatePreparation，而不只在节点结束时投影。
4. retry_template_preparation 复用当前 Workflow thread：unfinished Attempt 先 Recovery；失败且不可恢复时创建新 Attempt，并写 retryOf=旧 attemptId。
5. Retry 不重跑、不重新确认 TechnicalPlan，也不改变 Template → Development Gate。
6. Bootstrap DOWNLOAD 与 Reconcile UPDATE 可以共享 UI 表达，但后端执行边界与 durable Attempt 必须明确区分。
```

## XR-6：扩展 E2E、故障注入与最终架构门禁

必须覆盖：

```text
新 Bootstrap 直接产生 V2 State
login → authorization 的真实共享文件收敛
APPLY 重放、RECONCILE 漂移修复、RECONCILE 后置条件失败
下载中断 / ZIP digest 失败 / Strategy 内存失败 / Apply 多文件中断
NPM_BUILD / MAVEN_TEST Sandbox 失败且真实 Workspace 无污染
State Commit 前、atomic rename 后、Attempt finalize 前 crash
并发 Retry 与 Run Gate
旧 State / 旧 Attempt / 旧任务数据明确拒绝
```

最终静态门禁：

```text
生产代码不得继续引用旧 ChangeSet、旧 State consumer、Git rollback、独立 Health 阶段。
```

## 修复执行顺序

```text
XR-1
→ XR-2
→ XR-3
→ XR-4
→ XR-5
→ XR-6
```

其中 XR-1、XR-2、XR-3 是阻塞项；未完成前不得扩大 UI 或 E2E 范围，也不得进入最终验收。

---

# 第四章 最终验收准则

只有以下条件同时成立，才视为本次 Capability Reconcile 重构完成：

```text
协议：
- /v1/update 双端统一为单次 204 / 200 Strategy 协议
- mode 只允许 APPLY | RECONCILE
- RECONCILE 仅接受与 currentTemplateState.requested 语义一致的 requestedConfig，不自动转为 APPLY
- 请求不包含 Workspace Snapshot / Context
- Service 不返回最终 Workspace ChangeSet
- Strategy / Validation / Package Schema 已冻结并双端验证

一致性：
- Crash 后存在唯一 Roll-forward Recovery 路径
- State Commit 临界区可通过 digest 判定
- 未完成 Attempt 阻断后续开发

Capability 验收：
- RECONCILE 下通过 Strategy + Capability Postcondition Validation 确认完整落地
- State 与 Workspace 漂移通过 RECONCILE 的幂等 Strategy + Capability Postcondition Validation 发现和收敛
- RECONCILE 的 Validation Plan 失败时 Fail Closed，不新增独立 Health 阶段或 Health mode
- RECONCILE 不改变 nextTemplateState.requested / effective 或 Capability Config

Validation：
- Build/Test 类检查不直接污染真实 Workspace
- Validation 结果结构化并可供 UI / Retry / 诊断使用

当前合约边界：
- 仅支持 V2 TemplateState、Strategy Package 与 ReconcileAttempt
- 旧接口、旧运行态和旧 Workspace 返回明确的不支持错误，必须重新初始化

产品：
- Template Preparation 独立显示下载/更新进度与日志
- 模板操作可独立 Retry
- TechnicalPlan 与模板的原依赖不变
- 是否进入开发阶段的原判断逻辑不变

架构：
- V2 不依赖 Git clean / Git rollback / Persistent File Journal / Transaction Snapshot
- XCodeAgent 仍是 Workspace 唯一代码执行事实源
- Template Service 仍是 Capability / Strategy 规划权威
```
