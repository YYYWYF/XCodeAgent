# XCodeAgent Builtin 权限 Schema → Seed 自动编排与恢复改造方案（Codex 实施版）

> 目标仓库：`https://github.com/YYYWYF/XCodeAgent.git`，分支 `dev_agent`  
> 代码基线（本次核验）：`9f73f6054edd4222d440015fa53c17e2f77613b7`（2026-11-06）  
> SaaS 对照：`https://github.com/Bettetman/managed-database-service.git`，`main` 提交 `0e44f7344b5b532deb50cf953db659c6de13fd37`  
> 目标：**用户无需手动刷新 Schema 结果或点击“继续初始化权限数据”，由 XCodeAgent 自动完成建表、状态查询、结构核验、Seed 写入、结果核验；中断后安全恢复。**

## 0. 给 Codex 的执行约束

1. 仅修改 XCodeAgent；不修改 SaaS、模板服务或非 Builtin 数据源的既有流程。SaaS Seed 已提供 `POST /data-operations/batch`、`GET /data-operations/{operationId}`、`GET /data-operations/by-key/{idempotencyKey}`，直接复用。
2. **不得把两个 SaaS POST 合并，不得把 GET/refresh 改成隐式重发 POST。** Schema 与 Seed 保持各自持久化操作身份，所有等待只发 GET。
3. 沿用现有 Schema/Seed 校验、幂等、资源归属检查、表名投影、原操作证据，不删除或放松安全门禁；`UNKNOWN`、指纹漂移、绑定变化、损坏状态一律 fail closed。
4. 建表回执 `202`、Seed 回执 `202` 都只是“受理”，不是成功。只有四表结构核验和 Seed 终态/结果核验全部成功，才能标记 `ready`。
5. Graph 既有策略不变：确认 DAG 的权限初始化异常不阻断代码 Build；但**不能**把 Graph 节点 completed 当成数据库已就绪，预览仍必须满足 `seed_ready(...)`。
6. 后端异步推进不能依赖 React 组件仍挂载；进度查询必须纯只读；工作区关闭、Backend 重启、再次打开时能基于原状态恢复。
7. 任何 UI、日志和持久的公开摘要都不得暴露 Subject、初始管理员列表、Seed 行数据、SQL 正文、连接凭据。
8. 正式实现前先回检以下文件的现状并保持可运行的测试基线，不要重写既有注册和权限初始化链路。

## 1. 已确认的现状及断点

| 文件 | 当前行为 | 需调整的点 |
| --- | --- | --- |
| `Backend/app/graph/nodes/builtin_authorization_bootstrap.py` | 校验 Build DAG，调用 `run_builtin_authorization_bootstrap()`，然后 Graph 继续 Build | 在校验通过后改为启动或接管**独立自动编排任务**；立即返回真实状态摘要，不等待整个远端异步操作完成 |
| `Backend/app/services/builtin_authorization_bootstrap.py` | 建表 POST 202 后返回“请点击刷新”；`_query_operation()` 每次只 GET 一次，成功只调用 `_ready()` | 保留其一次操作/查询/结构核验能力，由编排器重复安全调用；不允许自身无限递归或自动重发 |
| `Backend/app/services/builtin_authorization_seed_runtime.py` | 可按 `operationId` 或 `idempotencyKey` 查原操作；成功校验后保存 `seed_ready`；POST 202 后返回“请刷新” | 复用其一次受理/单次查询能力，编排器在 Schema 就绪后自动触发 |
| `Backend/app/services/builtin_authorization_patch.py` | `run_patch` 只在当前请求内 Schema 已就绪时接续 Seed；`refresh_patch` 仅查询 | 让 `run_patch` 调用同一个编排器（幂等启动）；保留 `refresh_patch` 纯查询兜底 |
| `Backend/app/protocols/builtin_authorization.py` | AG-UI `get/run_patch/refresh_patch/refresh` | `get` 返回统一任务状态；`run_patch` 幂等启动；已有 `refresh` 继续**严格只读** |
| `Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/AuthorizationBootstrapPanel.tsx` | 展示手动刷新、继续 Seed | 正常运行仅展示自动执行状态；异常才显示人工恢复入口 |
| `Frontend/src/renderer/src/components/AiChatPanel/components/WorkspaceDebugDock/BuiltinPatchCard.tsx` | Schema/Write/Verify 三段 UI，但每次需要用户触发 | 复用三段 UI 展示自动任务，不新增第二个初始化通道 |
| `Backend/app/services/project_launcher.py` | Builtin 预览要求 `seed_ready()` | 保持门禁；把未就绪提示改为“正在自动初始化/待核对”，不能绕过 |

现有持久化路径：`.devagentstudio/runtime/builtin-authorization-bootstrap/state.json`，通过 `authorization_workspace_projector.RUNTIME` / `read_record(...)` 访问。

**关键细节：** `builtin_authorization_seed_runtime._verify_current()` 将整份 `state.json` 与请求前快照比较。因此编排阶段、轮询时间、worker 心跳**不要写入这个文件**，否则会被误判为 Seed 输入并发变化。新增独立状态文件。

## 2. 目标用户旅程

```
用户确认 Build DAG / 开始开发
          |
          v
    Builtin 权限初始化 [自动]
          |
          +-- 1. 检查/初始化四张权限表
          |       -> 首次受理 CREATE_BATCH（最多 1 次）
          |       -> 自动 GET 原 operationId
          |       -> SUCCEEDED + tables/check 严格结构核验
          |
          +-- 2. 初始化权限数据
          |       -> 先按原 ID/幂等键 GET
          |       -> 确认未受理时允许首次 POST
          |       -> 自动 GET 原 ID/幂等键
          |
          +-- 3. 回读核验
                  -> SaaS verified=true + 已有指纹/表名/数量核验
                  -> schemaReady=true && seedReady=true
                  -> ready

代码 Build 可以并行继续；预览与权限运行态仍以 seedReady 为唯一准入事实。
```

UI 仅展示一个“权限数据库初始化”任务和三个子步骤：`检查并创建权限表`、`写入权限数据`、`核验初始化结果`。正常无需用户点击刷新/继续。异常统一显示“待核对”的原因与可执行操作。

## 3. 后端设计：受管编排器（P0）

### 3.1 新增 `Backend/app/services/builtin_authorization_orchestrator.py`

提供明确的接口（实际可采用类封装，但语义保持一致）：

```python
ensure_builtin_authorization_task(workspace: str, *, source: str) -> dict
get_builtin_authorization_task(workspace: str) -> dict        # 纯本地只读
resume_builtin_authorization_task(workspace: str) -> dict     # 仅安全恢复/接管，不表示批准未知重放
stop_builtin_authorization_task(workspace: str) -> None      # 删除/退出时停止本地 worker
```

- `ensure`：规范化工作区；核验 Builtin + authorization.enabled + REGISTERED 绑定 + 已确认 TechnicalPlan + 当前已确认/有效的 Build DAG；如果 Seed 已就绪直接返回；否则去重启动一个后台 worker，返回 `running/waiting` 摘要。
- `tick`：每次只推进一个安全状态转换，利用现有 Schema/Seed 单次函数，不另写 SaaS HTTP 或第二套 DDL/Seed 编译器。
- 每次进入远端请求前复核 `datasourceKey/datasourceId`、DDL 指纹、Manifest、初始管理员输入、权限开关及 DAG 有效性；变化时停止并记录 `inputs_changed`，不得使用旧输入自动写入。
- 完成 Schema 后**立即在下一步自动进入 Seed**，不能再向 UI 返回 `continue` 作为必经操作。
- worker 必须归属于 workspace，防止两次 Build、独立补丁和窗口重复触发多个初始化任务。
- 不要从 Graph 的同步节点直接调用 `asyncio.create_task()`（那里可能运行在线程中）；使用由应用生命周期管理的受界线程/执行器或复用现有适当的 worker 基础设施，避免裸 daemon 线程无托管地退出。

**推荐复用（非重写）原子函数：**

1. Schema 一次动作：`run_builtin_authorization_bootstrap(workspace, plan, schema_only=True)`。它会复用已就绪 Schema、查询原批次，或首次安全受理建表。其 `manual_required` 可能仅表示 `RUNNING`，编排器必须结合状态判断，不能把 status 字符串当失败。
2. 确认 `schema_ready(root, application)` 后：`run_builtin_authorization_bootstrap(workspace, plan, schema_only=False)` 进入已有的 `run_builtin_authorization_seed()`；后续根据原 `seedOperationId` 或 `idempotencyKey` 自动 GET。
3. `schema_only=True` 模式不会写 Seed；已有 `refresh_builtin_authorization()` / `refresh_builtin_authorization_seed()` 继续保持纯查询、不触发后续 POST。自动推进只在编排器的 tick 内发生。
4. 如果复用函数的结果不足以准确区分 `RUNNING`、已核验、明确失败、结果未知，应向内部返回对象增加结构化字段，而非依赖中文字面消息判断。

### 3.2 任务状态机

建议独立状态文件：

`.devagentstudio/runtime/builtin-authorization-bootstrap/orchestration.json`

示例（**新合同，不是现有文件**）：

```json
{
  "schemaVersion": 1,
  "status": "running",
  "stage": "schema",
  "datasourceKey": "12ab34cd56ef",
  "datasourceId": 2,
  "ddlFingerprint": "<existing fingerprint>",
  "manifestFingerprint": "<confirmed manifest fingerprint>",
  "schemaOperationId": "12345",
  "seedOperationId": null,
  "failureCategory": null,
  "updatedAt": "2026-11-06T08:00:00Z",
  "lastCheckedAt": "2026-11-06T08:00:00Z"
}
```

- `status`: `pending | running | waiting | ready | needs_attention | failed | cancelled`。
- `stage`: `schema | seed | verify | done`。
- 实际操作身份证据、验证事实仍以现有 `state.json` 为**唯一权威来源**；`orchestration.json` 只是可重建的编排/展示快照，不得用于认定 Schema 或 Seed 成功。
- 写入必须原子化、路径受限，拒绝 symlink 和损坏结构；不要在编排文件中保存 SQL/Seed payload/Subject。
- 读取状态时可以根据 `state.json` 重建公开摘要，但不可以因为 `orchestration.json` 丢失就推断“首次提交”。

| 权威状态 | 编排行为 | 是否允许 POST |
| --- | --- | --- |
| Schema 未受理、无阻断历史，输入确认 | 检查并受理一次建表 | 仅首次 Schema POST |
| Schema 有原 ID 且 `RUNNING` | 自动 GET 原 ID | 否 |
| Schema `SUCCEEDED` 且四表结构核验通过 | 标记 Schema 就绪并自动进入 Seed | 仅当 Seed 符合首次提交条件 |
| Schema `FAILED` | 进入 `needs_attention`，保留逐表错误证据 | 否；必须显式恢复并重新校验 |
| Schema `UNKNOWN` / `submitting` 无可确认身份 | 查询已知原操作/历史，无法确认时人工核对 | 否 |
| Seed 没有本地记录 | 先 GET `by-key`，确认无记录且满足现有安全门禁时首次 POST | 仅首次 Seed POST |
| Seed 有原 ID 且 `RUNNING` | 自动 GET 原 ID | 否 |
| Seed 已提交但回执丢失 | 以持久化原 `idempotencyKey` GET `by-key` | 否 |
| Seed `SUCCEEDED && verified`，指纹/表名/行数复核通过 | `ready` | 否 |
| Seed `FAILED / UNKNOWN / 404` 与身份冲突 | 进入 `needs_attention` 或明确失败，保留证据 | 否 |
| 任意阶段绑定、DDL、Manifest、管理员等变化 | `needs_attention(inputs_changed)` | 否 |

### 3.3 自动轮询与节流

- worker 只在 Backend 存活、目标工作区仍有效时运行；各次 GET 使用现有 HTTP 超时与无重试客户端。**不自动重试任何 POST。**
- 正常 `RUNNING`：建议在任务内按 `2s → 3s → 5s → 10s` 间隔自动 GET，随后封顶 10s。配置为可注入时钟/退避策略，方便测试。
- GET 遇临时网络错误：保留原状态和原操作身份，仅退避后再 GET，既不把网络错误标记为业务失败，也不重发 SQL/DML。
- 单任务连续自动查询可设置如 10 分钟上限；到上限标记 `needs_attention(timeout)`，不自动重放操作。超时不是 SaaS 终态失败；保留安全的只读查询入口。
- 不得在 `builtin_lock` 或跨进程写锁内 `sleep`；每一次读取、校验、POST/GET及状态落盘作为受锁 tick，tick 结束释放锁，等待期间不持锁。
- **多实例安全**：`builtin_lock` 当前只是进程内 `RLock`。项目已依赖 `portalocker`，新增工作区级跨进程短事务锁，在决定首次 POST 与状态落盘的整个 tick 内加锁；在锁内重新读取权威状态。进程内 worker registry 只负责降噪，不能替代跨进程防重复。
- 同一个 workspace 的旧 worker 失效、被取消后不得覆盖新输入或新任务的状态；对写入使用 task generation/identity 核对。

### 3.4 自动恢复（必须交付）

- **首次触发**：`builtin_authorization_bootstrap` Graph 节点通过 DAG 门禁后调用 `ensure(...)`，异步启动，节点立即返回真实 `initializing` / `ready` / `needs_attention` 摘要；Graph `completed` 只表示节点调度结束。
- **再次 Build**：`ensure(...)` 检测到同一任务正在运行则只复用，不再次提交。
- **重新打开应用**：在 `Backend/app/protocols/application_lifecycle.py` 的 `workspace_attach` 成功分支执行非阻塞 `resume(...)`；只接管当前有未完成 Builtin 权限任务的工作区。不要在全局 `lifespan` 无限制扫描文件系统。
- **后端断线重连但工作区未切换**：前端已有 workspace attach/reconnect 入口须确保会调用 `resume(...)`；不要把 `GET` 偷换成启动/写入动作。
- **重启恢复优先级**：先读取 `state.json` → 按已知 Schema ID / Seed ID / Seed 幂等键 GET → 验证绑定与指纹 → 决定只读继续或下一次安全首次提交。即使 worker 上次在 POST 前/后崩溃，也不能因内存状态清空而重复提交。
- **Schema 无 operationId、状态为 submitting/unknown**：只有现有只读结构核验及精确匹配的远端记录能证明成功；否则人工核对，绝不能自动重建。
- **Seed 无 operationId、已有 submitting/unknown 保护**：按原幂等键查询；404 并不足以自动清空保护然后重发，按现有谨慎语义人工核对。
- **应用删除**：在 `Backend/app/protocols/application_deletion.py` 的删除准备/最终收尾流程取消 worker、等待当前安全 tick 退出，然后清理本地 registry；已受理的远端操作不能因本地删除而假定取消，保留必要审计信息。
- **权限关闭/切库**：取消编排并拒绝过期结果写回；不自动撤销远端已执行的 DDL/DML。

## 4. 原 Graph、状态展示与协议改造（P1）

### 4.1 Graph 节点

`Backend/app/graph/nodes/builtin_authorization_bootstrap.py`

- `_build_plan_gate_error()` 必须继续在任何远端操作之前执行；失败保持 Graph `failed`，零副作用。
- 校验通过后 `ensure_builtin_authorization_task(workspace, source="build")`，返回可展示的受管任务摘要；调用必须快速返回，不同步等待 10 分钟轮询。
- 保留 `authorization_bootstrap_result` / `builtin_authorization_bootstrap_result` 输出键和 `executor="builtin_saas"`；新增 `initializationStatus` / `initializationStage`（或受控子对象），仍提供现有 `schemaReady` / `seedReady` 兼容字段。
- 现有 `route_authorization_bootstrap` 仍允许 Build 继续；UI 不能把 `phase: completed` 当作 `schemaReady/seedReady`。
- `select_authorization_bootstrap_node()` 与已核验 `seed_ready()` 路由保持不变。Direct 模式继续走模板 Java Bootstrap。

### 4.2 AG-UI 和独立补丁

`Backend/app/protocols/builtin_authorization.py`、`Backend/app/services/builtin_authorization_patch.py`

- `get`：纯本地只读返回统一编排状态与三个阶段摘要，允许 UI 定期查询。
- `run_patch`：幂等 `ensure(...)`，不能新开一套 Schema/Seed 通道。
- `refresh_patch`、旧 `action="refresh"`：保持原操作的只读 GET 语义；不可连带触发 Seed POST、重新创建表或改动 Graph。
- 如需显式异常恢复，可增加受控 `resume` 动作；`resume` 仅接管/核查，不能表示“批准重建/重放”，明确失败后重新 CREATE 仍保留既有显式恢复安全门禁。
- 仅返回公开字段：`status/stage/schemaReady/seedReady/schemaOperationId/seedOperationId/failureCategory/message/updatedAt` 与已核验的 created/existing 计数；字段白名单、操作 ID 格式和错误脱敏沿用现有契约。
- 普通任务状态更新通过现有 AG-UI 事件投影或 UI 对 `get` 的只读自动刷新传播；断线后从持久化状态重建，不要求常驻一个长连接。

### 4.3 前端交互与视觉样式（按现有主题补充）

修改：

- `Frontend/src/shared/authorizationBootstrap.ts`
- `Frontend/src/renderer/src/service/builtinAuthorization.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/AuthorizationBootstrapPanel.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/ProcessSteps/AuthorizationBootstrapRefreshButton.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/WorkspaceDebugDock/BuiltinPatchCard.tsx`
- 必要时修改 `AiChatPanel.tsx` 中 `builtinAuthorizationActions` 的 onContinue；正常自动流程不再调用 `handleResumePlan(... resumeFrom='builtin_authorization_bootstrap')` 来触发 Seed。

#### 4.3.1 交互基准

整体交互参考现有“权限数据库初始化”卡片样式：**白底卡片、浅灰边框、大标题、右上角状态胶囊、纵向三段步骤、左侧圆形状态图标、当前步骤下方说明文案、步骤之间保留足够留白**。

要求：

1. 不新造一套视觉语言，严格沿用现有主题 token、间距、圆角、字体层级和状态色。
2. 保持单卡片纵向布局，不拆成多个卡片，不改成表格或复杂时间线。
3. 三个步骤固定为：
   - **权限数据表初始化**
   - **权限数据初始化**
   - **初始化结果核验**
4. 卡片右上角统一展示总状态胶囊，文案按状态映射：`运行中 / 已完成 / 待处理 / 失败`。
5. 每个步骤左侧图标风格与截图一致：
   - 已完成：绿色描边勾选圆图标
   - 进行中：中性描边圆图标 + 当前步骤说明 + 细进度条
   - 等待中：灰色实心或浅灰状态圆点
   - 失败/待处理：主题内告警色圆图标（优先复用现有异常图标）

#### 4.3.2 运行中状态

同一“权限数据库初始化”卡中展示三个阶段：**权限数据表初始化 → 权限数据初始化 → 初始化结果核验**。

- 第 1 阶段完成后，显示副文案：`4 张表已创建并核验`。
- 第 2 阶段运行中时，显示副文案：`正在自动查询数据写入结果`。
- 第 3 阶段等待时，显示副文案：`等待权限数据写入完成`。
- 当前运行中的步骤下方展示一条**细横向进度条**，仅用于表达“正在执行”，**不要求真实百分比**；禁止伪造 63%/78% 之类数值。
- `running/waiting` 时隐藏“刷新执行状态”“继续初始化权限数据”按钮，统一改为提示：`正在自动处理，无需手动刷新。`

页面可见且任务运行时，对**纯只读** `get` 做适度轮询，例如 2–5 秒；页面不可见或卡卸载时可以停止 UI 轮询，后台 worker 不受影响。

#### 4.3.3 已完成状态

当 `schemaReady=true && seedReady=true` 时：

- 卡片右上角状态胶囊显示 `已完成`。
- 三个步骤全部显示为已完成态。
- 第 3 步副文案显示：`权限表与权限数据均已核验就绪`。
- 底部不再展示操作按钮，仅保留必要的结果摘要（如 created/existing 计数，若当前输入仍匹配）。

#### 4.3.4 失败 / 待处理状态（保留重试按钮）

`needs_attention/failed` 时，卡片进入异常态，但仍保持与截图一致的基础布局，不切换成弹窗。

展示规则：

1. 顶部状态胶囊显示 `待处理` 或 `失败`。
2. 定位到失败步骤：
   - Schema 失败：第 1 步标红/告警，其后两步保持等待。
   - Seed 失败：第 1 步完成，第 2 步告警，第 3 步等待。
   - Verify 失败：前两步完成，第 3 步告警。
3. 在失败步骤下展示脱敏原因文案、`schemaOperationId` 或 `seedOperationId`。
4. **保留“重试”按钮**，但仅在后端返回允许恢复/重试时展示；文案统一使用 `重试`，不要出现“继续初始化权限数据”这种阶段性措辞。
5. 同时保留只读辅助按钮：
   - `查询原操作`：始终只读查询原 operationId
   - `查看详情`（如已有样式可复用）：展开失败原因或提示文案
6. 如果失败原因属于 `inputs_changed / seed_saas_contract_incompatible / unknown_without_identity` 等不允许直接重试的情况：
   - 不展示“重试”按钮
   - 仅展示“查询原操作”或“查看详情”
   - 明确提示需要人工核对

**重试按钮的前端规则：**

- 只在后端明确返回 `nextAction in {"retry", "resume", "run"}` 或等价的可恢复信号时显示。
- 点击“重试”调用统一的恢复/ensure 入口，不得直接绕过后端保护去重发 POST。
- 点击后按钮进入 loading，成功后卡片回到“运行中”；失败则继续保留异常态和错误提示。

#### 4.3.5 历史态与当前态区分

1. 旧历史 Run 卡只显示当时快照，不要继续自动刷新。
2. 当前活动 workspace 只显示一个最新进度卡，防止 Graph 历史回执和编排实时状态并排相互矛盾。
3. 工作区切换、会话切换、旧请求迟到时用 `workspace + generation/identity` 防止状态串台。
4. 保留已有异常显示和非 Builtin DDL 复制功能，不影响其他面板。

#### 4.3.6 建议的状态映射（供 Codex 直接实现）

| 后端状态 | 顶部胶囊 | 步骤表现 | 按钮 |
| --- | --- | --- | --- |
| `running` + `stage=schema` | 运行中 | 步骤1进行中，2/3等待 | 无 |
| `running` + `stage=seed` | 运行中 | 步骤1完成，步骤2进行中，3等待 | 无 |
| `running` + `stage=verify` | 运行中 | 步骤1/2完成，步骤3进行中 | 无 |
| `ready` | 已完成 | 步骤1/2/3完成 | 无 |
| `needs_attention` | 待处理 | 对应失败步骤告警 | 查询原操作；必要时重试 |
| `failed` | 失败 | 对应失败步骤告警 | 查询原操作；必要时重试 |

#### 4.3.7 一个建议的 UI 文案模板

- 卡片标题：`权限数据库初始化`
- 运行中提示：`正在自动处理，无需手动刷新。`
- 步骤1完成：`4 张表已创建并核验`
- 步骤2运行中：`正在自动查询数据写入结果`
- 步骤3等待：`等待权限数据写入完成`
- 成功完成：`权限表与权限数据均已核验就绪`
- 待处理：`当前结果待核对，请根据原操作结果继续处理。`
- 重试按钮：`重试`
- 只读按钮：`查询原操作`

> 注意：前端不要根据本地猜测擅自显示“可重试”；必须以后端返回的可恢复信号为准。

### 4.4 预览门禁

`Backend/app/services/project_launcher.py::_require_preview_authorization_ready()` 保持通过 `seed_ready()` 判断；未就绪提示改为“模拟数据库权限正在初始化，请在权限初始化卡查看当前状态；若结果待核对，请先完成核对”。禁止自动跳过 Seed 或因为 Build 已完成就启动受权限约束的预览。

## 5. 分步实施（按此顺序提交）

### PR1：后端编排器与持久化

1. 新建 `builtin_authorization_orchestrator.py` 和编排状态模型/独立存储读写模块。
2. 将 Schema/Seed 现有一次操作函数适配为可被 tick 调用的明确结构化结果；保留人工 `refresh` 纯查询接口。
3. 实现 workspace 去重、`portalocker` 跨进程串行、状态原子持久化、GET 退避和未知结果的 fail-closed 逻辑。
4. 新建 `Backend/tests/test_builtin_authorization_orchestrator.py`，以注入时钟、假 SaaS Transport、可控制状态转换的 fake executor 测试，不使用真实 sleep。

### PR2：运行入口和恢复

1. Graph `builtin_authorization_bootstrap` 接入 `ensure(...)`，严格保留 DAG 门禁及 Build 放行策略。
2. `workspace_attach` 入口恢复未完成任务；删除链路停止 worker；后端 `lifespan` shutdown 有序关闭 worker。
3. 独立 AG-UI `get/run_patch/refresh_patch` 接入统一任务，新增公开状态字段；读取仍不触发写入。
4. 针对首次 Build、再次 Build、断线重启、删除竞争及多进程并发补充集成用例。

### PR3：前端旅程与文档

1. 统一 Graph 节点和 Debug Patch 的当前任务展示；移除正常路径必须手动点击刷新的交互。
2. 增加只读进度查询、工作区重连恢复、异常兜底按钮及身份隔离。
3. 更新 `docs/` 中 Builtin 权限初始化操作文档；标明 202 仅受理、状态会自动查询、Build/预览门禁有区别。
4. Frontend/Backend 回归测试通过后，用可销毁数据库验证真实 Schema+Seed 端到端。

## 6. 必须新增的自动化测试

| 用例 | 预期结果 |
| --- | --- |
| 首次 Builtin+授权开启+已确认 DAG | 仅一次 CREATE POST；自动查原 ID；四表核验后仅一次 Seed POST；自动查原 ID；最终 ready |
| Schema 已就绪但无 Seed | 不重新建表，自动检查 Seed 并提交一次 |
| Schema RUNNING 连续多次 | 只 GET 原 ID，零额外 Schema/Seed POST |
| Schema SUCCEEDED 但 `tables/check` 不 ready | 不提交 Seed；继续只读核验/按上限进入待核对 |
| Schema FAILED、UNKNOWN、丢失受理回执 | 不自动重建、不启动 Seed；保留证据 |
| Seed RUNNING 连续多次 | 只 GET 原 ID，不重复提交 |
| Seed POST 响应丢失 | 按原 `idempotencyKey` GET；不重发 POST |
| Seed GET 404，已有本地 submitting/unknown | 不重发 POST，进入待核对 |
| Seed SUCCEEDED 但 verified、指纹、表名、计数不符 | 不标记 ready，保留错误 |
| Backend 重启 / workspace attach | 从原 ID 或幂等键接续，最终完成，无额外 POST |
| 两窗口 / 两次 Build / 两进程同时触发 | 每一阶段至多一个有效新 POST；后续接管同一操作 |
| 初始化中权限关闭、换库、改 DDL、改 Manifest/管理员 | 后续远端副作用被阻断，不写过期成功状态 |
| 文件丢失或损坏、软链接注入、worker 崩溃 | fail closed，不因状态丢失误当首次提交 |
| 停止应用 / 删除工作区 | 停止本地 worker，不能把已受理 SaaS 操作当作已撤销 |
| Build 与预览 | Build 可继续；Seed 未就绪时预览仍拒绝；ready 后允许 |
| Direct / external API / static 模式 | 完全不受新编排器影响 |
| `refresh`、`refresh_patch`、`get` | 只读或纯本地读；任何结果都不触发 POST |
| UI 活动/历史/工作区切换 | 仅当前任务自动更新；历史静态；无跨工作区串台 |

## 7. 必须提前校验的接口合同（单列前置依赖，不混入编排改造）

1. SaaS 最新 `main@0e44f734` **已经**实现独立 Seed POST 和两个 GET；本方案不再要求另建 SaaS API。但部署前确认管理库已执行 `src/main/resources/db/manual/202611_add_data_operation.sql`（已有管理库升级情形）。
2. XCodeAgent `authorization_manifest.py` 生成操作资源 `targetResourceRef="action:{pageId}:{actionId}"`；当前 SaaS Seed 校验器要求操作资源 `target_resource_ref` 以 `operation:` 开头，而且 XCodeAgent `builtin_authorization_seed.py::validate_seed_transport()` 主动拒绝所有操作资源。此合同差异会在 Seed 前置校验失败，**不能误判成编排失败**。须先独立对齐协议（优先保留已确认 Manifest 的语义），并更新测试。
3. Manifest 描述允许空字符串，但 SaaS Seed 目前只接收非空字符串；对于当前正式产物缺失/空描述也须先对齐合同，不能静默填造业务数据。
4. 如尚未完成上述合同对齐，自动编排器应给出 `needs_attention(seed_saas_contract_incompatible)`，严禁为让旅程通过而跳过校验、吞错或修改原 Seed 数据。
5. 提醒：SaaS `SUCCEEDED + verified=true` 由其事务回读保障；XCodeAgent 必须再校验现有 `requestFingerprint`、表名、行数和操作归属，不能只依赖状态字符串。

## 8. Codex 交付标准

交付应包含：

- 修改文件清单与说明，逐项对应 PR1/PR2/PR3；解释为什么没有修改 Direct 和模板侧逻辑。
- 新任务状态机与持久化合同，以及从 `state.json` 恢复的具体处理；解释崩溃发生在 POST 前/后时为何不会重复提交。
- 所有新增测试的运行命令、通过结果、无法运行的环境依赖；关键断言必须包含 POST 次数与 GET 次数。
- 一段可复现的手工联调旅程：首次 Builtin → 建表受理 → 自动 GET → Seed → 自动 GET → `ready` → 预览允许；再模拟进程重启、网络超时、状态 UNKNOWN 验证安全恢复。
- 不允许通过禁用/绕过 `schema_ready`、`seed_ready`、现有 DAG 门禁、SaaS 幂等规则或去掉人工核对机制来“修复”。

**最终验收一句话：首次 Builtin 权限初始化从“开始开发”到 `schemaReady=true && seedReady=true` 全程无需用户手动刷新或继续；任务中断可以接管原操作并继续，任何未确认结果均不能导致重复 Schema/Seed POST。**
