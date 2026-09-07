# XCodeAgent Workspace Bootstrap 实施方案

> 目标：TechnicalPlan 确认后，由 XCodeAgent Backend 调用 Template Engine 获取完整模板 ZIP，安全物化到当前 Workspace，建立 Git 基线，并以 `.xcodeagent/template-state.json` 作为模板领域唯一持久化元数据。
>
> 本文分为两部分：
>
> - **第一章：方案设计** —— 锁定最终架构、职责边界、契约、状态模型与运行语义；
> - **第二章：实施步骤** —— 按可独立开发、可独立验收的阶段推进，前一阶段未通过不得进入下一阶段。

---

# 第一章 方案设计

## 1.1 背景与目标

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

当前存在以下结构性问题：

1. 模板能力依赖 `main/auth` Git 分支，无法自然扩展到多 Capability 组合；
2. Electron 承担模板 clone、分支选择和初始化，职责过重；
3. 模板初始化、业务页面骨架、菜单/路由补丁混在同一阶段；
4. `templateVariant=main|auth` 已成为 Readiness、BuildContext、BuildTaskPlanner、权限门禁和模板 Skill 的旧事实源；
5. Template Engine 已引入 `TemplateState`，但 Engine Package、OpenAPI、Core 状态协议与 XCodeAgent 消费边界必须先冻结，才能切换运行路径。

目标架构：

```text
Template Engine
    = 根据 RequestedConfig 生成完整模板工程 + TemplateState

XCodeAgent Backend
    = 请求模板、下载、校验、安全物化、Git baseline、生命周期和后续 Build 编排

Electron
    = Desktop 容器和 OS 集成

Agent
    = 基于已准备好的工程执行真正业务代码开发
```

最终目标：

1. 首次创建应用不再由 Electron clone 前端/后端模板仓库；
2. 不再通过 `main/auth` 分支表达模板能力；
3. TechnicalPlan 确认后，由 Backend 启动 Workspace Bootstrap；
4. Backend 根据正式应用配置确定性编译 `RequestedConfig`；
5. Backend 通过普通 HTTP 调 Template Engine `POST /v1/generate` 获取 ZIP；
6. ZIP 只进入 Backend，不经过 AG-UI / Electron；
7. Backend 对 ZIP 做安全校验，并先解压到 staging；
8. Bootstrap 只负责模板工程落地，不生成业务页面、菜单或业务路由；
9. 模板领域只持久化 `.xcodeagent/template-state.json` 一个元数据文件；
10. XCodeAgent 不修改 TemplateState 内容，仅负责校验、落盘、读取和后续 `/update` 回传；
11. Workspace 初始化为独立 Git Repository 并创建模板 baseline commit；
12. Readiness、Build、权限投影、模板 Skill 全部从 TemplateState Capability 读取模板事实；
13. Bootstrap 与应用删除具备完整异步取消、提交临界区、失败回滚和 Workspace Attach 中断收尾。

---

## 1.2 非目标

本次改造明确不做：

- 不新增数据库或模板服务端持久化；
- 不在 Template Engine 保存 Project、Workspace、ChangeSet 生命周期；
- 不新增独立 Workspace Bootstrap 生命周期状态机；
- 不把 Bootstrap 做成 Agent / Skill；
- 不让 LLM 决定 ZIP 解压、文件移动、Git 初始化或 rollback；
- 不通过 AG-UI 传输 ZIP / Base64；
- 不在 Bootstrap 阶段生成业务页面、菜单、业务路由或权限业务代码；
- 不在 Bootstrap 阶段做 Workspace semantic scan；
- 不新增 `template-generation-manifest.json`、`bootstrap-journal.json` 等第二份模板元数据；
- V1 不支持 Bootstrap 失败后的原地 Retry / Resume。

---

## 1.3 核心职责边界

最终调用关系：

```text
TechnicalPlan confirmed
        ↓
Application Lifecycle
        ↓
WorkspaceBootstrapService
        │
        ├── compile RequestedConfig
        ├── Template Engine /v1/generate
        ├── stream ZIP
        ├── package validation
        ├── staging extraction
        ├── workspace materialization
        ├── git baseline
        ├── template-state persistence
        └── readiness
        ↓
READY_FOR_WORKBENCH
```

职责固定为：

```text
ApplicationLifecycle
    = 决定什么时候允许执行 Bootstrap

WorkspaceBootstrapService
    = 决定模板初始化具体怎么执行

TemplateMutationCoordinator
    = 持有异步 Bootstrap Task，协调删除、提交临界区和并发

TemplateState Reader
    = 为 Readiness / Build / Projection / Skill 提供统一模板事实读取

Workspace Inspection
    = Bootstrap 完成后理解工程结构和语义

Build Platform Projection
    = 在业务页面真实生成后写入 Route / Authorization 托管区
```

Bootstrap 不承担业务代码生成职责。

---

## 1.4 模板唯一事实源

正式原则：

> `.xcodeagent/template-state.json` 是 Workspace 中唯一持久化的模板领域元数据文件。

所有权：

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
- 修改 requested / effective；
- 修改 managed 状态；
- 自行推进 `templateRevision`；
- 自行声明 migration；
- 加入 `gitBaselineCommit` 等 XCodeAgent 私有字段。

不再保留 `template-generation-manifest.json`。

| 事实 | 唯一来源 |
|---|---|
| 模板 revision | `.xcodeagent/template-state.json` |
| Requested / Effective Capability | `.xcodeagent/template-state.json` |
| 模板 managed / migrations | `.xcodeagent/template-state.json` |
| 初始化是否成功 | `.xcodeagent/application-lifecycle.json` |
| Git 是否初始化 | `.git` / Git 命令 |
| Workspace 是否物化 | Workspace filesystem |
| Bootstrap 实时进度 | AG-UI |
| 当前事务 rollback 状态 | 内存 `BootstrapJournal` |

---

## 1.5 Template Package Contract

### 1.5.1 V1 顶层 Root 固定

V1 `/v1/generate` ZIP 固定为：

```text
template-package.zip
├── frontend/
├── backend/
└── .xcodeagent/
    └── template-state.json
```

V1 不允许其他 Workspace 顶层 managed root。

XCodeAgent Materializer 固定：

```python
MANAGED_ROOTS = ("frontend", "backend")
```

未来若新增 `infra/` 等 root，必须同时升级 Engine Package Contract 与 XCodeAgent allow-list。

### 1.5.2 `.xcodeagent` exact allow-list

唯一允许：

```text
.xcodeagent/template-state.json
```

禁止其他 `.xcodeagent/**` 和 `.git/**`。

### 1.5.3 Engine 契约一致性

以下必须描述同一契约：

```text
PackageBuilder
OpenAPI
REFACTOR
Contract Tests
```

---

## 1.6 TemplateState Contract

冻结链路：

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
XCodeAgent 稳定消费
```

最终 Schema 以 Engine OpenAPI 为唯一协议。

XCodeAgent 公共读取层：

```text
Backend/app/services/template_state.py
```

只提供读取、校验和查询，不定义第二份 Schema。

---

## 1.7 RequestedConfig 编译规则

XCodeAgent 只表达 Application Requested Intent，不做 Capability 依赖解析。

| Application | RequestedConfig |
|---|---|
| `auth.enable == true` | `login.enabled = true` |
| `auth.enable == false` | `login.enabled = false` |
| `authorization.enabled == true` | `authorization.enabled = true` |
| `authorization.enabled == false` | `authorization.enabled = false` |

禁止：

```python
if authorization_enabled:
    login_enabled = True
```

依赖解析只存在于 Template Engine。

TechnicalPlan 只用于一致性校验，例如：

```text
application.authorization.enabled
    ==
technicalPlan.authorization_manifest.enabled
```

---

## 1.8 Capability 事实源切换

新 Bootstrap 不再产生：

```text
main
auth
templateVariant
template_variant
```

所有模板能力统一读取：

```text
.xcodeagent/template-state.json
        ↓
TemplateState.effective.capabilities
```

需要迁移：

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

禁止正式运行：

```text
New Bootstrap + Old templateVariant Build
```

---

## 1.9 Bootstrap 与业务投影解耦

旧：

```text
frontend_scaffold.py
BIZ_MENUS
page placeholders
```

不进入新 Bootstrap。

新模板提供 `routes.tsx` managed markers。

业务投影顺序：

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

Agent 不直接修改平台 managed marker 区。

---

## 1.10 Backend 目标代码结构

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

---

## 1.11 ZIP 下载与安全

必须使用 streaming 下载、大小限制、timeout、async cancellation。

ZIP 安全必须拒绝：

- 路径穿越；
- 绝对路径；
- Windows drive；
- symlink；
- 特殊文件；
- 加密 ZIP；
- 重复/大小写冲突；
- 配额超限。

禁止直接 `extractall()`。

---

## 1.12 Workspace 事务与 Git

### Preflight

必须满足：

```text
frontend 不存在
backend 不存在
.git 不存在
.xcodeagent/template-state.json 不存在
```

### Preparation

```text
download
validate
extract
package contract check
```

只发生在 staging。

### Commit Section

```text
move frontend
move backend
git init
git baseline
atomic write template-state
readiness
```

### Rollback

只使用内存 `BootstrapJournal`。

### Git baseline

固定：

```text
git init
git config --local user.name XcodeAgent
git config --local user.email xcodeagent@local
写 .git/info/exclude
git add
git commit
```

Baseline commit：

```text
chore: initialize workspace from template
```

`.xcodeagent/` 不进入 baseline。

---

## 1.13 Lifecycle、AG-UI 与并发

Lifecycle 保持：

```text
AWAITING_TECHNICAL_PLAN_CONFIRMATION
        ↓
GENERATING_APPLICATION_TEMPLATE_FILES
        ↓
READY_FOR_WORKBENCH
或
APPLICATION_TEMPLATE_GENERATION_FAILED
```

### Server-owned execution

Bootstrap Task 由 Backend `TemplateMutationCoordinator` 持有。

AG-UI / Renderer 断连：

```text
只丢失进度
Bootstrap 继续
```

重复请求不得创建第二个 Task。

### Application Delete

Preparation 可取消；Commit Section 等待完成或回滚。

### Workspace Attach 中断收尾

Backend 不在启动时扫描 Workspace，也不恢复 Bootstrap。应用打开并重新接管一个已知 `workspaceRoot` 时，Frontend 必须先发起 `workspace_attach` AG-UI 动作；Backend 在 `TemplateMutationCoordinator` 内检查 lifecycle。

若 lifecycle 仍为 `GENERATING_APPLICATION_TEMPLATE_FILES` 且 Coordinator 无 active Task：

```text
cleanup frontend/
cleanup backend/
cleanup .git/
cleanup .xcodeagent/template-state.json
cleanup staging
        ↓
verify clean
        ↓
GENERATING → APPLICATION_TEMPLATE_GENERATION_FAILED
```

cleanup 未完成则保持 `GENERATING`，下次 Attach 继续收尾。

`get` 保持只读；Attach 是唯一允许把已知 Workspace 的中断 Bootstrap 确定性收尾为 failed 的入口。

---

## 1.14 Readiness Gate

精确检查：

1. lifecycle 合法；
2. RequirementSpec == CONFIRMED；
3. ProductPlan == CONFIRMED；
4. UiDesign IN {CONFIRMED, SKIPPED}；
5. TechnicalPlan == CONFIRMED；
6. TemplateState 存在且合法；
7. effective capabilities 与 application 没有冲突；
8. `frontend/package.json` 存在；
9. backend 入口存在；
10. Workspace 为独立 Git repo；
11. HEAD 已建立；
12. baseline 后 Git clean；
13. staging 无残留；
14. 新路径不依赖 `templateVariant`。

---

## 1.15 AG-UI / Frontend / Electron

AG-UI 收敛为：

```text
bootstrap_template_generation
```

Frontend 只触发一次 Backend Bootstrap。

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

---

# 第二章 实施步骤

## 2.1 实施原则

按 7 个阶段推进：

| 阶段 | 目标 | 是否改变正式路径 |
|---|---|---|
| 1 | 冻结 Engine Package + TemplateState | 否 |
| 2 | 建 XCodeAgent 契约基础层 | 否 |
| 3 | 建 Workspace 事务 / Git / Recovery | 否 |
| 4 | 准备 Consumer 与平台投影 | 否 |
| 5 | 完成 Backend Bootstrap 集成 | 默认否 |
| 6 | Runtime Cutover + E2E | 是 |
| 7 | 删除旧 Electron Clone | 是 |

---

## 2.2 阶段 1：冻结 Template Engine 契约

### 改动项

在 `springboot-template` 完成：

```text
PackageBuilder
OpenAPI
REFACTOR
Core TemplateState
Core PlanResult.nextTemplateState
Stage2 Fixture
Stage3 /v1/generate
Contract Tests
```

### 自动化验收

- ZIP 只含 `frontend/`、`backend/`、`.xcodeagent/template-state.json`；
- TemplateState Core/OpenAPI/ZIP 一致；
- 缺字段/非法字段拒绝；
- Stage2/Stage3 同 Schema。

### 人工验收

至少生成：

```text
A. login=false, authorization=false
B. login=true, authorization=false
C. login=false, authorization=true
```

执行：

```bash
unzip -l template-package.zip
```

C 场景检查 Requested 不被 XCodeAgent/调用方提前补 login，但 Effective 可由 Engine 自动补齐。

### 退出标准

- [ ] Package / OpenAPI / REFACTOR / Tests 一致；
- [ ] 顶层 root 固定；
- [ ] TemplateState Schema 冻结；
- [ ] Stage2/Stage3 Contract Tests 通过。

---

## 2.3 阶段 2：建设 XCodeAgent 契约基础层

### 改动项

新增：

```text
Backend/app/services/template_state.py
Backend/app/services/workspace_bootstrap/models.py
Backend/app/services/workspace_bootstrap/requested_config.py
Backend/app/services/workspace_bootstrap/template_engine_client.py
Backend/app/services/workspace_bootstrap/template_package.py
Backend/app/services/workspace_bootstrap/archive_security.py
```

修改：

```text
Backend/app/config.py
```

### 自动化验收

覆盖：

- RequestedConfig 三种合法 Application 组合：无能力、login only、authorization enabled；
- 非法 Application 组合 `authorization=true/auth=false` 在 Application 边界被拒绝；
- Engine 直接调用 `login=false, authorization=true` 时，Requested 保持原值且 Effective 可由 Engine 自动补 login；
- TechnicalPlan / application 冲突；
- valid/invalid TemplateState；
- 非法顶层 root；
- 非法 `.xcodeagent/**`；
- `.git/**`；
- 路径穿越、symlink、重复、大小写冲突、配额；
- streaming timeout / oversized download。

### 人工验收

正常 ZIP、`scripts/`、`.xcodeagent/foo.json`、`.git/config`、`../escape` 分别跑 Validator。

### 退出标准

- [ ] TemplateState 可稳定校验；
- [ ] RequestedConfig 为纯字段映射；
- [ ] Package/Security 测试全部通过；
- [ ] 当前正式初始化路径未改变。

---

## 2.4 阶段 3：Workspace 事务、Git 与 Workspace Attach 收尾

### 改动项

新增：

```text
workspace_bootstrap/materializer.py
workspace_bootstrap/git_manager.py
```

实现/重构：

```text
TemplateMutationCoordinator
BootstrapJournal
deletion fence
workspace_attach 中断收尾
```

可能修改：

```text
application_template_generation.py
workspace_process_registry.py
application_lifecycle.py
application deletion protocol
```

### 自动化验收

覆盖：

- root collision；
- `.git` / TemplateState 已存在；
- 第一个 root move 成功、第二个失败；
- git init/commit 失败；
- TemplateState 写后失败；
- final lifecycle CAS 失败；
- Preparation 删除；
- Commit 删除；
- Attach 发现 interrupted `GENERATING` 后执行受管范围清理；
- Attach 清理失败保持 GENERATING；
- Attach 收尾幂等。

### 人工验收

故障注入：

```text
第二 root move 失败
git commit 失败
readiness 强制失败
模拟 GENERATING 后重新打开应用并执行 Workspace Attach
```

失败后不得留下半成品 roots / `.git` / TemplateState。

### 退出标准

- [x] rollback 闭合；
- [x] deletion 两阶段语义闭合；
- [x] Workspace Attach 收尾幂等；
- [x] Git 使用 workspace_process_registry；
- [x] 不依赖 Frontend 即可验收。

---

## 2.5 阶段 4：TemplateState Consumer 与平台投影

### 改动项

迁移：

```text
tasks.py
build_task_planner.py
Authorization Projection
BuildContext
BuildTaskPlan
frontend template boundary Skill
springboot template boundary Skill
```

建设：

```text
routes.tsx marker contract
Platform Route Projection
Authorization Projection
```

新 Bootstrap 路径不再依赖：

```text
frontend_scaffold.py
BIZ_MENUS
page placeholder
```

### 自动化验收

固定 TemplateState fixture：

```text
无 capability
login only
authorization effective
```

验证：

- BuildContext 来自 TemplateState；
- Planner 不读 templateVariant；
- authorization gate 基于 effective capability；
- Skill 不依赖 main/auth；
- Route Projection 写 marker；
- Authorization Projection 写 RouteGuard/resourceKey；
- Bootstrap 不生成 placeholder/BIZ_MENUS。

### 人工验收

用“2 页面、1 个有权限”的 fixture：

1. 先生成真实页面；
2. 再投影 routes；
3. 受控页有 RouteGuard/resourceKey；
4. 非受控页正常；
5. 不产生 placeholder。

### 退出标准

- [ ] 所有新消费者可只靠 TemplateState；
- [ ] Route/Authorization Projection 可独立验收；
- [ ] 新 Bootstrap 不需要旧 post-processor。

---

## 2.6 阶段 5：Backend WorkspaceBootstrapService 集成

### 改动项

新增：

```text
workspace_bootstrap/service.py
```

修改：

```text
application_lifecycle.py
application_template_generation.py
application lifecycle protocol
application deletion protocol
AG-UI action stream
Backend config
Frontend applicationLifecycle service
Frontend Application Open / Workspace Attach 入口
```

AG-UI 增加：

```text
bootstrap_template_generation
workspace_attach
```

`bootstrap_template_generation` 必须把真实 Bootstrap Task 交给 `TemplateMutationCoordinator` 持有；AG-UI 流只订阅或等待该 Task。SSE generator 断连时不得取消 Coordinator 的 Task，通用 AG-UI action stream 必须支持该订阅脱钩语义。

### 自动化验收

覆盖：

- 正常成功；
- 四类 Spec Gate；
- Engine timeout / reject；
- unsafe ZIP；
- collision；
- Git failure；
- readiness failure；
- AG-UI 断连继续；
- Renderer 重连读取最终 lifecycle；
- 重复 trigger 不创建第二 Task；
- Application 删除；
- Workspace Attach 中断收尾。

### 人工验收

feature flag 下：

```text
TechnicalPlan confirmed
→ trigger bootstrap
→ 中途关闭 Renderer
→ Backend 继续
→ 重新打开
→ Workspace Attach 后 lifecycle 显示最终结果
```

再测一次应用删除和一次 Engine timeout。

### 退出标准

- [ ] Backend 可独立完成完整 Bootstrap；
- [ ] AG-UI 只触发/观察；
- [ ] Readiness 含 UiDesign confirmed|skipped；
- [ ] 成功和主要失败场景有集成测试；
- [ ] 尚未强制切换正式产品路径。

---

## 2.7 阶段 6：一次性 Runtime Cutover 与 E2E

### 改动项

同一次 Cutover：

```text
新 Bootstrap 开启
TemplateState Consumer 生效
templateVariant 退出
旧 business post-processor 停止
Frontend 改为单次 Backend Bootstrap
```

### E2E 自动化验收

三类应用：

```text
A. 无 login / authorization
B. login only
C. authorization enabled
```

每类完整走：

```text
TechnicalPlan confirmed
→ Bootstrap
→ READY
→ Workspace Inspection
→ Build DAG
→ First Build
→ Route / Authorization Projection
→ Validation
```

### 人工验收

新建三类应用并检查：

```bash
git -C <workspace> rev-parse HEAD
git -C <workspace> status --porcelain
```

确认：

- TemplateState 存在；
- 无 template-generation-manifest；
- 无 placeholder；
- First Build 正常；
- 权限应用无 templateVariant 门禁错误；
- routes.tsx 投影正确。

额外执行：

```text
Renderer 断连
Application 删除
重新打开已中断 Workspace 的 Attach 收尾
```

### 退出标准

- [ ] 三类应用全部 E2E 通过；
- [ ] 新 Bootstrap 与新 Consumer 同时生效；
- [ ] 正式路径不依赖 templateVariant/main/auth；
- [ ] First Build 正常；
- [ ] 异常场景通过。

---

## 2.8 阶段 7：删除旧 Electron Clone 与兼容代码

### 删除项

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
main/auth branch selection
templateVariant/template_variant 旧事实源
template-generation-manifest 逻辑
Bootstrap page placeholder / BIZ_MENUS
```

如果 `frontend_scaffold.py` 已无其他消费者则退役，否则只删 Bootstrap 相关调用。

### 自动化验收

全仓库搜索：

```text
templateVariant
template_variant
workspace:clone-template
cloneTemplate
DEFAULT_FRONTEND_TEMPLATE_REPO_URL
DEFAULT_BACKEND_TEMPLATE_REPO_URL
template-generation-manifest.json
```

不得存在正式路径依赖。

重新跑：

```text
Backend tests
Frontend tests
Bootstrap E2E
First Build E2E
```

### 人工验收

新建应用并确认：

- Backend 调 Template Engine；
- Electron 无 template git clone；
- Frontend 无模板 repo URL；
- 项目进入 Workbench 并完成 First Build。

### 退出标准

- [ ] Electron 不再 clone/选 branch；
- [ ] Frontend 不知道模板 Git 仓库；
- [ ] Engine 凭据仅在 Backend；
- [ ] templateVariant/main/auth 不再是事实源；
- [ ] 全量测试通过。

---

## 2.9 最终 Definition of Done

### Engine

- [ ] ZIP 顶层只允许 `frontend/`、`backend/`、`.xcodeagent/`；
- [ ] `.xcodeagent` 只允许 `template-state.json`；
- [ ] TemplateState Core/OpenAPI/Package 一致。

### XCodeAgent

- [ ] 唯一模板元数据为 TemplateState；
- [ ] 无 generation manifest / persistent journal；
- [ ] RequestedConfig 不重复解析 Capability 依赖；
- [ ] 所有消费者切到 effective capabilities。

### Bootstrap

- [ ] 不生成业务 placeholder/BIZ_MENUS；
- [ ] ZIP 安全与事务 rollback 闭合；
- [ ] Server-owned Task；
- [ ] SSE 断连不取消；
- [ ] deletion / Workspace Attach 中断收尾闭合。

### Git / UI

- [ ] 独立 Git repo；
- [ ] baseline clean；
- [ ] `.xcodeagent` 不入 baseline；
- [ ] Frontend 无 Engine 凭据；
- [ ] Electron 无模板 clone。

### E2E

- [ ] 普通、login、authorization 三类应用通过；
- [ ] First Build 基于 TemplateState Capability 正常通过；
- [ ] Renderer 断连、删除、Workspace Attach 中断收尾均通过。

---

# 第三章 本地回检后的 Runtime 收口实施方案

> 本章记录 2026-09-07 对 XCodeAgent 本地实现的回检结果，并把第二章尚未完成的
> Runtime Cutover 拆成可独立开发、可独立验收的步骤。本章不改变第一章的目标架构；
> 若第二章的通用阶段描述与本章的本地实施顺序冲突，以本章为当前仓库的执行顺序。

## 3.1 当前实现判断

### 已知事实

当前仓库已经具备以下新链路基础：

- `WorkspaceBootstrapService` 已能调用 Template Engine、下载 ZIP、校验 Package、物化 Workspace 并建立 Git baseline；
- `.xcodeagent/template-state.json` 的基础读取层已经存在；
- `TemplateMutationCoordinator` 已覆盖 Bootstrap、删除和 Workspace Attach 的进程内协调；
- Renderer 的正式模板生成入口已经改为触发 `bootstrap_template_generation`。

但正式 Runtime 尚未完成 Cutover：

- Build 仍从 `.xcodeagent/template-generation-manifest.json` 读取 `templateVariant`；
- BuildContext、BuildTaskPlan、Planner、Frontend Agent Prompt 和权限投影仍使用 `template_variant=main|auth`；
- Electron 仍保留 `workspace:clone-template`、模板仓库 URL、Git clone 子进程管理和 main/auth 分支选择；
- Lifecycle AG-UI 仍接受 `prepare_template_generation` 和 `complete_template_generation`；
- 二次 TechnicalPlan 确认仍可能提前写入 auth 路由、页面 placeholder 和 `BIZ_MENUS`；
- `workspace_attach` 已有 Backend action，但 Renderer 的应用恢复和打开入口尚未调用。

### 反向检验

旧 Electron clone 和旧 lifecycle 方法已经不在 Renderer 的主模板触发链上，单独看可能被
误判为无害死代码；但 Build 节点和权限投影仍直接依赖旧 manifest/variant。因此当前问题
不是单纯删除废弃代码，而是必须先补齐 TemplateState Consumer 和统一平台投影，再一次性
切走旧 Runtime。否则新 Bootstrap 生成的 Workspace 会在 First Build 因缺少旧 manifest 而失败。

### 保留边界

以下内容不是旧分支逻辑，必须保留：

- `application.auth.enable` 和 `application.authorization.enabled` 作为 Requested Intent；
- `login`、`authorization` 作为 Template Engine Capability；
- Spring Boot 工程中的 `auth` 权限领域模块；
- 权限规划、权限 Overlay、RouteGuard、资源常量和权限业务 API。

本次只删除 `auth`/`main` 作为模板 Git 分支、模板变体和 Build 事实源的含义。

## 3.2 实施总序与公共契约

实施顺序固定为：

```text
冻结 TemplateState Consumer
        ↓
补齐事务内 Bootstrap Readiness
        ↓
建设通用 Route / Authorization Projection
        ↓
迁移 BuildContext / Planner / Agent / Skill
        ↓
调整 Build 投影时序和二次规划行为
        ↓
一次性删除旧 Lifecycle / Electron Clone
        ↓
接通 Workspace Attach
        ↓
文档清理与三类应用 E2E
```

步骤可以在开发分支内独立提交，但步骤 3.7 完成前不得发布，避免再次形成
`New Bootstrap + Old templateVariant Build` 的混合运行态。

Build DAG 当前契约同步升级为 `build-dag.v4`，不兼容读取或回填 `build-dag.v3`。
删除顶层 `template_variant`，新增：

```json
{
  "template_context": {
    "state_path": ".xcodeagent/template-state.json",
    "template_revision": "engine-owned-revision",
    "effective_capabilities": {
      "login": { "enabled": true },
      "authorization": { "enabled": true }
    }
  }
}
```

`template_context` 是 Build Run 的只读绑定快照，不是第二份模板事实源。Build 启动时必须
重读 TemplateState 并核对 revision 与 effective capabilities；任何漂移直接阻断本次 Run。

Lifecycle AG-UI action 最终只保留：

```text
create
get
bootstrap_template_generation
workspace_attach
```

平台投影证据统一为 `platform_projection_evidence`，包含 route、authorization frontend、
AuthConstants 三部分；按 current-contract-only 规则不保留旧字段别名。

## 3.3 步骤 1：冻结 TemplateState Consumer 契约

### 改动项

1. 以 Template Engine OpenAPI 为唯一 Schema 来源，替换当前只检查四个顶层字段的浅校验；
2. 在 `Backend/app/services/template_state.py` 收敛以下公共能力：
   - 完整读取和校验 TemplateState；
   - 返回 `templateRevision`；
   - 返回规范化 `effective capabilities`；
   - 查询单个 capability；
   - 生成 Build 使用的只读 `template_context`；
   - 比较 Build 绑定快照与当前 TemplateState；
3. capability 判断只读取 `effective`，不得从 Application 或 TechnicalPlan 补全 Engine 依赖；
4. 建立无 capability、login only、authorization effective 三类固定 fixture。

### 自动化验收

- 三类合法 fixture 均可读取并生成稳定的 `template_context`；
- 缺字段、多字段、错误嵌套类型、空 revision、非法 managedFiles、非法 enabled 语义均被拒绝；
- 相同 TemplateState 的规范化结果稳定一致；
- revision 或 effective capability 漂移时比较失败；
- 本步骤测试不创建、不读取 generation manifest。

### 人工验收

对照 Engine OpenAPI 检查三类 fixture，确认字段和嵌套结构一致，且输出中不存在
`templateVariant`、`template_variant`、main/auth branch。

### 退出标准

- [ ] 后续模块只需依赖统一 TemplateState 读取层；
- [ ] XCodeAgent 没有与 Engine OpenAPI 冲突的第二套 Schema；
- [ ] TemplateState Consumer 测试全部通过。

## 3.4 步骤 2：补齐事务内 Bootstrap Readiness

### 改动项

Materializer 的固定顺序调整为：

```text
解压 staging
→ 移动 frontend/backend
→ 建立 Git baseline
→ 写入 TemplateState
→ 清除 staging
→ 完整 readiness
→ 提交事务成功
```

Readiness 必须检查：

1. RequirementSpec、ProductPlan、TechnicalPlan 为 `confirmed`；
2. UiDesign 为 `confirmed` 或 `skipped`；
3. TemplateState 完整合法；
4. TemplateState requested 与本轮 RequestedConfig 一致；
5. Application 请求启用的 capability 存在于 effective capabilities；
6. `frontend/package.json` 是普通文件；
7. `backend/pom.xml` 和 Spring Boot `Application.java` 入口存在；
8. `.git` 为当前 Workspace 的独立仓库；
9. `git rev-parse HEAD` 成功；
10. `git status --porcelain` 为空；
11. `.xcodeagent` 未进入 baseline；
12. `bootstrap-staging` 无残留。

Readiness 失败必须在 Materializer 事务中抛出，使本次 frontend、backend、`.git`、
TemplateState 全部回滚。`complete_workspace_bootstrap(succeeded=True)` 只能在事务校验通过后调用。

Bootstrap 只允许 lifecycle 处于 `GENERATING_APPLICATION_TEMPLATE_FILES` 时触发；READY、FAILED
或其他阶段必须在调用 Engine 前拒绝。失败后仍不支持原地 Retry/Resume。

### 自动化验收

- 分别注入缺前端入口、缺后端入口、无 HEAD、dirty baseline、capability 冲突和 staging 残留；
- 每类失败均将 lifecycle 写为不可恢复 FAILED，并清除 frontend/backend/`.git`/TemplateState；
- 正式规划文档、application.json 和 application-lifecycle.json 必须保留；
- AG-UI 返回失败，不能返回“可以进入工作台”的成功消息；
- READY/FAILED 后重复 trigger 不调用 Template Engine；
- 同一活动 Bootstrap 的并发 trigger 仍复用同一个 Server-owned Task。

### 人工验收

正常 Bootstrap 后执行：

```bash
git -C <workspace> rev-parse HEAD
git -C <workspace> status --porcelain
git -C <workspace> ls-files .xcodeagent
```

预期分别为：存在 HEAD、无 dirty 输出、`.xcodeagent` 无 tracked 文件。

### 退出标准

- [ ] 所有 readiness 失败都处于可回滚事务内；
- [ ] 不存在 FAILED lifecycle 携带半物化模板 roots；
- [ ] 成功 Workspace 满足本节全部 readiness 条件。

## 3.5 步骤 3：建设与权限无关的通用 Route Projection

### 改动项

1. 从现有权限前端投影中拆出通用页面 Route Projection；
2. 三类 TemplateState 对应的模板都必须提供相同 `routes.tsx` managed markers；
3. Route Projection 根据确认的 ProductPlan/TechnicalPlan 页面事实生成全部业务页面 import 和 route；
4. Route Projection 不要求 authorization capability，不创建页面文件，不写 `BIZ_MENUS`；
5. Authorization Projection 只负责 `resources.ts`、RouteGuard/resourceKey 和操作权限投影；
6. AuthConstants 投影改为检查 authorization effective capability 与固定 managed marker，不再读取后端分支；
7. 所有投影保持幂等，并通过统一平台变更捕获返回证据。

### 自动化验收

- 无 capability：写入普通页面路由，不生成权限资源；
- login only：写入普通页面路由，不错误启用 authorization；
- authorization effective：普通页无 RouteGuard，受控页包含正确 RouteGuard/resourceKey；
- 操作资源只进入 authorization effective 应用的 AuthConstants 托管区；
- marker 缺失、重复、顺序错误或 marker 外漂移时 fail closed；
- 连续执行两次，第二次无文件差异；
- 页面文件不存在时投影拒绝，且不生成 placeholder。

### 人工验收

使用“两个页面、一个受控页面”的 fixture：先创建两个真实页面，再执行平台投影。确认两条 route
均存在、仅受控页带权限守卫，且没有产生 placeholder 或 `BIZ_MENUS` 条目。

### 退出标准

- [ ] 所有应用使用同一 Route Projection；
- [ ] 权限只作为 route decoration；
- [ ] 投影模块不存在 branch/variant 判断。

## 3.6 步骤 4：迁移 BuildContext、Planner、Agent 和 Skills

### 改动项

1. `prepare_build_tasks` 读取 TemplateState，把 `template_context` 注入 BuildContext；
2. BuildTaskPlanner 删除 `template_variant` 参数和默认值；
3. 平台路由文件对所有 capability 都禁止 Agent 修改；
4. `frontend:route-registry` 永远不能成为模型任务；
5. authorization 资源、RouteGuard 和 AuthConstants 只允许平台投影修改；
6. Build DAG 切换为 `build-dag.v4` 并持久化 `template_context`；
7. Build Run 重读 TemplateState，要求 revision 和 effective capabilities 与确认 DAG 完全一致；
8. Frontend Agent Prompt 根据 authorization effective capability 决定是否提供 Permission/RESOURCES 指令；
9. 前后端模板 Skills 改读 `/.xcodeagent/template-state.json`，删除远程 clone、旧 manifest、
   main/auth 隔离和预建 placeholder/BIZ_MENUS 描述；
10. 保留 auth 领域模块的真实保护边界，不能把“删除模板分支”扩大为允许 Agent 修改权限基础设施。

### 自动化验收

- 三类 TemplateState 均能完成 DAG 生成；
- BuildContext 和 BuildTaskPlan 不包含 `template_variant`；
- authorization effective 时生成权限任务切片，其他两类不生成；
- 模型输出 route-registry 或共享路由修改任务时，三类应用均被拒绝；
- Build Run 遇到 TemplateState revision/capability 漂移时阻断；
- Prompt 和 Skills 测试不包含旧 manifest、main/auth branch 或占位页前提；
- `build-dag.v3` 被明确拒绝，不做运行时迁移或字段回填。

### 人工验收

分别输出三类应用的 BuildContext、BuildTaskPlan 和 Agent Prompt，确认差异只来自 capability，
没有任何模板分支差异。

### 退出标准

- [ ] 正式 Build 不再 import `application_template_generation.py`；
- [ ] Planner、Build Run、Agent 和 Skills 只使用 TemplateState；
- [ ] `build-dag.v4` 的生产、确认、恢复和消费测试同时通过。

## 3.7 步骤 5：调整 Build 投影时序和二次规划行为

### 改动项

Build 顺序固定调整为：

```text
确认并绑定 Build DAG
→ 执行页面/API/后端任务
→ 确认目标页面文件真实存在
→ Route Projection
→ Authorization Frontend Projection
→ AuthConstants Projection
→ 工程/业务验收
```

平台投影失败时必须把 Build 标记为 failed，不进入 Integration Test，并保存精确错误和
`platform_projection_evidence`。平台文件不得归入任一 Agent change set。Retry/Repair 重放同一
确认 DAG 的投影，且必须保持幂等。

二次 TechnicalPlan 确认后的确定性注入只保留仍有现行消费者的后端骨架：

- 删除前端权限投影；
- 删除页面 placeholder；
- 删除 `BIZ_MENUS` 同步；
- 将函数重命名为能准确表达“仅后端骨架”的名称；
- `frontend_scaffold.py` 无其他消费者后直接删除。

### 自动化验收

- Route Projection 调用发生在 page task 成功之后、验收之前；
- 页面任务失败时平台投影不执行；
- 页面文件缺失时平台投影失败并阻断后续测试；
- 平台投影文件只出现在 `platform_projection_evidence`；
- 二次规划确认不创建、删除或覆盖前端页面、菜单、路由文件；
- Repair 后重放投影不产生重复 route/resource/constants。

### 人工验收

对已有 Workspace 发起新增页面的正式 revision。TechnicalPlan 确认后检查页面和 route 尚未被
预创建；First Build 完成后再检查真实页面与 managed route 已按顺序出现。

### 退出标准

- [ ] Bootstrap 和规划阶段均不生成业务页面或菜单；
- [ ] 平台投影严格发生在真实页面生成后；
- [ ] Build 验收消费 Agent 代码与平台投影合并后的最终工程。

## 3.8 步骤 6：一次性删除旧 Lifecycle 与 Electron Clone

### Backend 删除项

```text
prepare_template_generation
complete_template_generation
TemplateDownloadTarget
TemplateDownloadResult
templateGenerationManifest response
application_template_generation.py
旧 template generation lock / delete fence
```

### Frontend / Electron 删除项

```text
workspace:clone-template
cloneGitRepo
模板来源、分支和 commit 校验
clone 子进程登记、终止和退出清理
默认前后端模板仓库 URL
resolveApplicationTemplateBranch
preload/window cloneTemplate API
TemplateDownloadTargetResult / TemplateDownloadResult
旧 lifecycle service 方法
```

Application deletion 只使用 Backend `TemplateMutationCoordinator` 和当前运行资源协调器。
`/health` 的 lifecycle capabilities 同步只发布四个当前 action。不保留废弃 action、类型别名、
旧 IPC 空实现或旧 manifest 兼容读取。

### 自动化验收

- 旧 lifecycle action 触发 Pydantic 校验失败；
- Electron 不再注册 `workspace:clone-template`；
- 删除项目时不调用任何 clone process stop 逻辑；
- Backend lifecycle/bootstrap/deletion 测试通过；
- `pnpm typecheck` 和 `pnpm build` 通过；
- `/health` 只列出四个当前 lifecycle action。

### 人工验收

新建应用时观察 Electron 日志和子进程，确认不出现 `git clone`；同时确认 Template Engine 请求
只由 Backend 发起。删除正在 Bootstrap 的应用，确认由 Backend Coordinator 完成取消或等待 Commit。

### 退出标准

- [ ] Frontend/Electron 不知道模板仓库 URL、分支或 Engine 凭据；
- [ ] Backend 不接受 Renderer 模板下载结果；
- [ ] 新旧 Bootstrap 协议不再并存。

## 3.9 步骤 7：接通 Workspace Attach 和中断恢复

### 改动项

1. Frontend 增加 `attachApplicationWorkspace` AG-UI 调用；
2. 应用列表恢复、用户打开应用、Workbench 首次挂载和 Renderer 重启恢复都必须先 Attach、再 Get；
3. 同一 Workspace 的并发 Attach 在 Renderer 内合并；
4. Backend Attach 固定行为：
   - 非 GENERATING：返回 `none`；
   - 当前 Backend 持有活动 Bootstrap：返回 `active`；
   - 孤儿 GENERATING：清理 frontend/backend/`.git`/TemplateState/staging 并写不可恢复 FAILED；
   - 清理失败：保持 GENERATING，下次 Attach 继续收尾；
5. `get` 保持严格只读，不隐式执行 Attach 或生命周期修复。

### 自动化验收

- active Bootstrap Attach 不取消任务、不修改 lifecycle；
- 孤儿 GENERATING Attach 完成清理并写 FAILED；
- cleanup failure 保持 GENERATING；
- READY/FAILED Attach 幂等返回 none；
- Renderer 打开应用的调用顺序为 attach → get；
- React StrictMode 或多入口并发只产生一个活动 Attach 请求。

### 人工验收

在 Bootstrap 中断 Backend，保留 GENERATING Workspace；重启应用并打开项目，确认 Attach 自动
清理并展示失败原因。再次打开时不得重复修改状态。

### 退出标准

- [ ] 所有 Workspace 接管入口都执行 Attach；
- [ ] 不存在只能人工删除的孤儿 Bootstrap 状态；
- [ ] `get` 的只读语义未被破坏。

## 3.10 步骤 8：清理当前契约文档和索引

### 改动项

同步更新：

```text
docs/CODEBASE_INDEX.md
docs/WORKFLOW.md
docs/AUTH.md
docs/XCODEAGENT_COMPLETE_WORKFLOW.md
docs/DAG_TASK_GENERATION_AND_VALIDATION_MININAL_CHANGE_PLAN.md
docs/DAG_TASK_GENERATION_UNIT_CANDIDATE_PLAN.md
docs/APPLICATION_TEMPLATE_GENERATION_STATUS_AND_PLAN.md
```

当前契约描述中必须删除：

- main/auth 模板分支选择和双端分支一致性；
- main 初始化 placeholder/BIZ_MENUS；
- auth 分支专属平台投影；
- generation manifest 门禁；
- Electron 下载模板和模板仓库 URL。

历史方案若必须保留，需在标题下明确标记“已被 `BOOTSTRAP_PLAN.md` 取代，不是当前运行契约”；
否则删除对应索引入口。只有真实自动化与人工验收通过后，才能勾选本文第二章和第三章的完成项。

### 自动化验收

全仓搜索：

```text
templateVariant
template_variant
workspace:clone-template
cloneTemplate
template-generation-manifest.json
resolveApplicationTemplateBranch
frontendTemplateUrl
backendTemplateUrl
```

正式源码和当前契约文档必须零命中。测试中仅允许存在“明确拒绝旧字段/动作”的负向用例。
Java `src/main`、Git 默认分支、数据源 ID，以及 `auth` 认证/权限领域名不属于清理目标。

### 人工验收

从 `CODEBASE_INDEX.md` 依次追踪 Bootstrap、Build、Projection 和 Attach，确认描述与实现一致；
阅读前后端模板 Skills，确认 Agent 不会再寻找旧 manifest 或基于模板分支决定行为。

### 退出标准

- [ ] 仓库文档不存在两套并行的当前契约；
- [ ] 索引能直接定位新的 TemplateState、投影和 Bootstrap 边界；
- [ ] 旧术语只存在于必要的拒绝测试或明确历史背景。

## 3.11 步骤 9：三类应用 E2E 与最终发布门禁

### 自动化 E2E

三类应用：

```text
A. login=false, authorization=false
B. login=true, authorization=false
C. login=true, authorization=true
```

每类完整执行：

```text
创建 Application/Lifecycle
→ 确认正式规划产物
→ Backend /v1/generate
→ ZIP 校验与物化
→ Git baseline
→ READY
→ Workspace Inspection
→ build-dag.v4 生成与确认
→ Page/API/Backend Tasks
→ Route/Authorization Projection
→ Integration Test
→ First Build 完成
```

共同断言：

- 只有 TemplateState，没有 generation manifest；
- BuildContext/BuildTaskPlan 没有 variant；
- Bootstrap 无 placeholder/BIZ_MENUS；
- 三类应用均存在业务 route；
- 仅 C 包含 RouteGuard、RESOURCES 和 AuthConstants 业务投影；
- Git baseline clean；
- 平台投影证据与 Agent change set 分离。

### 异常 E2E

必须覆盖：

- Renderer 在下载中断连，Backend 继续完成；
- 删除发生在 Preparation；
- 删除发生在 Commit Section；
- Backend 中断后的 Workspace Attach；
- Engine timeout/reject；
- unsafe ZIP；
- TemplateState capability/revision 漂移；
- route/AuthConstants marker 漂移。

### 最终验证命令

Backend：

```bash
python3 -m py_compile <changed-python-files>
Backend/.venv/bin/python -m pytest -q Backend/tests
curl -sS http://127.0.0.1:8000/health
```

Frontend：

```bash
cd Frontend
pnpm typecheck
pnpm build
```

Frontend UI 必须在已运行 Electron 应用中验证，不得用 Vite 浏览器页面替代。

### 人工验收

在 Electron 中分别新建 A/B/C 三类应用并完成 First Build，逐一核对 Workspace 文件、
页面路由、权限守卫、生命周期卡片和 Git 状态。随后各执行一次 Renderer 断连、Bootstrap
期间删除以及孤儿 GENERATING Workspace 重开，确认 UI 结果与 Backend lifecycle 一致。

### 发布退出标准

- [ ] 所有自动化测试通过；
- [ ] Electron 人工验收通过；
- [ ] 三类应用均完成 First Build；
- [ ] 正式 Runtime 不存在 main/auth 分支或 templateVariant 依赖；
- [ ] Renderer 断连、删除和 Workspace Attach 满足事务边界；
- [ ] 任一验收失败都阻止合并或发布，不增加兼容分支临时绕过。
