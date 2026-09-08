# XCodeAgent Workspace Bootstrap 实施方案

> 本文只描述**首次创建应用时的模板初始化**。  
> 模板体系公共架构、TemplateState、Capability、Desired/Requested/Effective 和 Ownership 规则见 [`TEMPLATE_REFACTOR.md`](./TEMPLATE_REFACTOR.md)。  
> 已有 Workspace 在 TechnicalPlan Revision 后的模板能力增量更新见 [`TEMPLATE_RECONCILE_PLAN.md`](./TEMPLATE_RECONCILE_PLAN.md)。

---

# 第一章 方案设计

## 1.1 目标

Bootstrap 解决的问题是：

> 在 Workspace 尚未初始化时，由 XCodeAgent Backend 调用 Template Engine `/v1/generate` 获取完整模板包，安全物化 frontend/backend，建立独立 Git baseline，并持久化 `.xcodeagent/template-state.json`，最终进入 Workbench。

目标链路：

```text
TechnicalPlan Confirmed
        ↓
Application Lifecycle
        ↓
Compile Initial RequestedConfig
        ↓
POST /v1/generate
        ↓
Full Template Package
        ↓
Validate / Stage
        ↓
Materialize frontend/backend
        ↓
Git Init + Baseline
        ↓
Persist TemplateState
        ↓
Readiness
        ↓
READY_FOR_WORKBENCH
```

Bootstrap 仅用于：

```text
First Creation
```

不得用于后续 TechnicalPlan Revision。

---

## 1.2 非目标

Bootstrap 明确不负责：

- 后续 Capability Add/Remove；
- Template Engine `/v1/update`；
- Existing Workspace ChangeSet Apply；
- Template Revision Upgrade；
- Agent 业务代码生成；
- 页面 placeholder；
- `BIZ_MENUS`；
- 业务 Route；
- Authorization 业务资源投影；
- Workspace semantic scan；
- LLM 决定文件解压、Git、rollback；
- Template Engine 服务端保存 Project/Workspace 生命周期；
- 第二份模板元数据。

---

## 1.3 Bootstrap 前置条件

Bootstrap 只允许在“未初始化 Workspace”执行。

Preflight 固定检查：

```text
frontend 不存在
backend 不存在
.git 不存在
.xcodeagent/template-state.json 不存在
```

生命周期必须处于允许首次模板初始化的阶段，例如：

```text
AWAITING_TECHNICAL_PLAN_CONFIRMATION
        ↓ TechnicalPlan confirmed
GENERATING_APPLICATION_TEMPLATE_FILES
```

如果 Workspace 已存在 TemplateState，应进入 Reconcile 语义，而不是重新 Bootstrap。

---

## 1.4 Initial RequestedConfig

Bootstrap 使用公共 Desired Capability 编译规则。

目标状态：

```text
Confirmed TechnicalPlan
        ↓
template_capabilities
        ↓
RequestedConfig
```

过渡期如果 TechnicalPlan 尚未显式提供 `template_capabilities`，可以从现有已确认 Artifact 编译，但必须遵守：

1. 不解析 Capability dependency；
2. authorization 不在 XCodeAgent 内自动补 login；
3. application.json 只表示创建阶段初始 Intent；
4. 产出的 RequestedConfig 必须与 Template Engine API 契约一致。

---

## 1.5 `/v1/generate` Package Contract

V1 Full Package 固定：

```text
template-package.zip
├── frontend/
├── backend/
└── .xcodeagent/
    └── template-state.json
```

允许 Workspace managed root：

```python
MANAGED_ROOTS = ("frontend", "backend")
```

`.xcodeagent` exact allow-list：

```text
.xcodeagent/template-state.json
```

禁止：

```text
.git/**
其他 .xcodeagent/**
其他未声明顶层 root
```

未来增加 `infra/` 等 root，必须同时升级：

```text
Engine Package Contract
XCodeAgent allow-list
Contract Tests
```

---

## 1.6 Package 下载与安全

必须：

```text
HTTP streaming
timeout
package size limit
SHA-256（如契约提供）
async cancellation
staging
```

ZIP 必须拒绝：

- `..` 路径穿越；
- 绝对路径；
- Windows drive path；
- symlink；
- 特殊文件；
- 加密 ZIP；
- 重复 path；
- 大小写冲突；
- 解压配额超限；
- 非法顶层 root；
- `.git/**`；
- 非 allow-list 的 `.xcodeagent/**`。

禁止：

```python
zip.extractall(workspace)
```

所有内容先进入 staging。

---

## 1.7 Workspace Bootstrap Transaction

Bootstrap 事务分为：

```text
Preparation
Commit Section
Rollback
```

### Preparation

只在 staging：

```text
download
        ↓
archive security validation
        ↓
package contract validation
        ↓
extract staging
        ↓
TemplateState validation
```

不修改正式 frontend/backend。

### Commit Section

固定顺序：

```text
move frontend
        ↓
move backend
        ↓
git init
        ↓
git baseline commit
        ↓
atomic write template-state
        ↓
remove staging
        ↓
full readiness
        ↓
commit success
```

### Rollback

事务中任一步失败，清理本轮 Bootstrap 创建的：

```text
frontend/
backend/
.git/
.xcodeagent/template-state.json
staging/
```

保留：

```text
application.json
application-lifecycle.json
Requirement / ProductPlan / UiDesign / TechnicalPlan
```

V1 不做失败后的原地 Resume；失败应进入确定的 Failed lifecycle。

---

## 1.8 Git Baseline

Workspace 初始化为独立 Git Repository。

固定流程：

```bash
git init
git config --local user.name XcodeAgent
git config --local user.email xcodeagent@local
git add
git commit
```

Baseline commit：

```text
chore: initialize workspace from template
```

`.xcodeagent/` 不进入 baseline。

成功后必须满足：

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

## 1.9 TemplateMutationCoordinator

Bootstrap Task 由 Backend server-owned coordinator 持有。

职责：

```text
同一 Workspace 单实例 Bootstrap
Task ownership
重复 trigger 复用
Application Delete 协调
Commit critical section
Workspace Attach 中断收尾
```

AG-UI / Renderer 只是触发和观察。

因此：

```text
SSE disconnect
≠
cancel Bootstrap
```

Renderer 断连时，真实 Backend Bootstrap Task 继续执行。

---

## 1.10 Application Delete

删除行为按 Bootstrap 当前阶段处理。

### Preparation

可以取消 Task 并清理 staging。

### Commit Section

必须等待 Commit 成功或 Rollback 完成后，再执行应用删除。

不能在：

```text
frontend 已 move
backend 未 move
```

等半事务状态直接并发删除。

---

## 1.11 Workspace Attach 中断收尾

Backend 不在进程启动时扫描和恢复所有 Workspace。

用户重新打开一个已知 Workspace 时：

```text
Frontend
    ↓
workspace_attach
    ↓
TemplateMutationCoordinator
```

若发现：

```text
lifecycle = GENERATING_APPLICATION_TEMPLATE_FILES
Coordinator 无 active task
```

说明此前 Bootstrap 被进程级中断。

执行确定性收尾：

```text
cleanup frontend
cleanup backend
cleanup .git
cleanup template-state
cleanup staging
        ↓
verify clean
        ↓
GENERATING → APPLICATION_TEMPLATE_GENERATION_FAILED
```

若 cleanup 未完成：

```text
保持 GENERATING
```

下次 Attach 继续清理。

`get` 保持只读；`workspace_attach` 是中断 Bootstrap 收尾入口。

---

## 1.12 Readiness Gate

Bootstrap 事务结束前必须验证：

1. RequirementSpec == CONFIRMED；
2. ProductPlan == CONFIRMED；
3. UiDesign IN {CONFIRMED, SKIPPED}；
4. TechnicalPlan == CONFIRMED；
5. TemplateState 存在并完整合法；
6. TemplateState.requested 与本轮 RequestedConfig 一致；
7. 初始请求能力已经存在于 effective capability；
8. `frontend/package.json` 是普通文件；
9. `backend/pom.xml` 存在；
10. Spring Boot Application 入口存在；
11. Workspace 是独立 Git repo；
12. HEAD 存在；
13. Git working tree clean；
14. `.xcodeagent` 不进入 baseline；
15. staging 无残留；
16. 正式路径不依赖 templateVariant/main/auth branch。

Readiness 必须位于 Bootstrap 事务内。

Readiness 失败：

```text
rollback
        ↓
APPLICATION_TEMPLATE_GENERATION_FAILED
```

不得产生：

```text
FAILED lifecycle + 半物化模板
```

---

## 1.13 Bootstrap 与业务开发解耦

Bootstrap 完成时只要求：

```text
模板固定能力已经物化
TemplateState 已建立
Git baseline 已建立
```

不提前生成：

```text
业务页面
页面 placeholder
业务菜单
业务 Route
权限 resourceKey
AuthConstants 业务常量
```

后续：

```text
READY_FOR_WORKBENCH
        ↓
Workspace Inspection
        ↓
Build DAG
        ↓
Agent Build
        ↓
Platform Projection
```

---

## 1.14 Frontend / Electron 收口

正式 Bootstrap 路径：

```text
Frontend
    ↓
AG-UI bootstrap_template_generation
    ↓
Backend WorkspaceBootstrapService
    ↓
Template Engine
```

Electron 不再负责模板 Git clone。

最终删除正式路径依赖：

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
main/auth template branch selection
```

Electron 只保留 Desktop 容器和 OS 集成职责。

---

# 第二章 实施计划

## 2.1 实施原则

Bootstrap 实施遵循：

```text
先冻结契约
→ 再完成事务
→ 再迁移消费者
→ 最后一次性 Runtime Cutover
```

不得长期运行：

```text
New Bootstrap + Old templateVariant Build
```

---

## 2.2 阶段 1：冻结 Generate 契约

Template Engine 侧冻结：

```text
PackageBuilder
OpenAPI
TemplateState
/v1/generate
Contract Tests
```

自动化验收：

- ZIP 只含 frontend/backend/template-state；
- TemplateState Core/OpenAPI/Package 一致；
- Requested 不被调用方补 dependency；
- Effective 由 Engine 解析；
- 非法 Package 被拒绝。

退出标准：

- [ ] Generate Package Contract 稳定；
- [ ] TemplateState Schema 稳定；
- [ ] XCodeAgent 可按 OpenAPI 完整校验。

---

## 2.3 阶段 2：建设公共 Engine Client 与 Package Validator

目标结构：

```text
Backend/app/services/template_engine/
├── client.py
├── models.py
├── archive_security.py
└── package_validation.py
```

Bootstrap 侧保留：

```text
workspace_bootstrap/
├── requested_config.py
├── template_package.py
└── ...
```

验收：

- streaming timeout；
- oversized download；
- unsafe ZIP；
- 非法 root；
- 非法 `.xcodeagent`；
- valid/invalid TemplateState。

退出标准：

- [ ] `/v1/generate` 不依赖 Electron；
- [ ] Package Security 测试闭合。

---

## 2.4 阶段 3：Workspace Transaction / Git / Recovery

实现：

```text
workspace_bootstrap/materializer.py
workspace_bootstrap/git_manager.py
TemplateMutationCoordinator
BootstrapJournal
deletion fence
workspace_attach
```

故障注入：

```text
第二 root move 失败
git init 失败
git commit 失败
TemplateState 写失败
Readiness 失败
Attach cleanup 失败
```

所有失败必须回到确定状态。

退出标准：

- [ ] rollback 闭合；
- [ ] deletion 两阶段语义闭合；
- [ ] Attach 收尾幂等；
- [ ] Git baseline 可独立验收。

---

## 2.5 阶段 4：TemplateState Consumer 与 Build Runtime Cutover

公共 TemplateState Consumer 规则见 `TEMPLATE_REFACTOR.md`。

本阶段完成：

```text
tasks.py
build_task_planner.py
BuildContext
BuildTaskPlan
Agent Prompt
Template Boundary Skills
Authorization Projection gate
```

从：

```text
templateVariant / template_variant
```

迁移到：

```text
TemplateState.effective
template_context
```

同时建设与 authorization 无关的通用 Route Projection。

退出标准：

- [ ] 无 Capability / login / authorization 三类状态均能生成 DAG；
- [ ] Build 正式路径不读取 generation manifest；
- [ ] Agent 不修改 Route/platform managed region。

---

## 2.6 阶段 5：WorkspaceBootstrapService 集成

正式服务：

```text
WorkspaceBootstrapService
```

负责：

```text
compile RequestedConfig
generate
download
validate
materialize
git baseline
TemplateState
readiness
```

AG-UI：

```text
bootstrap_template_generation
workspace_attach
```

验收：

- 正常成功；
- Engine timeout/reject；
- unsafe ZIP；
- Git failure；
- readiness failure；
- AG-UI disconnect；
- duplicate trigger；
- application delete；
- workspace attach recovery。

退出标准：

- [ ] Backend 可独立完成 Bootstrap；
- [ ] AG-UI 只触发/观察；
- [ ] Renderer 断连不影响 server-owned task。

---

## 2.7 阶段 6：一次性 Runtime Cutover

同一次发布完成：

```text
新 Bootstrap 开启
TemplateState Consumer 生效
templateVariant 退出
旧 business post-processor 停止
Frontend 只调用 Backend Bootstrap
```

不得拆成长期兼容模式。

E2E：

```text
A. 无 Capability
B. login only
C. authorization
```

完整走：

```text
TechnicalPlan Confirmed
→ Bootstrap
→ READY
→ Workspace Inspection
→ Build DAG
→ First Build
→ Platform Projection
→ Validation
```

---

## 2.8 阶段 7：删除旧 Electron Clone

全仓搜索并清理：

```text
templateVariant
template_variant
workspace:clone-template
cloneTemplate
DEFAULT_FRONTEND_TEMPLATE_REPO_URL
DEFAULT_BACKEND_TEMPLATE_REPO_URL
template-generation-manifest.json
main/auth template branch selection
Bootstrap placeholder
BIZ_MENUS
```

保留：

```text
login capability
authorization capability
auth 领域模块
权限规划与运行时权限功能
```

删除的是：

```text
main/auth 作为模板分支和模板变体的含义
```

不是删除权限能力。

---

## 2.9 Bootstrap EDD

至少覆盖：

### 场景 A：无 Capability

预期：

```text
Full Package 正常
TemplateState effective 为空
First Build 正常
```

### 场景 B：login only

预期：

```text
login fixed files 存在
effective.login=true
```

### 场景 C：authorization

Requested 可保持：

```text
login=false
authorization=true
```

Engine Effective：

```text
login=true
authorization=true
```

### 场景 D：Renderer 断连

预期：

```text
Backend Task 继续
重新 Attach 可读取最终 lifecycle
```

### 场景 E：事务故障

预期：

```text
无半物化 frontend/backend/.git/template-state
```

### 场景 F：中断 Attach

预期：

```text
GENERATING + no active task
→ cleanup
→ FAILED
```

---

## 2.10 Definition of Done

- [ ] `/v1/generate` 是首次模板唯一正式入口；
- [ ] Workspace 由 Backend 安全物化；
- [ ] Git baseline 成立且 clean；
- [ ] TemplateState 唯一持久化模板元数据；
- [ ] Bootstrap 不生成业务页面/菜单/业务路由；
- [ ] Server-owned Task 不受 SSE disconnect 影响；
- [ ] 删除与 Attach recovery 闭合；
- [ ] Electron 不 clone 模板；
- [ ] templateVariant/main/auth branch 不再作为模板事实；
- [ ] 三类应用 First Build E2E 通过；
- [ ] 后续 Revision 不再次执行 Bootstrap，而进入 Template Reconcile。
