# XCodeAgent 模板增量更新启动验收重构方案
 
> 目标：将模板增量更新后的验证机制，从独立 Sandbox / Maven / npm Runtime 验证，收敛为复用现有项目启动能力，对当前真实 Workspace 执行一次完整重启，以项目能够重新构建并启动到 Ready 作为模板更新的基础运行态验收。

---

# 第一章 方案设计

## 1.1 背景与问题

当前 Template Reconcile V2 在模板增量更新完成后，会根据 Template Engine 返回的 `validationPlan` 执行独立验证。

现有验证能力包含：

- `NPM_BUILD`
- `NPM_TEST`
- `MAVEN_TEST`
- `MAVEN_PACKAGE`
- `REAL_WORKSPACE`
- `SANDBOX`

其中 Sandbox 模式还需要准备独立执行目录、复制 Workspace，并在隔离环境中执行 Maven/npm 命令。

这套机制存在几个问题：

1. **与现有项目启动能力重复。**  
   XCodeAgent 已经存在统一的 `launch_project` / `launch_project_preview()`，真实项目启动本身就会经历构建、进程启动和 Ready 检测。

2. **额外引入 Runtime 环境耦合。**  
   Template Reconcile 中独立执行 `mvn test` / `npm build`，容易继承 XCodeAgent Backend 自身环境，例如错误继承 JDK 版本。

3. **验证目标与真实开发环境不一致。**  
   Sandbox 中能够通过并不等于当前真实 Workspace 能够正常运行；反过来，真实工程本身已经能够启动时，再维护一套独立 Runtime 验证价值有限。

4. **实现复杂度高。**  
   Sandbox、临时目录、Workspace copy、命令执行、超时、日志、环境解析等能力与模板更新本身职责并不匹配。

因此，本次改造将模板增量更新后的基础运行态验收统一收敛为：

> **模板变更 Apply 到当前真实 Workspace 后，强制重启当前应用；只要更新后的应用能够重新构建、启动并达到 Ready，即认为模板运行态验收通过。**

---

## 1.2 现有代码中的关键事实

### 1.2.1 Template Reconcile 的事务边界已经基本正确

当前主流程本质上已经是：

```text
Template Engine
    ↓
StrategyUpdatePackage
    ↓
persist PREPARED Attempt
    ↓
执行 Modification Strategy
    ↓
WorkingCopy
    ↓
Apply 到真实 Workspace
    ↓
VALIDATING
    ↓
Validation Plan
    ↓
成功后
    ↓
写入 TemplateState
    ↓
Attempt = SUCCEEDED
```

当 Apply 或 Validation 失败时，通过 Working Copy 恢复本次修改。

因此，本次不需要推翻 Reconcile 事务模型，只需要替换 Validation 的运行态实现。

### 1.2.2 `runtime_v2.py` 不属于要删除的“项目 Runtime”

`Backend/app/services/template_reconcile/runtime_v2.py` 虽然命名中带 Runtime，但其职责并不是 Java/Node/Maven 执行环境。

它承担的是：

- Reconcile Attempt 持久化；
- Workspace 单写锁；
- Current Attempt Pointer；
- Crash Recovery；
- State Digest 恢复判断；
- Roll-forward。

因此：

> **`runtime_v2.py` 必须保留。**

它是 Template Reconcile 的事务 Runtime，而不是项目 Build Runtime。

### 1.2.3 现有 Project Launcher 已经覆盖核心验收能力

现有项目启动链路已经能够完成：

#### Backend

```text
停止旧 Backend
    ↓
Maven clean install
    ↓
查找 JAR
    ↓
java -jar
    ↓
等待 Backend Ready
```

因此模板增量更新没有必要再单独执行：

```text
mvn test
mvn package
```

#### Frontend

现有 Frontend Launcher 已经能够：

```text
识别 npm/pnpm
    ↓
安装依赖
    ↓
启动 dev server
    ↓
HTTP / Log Ready Check
```

但有一个关键差异：

> 当前 Frontend Launcher 如果发现已有健康 Dev Server，会直接复用，而不是重新启动。

因此模板更新验收不能简单原样调用 `launch_project_preview()`，需要给现有 Launcher 增加明确的 **强制重启模式**。

---

## 1.3 本次改造的总体原则

本次方案遵循以下原则：

1. **不建设新的 Runtime Framework。**
2. **不建设新的 Sandbox Framework。**
3. **不在 Template Reconcile 中直接执行 Maven/npm/pnpm。**
4. **真实项目启动统一交给现有 Project Launcher。**
5. **Template Reconcile 只负责事务编排。**
6. **保留 WorkingCopy、Attempt、Recovery、Roll-forward。**
7. **TemplateState 仍然只能在验收成功后提交。**
8. **模板更新成功后，项目保持运行。**
9. **模板更新失败后，恢复 Workspace；如果更新前项目正在运行，则恢复旧版本运行态。**
10. **模板更新不能影响 UI Design Preview 等非 standard preview Runtime。**

---

## 1.4 最终目标架构

重构后职责收敛为：

```text
Template Engine
│
├── 计算 next TemplateState
├── 生成 Modification Strategy
└── 生成必要的结构性 Postcondition


Template Reconcile
│
├── Attempt
├── Lock
├── Strategy Execution
├── WorkingCopy
├── Apply / Rollback
├── Static Postcondition
├── 调用 Project Launcher
└── TemplateState Commit


Project Launcher
│
├── 项目运行状态识别
├── Backend build
├── Backend restart
├── Frontend restart
└── Ready Check


runtime_v2.py
│
├── Attempt Persistence
├── Single Writer
├── Recovery
└── Roll-forward
```

最终不再存在：

```text
Template Reconcile Sandbox
Template Build Runtime
Template Validation Runtime
第二套 Maven Executor
第二套 npm/pnpm Executor
```

---

## 1.5 新的 Template Reconcile 主流程

目标流程：

```text
TemplateEngine.update()
        ↓
StrategyUpdatePackage
        ↓
保存 PREPARED Attempt
        ↓
执行 Strategy
        ↓
Apply WorkingCopy 到真实 Workspace
        ↓
执行静态 Postcondition
        ↓
读取项目更新前运行状态
        ↓
强制重启 standard project preview
        ↓
Backend build + start + Ready
        ↓
Frontend restart + Ready
        ↓
启动成功
        ↓
Commit TemplateState
        ↓
Attempt SUCCEEDED
```

从原来的：

```text
Apply
  ↓
Sandbox / Maven / npm Validation
  ↓
Commit
```

调整为：

```text
Apply
  ↓
Static Validation
  ↓
Restart Project
  ↓
Ready
  ↓
Commit
```

---

## 1.6 验收定义

模板增量更新成功不再定义为：

> 在独立 Sandbox 中执行 npm/maven 命令通过。

而定义为：

> Strategy 正确落盘、结构性 Postcondition 成立，并且更新后的真实应用能够重新构建并启动到 Ready。

最终 Acceptance Gate：

```text
1. Strategy Apply Success
        AND
2. Static Postcondition Success
        AND
3. Backend Launch Success
        AND
4. Frontend Launch Success
```

只有全部成功：

```text
TemplateState = nextTemplateState
Attempt = SUCCEEDED
```

---

## 1.7 Project Launcher 改造方案

### 1.7.1 `launch_project_preview()` 增加强制重启语义

修改：

```text
Backend/app/services/project_launcher.py
```

现有：

```python
launch_project_preview(
    workspace_path,
    *,
    on_progress=None,
)
```

扩展为：

```python
launch_project_preview(
    workspace_path,
    *,
    on_progress=None,
    force_restart=False,
)
```

正常 Build / Preview：

```python
launch_project_preview(workspace)
```

行为保持不变。

Template Reconcile：

```python
launch_project_preview(
    workspace,
    force_restart=True,
)
```

---

### 1.7.2 Backend 不增加第二套启动策略

Backend Launcher 当前本身已经执行：

```text
停止旧 Backend
    ↓
mvn clean install
    ↓
java -jar
    ↓
Ready Check
```

因此 Template Reconcile 不需要感知：

- Maven；
- Java；
- JDK；
- `JAVA_HOME`；
- JAR 路径。

这些全部继续封装在 Backend Launcher 中。

---

### 1.7.3 Frontend 必须支持真正重启

修改：

```text
Backend/app/services/frontend_project_launcher.py
```

扩展：

```python
launch_frontend_project(
    workspace,
    ...,
    force_restart: bool = False,
)
```

默认：

```text
force_restart = false
    ↓
发现已有健康 Frontend
    ↓
继续复用
```

模板验收：

```text
force_restart = true
    ↓
跳过 _reuse_ready_server()
    ↓
停止旧 standard frontend
    ↓
重新安装/检查依赖
    ↓
重新启动 dev server
    ↓
Ready Check
```

这样可以保证：

> 模板更新后的 Frontend 真正经历了一次从零重新启动。

---

## 1.8 项目运行状态识别

建议在：

```text
Backend/app/services/project_launcher.py
```

增加：

```python
inspect_project_preview(workspace)
```

返回类似：

```json
{
  "backend": {
    "running": true
  },
  "frontend": {
    "running": true
  },
  "running": true
}
```

该函数只允许：

- 查询 PID；
- 检查 Process Alive；
- 必要时执行 Readiness Probe。

不得：

- 启动项目；
- 停止项目；
- 修改 Workspace。

其作用有两个：

1. 给用户提供正确提示；
2. 模板更新失败后判断是否需要恢复旧项目运行态。

该状态属于本次 Reconcile Runtime Context，不进入 TemplateState。

---

## 1.9 已启动与未启动项目的处理

### 场景 A：更新前项目已经运行

流程：

```text
读取 running state
    ↓
Apply Template
    ↓
Static Validation
    ↓
Force Restart
    ↓
Ready
    ↓
Commit
```

用户提示：

```text
模板已更新，正在重新启动项目进行验证。
```

成功后：

```text
项目保持 running
```

---

### 场景 B：更新前项目未运行

不应直接跳过启动验收。

提示：

```text
当前项目未运行，将启动项目验证本次模板更新。
```

然后：

```text
Apply Template
    ↓
Static Validation
    ↓
launch_project_preview(force_restart=True)
    ↓
Ready
    ↓
Commit
```

成功后仍保持项目运行。

---

## 1.10 Static Validation 的职责

`validation_v2.py` 不完全删除，而是收敛为纯静态验证。

保留：

```text
CAPABILITY_POSTCONDITION
FILE_EXISTS
STRUCTURE_CHECK
JSON_STRUCTURE_CHECK
```

其目标是回答：

> Modification Strategy 是否真正产生了预期的模板结构。

不再负责回答：

> 项目是否能够构建和运行。

后者统一交给 Project Launcher。

建议将：

```python
execute_reconcile_validation_v2(...)
```

重命名/收敛为：

```python
execute_reconcile_postconditions_v2(...)
```

或：

```python
execute_static_validation_plan_v2(...)
```

---

## 1.11 删除的 Runtime/Sandbox 能力

从：

```text
Backend/app/services/template_reconcile/validation_v2.py
```

删除实际执行层：

```text
NPM_BUILD executor
NPM_TEST executor
MAVEN_TEST executor
MAVEN_PACKAGE executor

Sandbox workspace preparation
Sandbox workspace copy
Sandbox temp directory
command subprocess abstraction
command timeout
Sandbox-specific log handling
```

注意：

> 本次删除的是执行能力，不是立即破坏 V2 Wire Protocol。

---

## 1.12 Protocol 兼容策略

当前 `StrategyUpdatePackageV2` 仍可能包含：

```text
NPM_BUILD
NPM_TEST
MAVEN_TEST
MAVEN_PACKAGE
executionMode
SANDBOX
```

本轮不建议立即从 Protocol 中删除。

第一阶段兼容策略：

```text
Template Engine 暂时仍可返回旧字段
        ↓
XCodeAgent Parser 继续接受
        ↓
Template Reconcile 忽略 command validation
        ↓
统一执行 project restart acceptance
```

待 Template Engine 后续停止生成这些字段，再升级新的 Protocol 版本并正式删除。

因此原则是：

> **先删除执行能力，再演进协议。**

---

## 1.13 TemplateState 提交边界

必须继续保持：

```text
Apply Workspace
    ↓
Static Check
    ↓
Project Restart
    ↓
Ready
    ↓
TemplateState.write(nextState)
```

不能调整为：

```text
Apply
    ↓
TemplateState.write()
    ↓
Restart
```

否则项目启动失败时可能出现：

```text
Workspace 已回滚
TemplateState 却已经指向新模板
```

当前 State 延迟提交原则必须完整保留。

---

## 1.14 启动失败时的事务处理

假设：

```text
Apply Template
    ↓
Backend Build Success
    ↓
Backend Start Success
    ↓
Frontend Start Failed
```

不能只恢复文件。

正确顺序：

```text
Project Launch Failed
        ↓
停止本次启动的新版本 standard preview
        ↓
restore_working_copy_v2()
        ↓
Workspace 恢复旧模板
        ↓
TemplateState 保持旧值
        ↓
如果更新前项目正在运行
        ↓
重新启动旧版本项目
        ↓
Attempt FAILED
```

即：

```text
Runtime cleanup
    ↓
File rollback
    ↓
Previous runtime restore
```

---

## 1.15 两类失败恢复

### 更新前项目正在运行

失败后：

```text
停止失败的新版本
    ↓
恢复旧文件
    ↓
重新启动旧项目
```

提示：

```text
模板更新后的项目启动失败，已回滚本次模板修改并恢复原项目运行。
```

### 更新前项目未运行

失败后：

```text
停止失败的新版本
    ↓
恢复旧文件
    ↓
保持 stopped
```

提示：

```text
模板更新后的项目启动失败，已回滚本次模板修改。
```

---

## 1.16 Crash Recovery 与 Roll-forward

保留 `runtime_v2.py` 的：

- Attempt；
- Digest；
- Run Gate；
- `REPLAY`；
- `FINALIZE`；
- Roll-forward。

### REPLAY

TemplateState 尚未提交：

```text
重新 Apply
    ↓
Static Check
    ↓
Restart Project
    ↓
Commit
```

直接复用新的 `_execute()`。

### FINALIZE

如果：

```text
TemplateState == nextState
```

说明状态已经正式提交。

此时：

```text
确保新版本项目能够运行
    ↓
成功 → Attempt SUCCEEDED
```

如果启动失败：

> 不允许再按照普通失败路径回滚 Workspace / TemplateState。

仍遵循 Roll-forward 原则。

---

## 1.17 Attempt Phase

第一阶段不修改持久化 Phase：

```text
PREPARED
APPLYING
VALIDATING
COMMITTING_STATE
SUCCEEDED
FAILED
RECOVERY_REQUIRED
```

其中：

```text
VALIDATING
```

新的语义变为：

> 静态结构验证 + 真实项目启动验收。

更细粒度信息通过 Event 表达：

```text
STATIC_VALIDATION_STARTED
STATIC_VALIDATION_COMPLETED

PROJECT_RESTART_STARTED
BACKEND_RESTART_COMPLETED
FRONTEND_RESTART_COMPLETED
PROJECT_RESTART_COMPLETED
```

避免为了本次重构引入 Durable Phase 兼容问题。

---

## 1.18 前端展示

继续复用现有：

```text
Frontend/src/renderer/src/components/
AiChatPanel/components/WorkflowRunCard/
TemplatePreparingCard.tsx
```

不新增独立 Runtime 页面。

建议事件/日志文案：

```text
正在应用模板更新
模板文件更新完成
正在检查模板结构

项目当前正在运行，正在重新启动项目进行验证
```

或：

```text
当前项目未运行，将启动项目验证本次模板更新
```

随后：

```text
正在构建并启动后端
后端启动成功
正在重新启动前端
前端启动成功
模板更新验证通过
```

失败：

```text
模板更新后的项目启动失败，正在回滚本次模板修改
模板文件已恢复
```

如果之前项目正在运行：

```text
已恢复原项目运行
```

---

# 第二章 分阶段实施步骤

## 2.1 阶段一：Project Launcher 能力补齐

### 目标

不修改 Template Reconcile 主链路，先让现有 Project Launcher 具备模板验收所需的强制重启能力。

本阶段完成后应能够单独证明：

> XCodeAgent 可以在当前真实 Workspace 上执行一次可靠的完整重启，而不需要任何 Sandbox Runtime。

### 改动一：Frontend Launcher 增加 `force_restart`

文件：

```text
Backend/app/services/frontend_project_launcher.py
```

新增：

```python
launch_frontend_project(
    workspace,
    ...,
    force_restart: bool = False,
)
```

规则：

```text
force_restart=False
→ 保持现有 server reuse 行为

force_restart=True
→ 禁止 reuse
→ 停止旧 standard frontend
→ 重新启动
→ Ready Check
```

### 改动二：Project Launcher 透传 `force_restart`

文件：

```text
Backend/app/services/project_launcher.py
```

扩展：

```python
launch_project_preview(
    workspace_path,
    *,
    on_progress=None,
    force_restart=False,
)
```

普通 Workflow 仍然：

```python
launch_project_preview(workspace)
```

Template Reconcile 后续使用：

```python
launch_project_preview(
    workspace,
    force_restart=True,
)
```

### 改动三：增加 `inspect_project_preview`

文件：

```text
Backend/app/services/project_launcher.py
```

新增：

```python
inspect_project_preview(workspace)
```

只读取：

- backend running；
- frontend running；
- overall running。

不得产生副作用。

### 改动四：确认 standard preview 独立停止能力

模板更新只允许操作：

```text
standard Backend
standard Frontend
```

不能影响：

```text
UI Design Preview
其他设计态 Runtime
```

如果当前 stop 能力会一起关闭其它 Runtime，则增加：

```text
stop_standard_project_preview()
```

或者将 standard runtime replacement 封装到 launcher 内部。

### 测试

至少覆盖：

```text
force_restart=False
→ healthy frontend 被复用

force_restart=True
→ healthy frontend 不被复用

force_restart=True
→ 旧 frontend 被停止

force_restart=True
→ frontend 真正重新启动

Backend 仍执行现有 build + restart + Ready

inspect_project_preview()
→ 无副作用
```

### 阶段验收

满足：

- 普通 Launch 行为不变；
- `force_restart=True` 可完整重启；
- Frontend 不错误 reuse；
- Backend/Frontend Ready 正常；
- 不影响 UI Design Preview。

---

## 2.2 阶段二：Validation 能力收敛

### 目标

将 Template Reconcile Validation 从：

```text
结构验证
+
Maven/npm command validation
+
Sandbox
```

收敛为：

```text
纯静态 Postcondition
```

暂时不改变正式 Reconcile 成功路径。

### 改动一：保留静态验证

保留：

```text
CAPABILITY_POSTCONDITION
FILE_EXISTS
STRUCTURE_CHECK
JSON_STRUCTURE_CHECK
```

### 改动二：停止执行 Command Validation

Template Reconcile 不再实际执行：

```text
NPM_BUILD
NPM_TEST
MAVEN_TEST
MAVEN_PACKAGE
```

也不再执行：

```text
REAL_WORKSPACE
SANDBOX
```

两套命令运行模式。

### 改动三：拆除 Sandbox 实现

文件：

```text
Backend/app/services/template_reconcile/validation_v2.py
```

移除：

```text
Sandbox 临时目录
Workspace copy
Sandbox command runner
Maven subprocess executor
npm/pnpm subprocess executor
command timeout
Sandbox-specific logs
```

### 改动四：函数语义收敛

建议将：

```python
execute_reconcile_validation_v2(...)
```

调整为：

```python
execute_reconcile_postconditions_v2(...)
```

或：

```python
execute_static_validation_plan_v2(...)
```

### 改动五：保留 V2 Protocol 解析兼容

继续允许 Parser 接收：

```text
NPM_BUILD
NPM_TEST
MAVEN_TEST
MAVEN_PACKAGE
executionMode
SANDBOX
```

但不执行。

### 测试

覆盖：

```text
Static Postcondition 正常执行

Command Validation 输入不启动 subprocess

SANDBOX executionMode 不创建临时 Workspace

旧 StrategyUpdatePackageV2 仍能正常解析
```

### 阶段验收

满足：

- Reconcile 不再创建 Sandbox；
- 不再运行 Maven/npm；
- Static Postcondition 正常；
- V2 Protocol 不破坏。

---

## 2.3 阶段三：Template Reconcile 接入真实启动验收

### 目标

正式将 Template Reconcile 从：

```text
Apply
→ Command Validation
→ Commit
```

切换为：

```text
Apply
→ Static Postcondition
→ Force Restart
→ Ready
→ Commit
```

### 改动一：Apply 前记录项目运行状态

在 `_execute()` Apply 前：

```python
pre_launch_state = inspect_project_preview(root)
```

只作为当前 Attempt 的 Runtime Context。

### 改动二：重构 `_validate()`

新流程：

```text
Static Postcondition
    ↓
Passed
    ↓
launch_project_preview(force_restart=True)
    ↓
Backend Ready
    ↓
Frontend Ready
```

只有全部成功：

```text
Validation Passed
```

### 改动三：Reconcile 不感知技术栈命令

`TemplateReconcileService` 中不得重新出现：

```text
mvn
java
npm
pnpm
vite
react-scripts
```

只允许调用 Project Launcher。

### 改动四：保持 TemplateState 延迟提交

继续严格：

```text
Apply
↓
Static Check
↓
Restart
↓
Ready
↓
TemplateState.write(nextState)
```

### 改动五：成功后的项目状态

无论模板更新前是否运行：

```text
模板更新成功
→ 项目最终保持 running
```

### 测试

覆盖：

```text
原本 running
→ 更新
→ restart success
→ commit

原本 stopped
→ 更新
→ launch success
→ commit

Static Validation fail
→ 不启动
→ 不 commit

Backend launch fail
→ 不 commit

Frontend launch fail
→ 不 commit
```

### 阶段验收

Template Reconcile 正式完成：

> 从独立 Runtime 验收切换到真实工程启动验收。

---

## 2.4 阶段四：失败回滚与运行态恢复

### 目标

补齐失败事务，使：

```text
Workspace
TemplateState
Project Runtime
```

尽量恢复到模板更新之前状态。

### 改动一：失败回滚顺序调整

项目启动失败后：

```text
停止失败的新版本 standard preview
        ↓
restore_working_copy_v2()
        ↓
Workspace 恢复旧版本
        ↓
TemplateState 保持旧值
```

### 改动二：恢复更新前运行态

如果：

```text
pre_launch_state.running = true
```

则：

```text
restore old files
    ↓
launch old project
```

如果：

```text
pre_launch_state.running = false
```

则：

```text
restore old files
    ↓
keep stopped
```

### 改动三：抽象恢复函数

建议增加：

```python
_restore_after_failed_acceptance(...)
```

职责：

```text
stop failed runtime
restore working copy
restore previous runtime state
record recovery evidence
```

### 改动四：细化错误码

至少区分：

```text
POSTCONDITION_FAILED

PROJECT_LAUNCH_FAILED

ROLLBACK_RESTORE_FAILED

PREVIOUS_RUNTIME_RESTORE_FAILED
```

不要继续全部包装为：

```text
VALIDATION_FAILED
```

### 改动五：继续复用 WorkingCopy

继续使用：

```text
WorkingCopyStoreV2
restore_working_copy_v2
```

不新增：

```text
git reset
git worktree rollback
sandbox rollback
```

### 测试

覆盖：

```text
Backend fail
→ 新进程清理
→ workspace rollback

Frontend fail
→ 已启动 Backend 被停止
→ workspace rollback

之前 running
→ rollback 后旧项目恢复运行

之前 stopped
→ rollback 后保持 stopped

旧项目恢复失败
→ 同时保留原始错误与恢复错误
```

### 阶段验收

模板更新具备：

```text
文件事务
+
TemplateState 事务
+
运行态恢复
```

完整闭环。

---

## 2.5 阶段五：Recovery、协议与历史代码清理

### 目标

主链路稳定后，再处理 Crash Recovery、Protocol 演进和历史代码删除。

### 改动一：回检 Crash Recovery

继续保留：

```text
Backend/app/services/template_reconcile/runtime_v2.py
```

以及：

```text
Attempt
Workspace Single Writer
Digest
REPLAY
FINALIZE
Roll-forward
```

### 改动二：调整 REPLAY

TemplateState 尚未提交：

```text
REPLAY
↓
重新 Apply
↓
Static Postcondition
↓
Force Restart
↓
Commit
```

直接复用新的 `_execute()`。

### 改动三：调整 FINALIZE

如果：

```text
TemplateState == nextState
```

则：

```text
确保项目可以运行
↓
补齐 Attempt SUCCEEDED
```

失败时：

> 禁止回滚已经 Commit 的 TemplateState / Workspace。

### 改动四：保持现有 Durable Phase

继续保留：

```text
PREPARED
APPLYING
VALIDATING
COMMITTING_STATE
SUCCEEDED
FAILED
RECOVERY_REQUIRED
```

细粒度状态只通过 Event 增加。

### 改动五：调整 TemplatePreparingCard 日志

继续复用：

```text
TemplatePreparingCard.tsx
```

增加：

```text
正在应用模板更新
模板文件更新完成
正在检查模板结构
正在重新启动项目进行验证
后端启动成功
前端启动成功
模板更新验证完成
```

失败时：

```text
项目启动失败，正在恢复本次模板修改
模板文件已恢复
原项目已恢复运行
```

### 改动六：推动 Template Engine 停止生成 Command Validation

等 XCodeAgent 已稳定忽略旧 command validation 后，再修改 Template Engine 停止输出：

```text
NPM_BUILD
NPM_TEST
MAVEN_TEST
MAVEN_PACKAGE
SANDBOX
```

### 改动七：后续升级 Protocol

待两端稳定后再考虑 `StrategyUpdatePackage V3`，正式删除：

```text
Command Validation Types
executionMode
SANDBOX
```

### 改动八：删除废弃代码

确认无调用后删除：

```text
Sandbox helpers
Command runners
Sandbox copy
Maven validation executor
npm validation executor
相关测试
```

明确不能删除：

```text
runtime_v2.py
WorkingCopy
Attempt
Recovery
Digest
Run Gate
```

### 测试

覆盖：

```text
REPLAY 可以重新完成 Restart Acceptance

FINALIZE 不执行 Workspace rollback

State 已 Commit + Attempt 写失败
→ 恢复后仍 Roll-forward

历史 V2 Package 可以正常读取

前端刷新后仍能恢复 TemplatePreparation 状态
```

---

# 第三章 实施顺序与里程碑

建议严格按照以下顺序推进：

```text
阶段一
Project Launcher 补齐 force restart
        ↓
阶段二
删除 Sandbox / Command Validation 执行能力
        ↓
阶段三
Template Reconcile 接入真实项目启动验收
        ↓
阶段四
补齐失败回滚和旧运行态恢复
        ↓
阶段五
Recovery 回检 + Protocol 演进 + 历史代码清理
```

各阶段定位：

| 阶段 | 核心成果 | 是否影响主流程 |
|---|---|---|
| 阶段一 | Launcher 支持强制重启 | 否 |
| 阶段二 | Sandbox / Command Validation 下线 | 小 |
| 阶段三 | 模板验收切换为真实项目启动 | 是 |
| 阶段四 | 失败事务与运行态恢复完整 | 是 |
| 阶段五 | Recovery、协议和历史代码最终收敛 | 小 |

真正的主链路切换点是：

```text
阶段三
```

因此推荐：

```text
阶段一 + 阶段二
→ 先独立合入并验证稳定

阶段三
→ 再切换 Template Reconcile 主验收链路

阶段四
→ 补齐失败事务

阶段五
→ 最后清理历史协议和废弃代码
```

---

# 第四章 Definition of Done

本次重构最终完成必须同时满足：

1. Template Reconcile 不再创建 Sandbox Workspace。
2. Template Reconcile 不再直接运行 `npm build/test`、`pnpm build/test`、`mvn test/package`。
3. Backend 模板验收统一复用现有 Backend Launcher 的真实构建、启动和 Ready Check。
4. Frontend 模板验收必须真正重启，不允许因为已有健康 Server 而直接 reuse。
5. 普通 `launch_project` 行为保持不变，仍允许复用健康 Frontend Server。
6. TemplateState 必须在项目启动验收成功之后才提交。
7. 启动失败必须恢复本次模板修改。
8. 更新前项目正在运行时，失败后恢复旧版本运行态。
9. 更新前项目未运行时，失败后保持未运行。
10. 模板更新成功后项目保持 running。
11. Template Reconcile 不影响 UI Design 等非 standard preview Runtime。
12. `runtime_v2.py` 的 Attempt、Lock、Crash Recovery、Roll-forward 完整保留。
13. 历史 `StrategyUpdatePackageV2` 仍可以解析，不因删除 Sandbox Execution 而破坏现有协议。
14. `TemplatePreparingCard` 可以展示“重启项目进行验证”的过程和启动失败原因。
15. Reconcile E2E 覆盖 Apply、Restart、Rollback、Retry、REPLAY、FINALIZE。

---

# 第五章 最终架构原则

本次重构完成后，Template Update 的职责可以最终收敛为：

```text
Template Update
=
ChangeSet / Strategy Apply
+
Static Postcondition
+
Project Restart
+
Startup Ready Validation
+
State Commit / Rollback
```

最终职责边界：

> **模板负责修改当前工程；Project Launcher 负责证明修改后的真实工程仍然可以运行；Template Reconcile 负责把两者组织在一个可回滚、可恢复的事务中。**

不再为模板增量更新维护独立的：

```text
RuntimeSpec
ResolvedRuntime
RuntimeResolver
RuntimeRegistry
LocalRuntimeExecutor
SandboxExecutor
Validation Sandbox
```

这既复用了 XCodeAgent 现有项目启动能力，也保留了 `template_refactor` 分支现有 Reconcile V2 中最有价值的 WorkingCopy、Attempt、Recovery 和 Roll-forward 机制。
