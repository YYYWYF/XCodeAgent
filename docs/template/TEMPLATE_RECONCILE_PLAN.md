# XCodeAgent Template Capability Reconcile 实施方案

> 本文只描述**已有 Workspace 在正式 TechnicalPlan Revision 后的模板能力增量收敛**。  
> 模板体系公共架构见 [`TEMPLATE_REFACTOR.md`](./TEMPLATE_REFACTOR.md)；  
> 首次模板初始化见 [`BOOTSTRAP_PLAN.md`](./BOOTSTRAP_PLAN.md)。

---

# 第一章 方案设计

## 1.1 目标

应用首次 Bootstrap 完成后，后续需求变化可能新增或移除固定技术能力，例如：

```text
authorization
tracking
audit
redis
file-storage
```

此时不得：

```text
重新下载完整模板
重新初始化 Git
重新执行 Bootstrap
让 Agent 自己补模板固定代码
```

正确模式：

```text
New TechnicalPlan Confirmed
        ↓
Compile New RequestedConfig
        ↓
Load Current TemplateState
        ↓
POST /v1/update
        ↓
204 NO_CHANGE
    or
Update Package
        ↓
Safe Workspace Reconcile
        ↓
Persist Next TemplateState
        ↓
Deterministic Skeleton
        ↓
issue_revision_continuation
        ↓
inspect_workspace
        ↓
prepare_build_tasks
        ↓
Agent Build
```

核心原则：

> XCodeAgent 不负责计算 Capability Diff；XCodeAgent 负责将 Template Engine 返回的 ChangeSet 安全应用到真实 Workspace。

---

## 1.2 非目标

V1 Reconcile 明确不做：

- 再次调用 `/v1/generate`；
- 重新初始化 Workspace；
- 重新 `git init`；
- Template Revision Upgrade；
- 任意三方 Merge；
- Agent 自动解决模板冲突；
- XCodeAgent 自己解析 Capability dependency；
- XCodeAgent 自己识别“缺哪些模板文件”；
- 通过 Reconcile 生成业务 Page/Service；
- 用 Reconcile 代替 Platform Projection。

---

## 1.3 触发条件

Reconcile 只发生在：

```text
Workspace 已 Bootstrap
TemplateState 存在
TechnicalPlan 正式 Revision 已确认
```

目标入口：

```text
Confirmed TechnicalPlan Revision
        ↓
Template Reconcile Gate
```

它不重新进入：

```text
GENERATING_APPLICATION_TEMPLATE_FILES
```

因为该 lifecycle 只属于首次 Bootstrap。

---

## 1.4 Revision 中的正确时序

现有正式 Revision 在 TechnicalPlan 确认后通常会进入：

```text
Deterministic Skeleton
        ↓
issue_revision_continuation
```

目标调整为：

```text
TechnicalPlan Confirmed
        ↓
Template Reconcile
        ↓
Deterministic Skeleton
        ↓
issue_revision_continuation
```

必须满足：

> Template Reconcile 成功或 NO_CHANGE，是签发 revision continuation 的前置条件。

Reconcile 失败：

```text
不得 continuation
不得 Build
保持原 TemplateState
```

---

## 1.5 统一 Revision Finalizer

不能只在某一个 Graph Node 中接入 `/v1/update`。

当前正式 TechnicalPlan Revision 至少可能来自：

```text
Design Stage Revision
Workbench TechnicalPlan Revision
```

建议统一收口：

```python
finalize_confirmed_technical_plan_revision(...)
```

内部：

```text
reconcile_workspace_template()
        ↓
inject_deterministic_backend_skeleton()
        ↓
issue_revision_continuation()
```

所有正式 TechnicalPlan Revision 入口最终都必须调用该服务。

---

## 1.6 RequestedConfig

Reconcile 复用 `TEMPLATE_REFACTOR.md` 的公共 Desired/Requested/Effective 模型。

目标：

```text
New TechnicalPlan.template_capabilities
        ↓
New RequestedConfig
```

例如：

```json
{
  "template_capabilities": {
    "login": {
      "enabled": false,
      "config": {}
    },
    "authorization": {
      "enabled": true,
      "config": {}
    }
  }
}
```

XCodeAgent 可以请求：

```text
login=false
authorization=true
```

Template Engine 决定 Effective：

```text
login=true
authorization=true
```

禁止通过旧 application.json 的创建时开关阻止 Revision 新增 Capability。

---

## 1.7 `/v1/update` 调用契约
XCodeAgent 读取并校验 .xcodeagent/template-state.json，将其完整 JSON 内容作为 currentTemplateState 提交给 Template Service

输入：

```json
{
  "currentTemplateState": {
    "templateRevision": "...",
    "managedFiles": {},
    "requested": {},
    "effective": {}
  },
  "requestedConfig": {
    "capabilities": {}
  },
  "requestedConfig": {
    "capabilities": {
      "authorization": {
        "enabled": true,
        "config": {}
      }
    }
  }
}
```

结果：

### NO_CHANGE

```text
HTTP 204
```

XCodeAgent：

```text
不改 Workspace
不改 TemplateState
继续 Revision
```

### CHANGE

返回 Update Package。

---

## 1.8 Update Package

增量包按如下结构理解：

```text
update-package.zip
├── change-set.json
├── next-template-state.json
└── payload/
    ├── frontend/...
    └── backend/...
```

### change-set.json

包含：

```text
ADD_FILE
UPDATE_FILE
DELETE_FILE
```

### payload

只允许：

```text
ADD_FILE
UPDATE_FILE
```

对应的新内容。

### next-template-state.json

表示 ChangeSet 完整成功后的目标模板状态。

XCodeAgent：

```text
validate
persist
consume
```

但不修改 Engine 返回的内容。

---

## 1.9 为什么不能直接解压 Update ZIP

Template Engine 根据：

```text
Current TemplateState
+
New RequestedConfig
```

计算“应该怎么变”。

但它不知道当前 Workspace 是否已经：

```text
被 Agent 修改
被用户修改
被 Platform Projection 修改
出现未完成事务
存在其他并发 Mutation
```

因此：

```text
Template Engine
    = 计算 Desired Change

XCodeAgent
    = 判断 Actual Workspace 是否允许 Apply
```

禁止：

```python
extract update.zip directly into workspace
```

---

## 1.10 Package Validator

Apply 前必须校验：

| 对象 | 规则 |
| --- | --- |
| `change-set.json` | 必须且只能一个 |
| `next-template-state.json` | 必须且只能一个 |
| payload | 只能对应 ADD/UPDATE |
| path | 只能进入允许的工程 root |
| `.git/**` | 禁止 |
| `.xcodeagent/**` | ChangeSet 禁止直接修改 |
| ADD | current state 不存在，next state 存在 |
| UPDATE | current/next state 都存在 |
| DELETE | current 存在，next 不存在 |
| payload content | 与 operation / next managedFiles 一致 |
| next state | 通过 TemplateState Validator |
| ZIP entry | 禁止 traversal / absolute / symlink 等 |

Update Package 的安全规则复用公共 Engine archive security 能力，不维护第二套 ZIP 安全实现。

---

## 1.11 Workspace Preflight

Package 合法不等于 Workspace 可以安全 Apply。

基础前置条件：

```text
ADD_FILE
→ target 不存在

UPDATE_FILE
→ target 存在

DELETE_FILE
→ target 存在
```

同时根据 Ownership 做冲突检测。

---

## 1.12 Engine Exclusive 文件

对 Engine 完整拥有的模板文件：

```text
UPDATE / DELETE 前
```

检查当前 Workspace 内容是否仍符合 old TemplateState 的模板基线。

若已被用户或 Agent 修改：

```text
TEMPLATE_MANAGED_FILE_CONFLICT
```

fail closed。

不得静默覆盖。

---

## 1.13 Platform Overlay 文件

存在共享所有权的文件，例如：

```text
frontend/src/constants/routes.tsx
frontend/src/constants/resources.ts
AuthConstants.java
```

不能通过：

```text
current file == old managedFiles[path]
```

判断冲突。

处理原则：

```text
Template Engine
→ host/scaffold

Platform
→ marker / managed region / projection
```

V1 可以建立明确的 Platform Overlay registry，对这些文件走特殊校验。

长期演进应将共享所有权收敛为：

```text
managed marker
structured node operation
partial ownership
```

避免 Engine 和 Platform 同时声明 whole-file ownership。

---

## 1.14 Transactional Apply

Reconcile 必须使用事务模型：

```text
Download Update Package
        ↓
Secure Package Validation
        ↓
Parse ChangeSet
        ↓
Validate Next TemplateState
        ↓
Workspace Preflight
        ↓
Create Rollback Journal / Staging
        ↓
Apply ADD / UPDATE / DELETE
        ↓
Post Validation
        ↓
Atomic Replace template-state.json
        ↓
Commit
```

最重要约束：

> `next-template-state.json` 最后写入。

禁止：

```text
TemplateState 已变成新状态
Workspace 只 Apply 一半
```

---

## 1.15 Rollback

Apply 中任一步失败：

```text
恢复 UPDATE 前内容
恢复 DELETE 文件
删除本轮 ADD 文件
清除 staging/journal
保持 old template-state.json
```

然后：

```text
Reconcile Failed
Revision continuation 不签发
```

同一请求可以重新执行，因为 current TemplateState 仍然是旧状态。

---

## 1.16 幂等

同一 TechnicalPlan 重复确认时：

```text
Current TemplateState
+
RequestedConfig
```

如果已经收敛：

```text
/v1/update → 204
```

XCodeAgent 映射为：

```text
NO_CHANGE
```

并直接进入下一阶段。

不生成空 ChangeSet，不修改文件 mtime，不写 TemplateState。

---

## 1.17 与 Deterministic Skeleton 的关系

Reconcile 只处理模板固定技术能力。

顺序：

```text
Template Reconcile
        ↓
Business Deterministic Skeleton
        ↓
Agent Build
```

例如：

```text
AuthProvider
→ Template Reconcile

OrderPO / OrderMapper
→ Deterministic Skeleton

OrderService
→ Agent
```

Skeleton 不应该补权限模板固定能力。

---

## 1.18 与 Build DAG 的关系

Reconcile 成功后先持久化最新 TemplateState，再：

```text
issue_revision_continuation
        ↓
inspect_workspace
        ↓
prepare_build_tasks
```

因此 Build DAG 读取到：

```text
latest TemplateState.effective
```

例如：

```text
effective.authorization=true
```

后续 Authorization Overlay / Platform Projection 按现有能力门禁继续运行。

---

## 1.19 与 Platform Projection 的关系

Template Reconcile 不执行本应用业务 Projection。

顺序：

```text
Template Reconcile
        ↓
Skeleton
        ↓
Build DAG
        ↓
Agent Build
        ↓
Route Projection
        ↓
Authorization Projection
        ↓
Validation
```

如果 Reconcile 新增 authorization：

Template Engine 负责补：

```text
AuthProvider
RouteGuard
Permission
AuthConstants host
权限管理页面
Interceptor
Migration
```

业务平台投影仍在 Build 后写：

```text
业务 Route
resourceKey
RESOURCES
AuthConstants 业务操作常量
```

---

## 1.20 Capability Remove

V1 可以支持 Capability Remove，但必须遵守 Engine Desired State。

例如：

```text
Current Effective:
login
authorization

New Requested:
login only
```

Engine 可以生成：

```text
DELETE authorization fixed files
保留 login
```

XCodeAgent 不自行判断哪些文件应该删除，只安全执行 Engine ChangeSet。

删除前同样进行 ownership/conflict 校验。

---

## 1.21 Capability Config Change

如果 Capability 有 Config：

```text
redis.host
tracking.mode
authorization.xxx
```

Config 变化也统一走：

```text
Current TemplateState
+
New RequestedConfig
→ /v1/update
```

XCodeAgent 不为 Config Change 建第二套 patch 逻辑。

---

## 1.22 Template Revision Upgrade 暂不开放

V1 Reconcile 只保证：

```text
Capability Add
Capability Remove
Capability Config Change
Same-revision NO_CHANGE
```

不主动允许：

```text
TemplateRevision R1 → R2
```

因为模板版本升级可能大范围修改：

```text
base files
overlay host
shared managed file
```

它需要更完整的：

```text
3-way merge
structured operation
migration state
conflict resolution workflow
```

后续独立建设。

---

# 第二章 实施计划

## 2.1 实施原则

实施顺序：

```text
统一 RequestedConfig
        ↓
公共 Engine Client.update
        ↓
Update Package Validator
        ↓
Workspace Preflight
        ↓
Transactional Applier
        ↓
Revision Finalizer
        ↓
Build / Projection 联调
        ↓
Ownership Conflict EDD
```

不要先在多个 Graph Node 内直接调用 `/v1/update`。

---

## 2.2 阶段 1：统一 Desired Capability 编译

目标：

```text
TechnicalPlan
→ RequestedConfig
```

改造重点：

- TechnicalPlan 目标增加 `template_capabilities`；
- application.json 降级为 Initial Intent；
- 删除 application 与 Revision TP 必须永久一致的约束；
- 删除 XCodeAgent 内 `authorization → login` 依赖推导；
- RequestedConfig 编译器成为纯确定性映射。

兼容期如果暂不改 TechnicalPlan Schema：

- 从最新已确认正式 Artifact 编译；
- 必须允许 Revision 改变 Capability；
- 只有一个 Desired authority；
- 后续无损迁移到显式 `template_capabilities`。

验收：

```text
初始 authorization=false
Revision authorization=true
→ RequestedConfig 合法
```

---

## 2.3 阶段 2：扩展公共 TemplateEngineClient

目标接口：

```python
generate(requested_config)

update(
    current_template_state,
    requested_config,
)
```

建议结果：

```text
TemplateUpdateResult
├── kind = NO_CHANGE | CHANGE
├── package_path
├── sha256
├── size
└── content_type
```

映射：

```text
204 → NO_CHANGE
200 application/zip → CHANGE
```

验收：

- generate 不回归；
- update 204 正常；
- update ZIP streaming；
- timeout / size / archive security 与 Bootstrap 共享。

---

## 2.4 阶段 3：Update Package Validator

新增：

```text
workspace_template_reconcile/package.py
```

负责：

```text
parse change-set
parse next-template-state
payload indexing
operation consistency
path validation
state transition validation
```

非法情况必须 fail closed：

```text
缺 metadata
重复 metadata
payload 缺失
payload 多余
非法 operation
path traversal
.git
.xcodeagent mutation
next state 非法
operation 与 managedFiles 不一致
```

---

## 2.5 阶段 4：Workspace Preflight 与 Ownership Registry

新增：

```text
workspace_template_reconcile/preflight.py
```

第一阶段建立：

```text
ENGINE_EXCLUSIVE
PLATFORM_OVERLAY
```

两类 ownership。

ENGINE_EXCLUSIVE：

```text
UPDATE/DELETE
→ 校验当前内容未偏离模板基线
```

PLATFORM_OVERLAY：

```text
按 marker/平台投影契约做特殊校验
```

验收：

- 用户修改独占模板文件 → conflict；
- Platform 合法 Projection → 不误报；
- Engine ChangeSet 越界到 Business 文件 → 拒绝。

---

## 2.6 阶段 5：Transactional Applier

新增：

```text
workspace_template_reconcile/
├── service.py
├── models.py
├── package.py
├── preflight.py
├── applier.py
└── rollback.py
```

执行：

```text
backup/journal
→ ADD
→ UPDATE
→ DELETE
→ post validate
→ atomic template-state replace
→ commit
```

失败：

```text
rollback files
保持 old TemplateState
```

验收：

- ADD success；
- UPDATE success；
- DELETE success；
- 中途失败恢复；
- TemplateState 不领先 Workspace；
- 重试成功。

---

## 2.7 阶段 6：统一 TechnicalPlan Revision Finalizer

新增公共入口：

```text
technical_plan_revision_finalize.py
```

或等价服务。

负责：

```text
Template Reconcile
        ↓
Deterministic Skeleton
        ↓
issue_revision_continuation
```

接入：

```text
Design Stage Revision
Workbench TechnicalPlan Revision
```

验收：

- 两种入口都无法绕过 Reconcile；
- Reconcile failure 不 continuation；
- NO_CHANGE 正常 continuation；
- TemplateState 在 inspect_workspace 前完成更新。

---

## 2.8 阶段 7：Build / Projection 联调

验证：

```text
Revision TP authorization=true
        ↓
Reconcile
        ↓
effective.authorization=true
        ↓
inspect_workspace
        ↓
prepare_build_tasks
        ↓
template_context
        ↓
Agent Build
        ↓
Authorization Projection
```

验收：

- DAG 绑定最新 TemplateState；
- Agent 不生成固定权限模板；
- Platform Projection 正常检测 authorization effective；
- Route / AuthConstants host 已在 Build 前存在。

---

## 2.9 阶段 8：EDD

### 场景 A：新增 authorization

初始：

```text
login=false
authorization=false
```

Revision：

```text
authorization=true
```

预期：

```text
effective.login=true
effective.authorization=true
```

且固定权限能力文件被增量加入。

### 场景 B：重复同一 Revision

预期：

```text
/v1/update → 204
Workspace 无变化
TemplateState 无变化
```

### 场景 C：删除 authorization

预期：

```text
authorization fixed files removed
effective.authorization removed
```

依赖能力由 Engine 决定是否保留。

### 场景 D：Apply 中途失败

预期：

```text
Workspace rollback
old TemplateState 保留
no continuation
```

### 场景 E：独占文件被用户修改

预期：

```text
TEMPLATE_MANAGED_FILE_CONFLICT
```

不得覆盖。

### 场景 F：Platform Overlay 已被正常投影

预期：

```text
不能因为 whole-file hash 不同误判冲突
```

### 场景 G：两类 Revision 入口

预期：

```text
Design Stage Revision
Workbench Revision
```

都经过相同 Reconcile Gate。

### 场景 H：Capability Config Change

预期：

```text
由 Engine 计算 ChangeSet
XCodeAgent 不写能力专用 patch
```

---

## 2.10 V1 范围

V1 正式支持：

```text
Capability Add
Capability Remove
Capability Config Change
NO_CHANGE
Transactional Apply
Rollback
Retry
Revision Gate
Ownership Conflict Detection
```

V1 不支持：

```text
Template Revision Upgrade
复杂 3-way merge
跨版本 Migration 编排
Agent 自动解决模板冲突
```

---

## 2.11 Definition of Done

- [ ] Revision 不再次执行 Bootstrap；
- [ ] `/v1/update` 已由公共 Engine Client 接入；
- [ ] Desired Capability 可以随正式 TechnicalPlan Revision 变化；
- [ ] XCodeAgent 不解析 Capability dependency；
- [ ] Update Package 在 Apply 前完整校验；
- [ ] ChangeSet 不直接解压覆盖 Workspace；
- [ ] Apply 具备 rollback；
- [ ] `next-template-state.json` 最后提交；
- [ ] Reconcile 成功/NO_CHANGE 是 continuation 前置条件；
- [ ] Design Stage 和 Workbench Revision 使用同一 finalizer；
- [ ] Build 读取最新 TemplateState；
- [ ] Engine Exclusive / Platform Overlay 已有冲突保护；
- [ ] Capability Add/Remove/Config Change EDD 通过；
- [ ] Template Revision Upgrade 保持关闭。
