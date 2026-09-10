# XCodeAgent Template Capability Reconcile 方案设计与实施计划

> 适用对象：XCodeAgent  
> 核心职责：消费 Template Service 返回的 Modification Strategy，在当前 Workspace 上执行幂等增量修改，并负责事务、回滚、Validation 与 TemplateState 最终提交。  
> 明确边界：XCodeAgent 是 **Workspace Runtime 与唯一代码事实执行方**。

---

# 第一章 方案设计

## 1. 目标与核心原则

XCodeAgent 的职责固定为：

```text
Template Service
返回 Modification Strategy
        ↓
XCodeAgent Acquire Workspace Lock
        ↓
读取当前 Workspace
        ↓
在当前最新代码上执行 Strategy
        ↓
Journal
        ↓
Validation
        ↓
Commit nextTemplateState
```

核心原则：

> 每次 Capability Reconcile 均以事务开始时的当前 Workspace 为代码事实源，在当前最新代码上执行幂等的 ADD / UPDATE 增量变换。XCodeAgent 不要求 Workspace 与任何历史模板版本一致，也不维护历史 managed-file 基线。

---

## 2. XCodeAgent 职责边界

XCodeAgent 负责：

```text
Workspace Write Lock
调用 Template Service /v1/update
解析 Update Package
读取当前 Workspace 文件
执行 Modification Strategy
AST / parser / text transformation
检查 ADD 前置条件
同一路径 Strategy 串行
Workspace Snapshot（本地事务内部）
文件 SHA（本地事务内部）
File Journal
Apply
Rollback
Validation Plan 执行
TemplateState Atomic Commit
```

XCodeAgent 不负责：

```text
Capability Dependency Resolution
Capability Config Canonicalization
判断 ENABLE / CONFIG_CHANGE / RELEASE_REFRESH
Addition CREATE / MAINTAIN 分类
Capability Metadata 复制
生成 Modification Strategy
生成 candidate nextTemplateState
```

这些由 Template Service 负责。

---

## 3. `/v1/update` 调用模型

XCodeAgent 调用：

```text
POST /v1/update
```

请求只包含：

```text
currentTemplateState
requestedConfig
mode
```

不包含：

```text
Workspace Snapshot
Workspace Content
Workspace SHA
```

如果返回：

```http
204 No Content
```

表示：

```text
NO_CHANGE
```

XCodeAgent直接结束。

如果返回：

```text
Update Package
```

则进入本地执行事务。

---

## 4. Workspace Write Transaction

正确顺序：

```text
Acquire Workspace Write Lock
        ↓
Read current TemplateState
        ↓
POST /v1/update
        ↓
204?
  YES → Unlock

  NO
        ↓
Receive Strategy Package
        ↓
Create Transaction Snapshot / Journal Context
        ↓
Execute Strategies
        ↓
Validation
        ↓
Commit nextTemplateState
        ↓
Unlock
```

整个：

```text
/update
→ Strategy Execute
→ Validation
→ State Commit
```

都必须处于同一个 Workspace Write Lock 中。

---

## 5. Workspace Snapshot 的真实定位

Workspace Snapshot 只是：

```text
XCodeAgent 本地事务内部结构
```

它不发送给 Service。

用途：

```text
记录 Strategy 执行前当前文件状态
生成 Journal
检测外部并发修改
支持 rollback
```

例如：

```text
App.tsx
content=A
sha256=X
```

只存在于 XCodeAgent Transaction Context。

---

## 6. Modification Strategy Executor

XCodeAgent 提供统一：

```text
ModificationStrategyExecutor
```

根据 Strategy Type 分发：

```text
ADD_FILE
ENSURE_IMPORT
ENSURE_NPM_DEPENDENCY
ENSURE_MAVEN_DEPENDENCY
ENSURE_REACT_PROVIDER
ENSURE_ROUTE
ENSURE_MENU_ITEM
ENSURE_SPRING_BEAN
ENSURE_INTERCEPTOR
TEXT_ANCHOR_INSERT
```

推荐每种 Strategy 有独立 Handler：

```text
StrategyHandler<T>
```

例如：

```text
EnsureImportHandler
EnsureReactProviderHandler
EnsureRouteHandler
EnsureMavenDependencyHandler
```

---

## 7. Working Copy

同一路径的多个 Strategy 必须作用在同一 Working Copy 上：

```text
Current Workspace File A
        ↓
Strategy 1
        ↓
Working Copy B
        ↓
Strategy 2
        ↓
Working Copy C
        ↓
最终一次写盘
```

禁止：

```text
每个 Strategy 各自重新读取磁盘
每个 Strategy 各自直接覆盖文件
```

否则共享文件容易互相覆盖。

---

## 8. Strategy 幂等

每个 Strategy Handler 必须满足：

```text
S(S(code)) = S(code)
```

例如：

```text
ENSURE_IMPORT
```

如果 import 已存在：

```text
NO_CHANGE
```

不能重复插入。

同理：

```text
ENSURE_ROUTE
ENSURE_REACT_PROVIDER
ENSURE_MAVEN_DEPENDENCY
```

都必须幂等。

---

## 9. ADD_FILE

Service 返回：

```json
{
  "type": "ADD_FILE",
  "additionId": "login.login-page",
  "target": "frontend/src/pages/Login/index.tsx",
  "sourceRef": "payload/login/login-page.tsx",
  "precondition": "TARGET_MUST_NOT_EXIST"
}
```

XCodeAgent执行：

```text
target 不存在
→ 可以 ADD

target 已存在
→ ADDITION_TARGET_CONFLICT
```

不能：

```text
覆盖已有文件
```

---

## 10. TRANSFORM_FILE

Service 返回：

```text
target
+
Strategy Descriptor
```

XCodeAgent：

```text
读取当前文件
→ parser / AST / structured edit
→ 应用 Strategy
→ Working Copy
```

如果：

```text
目标文件不存在
```

返回：

```text
TRANSFORM_TARGET_NOT_FOUND
```

如果：

```text
结构无法唯一识别
```

返回：

```text
TRANSFORM_TARGET_AMBIGUOUS
```

如果：

```text
现有业务代码与 Strategy 语义冲突
```

返回：

```text
TRANSFORM_CONFLICT
```

禁止 fallback：

```text
用模板完整文件覆盖
```

---

## 11. 本地 SHA 与并发安全

虽然 Service 不知道 SHA，但 XCodeAgent 本地仍建议记录：

```text
beforeSha
```

用途：

```text
防止 IDE
人工编辑
外部进程
```

在事务中途修改文件。

流程：

```text
读取文件 A
→ SHA(A)=X
→ Working Copy 计算 B
→ 真正写入前再次读取磁盘
→ SHA(current)==X ?
```

相等：

```text
写 B
```

不相等：

```text
WORKSPACE_CHANGED_DURING_UPDATE
```

整个事务失败。

这不是历史一致性检查，只是：

```text
本次事务的乐观并发保护
```

---

## 12. File Journal

每次事务建立 Journal。

UPDATE：

```text
path
old bytes
old metadata（必要时）
```

ADD：

```text
path
created=true
```

若 Strategy、Apply 或 Validation 失败：

```text
逆序 rollback
```

规则：

```text
TemplateState 在 rollback 成功前绝不推进
```

---

## 13. Validation Plan

Template Service 返回：

```text
validation-plan.json
```

XCodeAgent 执行。

可能包括：

```text
FILE_EXISTS
STRUCTURE_CHECK
NPM_BUILD
NPM_TEST
MAVEN_TEST
MAVEN_PACKAGE
```

执行时机：

```text
所有文件变更 Apply 后
TemplateState Commit 前
```

Validation失败：

```text
Rollback
保留旧 TemplateState
```

---

## 14. Candidate nextTemplateState

Service 返回：

```text
next-template-state.json
```

它只是：

```text
candidate
```

XCodeAgent不能收到后立即落盘。

只有：

```text
Strategy 全部成功
+
Apply 成功
+
Validation 成功
```

之后才能：

```text
Atomic Commit TemplateState
```

---

## 15. TemplateState Atomic Commit

推荐：

```text
write temp file
→ fsync
→ atomic rename
```

保证：

```text
不会出现半写 State
```

如果 Workspace 修改成功但 State 写失败：

```text
必须 rollback Workspace
```

保持：

```text
Workspace
与
TemplateState
```

事务一致。

---

## 16. `/v1/update/plan`

当前 XCodeAgent 不依赖：

```text
/v1/update/plan
```

未来如果产品增加：

```text
“查看本次会改什么”
```

可以调用 `/v1/update/plan` 做 Preview。

但真正更新仍然：

```text
重新调用 /v1/update
```

不允许：

```text
plan → 直接本地执行
```

因为 `/v1/update` 才是权威执行策略来源。

---

## 17. Current Workspace First

整个执行模型必须始终保持：

```text
Service Strategy
+
Current Workspace
        ↓
XCodeAgent Strategy Executor
```

不能退化成：

```text
Service 返回最终完整目标文件
→ XCodeAgent 覆盖 Workspace
```

除非 Operation 本身明确是：

```text
ADD_FILE
```

且目标路径必须原本不存在。

---

# 第二章 实施计划

## XC-0：冻结 Strategy Execution Contract

与 Service 对齐：

```text
Modification Strategy Schema
Validation Plan Schema
Update Package Schema
Error Codes
```

增加 Contract Fixture。

---

## XC-1：实现 TemplateState V2 Reader / Writer

支持：

```text
schemaVersion
templateRevision
releaseDigest
requested
effective
appliedAdditions
```

不兼容旧 State。

旧 State：

```text
TEMPLATE_STATE_SCHEMA_UNSUPPORTED
```

---

## XC-2：实现 `/v1/update` Client

请求：

```text
currentTemplateState
requestedConfig
mode
```

响应：

```text
204
→ NO_CHANGE

200 ZIP
→进入本地 Reconcile
```

Client 不上传 Workspace。

---

## XC-3：实现 WorkspaceTransaction

新增：

```text
WorkspaceWriteLock
WorkspaceTransaction
TransactionFileSnapshot
FileJournal
```

事务边界：

```text
lock
→ update
→ execute
→ validate
→ state commit
→ unlock
```

---

## XC-4：实现 WorkingCopyStore

职责：

```text
按 path 延迟读取当前文件
缓存本次事务 Working Copy
同一路径多个 Strategy 串行修改
最终统一 Apply
```

要求：

```text
同一个 path 只做一次最终写入
```

---

## XC-5：实现 Strategy Executor Framework

新增：

```text
ModificationStrategyExecutor
StrategyHandler
StrategyExecutionContext
StrategyExecutionResult
```

先实现基础类型：

```text
ADD_FILE
TEXT_ANCHOR_INSERT
ENSURE_IMPORT
```

再逐步增加结构化 Handler。

---

## XC-6：实现前端 Strategy

至少：

```text
ENSURE_IMPORT
ENSURE_REACT_PROVIDER
ENSURE_ROUTE
ENSURE_MENU_ITEM
ENSURE_NPM_DEPENDENCY
```

推荐使用：

```text
AST / parser
```

优先于纯文本替换。

验收：

```text
业务代码已被修改
仍能正确插入

重复执行
无重复结构
```

---

## XC-7：实现后端 Strategy

至少：

```text
ENSURE_MAVEN_DEPENDENCY
ENSURE_SPRING_BEAN
ENSURE_INTERCEPTOR
```

Java 修改优先：

```text
Java Parser / AST
```

必要时使用明确 Anchor Strategy。

---

## XC-8：实现 ADD_FILE Precondition

执行：

```text
TARGET_MUST_NOT_EXIST
```

发现目标已存在：

```text
ADDITION_TARGET_CONFLICT
```

不得覆盖。

---

## XC-9：实现本地 Transaction Snapshot + SHA Guard

首次读取文件时记录：

```text
before bytes
before sha256
```

最终写入前重新比较。

变化：

```text
WORKSPACE_CHANGED_DURING_UPDATE
```

---

## XC-10：实现 File Journal / Rollback

覆盖：

```text
ADD rollback
UPDATE rollback
多文件逆序 rollback
rollback 失败
```

rollback 失败：

```text
WORKSPACE_ROLLBACK_FAILED
```

并输出明确诊断。

---

## XC-11：实现 Validation Plan Executor

支持：

```text
FILE_EXISTS
STRUCTURE_CHECK
NPM_BUILD
NPM_TEST
MAVEN_TEST
MAVEN_PACKAGE
```

失败：

```text
rollback
不提交 State
```

---

## XC-12：实现 Atomic State Commit

只有事务全部成功后：

```text
write temp
fsync
atomic rename
```

State commit 失败：

```text
rollback Workspace
```

---

## XC-13：login E2E

场景：

```text
当前 Workspace 已有业务代码
→ Service 返回 login Strategy
→ XCodeAgent执行
→ 原业务代码保留
→ login 生效
→ Validation 成功
→ State 提交
```

重复 RECONCILE：

```text
无重复 import
无重复 provider
无重复 route
```

---

## XC-14：authorization E2E

覆盖：

```text
login
→ authorization
```

共享文件：

```text
App.tsx
routes
menu
package.json
pom.xml
```

必须通过 Working Copy 串行处理。

---

## XC-15：并发与失败注入

至少：

```text
Strategy 中途失败
外部修改文件
ADD target 已存在
Validation 失败
State commit 失败
Rollback 失败
```

确认：

```text
失败不推进 TemplateState
```

---

## XC-16：最终架构门禁

生产代码中不得出现：

```text
Capability Dependency Resolver
Capability Config Canonicalizer
Addition CREATE / MAINTAIN Planner
Release Refresh Planner
Capability Metadata Loader
```

这些属于 Service。

同时 XCodeAgent 必须保留：

```text
Workspace Runtime
Strategy Executor
Transaction Snapshot
SHA Guard
Journal
Rollback
Validation
State Commit
```

最终数据流固定为：

```text
Template Service
Modification Strategy
        ↓
XCodeAgent
+
Current Workspace
        ↓
Working Copy
        ↓
Apply
        ↓
Validation
        ↓
TemplateState Commit
```
