# Application Development Planning

## Scope

Workbench 读取 `.xcodeagent/plans/technical-plan.json`，以 ProductPlan `pages` 作为页面事实，并按 `pageId` 合并 TechnicalPlan `pages[].references`；API 大纲直接投射 `api_contracts`，实体大纲投射 TechnicalPlan 顶层 `entities` 和独立数据源绑定状态。当前流程不生成或读取页面/API详设文件。

页面视觉、组件、交互入口和状态呈现以已确认 React UI 稿为权威；UI 阶段被跳过时依据 ProductPlan、TechnicalPlan 和模板技能实现。`PageImplementationContract` 仍由 ProductPlan、UiManifest 和 TechnicalPlan 在运行时确定性编译，不写入独立页面详设。

选择页面或 API 后，主 `/workflow/run` 首先进入 `development_readiness_gate`。门禁按页面的 `requiredEndpointIds` 或选中的 API Endpoint 收集 `entity_ids`，并校验所有关联实体是否已有已确认 EntitySourceBinding：通过后进入 `inspect_workspace`；缺失时返回 `entity_source_binding_required`、`missing_entities` 和当前 `development_target`，但不自动切换实体。用户必须从实体大纲手动进入独立 EntitySourceBinding，确认完成后重新发起原页面/API开发。

EntitySourceBinding 只负责实体的数据源类型与物理来源绑定，保留 database、external API、static 三类方案、AI 映射辅助、建表/补列以及高危 DDL 审批。确认后只写入 `.xcodeagent/plans/entities/entity--<entityId>.json/.md`，并独立结束，不自动续跑页面/API任务。`prepare_build_tasks` 在生成 Build DAG 前再次执行同一实体绑定复检，防止恢复旧 checkpoint 或绕过入口门禁。

页面、API 和实体各自保留独立本地会话归属与 AG-UI `threadId`。页面/API开发、EntitySourceBinding、Build DAG 确认以及后续执行都使用 `/workflow/run` 的完整 AG-UI 生命周期；无目标普通协作继续使用独立 `/conversation/run`。

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
