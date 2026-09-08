# XCodeAgent 模板重构总体方案

> 本文定义 XCodeAgent 模板体系重构后的**总体架构与公共契约**。  
> 首次模板初始化的详细实施见 [`BOOTSTRAP_PLAN.md`](./BOOTSTRAP_PLAN.md)；  
> 后续 TechnicalPlan Revision 中模板能力增量更新的详细实施见 [`TEMPLATE_RECONCILE_PLAN.md`](./TEMPLATE_RECONCILE_PLAN.md)。

---

# 第一章 方案设计

## 1.1 背景与目标

旧模板体系以 frontend/backend 模板仓库和 `main/auth` Git 分支表达模板差异，Electron 负责 clone 和分支选择，XCodeAgent Backend 再通过后处理补页面、菜单、路由等内容。随着登录、权限、审计、追踪、缓存、文件存储等固定技术能力增加，这种模式会产生以下问题：

1. Git 分支无法自然表达多个 Capability 的组合关系；
2. Electron 承担模板下载与初始化职责，边界过重；
3. 模板固定能力、业务骨架、业务实现和平台投影混在同一流程；
4. `templateVariant=main|auth` 成为多个模块的隐式事实源；
5. 首次模板生成和后续模板能力变化缺少统一的状态协议；
6. 模板文件与 Agent、用户、平台 Projection 的所有权边界不清晰。

重构后的核心目标是：

> Template Engine 负责描述并计算“模板应该是什么”；XCodeAgent 负责把目标模板状态安全协调到真实 Workspace，并在其上继续业务开发。

总体架构：

```text
Template Source
    = Capability 定义、固定文件、依赖、扩展点、Migration

Template Engine
    = Desired State Planner
    = RequestedConfig + Current TemplateState → Target TemplateState + Changes

XCodeAgent
    = Actual Workspace Reconciler
    = 生命周期、下载、校验、事务落盘、冲突保护、Build 编排

Agent
    = 基于已经准备好的模板能力实现业务代码

Platform Projection
    = 基于确认的业务事实写入平台托管区域
```

---

## 1.2 总体职责边界

| 能力 | Template Source / Engine | XCodeAgent |
| --- | --- | --- |
| Capability 定义 | 负责 | 不负责 |
| Capability 依赖解析 | 负责 | 不负责 |
| 模板固定文件 | 负责 | 不负责 |
| npm/Maven 模板依赖 | 负责 | 不负责 |
| Provider/Route/Interceptor 扩展点 | 负责 | 不负责 |
| Template Migration 定义 | 负责 | 不负责 |
| TemplateState Schema 与内容 | 定义 | 校验、持久化、读取 |
| Capability Diff | 负责 | 不负责 |
| ChangeSet 生成 | 负责 | 不负责 |
| TechnicalPlan 生命周期 | 不负责 | 负责 |
| 真实 Workspace 状态 | 不感知 | 负责 |
| ChangeSet 安全 Apply | 不负责 | 负责 |
| Git / rollback / concurrency | 不负责 | 负责 |
| Build DAG / Agent Build | 不负责 | 负责 |
| 平台 Route / Authorization Projection | 不负责 | 负责 |

正式原则：

```text
Template Engine = Desired State Planner
XCodeAgent      = Actual Workspace Reconciler
```

XCodeAgent 不维护第二套 Capability dependency graph，也不根据文件是否存在自行推断“缺哪个模板能力”。

---

## 1.3 Template Capability 模型

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
config
```

例如：

```yaml
authorization:
  requires:
    - login
```

调用方只表达：

```text
authorization.enabled = true
```

Template Engine 自动解析：

```text
authorization
    ↓ requires
login
```

禁止 XCodeAgent 编写：

```python
if authorization_enabled:
    login_enabled = True
```

新增 Capability 时，原则上只修改 Template Source / Engine 契约和 TechnicalPlan 可表达能力，不修改 Build Agent 的模板依赖解析逻辑。

---

## 1.4 TemplateState：模板领域唯一持久化事实

Workspace 中模板领域唯一持久化元数据固定为：

```text
.xcodeagent/template-state.json
```

所有权：

```text
Template Engine Owns Schema + Content
XCodeAgent Owns Persistence + Consumption
```

XCodeAgent 可以：

- 按 Engine OpenAPI 校验 TemplateState；
- 原子持久化；
- 读取 `templateRevision`；
- 读取 `requested`；
- 读取 `effective`；
- 读取 `managedFiles`；
- 在 `/v1/update` 时原样回传 current TemplateState；
- 为 Build 生成只读 `template_context` 快照。

XCodeAgent 不可以：

- 自行新增 TemplateState 字段；
- 修改 Engine 返回的 requested/effective；
- 自行推进 templateRevision；
- 自行修改 managedFiles；
- 写入 Git commit、Build Run 等 XCodeAgent 私有信息。

不再引入：

```text
template-generation-manifest.json
templateVariant
template_variant
```

作为模板事实源。

---

## 1.5 Desired / Requested / Effective 状态模型

模板能力需要明确区分三层状态。

### 1.5.1 Desired

Desired 表示最新正式技术方案希望应用具备的模板能力。

目标模型建议由 TechnicalPlan 显式表达：

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

这里允许：

```text
login=false
authorization=true
```

因为 Desired 不负责依赖解析。

### 1.5.2 Requested

XCodeAgent 从正式技术方案确定性编译出 Template Engine 请求：

```json
{
  "capabilities": {
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

### 1.5.3 Effective

Template Engine 解析依赖后得到实际生效能力：

```text
effective.login = true
effective.authorization = true
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
Template Engine dependency resolution
        ↓
TemplateState.effective
```

Build、Projection、Skill 只根据：

```text
TemplateState.effective
```

判断模板能力是否真实存在。

---

## 1.6 application.json 的定位

`application.json` 定位为：

> 应用创建阶段的初始用户意图。

它不是应用生命周期中模板能力的永久最终事实源。

目标链路：

```text
首次创建

application.json
      ↓
Requirement / TechnicalPlan
      ↓
TechnicalPlan.template_capabilities
      ↓
RequestedConfig
      ↓
/v1/generate
```

```text
正式 Revision

Requirement Change
      ↓
New TechnicalPlan
      ↓
New template_capabilities
      ↓
RequestedConfig
      ↓
/v1/update
```

因此不能长期要求：

```text
application.authorization.enabled
==
technicalPlan.authorization_manifest.enabled
```

否则会阻断：

```text
首次创建 authorization=false
        ↓
后续需求新增权限
        ↓
新 TechnicalPlan authorization=true
```

的正常演进。

### 兼容期

如果当前 TechnicalPlan Schema 尚未增加 `template_capabilities`，可以暂时由 XCodeAgent 从已确认的正式 Artifact 确定性编译 Desired Config，但必须满足：

1. 只有一个 Desired Capability 权威来源；
2. application.json 不得阻塞正式 Revision；
3. XCodeAgent 不做 Capability 依赖解析；
4. 后续迁移到 TechnicalPlan 显式字段时不改变 Engine 契约。

---

## 1.7 模板生命周期的两种 Mutation

模板重构后存在两类完全不同的 Workspace Mutation。

### 1.7.1 Bootstrap

适用条件：

```text
Workspace 尚未存在 frontend/backend
无 TemplateState
无独立 Git baseline
```

执行：

```text
TechnicalPlan Confirmed
        ↓
RequestedConfig
        ↓
POST /v1/generate
        ↓
Full Template Package
        ↓
Workspace Bootstrap
        ↓
Git Baseline
        ↓
TemplateState
        ↓
READY_FOR_WORKBENCH
```

详细方案见：

```text
docs/BOOTSTRAP_PLAN.md
```

### 1.7.2 Reconcile

适用条件：

```text
Workspace 已存在
TemplateState 已存在
Git 已存在
业务代码可能已经存在
```

执行：

```text
New TechnicalPlan Confirmed
        ↓
New RequestedConfig
        ↓
Current TemplateState
        ↓
POST /v1/update
        ↓
204 / ChangeSet + next TemplateState
        ↓
Safe Workspace Apply
        ↓
Commit next TemplateState
        ↓
Revision Continuation
```

详细方案见：

```text
docs/TEMPLATE_RECONCILE_PLAN.md
```

正式定义：

```text
Bootstrap ≠ Reconcile
```

Bootstrap 是“从无到有”。

Reconcile 是“已有 Workspace 的模板 Desired State 收敛”。

---

## 1.8 Capability Reconcile 与 Template Revision Upgrade

必须进一步区分：

### Capability Reconcile

同一 Template Revision 上：

```text
authorization false → true
tracking false → true
```

主要变化来自 Capability 文件集合、扩展点和 Migration。

### Template Revision Upgrade

模板版本：

```text
R1 → R2
```

即使 Capability 不变，也可能大量修改基础模板文件。

两者风险不同：

```text
Capability Reconcile
    = 能力增删 / 配置变化

Template Revision Upgrade
    = 模板基线升级 / 大范围文件冲突
```

V1 只正式支持 Capability Reconcile。

Template Revision Upgrade 后续独立设计，不和首版增量更新混在一起。

---

## 1.9 Workspace 文件所有权模型

真实 Workspace 至少区分三类文件。

### Engine Exclusive

Template Engine 完整拥有，例如：

```text
AuthProvider.tsx
Permission.tsx
RoleController.java
ResourcePermissionInterceptor.java
模板 Migration
```

约束：

```text
Agent 不修改
Platform 不修改
```

Update/Delete 前可以检查 Workspace 内容是否仍符合当前模板基线；不一致时 fail closed：

```text
TEMPLATE_MANAGED_FILE_CONFLICT
```

### Platform Overlay

Template Engine 提供 host/scaffold，XCodeAgent 平台负责业务投影，例如：

```text
frontend/src/constants/routes.tsx
frontend/src/constants/resources.ts
AuthConstants.java
```

这些文件不能简单通过：

```text
current file == old managedFiles[path]
```

做整文件冲突判断。

长期目标应采用：

```text
managed marker
managed region
structured operation
```

明确局部所有权。

### Business / Agent

业务 Page、Service、业务 Controller 等由业务生成过程负责，Template Engine 不应越界接管。

---

## 1.10 Template Capability、业务骨架和 Agent Build 的边界

三类代码必须分开：

```text
Template Capability
= 平台固定技术能力

Deterministic Skeleton
= 可以从 TechnicalPlan 确定性推导的业务骨架

Agent Build
= 需要 Agent 实现的非确定性业务逻辑
```

例如：

```text
AuthProvider / RoleController
→ Template Capability

OrderPO / OrderMapper
→ Deterministic Skeleton

OrderService.processOrder()
→ Agent Build
```

统一顺序：

```text
Template Capability Ready
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

---

## 1.11 Build 与 TemplateState

Build 不读取：

```text
templateVariant
main/auth branch
application.json 推导出的实际模板状态
```

Build 绑定：

```json
{
  "template_context": {
    "state_path": ".xcodeagent/template-state.json",
    "template_revision": "...",
    "effective_capabilities": {
      "login": {
        "enabled": true
      },
      "authorization": {
        "enabled": true
      }
    }
  }
}
```

`template_context` 是 Build Run 的只读绑定快照，不是第二份模板事实源。

Build 启动时应重新读取 TemplateState，并校验：

```text
templateRevision
effective capabilities
```

与 DAG 绑定快照一致。发生漂移时阻断当前 Build Run。

---

## 1.12 Platform Projection

平台投影属于业务生成后的确定性步骤，不属于 Bootstrap，也不属于 Template Engine。

统一顺序：

```text
Build DAG
    ↓
Agent 生成真实业务文件
    ↓
Route Projection
    ↓
Authorization Frontend Projection
    ↓
AuthConstants Projection
    ↓
Validation
```

职责：

### Template Engine

提供：

```text
Route host / markers
AuthProvider
RouteGuard
Permission
AuthConstants host
权限管理页面
Interceptor
Migration
```

### Platform Projection

提供：

```text
本应用页面 Route
页面 resourceKey
业务资源目录
业务操作资源常量
RouteGuard decoration
```

Agent 不直接修改平台 managed marker 区。

---

## 1.13 公共 Template Engine Client

Bootstrap 和 Reconcile 应复用统一客户端：

```text
Backend/app/services/template_engine/
├── client.py
├── models.py
├── archive_security.py
└── package_validation.py
```

接口：

```python
generate(requested_config)
update(current_template_state, requested_config)
```

后续可增加：

```python
plan(current_template_state, requested_config)
```

不要分别在 `workspace_bootstrap` 和 `workspace_template_reconcile` 中维护两套 HTTP、timeout、streaming、ZIP security 逻辑。

---

## 1.14 公共 Template Mutation 协调

Bootstrap 和 Reconcile 都属于 Template Mutation。

可以共享：

```text
TemplateMutationCoordinator
```

负责：

```text
同一 Workspace 互斥
异步 Task ownership
删除协调
Commit critical section
状态查询
```

但两者的事务实现保持独立：

```text
Bootstrap Transaction
= create roots + git baseline

Reconcile Transaction
= ADD / UPDATE / DELETE existing workspace files
```

不要为了复用而把两种事务合并成一个复杂 Materializer。

---

## 1.15 最终代码结构

目标结构建议：

```text
Backend/app/services/
│
├── template_state.py
├── technical_plan_revision_finalize.py
│
├── template_engine/
│   ├── client.py
│   ├── models.py
│   ├── archive_security.py
│   └── package_validation.py
│
├── workspace_bootstrap/
│   ├── service.py
│   ├── models.py
│   ├── requested_config.py
│   ├── template_package.py
│   ├── materializer.py
│   └── git_manager.py
│
└── workspace_template_reconcile/
    ├── service.py
    ├── models.py
    ├── package.py
    ├── preflight.py
    ├── applier.py
    └── rollback.py
```

---

# 第二章 实施计划

## 2.1 实施总序

整体模板重构按以下顺序推进：

| 阶段 | 目标 |
| --- | --- |
| 1 | 冻结 Template Engine / TemplateState 公共契约 |
| 2 | 完成首次 Bootstrap |
| 3 | 完成 TemplateState Consumer 与 Runtime Cutover |
| 4 | 删除 main/auth、templateVariant、Electron Clone 旧路径 |
| 5 | 建立统一 Desired Capability 编译规则 |
| 6 | 接入 `/v1/update` Capability Reconcile |
| 7 | 建立 Ownership 与冲突保护 |
| 8 | 完成 Bootstrap + Revision E2E |
| 9 | 后续独立建设 Template Revision Upgrade |

Bootstrap 的具体开发与验收步骤见：

```text
docs/BOOTSTRAP_PLAN.md
```

Reconcile 的具体开发与验收步骤见：

```text
docs/TEMPLATE_RECONCILE_PLAN.md
```

---

## 2.2 公共契约先于专项实施

以下内容只能在本文定义，两个子计划只引用，不重复定义：

```text
TemplateState ownership
Desired / Requested / Effective
Capability dependency ownership
application.json 定位
Template Engine / XCodeAgent 职责边界
Engine Exclusive / Platform Overlay ownership
Build template_context
Platform Projection 边界
Capability Reconcile vs Template Revision Upgrade
```

如果子计划与本文出现冲突：

> 以 `TEMPLATE_REFACTOR.md` 的公共契约为准，子计划只决定具体实施顺序。

---

## 2.3 总体验收场景

模板重构至少覆盖：

```text
A. 首次创建：无 Capability
B. 首次创建：login only
C. 首次创建：authorization，Engine 自动补 login
D. Revision：新增 authorization
E. Revision：删除 authorization
F. Revision：重复相同 TechnicalPlan → NO_CHANGE
G. Reconcile Apply 中途失败 → rollback
H. Engine Exclusive 文件被修改 → conflict
I. Platform Overlay 文件存在合法平台差异 → 不误判
J. Build DAG 绑定最新 TemplateState
```

---

## 2.4 总体 Definition of Done

最终满足：

- `.xcodeagent/template-state.json` 是唯一模板领域持久化状态；
- `main/auth` 不再表示模板变体；
- `templateVariant/template_variant` 不再作为正式运行事实；
- Electron 不负责模板 Git clone；
- XCodeAgent 不做 Capability dependency resolution；
- `/v1/generate` 只用于 Bootstrap；
- `/v1/update` 只用于已有 Workspace 的 Reconcile；
- Build 统一读取 TemplateState effective capability；
- Template Reconcile 成功是正式 Revision continuation 的前置门禁；
- Template Engine 不直接修改真实 Workspace；
- XCodeAgent 对所有模板 Mutation 提供安全校验和事务保护；
- Agent 不负责固定模板能力，也不修改平台托管 Projection 区；
- Capability Add/Remove/Config Change 可独立于 Build Agent 演进；
- Template Revision Upgrade 尚未开放时，不允许隐式升级模板 revision。
