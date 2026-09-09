# XCodeAgent Workspace Bootstrap 实施方案

> 目标：TechnicalPlan 确认后，由 XCodeAgent Backend 调用 Template Engine 获取完整模板 ZIP，安全物化到当前 Workspace，建立 Git 基线，并以 `.xcodeagent/template-state.json` 作为模板领域唯一持久化元数据。

---

## 1. 背景

当前首次模板初始化链路主要为：

```text
TechnicalPlan confirmed
        ↓
Frontend / Renderer
        ↓
Electron IPC
workspace:clone-template
        ↓
分别 clone frontend / backend 模板仓库
        ↓
根据 application.json.authorization.enabled 选择 main / auth 分支
        ↓
Backend prepare_template_generation
        ↓
main：补页面占位 + 菜单
auth：校验模板契约
        ↓
complete_template_generation
        ↓
ready_for_workbench
```

主要问题：

1. 模板能力依赖 `main/auth` Git 分支，无法扩展到多 Capability 组合。
2. Electron 承担模板 clone、分支选择和初始化，职责过重。
3. 模板初始化、业务页面骨架、菜单/路由补丁混在同一阶段。
4. `templateVariant=main|auth` 已成为 Readiness、BuildContext、BuildTaskPlanner、权限门禁和模板 Skill 的旧事实源。
5. 新 Template Engine 已引入 `TemplateState`，但 Engine Package、OpenAPI、Core 状态协议与 XCodeAgent 消费边界尚未完全冻结。

目标架构：

```text
Template Engine
    = 根据 RequestedConfig 生成完整模板工程 + TemplateState

XCodeAgent Backend
    = 下载、校验、安全物化、Git baseline、生命周期与后续 Build 编排

Electron
    = Desktop 容器和 OS 集成

Agent
    = 基于已准备好的工程执行真正业务代码开发
```

---

## 2. 核心目标

1. 首次创建应用时，不再由 Electron clone 前端/后端模板仓库。
2. 不再通过 `main/auth` 分支表达模板能力。
3. TechnicalPlan 确认后，由 Backend 启动 Workspace Bootstrap。
4. Backend 根据正式应用配置确定性编译 `RequestedConfig`。
5. Backend 通过普通 HTTP 调 Template Engine `/generate` 获取 ZIP。
6. ZIP 只进入 Backend，不经过 AG-UI / Electron。
7. Backend 对 ZIP 做完整安全校验并先解压到 staging。
8. Workspace Bootstrap 只负责模板工程落地，不生成业务页面、菜单或业务路由。
9. 模板领域只持久化一个元数据文件：`.xcodeagent/template-state.json`。
10. XCodeAgent 不修改 TemplateState 内容，仅负责校验、落盘、读取和后续 Update 回传。
11. Workspace 初始化为独立 Git Repository 并创建模板 baseline commit。
12. 下游 Readiness、Build、权限投影、模板 Skill 全部从 TemplateState Capability 读取模板事实，不再读取 `templateVariant`。
13. Bootstrap 与应用删除具备完整异步取消、提交临界区与失败回滚模型。

---

## 3. 非目标

本阶段明确不做：

- 不新增数据库或模板服务端持久化。
- 不在 Template Engine 保存 Project、Workspace、ChangeSet 生命周期。
- 不新增独立 Workspace Bootstrap 生命周期状态机。
- 不把 Bootstrap 做成 Agent / Skill。
- 不让 LLM 决定 ZIP 解压、文件移动、Git 初始化或 rollback。
- 不通过 AG-UI 传输 ZIP/Base64。
- 不在 Bootstrap 阶段生成业务页面、菜单、业务路由或权限业务代码。
- 不在 Bootstrap 阶段做 Workspace semantic scan。
- 不新增 `template-generation-manifest.json`、`bootstrap-journal.json` 等第二份模板元数据。
- V1 不支持 Bootstrap 失败后的原地 Retry / Resume。

---

## 4. 核心状态与事实源

### 4.1 模板事实：唯一使用 `.xcodeagent/template-state.json`

正式原则：

> `.xcodeagent/template-state.json` 是 Workspace 中唯一持久化的模板领域元数据文件。

其 Schema 和内容由 Template Engine 定义和生成：

```text
Template Engine Owns Schema + Content
XCodeAgent Owns Persistence + Consumption
```

XCodeAgent 可以：

- 校验 Schema；
- 原子持久化；
- 读取 `templateRevision`；
- 读取 `effective capabilities`；
- 读取 managed / migrations；
- 在后续 `/update` 时原样回传 current TemplateState。

XCodeAgent 不可以：

- 自行新增字段；
- 修改 requested/effective；
- 修改 managed 状态；
- 自行推进 templateRevision；
- 自行声明 migration；
- 加入 `gitBaselineCommit` 等 XCodeAgent 私有字段。

### 4.2 应用生命周期事实

继续由：

```text
.xcodeagent/application-lifecycle.json
```

负责：

```text
AWAITING_TECHNICAL_PLAN_CONFIRMATION
        ↓
GENERATING_APPLICATION_TEMPLATE_FILES
        ↓
READY_FOR_WORKBENCH
或
APPLICATION_TEMPLATE_GENERATION_FAILED
```

TemplateState 不承担应用生命周期职责。

### 4.3 代码事实

由真实 Workspace + Git 自身负责：

```text
.git/
Workspace filesystem
Workspace Inspection
```

不再用额外 JSON 重复记录“Git 是否初始化”“文件是否已经物化”等事实。

### 4.4 不再保留 `template-generation-manifest.json`

原有 manifest 的职责分别由以下真实事实替代：

| 原职责 | 新唯一事实源 |
|---|---|
| 模板 revision | `template-state.json` |
| Requested / Effective Capability | `template-state.json` |
| 模板受管内容 | `template-state.json` |
| 初始化是否成功 | `application-lifecycle.json` |
| Git 是否初始化 | `.git` / `git rev-parse` |
| Workspace 文件是否存在 | Workspace filesystem |
| 执行中进度 | AG-UI |
| 当前事务 rollback 状态 | 内存 `BootstrapJournal` |

---

## 5. 新 Bootstrap 上线前的 5 个 P0 门禁

新 Bootstrap 可以提前开发，但以下 5 个 P0 全部关闭前不得启用生产运行路径。

### P0-1：Template Package Contract 必须冻结

Engine 当前实际 Package 形态作为目标基础：

```text
template-package.zip
├── frontend/
├── backend/
├── ...
└── .xcodeagent/
    └── template-state.json
```

不再采用：

```text
manifest.json
template-state.json
workspace/
```

#### 唯一 `.xcodeagent` 规则

ZIP 中 `.xcodeagent` 下必须且只能存在：

```text
.xcodeagent/template-state.json
```

必须拒绝：

```text
.xcodeagent/application.json
.xcodeagent/application-lifecycle.json
.xcodeagent/plans/**
.xcodeagent/specs/**
.xcodeagent/cache/**
.xcodeagent/template-generation-manifest.json
其他任何 .xcodeagent/**
.git/**
```

#### P0-1 关闭条件

Template Engine 以下内容完全一致：

```text
PackageBuilder
OpenAPI
REFACTOR
Contract Tests
```

并统一规定：

```text
workspace files at ZIP root
+
.xcodeagent/template-state.json
```

---

### P0-2：TemplateState Contract 必须先在 Engine 冻结

`.xcodeagent/template-state.json` 就是 TemplateState 的正式载体，不存在第二份 TemplateState 或 XCodeAgent 私有模板状态文件。

冻结顺序：

```text
Engine Core TemplateState
        ↓
Engine OpenAPI TemplateState Schema
        ↓
Core PlanResult.nextTemplateState
        ↓
PackageBuilder 写入 .xcodeagent/template-state.json
        ↓
Stage2 / Stage3 Contract Tests
        ↓
XCodeAgent 开始稳定消费
```

最终字段必须以 Engine REFACTOR 的正式 Schema 为准。当前预期至少覆盖：

```text
templateRevision
requested
effective
capabilities
managed.files
managed.nodes
migrations
```

若最终命名不同，以 Engine 冻结后的 OpenAPI 为唯一协议，不允许 XCodeAgent 提前发明兼容字段。

#### XCodeAgent 公共读取层

由于 Readiness、Build、权限投影和 Skill 都需要消费 TemplateState，读取适配器应放在 Bootstrap 子包之外：

```text
Backend/app/services/template_state.py
```

只负责：

```python
load_template_state()
validate_template_state()
template_revision()
effective_capabilities()
has_capability()
```

它始终读取：

```text
.xcodeagent/template-state.json
```

不定义第二份存储格式。

#### P0-2 关闭条件

Engine 的：

```text
TemplateState.java
OpenAPI
Core PlanResult
PackageBuilder
Stage2 Test
Stage3 Test
```

使用完全一致的 TemplateState Schema，并完成契约测试。

---

### P0-3：`templateVariant` 必须在 Bootstrap Cutover 前整体退出

禁止出现以下运行组合：

```text
New Template Engine Bootstrap
+
Old templateVariant Build
```

因为新 Bootstrap 不再产生：

```text
main
auth
templateVariant
template_variant
```

所有模板能力事实统一改为：

```text
.xcodeagent/template-state.json
        ↓
TemplateState.effective.capabilities
```

必须在同一次运行时 Cutover 前迁移：

```text
application_template_generation.py
Backend/app/graph/nodes/tasks.py
Backend/app/services/build_task_planner.py
Authorization Platform Projection
BuildContext
BuildTaskPlan
frontend-template-modification-boundary/SKILL.md
springboot-template-modification-boundary/SKILL.md
相关测试
```

#### BuildContext 新结构

旧：

```json
{
  "template_variant": "auth"
}
```

新：

```json
{
  "template": {
    "revision": "R12",
    "capabilities": ["login", "authorization"]
  }
}
```

#### 权限门禁

旧：

```text
authorization enabled
AND templateVariant != auth
    → fail
```

新：

```text
authorization enabled
AND TemplateState.effective.capabilities.authorization.enabled != true
    → fail
```

#### 开发顺序与运行顺序分离

代码可以分多个 Commit 开发，但运行时必须一次性切换：

```text
所有 TemplateState Consumers Ready
        ↓
开启 New Bootstrap
```

不得跨版本运行“新 Bootstrap + 旧 Build 门禁”。

---

### P0-4：旧业务 Scaffold 必须与 Bootstrap 解耦

当前旧流程依赖：

```text
frontend_scaffold.py
src/constants/menus.ts
BIZ_MENUS
page placeholders
```

新模板已经使用：

```text
src/constants/routes.tsx
XCODEAGENT_BUSINESS_ROUTE_IMPORTS_*
XCODEAGENT_BUSINESS_ROUTES_*
```

新 Bootstrap 不再保留旧 post-processor，也不适配 `BIZ_MENUS`。

#### Bootstrap 最终边界

```text
Template ZIP
 ↓
安全物化
 ↓
Git baseline
 ↓
TemplateState
 ↓
Readiness
```

Bootstrap 不读取：

```text
ProductPlan.pages
pageId
actionId
menu
business route
```

#### 新业务路由投影位置

业务页面、菜单/路由、RouteGuard 等确定性投影进入 Build 平台阶段：

```text
Bootstrap
 ↓
Git Baseline
 ↓
Workspace Inspection
 ↓
Build DAG
 ↓
Page Agent 生成真实页面
 ↓
Platform Route Projection
 ↓
Authorization Projection
 ↓
Validation
```

模板只提供固定 managed marker：

```text
src/constants/routes.tsx

XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START
XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END

XCODEAGENT_BUSINESS_ROUTES_START
XCODEAGENT_BUSINESS_ROUTES_END
```

这样 Bootstrap 后 Git baseline 保持干净，也不需要创建业务占位页面。

---

### P0-5：异步删除、Commit 临界区和失败回滚必须闭合

新 Bootstrap 包含：

```text
HTTP Streaming
ZIP Validation
Staging Extraction
Workspace Materialization
Git Process
TemplateState Write
Readiness
```

现有同步模板锁不能完整覆盖异步下载与应用删除，需要升级为能够协调异步任务的模板写入机制。

#### Server-owned execution 与 AG-UI 断连

Bootstrap 是由 Backend 持有的 Server-owned execution，而不是由 AG-UI SSE 请求协程持有：

```text
Renderer 发起 AG-UI Bootstrap Run
        ↓
Backend / TemplateMutationCoordinator 持有 Bootstrap Task
        ↓
AG-UI 仅订阅进度与最终结果
```

AG-UI 或 Renderer 断连不得取消 Bootstrap Task；断连只会丢失实时进度订阅。客户端重新连接后通过 lifecycle 读取最终状态。只有应用删除流程可以请求取消 Preparation 阶段。

#### 两阶段事务模型

##### A. Cancellable Preparation

```text
compile RequestedConfig
 ↓
HTTP request
 ↓
stream ZIP
 ↓
ZIP validate
 ↓
extract staging
 ↓
package contract validate
```

该阶段可取消。

应用删除发生时：

```text
mark deleting
 ↓
cancel bootstrap asyncio task
 ↓
close httpx stream
 ↓
cleanup temp ZIP
 ↓
cleanup staging
```

正式 Workspace 尚未变化。

##### B. Workspace Commit Critical Section

在正式写 Workspace 前再次检查 deletion fence，然后进入短临界区：

```text
materialize managed roots
 ↓
git init
 ↓
baseline commit
 ↓
atomic write .xcodeagent/template-state.json
 ↓
Readiness Gate
```

进入 Commit Section 后不允许在两个 root move 之间强杀任务。

删除流程此时应：

```text
mark deleting
 ↓
wait commit success or rollback complete
 ↓
delete workspace
```

#### 内存 BootstrapJournal

V1 不支持 Resume，因此 Journal 只存在内存：

```python
BootstrapJournal(
    materialized_roots=[],
    git_created=False,
    template_state_written=False,
)
```

禁止新增：

```text
bootstrap-journal.json
template-generation-manifest.json
```

#### Backend 重启后的 Orphaned Bootstrap 清理

Backend 重启发现 lifecycle 仍为：

```text
GENERATING_APPLICATION_TEMPLATE_FILES
```

时，不恢复、不续跑 Bootstrap。它必须在 `TemplateMutationCoordinator` 和删除栅栏内，按 P0-1 冻结的首次 Bootstrap 受管范围执行幂等清理：

```text
frontend/
backend/
.git/
.xcodeagent/template-state.json
.xcodeagent/.bootstrap-staging/
```

只有确认上述受管内容均已清理后，才原子将 lifecycle 转为：

```text
APPLICATION_TEMPLATE_GENERATION_FAILED
```

如果清理或 lifecycle 写入失败，保持 `GENERATING_APPLICATION_TEMPLATE_FILES`，使下一次 Backend 启动重复同一清理流程；绝不在没有完整 Readiness 的情况下推进为 `READY_FOR_WORKBENCH`。

#### 首次 Bootstrap Preflight

必须满足：

```text
frontend 不存在
backend 不存在
.git 不存在
.xcodeagent/template-state.json 不存在
```

#### Rollback 规则

- 下载/解压失败：只清理 temp ZIP 和 staging。
- Root 已物化后失败：按 reverse journal 回滚本轮创建的 root。
- `git init` 后失败：仅在 `.git` 是本轮创建时删除 `.git`。
- TemplateState 写入后失败：删除本轮首次创建的 `.xcodeagent/template-state.json`。
- Readiness 或 lifecycle final CAS 失败：回滚 TemplateState、`.git`、managed roots。

V1 中：

```text
APPLICATION_TEMPLATE_GENERATION_FAILED
```

仍视为当前初始化流程终态，不设计原地 Retry / Resume。

---

## 6. 目标 Backend 代码结构

Workspace Bootstrap 内部实现统一收敛到：

```text
Backend/app/services/workspace_bootstrap/
```

建议结构：

```text
Backend/app/services/
│
├── application_lifecycle.py
├── application_template_generation.py
├── template_state.py
├── workspace_process_registry.py
├── version_control.py
│
└── workspace_bootstrap/
    ├── __init__.py
    ├── service.py
    ├── models.py
    ├── requested_config.py
    ├── template_engine_client.py
    ├── template_package.py
    ├── archive_security.py
    ├── materializer.py
    └── git_manager.py
```

### 6.1 子包边界

`workspace_bootstrap/` 只负责首次模板工程安全物化：

```text
WorkspaceBootstrapService
        │
        ├── RequestedConfig Compiler
        ├── Template Engine Client
        ├── Template Package Validator
        ├── Secure Archive
        ├── Workspace Materializer
        └── Git Workspace Manager
```

上层只依赖稳定入口：

```python
from app.services.workspace_bootstrap import WorkspaceBootstrapService
```

不直接调用其内部 Client / Materializer / GitManager。

### 6.2 保持在子包之外的公共能力

```text
application_lifecycle.py
    = 决定什么时候允许执行 Bootstrap

application_template_generation.py
    = Readiness + 模板写入协调/删除栅栏

template_state.py
    = TemplateState 公共读取与查询适配器

workspace_process_registry.py
    = Workspace subprocess 基础设施

version_control.py
    = Workbench 阶段通用版本控制能力
```

TemplateState 不放在 Bootstrap 子包内，因为后续 Build、Readiness、权限投影、Skill 都需要消费它。

### 6.3 `archive_security.py`

V1 先放：

```text
Backend/app/services/workspace_bootstrap/archive_security.py
```

保持 Bootstrap 内部闭环。后续若 `user_skill_imports.py` 等形成稳定复用需求，再抽取公共 archive 安全基础设施。

---

## 7. 各组件职责

### 7.1 `workspace_bootstrap/requested_config.py`

职责：

```text
application.json
+
confirmed TechnicalPlan
        ↓
RequestedConfig
```

约束：

- Capability 由 Backend 确定性编译；
- Frontend 不传 Capability 列表；
- Renderer 不指定 revision / branch；
- TechnicalPlan 用于确认生成时机和校验规划事实，不从模型文本推断 Capability；
- `application.authorization.enabled` 与正式 `authorization_manifest.enabled` 必须一致。

### 7.2 `workspace_bootstrap/template_engine_client.py`

职责：调用 Template Engine 并流式下载 ZIP。

要求：

- `httpx.AsyncClient.stream()`；
- 分块写临时文件；
- 下载中计算 SHA-256；
- 最大 ZIP 大小限制；
- connect/read timeout；
- 支持 asyncio cancellation；
- 删除开始后关闭 stream，不得继续进入 materialize。

### 7.3 `workspace_bootstrap/template_package.py`

职责：校验 ZIP 安全与正式 Package Contract。

必须验证：

```text
frontend/、backend/ 等 managed root 合法
.xcodeagent/template-state.json 唯一存在
不存在其他 .xcodeagent/**
不存在 .git/**
TemplateState JSON 满足 Engine 冻结 Schema
```

### 7.4 `workspace_bootstrap/archive_security.py`

必须拒绝：

- 绝对路径；
- Windows Drive Path；
- `..`；
- NUL；
- 反斜杠路径；
- symlink；
- device/socket/fifo；
- 加密 ZIP；
- 重复路径；
- 大小写冲突；
- 文件数、单文件、总展开大小超限。

禁止：

```python
zipfile.extractall(...)
```

### 7.5 `workspace_bootstrap/materializer.py`

职责：

```text
validated staging
    ↓
preflight
    ↓
transactional managed-root move
    ↓
rollback on failure
```

只移动模板 managed roots，不覆盖既有 `.xcodeagent`。

### 7.6 `Backend/app/services/template_state.py`

职责是公共读取适配器，而不是 Schema owner：

```text
load
validate
revision query
capability query
managed query
```

正式文件永远是：

```text
.xcodeagent/template-state.json
```

### 7.7 `workspace_bootstrap/git_manager.py`

职责：

```text
git init
local identity
git add
git commit
read HEAD
verify clean status
```

必须复用 `workspace_process_registry.py`，不得直接裸用 shell/subprocess。

---

## 8. Template Package Contract

唯一正式结构：

```text
template-package.zip
├── frontend/
├── backend/
├── ...
└── .xcodeagent/
    └── template-state.json
```

不要求 `manifest.json`，不要求 `workspace/` 包装目录。

### 8.1 TemplateState

`.xcodeagent/template-state.json`：

- 由 Template Engine 生成；
- 在 ZIP 中必须唯一；
- 必须符合 Engine OpenAPI 冻结 Schema；
- XCodeAgent 不补字段、不重写内容。

### 8.2 `.xcodeagent` 安全规则

Package 校验器必须实现 exact allow-list：

```text
ALLOW:
.xcodeagent/template-state.json

DENY:
.xcodeagent/** 其他所有路径
```

### 8.3 TemplateState 落盘

ZIP 先完整解压到 staging。

正式 commit 时：

```text
staging/.xcodeagent/template-state.json
        ↓
校验
        ↓
atomic write
        ↓
workspace/.xcodeagent/template-state.json
```

不允许直接把 ZIP 中整个 `.xcodeagent` 目录覆盖到 Workspace。

---

## 9. Workspace 物化模型

TechnicalPlan 确认时 Workspace 已存在：

```text
workspace/
  .xcodeagent/
    application.json
    application-lifecycle.json
    specs/
    plans/
```

因此禁止整体 `rename staging -> workspace`。

首次 Bootstrap 默认 fail-closed：

```text
frontend/backend 已存在 → fail
.git 已存在 → fail
.xcodeagent/template-state.json 已存在 → fail
```

不自动覆盖历史工程。

---

## 10. Staging、Commit 与 Rollback

### 10.1 Staging

建议使用同一文件系统：

```text
workspace/.xcodeagent/.bootstrap-staging/<generationId>/
```

或等价的同卷 sibling 临时目录。

### 10.2 Preparation 阶段

```text
download
validate
extract
package contract check
```

全部发生在 staging，不修改正式 managed roots。

### 10.3 Commit Section

```text
move frontend
move backend
...
git init
git baseline
atomic write template-state
readiness
```

该临界区必须受 deletion fence 协调。

### 10.4 Rollback Journal

仅内存记录：

```python
BootstrapJournal(
    materialized_roots=[],
    git_created=False,
    template_state_written=False,
)
```

失败时严格按 reverse order 回滚本轮创建内容。

---

## 11. Git Workspace 初始化

Bootstrap 成功后：

```text
workspace/
  .git/
  .xcodeagent/
  frontend/
  backend/
```

固定流程：

```text
git init
git config --local user.name XcodeAgent
git config --local user.email xcodeagent@local
写 .git/info/exclude
git add
git commit
read HEAD
verify clean status
```

Baseline commit message：

```text
chore: initialize workspace from template
```

### 11.1 `.xcodeagent` 与 Git

V1 baseline 不提交 `.xcodeagent/**`，通过：

```text
.git/info/exclude
```

排除：

```text
.xcodeagent/
```

Git baseline SHA 不写入 TemplateState，也不新增其他 metadata 文件长期保存。

---

## 12. Application Lifecycle 与失败语义

继续复用：

```text
GENERATING_APPLICATION_TEMPLATE_FILES
        ↓
READY_FOR_WORKBENCH
或
APPLICATION_TEMPLATE_GENERATION_FAILED
```

Bootstrap 不新增状态机。

V1 中失败态为终态：

```text
APPLICATION_TEMPLATE_GENERATION_FAILED
```

不支持直接重新执行 Bootstrap。

如果未来需要 Retry，必须单独设计：

```text
FAILED -> explicit retry transition -> GENERATING
```

以及 generationId、cleanup precondition、revision/CAS 等规则，不在本次实现中隐式加入。

---

## 13. Workspace 删除与异步并发安全

现有模板写入锁和删除栅栏需要升级为可以跟踪异步 Bootstrap Task 的协调机制。

概念上建议收敛为：

```text
TemplateMutationCoordinator
```

至少维护：

```text
workspace
active async bootstrap task
deleting flag
commit-section state
```

Bootstrap Task 的所有权归 Coordinator；AG-UI 订阅断开不得移除、取消或替换该 Task。

### 13.1 删除发生在 Preparation

```text
mark deleting
cancel bootstrap task
close httpx stream
cleanup temp/staging
wait task exit
delete workspace
```

### 13.2 删除发生在 Commit Section

```text
mark deleting
wait commit success or rollback complete
delete workspace
```

不得在多 root move 中间强杀任务。

Git 子进程继续由 `workspace_process_registry.py` 统一管理和终止。

---

## 14. WorkspaceBootstrapService

入口：

```text
Backend/app/services/workspace_bootstrap/service.py
```

建议：

```python
class WorkspaceBootstrapService:
    async def bootstrap(
        self,
        workspace_root: str | Path,
        *,
        progress: ProgressReporter | None = None,
    ) -> WorkspaceBootstrapResult:
        ...
```

### 14.1 最终执行流程

```text
1. 校验 lifecycle / confirmed TechnicalPlan
2. 获取 TemplateMutationCoordinator 操作权
3. 编译 RequestedConfig
4. 调 Template Engine /generate
5. 流式下载 ZIP
6. ZIP 安全校验
7. Package Contract 校验
8. 解压到 staging
9. Workspace preflight
10. 再次检查 deletion fence
11. 进入 Commit Section
12. 物化 managed roots
13. git init + baseline commit
14. 原子写 .xcodeagent/template-state.json
15. Readiness Gate
16. lifecycle -> READY_FOR_WORKBENCH
17. 清理 staging / temp
```

失败：

```text
cancel/rollback current transaction
cleanup temp/staging
lifecycle -> APPLICATION_TEMPLATE_GENERATION_FAILED
rethrow stable error
```

AG-UI 订阅断开不是失败或取消条件；Bootstrap Task 继续由 `TemplateMutationCoordinator` 执行，并由 lifecycle 提供后续可读取的最终结果。

不写 `template-generation-manifest.json`。

---

## 15. AG-UI 协议调整

建议将前端原来的：

```text
prepare_template_generation
complete_template_generation
```

收敛为单次：

```text
bootstrap_template_generation
```

Backend 内部完成 lifecycle begin / success / failed。

AG-UI 只负责进度与结果，不传 ZIP。

建议进度：

```text
5%    validating_input
15%   requesting_template
30%   downloading_package
45%   validating_package
60%   extracting_package
75%   materializing_workspace
88%   creating_git_baseline
94%   persisting_template_state
98%   verifying_workspace
100%  ready
```

---

## 16. Frontend / Electron 改造

Frontend 最终只触发一次 Backend Bootstrap：

```text
bootstrapApplicationWorkspace
        ↓
AG-UI Run
```

保留：

- 同应用任务去重；
- loading；
- lifecycle 刷新；
- 成功后打开 Workbench；
- 失败展示稳定错误。

最终删除：

```text
DEFAULT_FRONTEND_TEMPLATE_REPO_URL
DEFAULT_BACKEND_TEMPLATE_REPO_URL
fetchTemplateCode
TemplateDownloadError
workspace.cloneTemplate
workspace:clone-template
TemplateDownloadResult
TemplateDownloadTarget
resolveApplicationTemplateBranch
main/auth branch selection logic
```

Electron 收敛为 Desktop Container / Workspace 目录选择 / OS Integration / Renderer-Backend Bridge。

---

## 17. `application_template_generation.py` 调整

删除：

```text
TemplateDownloadResult normalization
main/auth branch derivation
branch consistency validation
page placeholder generation
BIZ_MENUS generation
template-generation-manifest.json
```

保留或重构为：

```text
Readiness Gate
TemplateMutationCoordinator / deletion fence
Application template generation lifecycle coordination
```

如职责已明显收敛，后续可再拆分命名，但不是本次 P0 前置条件。

---

## 18. 业务页面 / 路由确定性投影

旧 `frontend_scaffold.py` 不进入新 Bootstrap。

新模板以 `routes.tsx` managed marker 作为确定性写入点。

执行顺序：

```text
Build DAG
 ↓
Page Tasks 生成真实页面
 ↓
Platform Route Projection
 ↓
Authorization Projection
 ↓
Validation
```

平台投影负责：

```text
business route import
business route entry
必要的 RouteGuard
精确 resourceKey
```

Agent 不直接修改平台托管 marker 区。

---

## 19. Readiness Gate

Bootstrap Gate 至少检查：

1. lifecycle 处于合法生成阶段；
2. RequirementSpec / ProductPlan / TechnicalPlan 已处于允许状态；
3. `.xcodeagent/template-state.json` 存在；
4. TemplateState 满足 Engine 冻结 Schema；
5. `effective capabilities` 与正式应用配置没有冲突；
6. `frontend/package.json` 存在；
7. `backend/pom.xml` 或允许的后端入口存在；
8. Workspace 是独立 Git Repository；
9. Git HEAD 已建立；
10. `git status --porcelain` 在首次 Bootstrap 完成时为空；
11. 不存在 Bootstrap staging 残留；
12. 不存在旧 `templateVariant` 依赖作为当前模板事实源。

不再检查：

```text
template-generation-manifest.json
manifest revision
manifest gitBaseline SHA
```

Bootstrap Gate 不负责 semantic scan、Build DAG、业务代码完整性或测试结果。

---

## 20. Workspace Scan 边界

Bootstrap 完成后：

```text
READY_FOR_WORKBENCH
 ↓
Workspace Inspection
 ↓
WorkspaceSnapshot / Code Graph / workspace_revision
```

职责：

```text
Workspace Bootstrap
    = 工程存在且结构健康

Workspace Inspection
    = 理解工程结构和语义
```

---

## 21. TemplateState Capability 消费规则

所有模板能力判断统一读取：

```text
.xcodeagent/template-state.json
```

旧：

```text
templateVariant = main | auth
```

新：

```text
TemplateState.templateRevision
TemplateState.effective.capabilities
```

模板 Skill、BuildContext、BuildTaskPlanner、权限平台投影均不得回退到 branch/variant 推断。

---

## 22. 配置项

建议：

```text
XCODEAGENT_TEMPLATE_ENGINE_BASE_URL
XCODEAGENT_TEMPLATE_ENGINE_TOKEN
XCODEAGENT_TEMPLATE_ENGINE_CONNECT_TIMEOUT_SECONDS=10
XCODEAGENT_TEMPLATE_ENGINE_READ_TIMEOUT_SECONDS=120
XCODEAGENT_TEMPLATE_PACKAGE_MAX_BYTES=104857600
XCODEAGENT_TEMPLATE_PACKAGE_MAX_FILES=10000
XCODEAGENT_TEMPLATE_PACKAGE_MAX_EXTRACTED_BYTES=524288000
```

凭据只存在 Backend 配置，不写入 application.json、TechnicalPlan、TemplateState、Frontend 或 Electron。

开发阶段可保留 Bootstrap Feature Flag，但正式 Cutover 后应删除长期双路径，避免长期维护两套模板事实源。

---

## 23. 错误分类

建议稳定错误码：

```text
TEMPLATE_CONFIG_INVALID
TEMPLATE_ENGINE_UNAVAILABLE
TEMPLATE_ENGINE_TIMEOUT
TEMPLATE_ENGINE_REJECTED
TEMPLATE_PACKAGE_TOO_LARGE
TEMPLATE_PACKAGE_INVALID
TEMPLATE_PACKAGE_UNSAFE_PATH
TEMPLATE_PACKAGE_CONTRACT_INVALID
TEMPLATE_STATE_INVALID
TEMPLATE_STATE_CAPABILITY_MISMATCH
WORKSPACE_COLLISION
WORKSPACE_DELETING
WORKSPACE_MATERIALIZATION_FAILED
WORKSPACE_ROLLBACK_FAILED
GIT_NOT_AVAILABLE
GIT_INIT_FAILED
GIT_BASELINE_FAILED
TEMPLATE_READINESS_FAILED
```

错误中不得暴露 Token / Authorization Header。

---

## 24. 文件级实施清单

### 24.1 新增

```text
Backend/app/services/template_state.py

Backend/app/services/workspace_bootstrap/__init__.py
Backend/app/services/workspace_bootstrap/service.py
Backend/app/services/workspace_bootstrap/models.py
Backend/app/services/workspace_bootstrap/requested_config.py
Backend/app/services/workspace_bootstrap/template_engine_client.py
Backend/app/services/workspace_bootstrap/template_package.py
Backend/app/services/workspace_bootstrap/archive_security.py
Backend/app/services/workspace_bootstrap/materializer.py
Backend/app/services/workspace_bootstrap/git_manager.py
```

测试：

```text
Backend/tests/test_template_state.py
Backend/tests/test_workspace_bootstrap_requested_config.py
Backend/tests/test_workspace_bootstrap_template_engine_client.py
Backend/tests/test_workspace_bootstrap_template_package.py
Backend/tests/test_workspace_bootstrap_materializer.py
Backend/tests/test_workspace_bootstrap_git_manager.py
Backend/tests/test_workspace_bootstrap_service.py
Backend/tests/test_template_mutation_coordinator.py
```

### 24.2 修改

```text
Backend/app/config.py
Backend/app/protocols/application_lifecycle.py
Backend/app/services/application_lifecycle.py
Backend/app/services/application_template_generation.py
Backend/app/services/workspace_process_registry.py
Backend/app/services/version_control.py
Backend/app/graph/nodes/tasks.py
Backend/app/services/build_task_planner.py
Backend/app/services/frontend_scaffold.py（删除旧调用或退役）

Frontend/src/renderer/src/hooks/useApplicationTemplateGeneration.ts
Frontend/src/renderer/src/service/applicationLifecycle.ts
Frontend/src/renderer/src/typings/*
Frontend/src/preload/index.ts
Frontend/src/preload/index.d.ts
Frontend/src/renderer/src/window.d.ts
Frontend/src/main/index.ts

Backend/app/builtin_skills/frontend-template-modification-boundary/SKILL.md
Backend/app/builtin_skills/springboot-template-modification-boundary/SKILL.md
docs/AUTH.md
docs/APPLICATION_TEMPLATE_GENERATION_STATUS_AND_PLAN.md
docs/CODEBASE_INDEX.md
```

### 24.3 删除或退役

```text
template-generation-manifest.json 及其所有读取/写入逻辑
Electron workspace:clone-template
Renderer fetchTemplateCode
TemplateDownloadResult
TemplateDownloadTarget
DEFAULT_FRONTEND_TEMPLATE_REPO_URL
DEFAULT_BACKEND_TEMPLATE_REPO_URL
resolveApplicationTemplateBranch
main/auth template branch selection
Bootstrap 阶段 page placeholder / BIZ_MENUS post processor
```

---

## 25. 实施阶段

这里区分“开发顺序”和“运行时 Cutover”。可以提前开发模块，但不能让不兼容的新旧路径跨版本运行。

### Phase 0A：Engine Package Contract Freeze

在 `springboot-template` 先完成：

```text
PackageBuilder
OpenAPI
REFACTOR
Contract Tests
```

唯一 Package 契约：

```text
workspace managed files at ZIP root
+
.xcodeagent/template-state.json
```

### Phase 0B：Engine TemplateState Contract Freeze

完成：

```text
Core TemplateState
OpenAPI TemplateState
Core PlanResult.nextTemplateState
Stage2 Fixture
Stage3 /generate ZIP
```

全部使用同一 Schema。

### Phase 0C：XCodeAgent TemplateState Consumer Ready

实现公共：

```text
Backend/app/services/template_state.py
```

并完成所有新消费者：

```text
Readiness
BuildContext
BuildTaskPlanner
Authorization Projection
frontend template boundary Skill
backend template boundary Skill
```

代码可以在 feature flag 下开发，但不得形成正式运行的“新 Bootstrap + 旧 templateVariant Build”。

### Phase 0D：业务 Route / Authorization Projection Ready

完成：

```text
routes.tsx marker contract
Platform Route Projection
Authorization Projection
删除 Bootstrap 对 frontend_scaffold/BIZ_MENUS/page placeholder 的依赖
```

### Phase 0E：Async Mutation / Rollback Closure

完成：

```text
TemplateMutationCoordinator
async HTTP cancellation
Commit Critical Section
BootstrapJournal
application deletion coordination
rollback tests
```

### Phase 1：运行时一次性 Cutover 到新 Bootstrap

只有 Phase 0A~0E 全部通过后，才启用：

```text
TechnicalPlan confirmed
 ↓
WorkspaceBootstrapService
 ↓
Template Engine /generate
 ↓
secure ZIP validation
 ↓
staging
 ↓
materialize
 ↓
git baseline
 ↓
.xcodeagent/template-state.json
 ↓
Readiness
 ↓
READY_FOR_WORKBENCH
```

同一次 Cutover 中：

- 新 Bootstrap 开启；
- `templateVariant/main/auth` 退出事实源角色；
- Bootstrap 旧业务 post processor 不再执行；
- Build/权限/Skill 统一读 TemplateState Capability。

### Phase 2：删除旧 Electron Clone 链路

在 Cutover 集成测试稳定后，删除旧 clone、repo URL、branch 类型与 IPC，避免长期双路径。

---

## 26. 测试要求

### 26.1 Engine Contract Gate

必须先由 Engine 覆盖：

- ZIP 根目录结构；
- `.xcodeagent/template-state.json` 唯一允许规则；
- TemplateState OpenAPI/Core/Package 一致；
- 缺字段/额外字段拒绝；
- Stage2/Stage3 同 Schema。

### 26.2 XCodeAgent Package Security

覆盖：

- 正常 ZIP；
- 缺 TemplateState；
- 多 TemplateState；
- 其他 `.xcodeagent/**`；
- `.git/**`；
- `../`；
- 绝对路径；
- Windows Drive；
- symlink / 特殊文件；
- 重复/大小写冲突；
- ZIP/文件数/展开大小超限。

### 26.3 TemplateState Consumer

覆盖：

- valid state；
- invalid schema；
- revision 读取；
- `login` / `authorization` capability；
- application config 与 effective capability 冲突；
- BuildContext 不再依赖 templateVariant。

### 26.4 Materialization / Rollback

覆盖：

- 正常物化；
- managed root 冲突；
- `.git` 预存在；
- TemplateState 预存在；
- 第一个 root 成功、第二个失败；
- git init 后失败；
- template-state 写入后 readiness 失败；
- final lifecycle CAS 失败；
- reverse rollback 后 Workspace 恢复；
- staging 清理。

### 26.5 Async Deletion

覆盖：

- AG-UI / Renderer 断连：Bootstrap Task 继续执行，lifecycle 最终状态可被重新读取；
- 下载中删除：HTTP task 被取消；
- staging 中删除：清理 staging；
- commit 前删除：不进入 materialize；
- commit 中删除：等待 success/rollback；
- Git 进程被 workspace registry 正确终止/等待；
- 删除后不得有后续物化写入。
- Backend 重启发现 orphaned `GENERATING_APPLICATION_TEMPLATE_FILES`：幂等清理受管范围后落为 failed；
- orphan 清理或 failed lifecycle CAS 失败：保持 generating，下一次启动重复清理，且不得推进 ready。

### 26.6 Git

覆盖：

- local identity；
- baseline commit；
- `.xcodeagent` 不进入 baseline；
- HEAD 可读取；
- baseline 后 status 干净。

### 26.7 Business Projection

覆盖：

- Bootstrap 不生成 page placeholder；
- Bootstrap 不修改 BIZ_MENUS；
- Page Task 完成后平台写 routes.tsx marker；
- 权限 RouteGuard/resourceKey 投影正确；
- Agent 不触碰平台托管 marker 区。

---

## 27. EDD / 验收标准

### EDD-01：Package Contract 唯一

Engine `/generate` 与 OpenAPI/REFACTOR/测试对 ZIP 结构描述一致。

### EDD-02：模板领域只有一个持久化元数据文件

必须只有：

```text
.xcodeagent/template-state.json
```

不得新增或继续依赖：

```text
template-generation-manifest.json
bootstrap-journal.json
```

### EDD-03：TemplateState 由 Engine 拥有

XCodeAgent 不修改其内容或私自扩展字段。

### EDD-04：新 Bootstrap 与旧 templateVariant 不共存

新 Bootstrap 开启时，Build/Readiness/权限/Skill 已全部切换到 TemplateState Capability。

### EDD-05：Bootstrap 不生成业务页面/路由

Bootstrap 结束时只完成模板工程、安全状态和 Git baseline。

### EDD-06：业务 Route 属于 Build 平台投影

页面真实存在后再由平台确定性写入 routes.tsx managed marker。

### EDD-07：ZIP 安全

任意非法路径或特殊文件不得逃逸 staging；`.xcodeagent` 仅允许 TemplateState。

### EDD-08：失败可回滚

首次 Bootstrap 任一步失败，不得留下半初始化 frontend/backend、`.git` 或 TemplateState。

### EDD-09：异步删除闭合

删除在 Preparation 可取消；删除在 Commit Section 必须等待 success/rollback 后继续。

### EDD-10：Git baseline 稳定

Bootstrap 成功后：

```text
git rev-parse HEAD
```

成功，且：

```text
git status --porcelain
```

为空。

### EDD-11：生命周期不新增分叉

仍为：

```text
GENERATING_APPLICATION_TEMPLATE_FILES
 -> READY_FOR_WORKBENCH
或
 -> APPLICATION_TEMPLATE_GENERATION_FAILED
```

### EDD-12：V1 不支持 Bootstrap Retry/Resume

失败态不得隐式重新进入 generating。

---

## 28. 风险与处理

### 风险 1：跨仓库 Contract 不同步

处理：Phase 0A/0B 必须先在 Engine 冻结，并以 Contract Test 作为 XCodeAgent 开工门禁。

### 风险 2：TemplateState Consumer 改造范围大

处理：可以分 Commit 开发，但新 Bootstrap 开启与事实源切换必须同一次运行时 Cutover。

### 风险 3：旧业务 Scaffold 残留

处理：明确 Bootstrap 不再兼容旧 `BIZ_MENUS`；Route/Authorization Projection 作为 Cutover 前置门禁。

### 风险 4：异步下载与删除竞争

处理：Bootstrap 由 Backend 持有，AG-UI 断连不取消；应用删除在 Preparation 可取消，在 Commit Section 等待 success/rollback，并通过统一 TemplateMutationCoordinator 协调。

### 风险 5：Backend 崩溃遗留半初始化工作区

处理：不持久化或恢复 BootstrapJournal。Backend 重启发现 orphaned generating lifecycle 时，只按 P0-1 冻结的受管范围幂等清理；验证清理完成后才落 failed。清理或 failed 写入失败时保持 generating，供下一次启动重复收尾。

### 风险 6：跨卷 rename

处理：staging 必须与 Workspace 同文件系统；跨卷时明确失败，不自动退化为非事务性复制。

### 风险 7：历史 Workspace

首次 Bootstrap 只允许未初始化 Workspace；已有 frontend/backend/.git/template-state 的 Workspace 默认 fail-closed。

---

## 29. 最终目标状态

```text
User
 ↓
RequirementSpec
 ↓
ProductPlan
 ↓
UiDesign
 ↓
TechnicalPlan confirmed
 ↓
GENERATING_APPLICATION_TEMPLATE_FILES
 ↓
WorkspaceBootstrapService
 ↓
compile RequestedConfig
 ↓
Template Engine /generate
 ↓
ZIP
 ↓
Secure Validation + Staging
 ↓
Commit managed roots
 ↓
Git Init + Baseline
 ↓
.xcodeagent/template-state.json
 ↓
Readiness Gate
 ↓
READY_FOR_WORKBENCH
 ↓
Workspace Inspection
 ↓
Build DAG
 ↓
Page/Backend Agent Tasks
 ↓
Platform Route Projection
 ↓
Authorization Projection
 ↓
Validation
```

模板领域事实始终只有：

```text
.xcodeagent/template-state.json
```

后续 Template Update 继续使用：

```text
current .xcodeagent/template-state.json
+
new RequestedConfig
        ↓
Template Engine /update
        ↓
Change Package
        ↓
成功后替换同一个 template-state.json
```

不随着 Bootstrap / Refresh / Update 新增其他模板元数据文件。

---

## 30. Definition of Done

只有全部满足才允许认为 Workspace Bootstrap 重构完成：

### Engine 前置契约

- [ ] PackageBuilder / OpenAPI / REFACTOR / Contract Test 的 ZIP 结构一致；
- [ ] ZIP 根目录为实际 Workspace managed files；
- [ ] `.xcodeagent` 中只允许 `template-state.json`；
- [ ] TemplateState Core/OpenAPI/Package Schema 完全一致；
- [ ] TemplateState 已覆盖正式 managed/capability/migration 状态。

### XCodeAgent TemplateState

- [ ] 模板领域唯一持久化元数据为 `.xcodeagent/template-state.json`；
- [ ] 不再存在 `template-generation-manifest.json` 依赖；
- [ ] `template_state.py` 只做公共读取/校验，不定义第二份 Schema；
- [ ] XCodeAgent 不改写 TemplateState 内容。

### Capability Cutover

- [ ] Readiness 不再依赖 templateVariant；
- [ ] BuildContext 不再依赖 templateVariant；
- [ ] BuildTaskPlanner 不再依赖 templateVariant；
- [ ] 权限平台投影只读 TemplateState Capability；
- [ ] frontend/backend template Skill 不再依赖 main/auth；
- [ ] 新 Bootstrap 与旧 templateVariant 不存在运行时混用。

### Bootstrap 边界

- [ ] Bootstrap 不生成 page placeholder；
- [ ] Bootstrap 不修改 BIZ_MENUS；
- [ ] 新业务 Route 使用 routes.tsx managed marker；
- [ ] Route/Authorization Projection 在 Build 平台阶段执行。

### 安全与事务

- [ ] ZIP 安全校验覆盖路径、特殊文件、大小与 `.xcodeagent` allow-list；
- [ ] staging 与正式 Workspace 分离；
- [ ] Preparation 支持异步取消；
- [ ] Commit Section 不会被删除任务中途强杀；
- [ ] rollback 能清除本轮 roots、`.git`、TemplateState；
- [ ] V1 不产生持久化 BootstrapJournal；
- [ ] Bootstrap failure 进入终态，不隐式 Retry。

### Git / Frontend / Electron

- [ ] Workspace 建立独立 Git Repository；
- [ ] baseline commit 后 status 干净；
- [ ] `.xcodeagent` 不进入 baseline；
- [ ] Frontend 不持有 Template Engine 凭据；
- [ ] Electron 不再执行模板 clone/branch selection；
- [ ] 旧 repo URL / clone IPC / TemplateDownload 类型最终删除。

### 集成验收

- [ ] Phase 0A~0E 全部通过后才允许开启新 Bootstrap；
- [ ] 首次成功链路完整通过；
- [ ] Template Engine failure / timeout / unsafe ZIP / collision / Git failure / readiness failure 均有测试；
- [ ] 下载中删除、Commit 中删除、rollback 后删除均有集成测试；
- [ ] 首次 Build 能直接基于 TemplateState Capability 正常通过门禁。
