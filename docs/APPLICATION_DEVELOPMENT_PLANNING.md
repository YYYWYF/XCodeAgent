# Application Development Planning

## Scope

Workbench 优先读取 `.xcodeagent/plans/technical-plan.json`，并以 ProductPlan 的 `pages` 作为页面事实，再按 `pageId` 合并 TechnicalPlan `pages[].references`；API 大纲投射 `api_contracts`，实体大纲投射 TechnicalPlan/ProjectPlan 的 `entities`。页面开发就绪状态来自已确认的页面 references，不再检查 `.xcodeagent/plans/pages/`，也不再用 PageDetail 蒙层锁住页面对话。工作台检查产物时仍返回页面、API 与实体目录，实体目录包含字段摘要和独立设计状态。

选择页面后，主 `/workflow/run` 从 ProductPlan、UiManifest 和 TechnicalPlan references 按需编译对应 `PageImplementationContract`：若 `requiredEndpointIds` 为空，直接进入工作区检查与任务规划；若存在接口依赖，只为缺失或失效的 EndpointDetail 生成批量开发确认。选择接口提交 `detailTargetType=endpoint`、`selectedApiContractId` 和 `selectedEndpointId`；选择实体提交 `detailTargetType=entity` 和 `selectedEntityId`。实体设计继续负责字段、数据源类型、来源绑定、业务规则与数据库表操作，确认后独立写入 `.xcodeagent/plans/entities/entity--<entityId>.json/.md`；接口设计只读引用绑定实体的已确认设计，接口或页面进入开发前必须通过实体确认门禁。

页面视觉、组件、交互入口和状态呈现以已确认 React UI 稿为权威；若 UI 阶段被跳过，则依据 ProductPlan、TechnicalPlan 和模板技能实现，不再生成新的 `plans/pages/page--<pageId>.*`。EndpointDetail 独立保存接口用途、处理逻辑及与已确认实体设计的绑定，页面任务上下文由 TechnicalPlan 切片、PageImplementationContract、React UI 路径、实体设计摘要及相关 EndpointDetail 共同组成。

无目标的“自由对话”以及已就绪页面、endpoint 或 entity 下的普通输入使用独立 `/conversation/run` AG-UI Graph；正式详情确认、设计修改、调试恢复、计划控制及尚未设计目标仍使用 `/workflow/run`。页面、接口和实体各自保留独立本地会话归属与 AG-UI `threadId`，左侧目标只决定会话归属和执行范围，不改变传输协议。

This flow uses the independent `/application-development-planning/run` AG-UI endpoint and its own thread id. It never enters or resumes the primary LangGraph workflow. A normal generation requires one model call; if the model returns genuine blocking questions, the answers are supplied to a second generation call. Confirmation is deterministic and model-free.

## Context Budget

The backend reads the fixed `<workspaceRoot>/.xcodeagent/application.json` and sends only application identity, scenario, terminal, layout, the datasource type without connection mode or credentials, auth, menus, APIs, and at most five short clarification answers. It never sends source files, repository trees, workflow history, tool logs, chat history, or secrets. The output is bounded by existing menu count, twenty tasks per menu, two to six acceptance criteria per generated task, and short field limits. This remains far below the 128k model context budget.

## Task and Persistence Contract

- The selected page receives a non-empty, ordered `developmentTasks` array; other pages are preserved and may remain unplanned. Array order is the visible 1, 2, 3 task order. Each task has a globally unique id, concise title and scope, `todo`/`in_progress`/`completed` status, direct `dependsOn`, derived `blocks`, covered feature names, and a separate acceptance-criteria list. Model-generated tasks always start as `todo`; the broader status enum allows later task completion updates without changing the storage shape.
- Routing, API-call infrastructure, navigation, and layout are treated as existing project capabilities. Generation must return `sharedModules: []`, and deterministic validation rejects newly proposed shared modules. The field is fixed as an empty current-contract declaration.
- `menus.developmentPlan` stores the plan summary, schema version, and global topological `executionOrder`.
- Generation and confirmation carry the selected page key through the AG-UI payload. Confirmation rereads the current workspace file, derives missing `menus`, `apis`, `schemas`, and `dataSources` from the confirmed ProjectPlan when necessary, validates that the plan covers exactly the selected page, derives reverse blockers, checks dependency existence and acyclicity, preserves other page plans, then writes through a sibling temporary file and atomic replacement.
- Existing page purposes, features, interactions, APIs, and unrelated application configuration are preserved.

## AG-UI Lifecycle

Generation and confirmation both emit run start, assistant message start, structured progress custom events, state snapshots, assistant text, a completed or failed custom result, message end, and run finish. Generation forwards model chunks as `TEXT_MESSAGE_CONTENT`; the frontend consumes the endpoint through `@ag-ui/client` and `@ag-ui/core` without handwritten SSE parsing.
