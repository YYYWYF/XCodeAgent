# 分层 Build DAG 实施计划

## 目标与边界

将代码生成从扁平 `tasks[]` 改为“全局 Unit 骨架 + 按需编译的叶子任务 DAG”：

```text
ProjectPlan
  → 全局 application/page/data_source Unit 骨架
  → 用户选择 application、page 或 data_source
  → prepare_build_tasks 编译目标 Unit 的任务子图
  → 自动补齐直接 API / 数据源 / 公共能力前置任务
  → BuildScheduler 裁剪并执行任务子图
  → scoped validation 与三层进度回写
```

- 不兼容旧 Build Task Plan；旧 `.xcodeagent/plans/build-task-plan.json` 失效后重新生成。
- Build DAG 是内部执行编排，不新增用户确认门禁；ProjectPlan、PageDetail、DataSourceDetail 的确认仍是代码生成前的业务门禁。
- 全局 Unit 骨架覆盖 ProjectPlan 中全部页面与数据源；只有用户选择、且详情已确认的 Unit 才生成叶子任务。
- 单页面生成必须加载 `WorkspaceSnapshot + ProjectPlan + PageDetail + 该页面 endpoint 直接关联的 DataSourceDetail`。

## 新的持久化模型

`build-task-plan.json` 使用 `build-dag.v2`：

```json
{
  "schema_version": "build-dag.v2",
  "application": {},
  "build_units": {},
  "unit_graph": {},
  "task_registry": {},
  "task_graph": {},
  "execution_history": []
}
```

- `build_units`：`application`、`page`、`data_source` 和公共能力 Unit。
- `unit_graph`：页面、数据源、公共能力之间的依赖关系。
- `task_registry` / `task_graph`：已准备 Unit 的可调度叶子任务和依赖关系。
- Unit 状态：`not_prepared → prepared → running → completed`，异常状态为 `failed` / `blocked`；已完成依赖可标记为 `reused`。
- 任务必须包含 `unit_id`、`source_refs`、`requires_capabilities`、`provides_capabilities`、精确 `change_scope`、验收标准与验证命令。

## 分阶段实施与验收

### 1. 建立分层 DAG 契约与持久化渲染

修改 `build_task_planner.py`、`task_documents.py`、`state.py`、`domain/models.py`，直接采用 `build-dag.v2`，移除旧计划读取和兼容分支。

验收：生成结果含 `build_units`、`unit_graph`、`task_registry`、`task_graph`；`BUILD_TASK_DAG.md` 分层展示公共模块、数据源和页面。

```bash
cd Backend
python3 -m unittest tests.test_build_task_planner
```

### 2. 确保全局 Unit 骨架并解析目标构建上下文

在一次 `prepare_build_tasks` 调用中顺序执行两个纯确定性服务：

```text
ensure_build_unit_skeleton(ProjectPlan, WorkspaceSnapshot)
  → resolve_target_build_context(当前 PageDetail 或 DataSourceDetail)
```

`BuildUnitSkeletonBuilder` 输入 ProjectPlan 与 WorkspaceSnapshot，首次创建：

- `page:<pageId>`；
- `data-source:<dataSourceId>`；
- `app:frontend-shell`、`app:route-registry`、`app:api-client`、`app:auth-guard`、`app:backend-bootstrap`、`app:integration` 等公共能力 Unit。

Unit 只描述模块和依赖，初始为 `not_prepared`，不创建代码任务，不调用模型。后续选择第二个页面或数据源时，骨架只校验 ProjectPlan / WorkspaceSnapshot 指纹；输入未变化时复用原骨架，不重复创建 Unit。

同一次调用中，`PageBuildContextResolver` 按当前选择目标解析：

```text
PageDetail.endpoint_dependencies
  → ProjectPlan.api_contracts.endpoint
  → data_source_id
  → DataSourceDetail artifact ref
```

输出当前 PageDetail 或 DataSourceDetail、页面直接关联的 DataSourceDetail、endpoint IDs、必需 Unit IDs 与 artifact hashes。缺失 endpoint、契约或确认详情时阻止本次构建。无关页面和数据源详情不得加载。

验收：首次订单页面请求创建全局 Unit 骨架并仅加载订单数据源详情；后续客户页面请求保持已有骨架，仅解析客户页面上下文；错误 endpoint 给出明确阻止原因。

```bash
cd Backend
python3 -m unittest tests.test_build_unit_skeleton tests.test_page_build_context_resolver
```

### 3. 重构 `prepare_build_tasks` 为按 Scope 的 Unit 编译入口

请求和 State 增加：

```json
{ "buildExecutionScope": { "type": "application|page|data_source", "targetId": "..." } }
```

`prepare_build_tasks` 的职责：读取 / 创建 Unit 骨架、解析目标上下文、计算缺失公共能力、调用模型生成相关 Unit 候选任务、交由 Planner 编译并持久化，然后自动进入 `build`。

验收：订单页面仅准备订单页面、订单数据源和必要公共能力；数据源 scope 不准备页面任务；没有 Build DAG 确认卡。

```bash
cd Backend
python3 -m unittest tests.test_prepare_build_tasks_guard tests.test_workflow_routing
curl -sS http://127.0.0.1:8000/health
```

### 4. 将 `build_task_planner` 升级为 Unit / Task DAG 编译器

输入为 Unit Skeleton、目标构建上下文、模型候选任务与当前 BuildTaskPlan；输出更新后的全局计划。其负责：任务物化、`unit_id` / `source_refs` 补齐、公共 capability 去重、endpoint 到数据源 API 任务依赖、任务图校验、文件锁与并行批次。

验收：`page:orders-list:ui` 显式依赖订单 API 任务；多个页面共用的 `app:api-client` 只创建一次；每个任务可定位归属 Unit 和来源 artifact。

```bash
cd Backend
python3 -m unittest tests.test_layered_build_task_planner
```

### 5. 公共 Unit 复用与定向失效

为 Unit 计算 `input_fingerprint`：公共 Unit 基于架构和 WorkspaceSnapshot，页面 Unit 基于 PageDetail / endpoint / 路由约定，数据源 Unit 基于 DataSourceDetail / schema / API contract。仅在相关输入变化时使该 Unit 及下游依赖重新准备。

验收：生成第二个页面时不重做公共路由或 API client；修改订单 PageDetail 时只重建订单页面与受影响依赖。

```bash
cd Backend
python3 -m unittest tests.test_layered_build_task_planner
```

### 6. 按 Unit 裁剪 BuildScheduler 执行图

新增 `ExecutionSliceResolver`：

- `application`：所有已准备、未完成任务；
- `page`：目标页面任务和未完成公共 / 直接数据源前置任务；
- `data_source`：目标数据源任务和未完成公共后端前置任务。

切片外任务不更新；已完成任务满足依赖并显示为复用；repair task 继承父任务 `unit_id` 与 `source_refs`。

验收：执行订单页面会先执行订单 API，但不会执行客户页面；已完成公共任务不再调用生成 Agent。

```bash
cd Backend
python3 -m unittest tests.test_execution_slice_resolver tests.test_build_scheduler tests.test_build_subgraph_scheduler
```

### 7. 接入按 Scope 的验证闭环

页面 Unit 需在其 API、UI、交互之后执行页面验证；数据源 Unit 需验证 schema、API、seed/mock 和接口测试。仅 application scope 执行全量构建、集成测试、启动和最终验收。

验收：单页完成后其 API 与页面均可验证，状态为“局部完成”；只有全应用执行才进入预览与验收。

```bash
cd Backend
python3 -m unittest tests.test_scoped_build_validation tests.test_testing_subgraph_events
curl -sS http://127.0.0.1:8000/health
```

### 8. 进度聚合、实时 AG-UI 与持久化快照

新增 `ProgressAggregator` 与 `.xcodeagent/runtime/development-progress.json`。按 application、page、data_source 聚合：

- application：Unit coverage、执行进度、验证进度；
- page：实现进度、API / 数据源就绪状态、验证状态；
- data source：实现和验证状态。

Build 过程中发出 `build.scope.started`、`build.unit.prepared`、`build.task.started`、`build.task.completed`、`build.task.failed`、`build.progress.updated`、`build.scope.validation.completed` 等 AG-UI 事件。

验收：页面 UI 完成但 API 未完成时显示“实现完成、依赖未就绪”；刷新后进度不丢失；实时事件与进度文件一致。

```bash
cd Backend
python3 -m unittest tests.test_progress_aggregator tests.test_build_subgraph_scheduler tests.test_workflow_request
```

### 9. 工作台与应用设置的进度体验

工作台增加“生成并验证此页面”“生成并验证此数据源”“生成全部未完成模块”入口。应用设置展示持久化的应用总体、公共能力、页面、数据源、当前任务、失败原因和最近验证结果。所有产品交互使用 AG-UI；新增 UI 覆盖浅色和深色主题。

验收：单页启动时可看到“复用应用壳 → 生成订单 API → 生成页面 → 验证”；离开聊天后应用设置仍能看到同一进度。

```bash
cd Frontend
pnpm build
```

### 10. 收口

补齐端到端测试：单页面自动补齐数据源、第二页面复用公共 Unit、单数据源生成、局部验证、全应用验收、Repair 归属、指纹失效、进度恢复。更新 `docs/WORKFLOW.md` 与 `docs/CODEBASE_INDEX.md`。

最终验收：

```bash
cd Backend
python3 -m unittest
curl -sS http://127.0.0.1:8000/health

cd ../Frontend
pnpm build
```
