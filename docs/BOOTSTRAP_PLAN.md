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

- [ ] rollback 闭合；
- [ ] deletion 两阶段语义闭合；
- [ ] Workspace Attach 收尾幂等；
- [ ] Git 使用 workspace_process_registry；
- [ ] 不依赖 Frontend 即可验收。

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
