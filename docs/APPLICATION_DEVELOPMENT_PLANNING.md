# Application Development Planning

## Scope

Workbench 的左侧 `Pages` 大纲投射 `.xcodeagent/plans/project_plan.json` 的 `frontend_pages`，并兼容读取既有 `.xcodeagent/plans/project-plan.json`。工作区是否已经存在页面设计只检查 `.xcodeagent/plans/pages/` 是否包含目录项：目录为空或不存在时，首次进入在对话区显示页面选择界面，同时以现有锁层禁止操作左侧应用大纲；目录非空时直接进入正常工作台。单个页面的“已设计”状态仍以对应的 `page--<pageId>.json` 详情文件为准。用户在大纲中选择尚无详情文件的页面时，保留正常对话布局但以蒙层锁住对话区，只允许启动该页详细设计。选择动作提交 `selectedPageId` 到主 `/workflow/run` AG-UI endpoint 并启动 `detail_confirmation`。页面级开发任务规划仍是后续独立 AG-UI action，不属于本入口动作。

首次选择界面或待设计页面蒙层会在所选页面生成或复核请求运行期间保持锁定。页面统一使用 `pageId` 作为标识。首次选择界面只展示页面信息和“开始生成”动作，不显示或判断页面的设计状态，因为该界面仅在 `plans/pages/` 为空时出现。请求返回后，工作台重新检查 `plans/pages/`，不使用仅存在于前端内存的临时完成标记提前解锁。主 Workflow 加载 ProjectPlan 时会补全 `plans/pages/` 和 `plans/data-source/` 下的外置详情 JSON；`detail_confirmation` 只展示当前选中页及其直接数据源。若该页尚无详情计划，返回空复核且设置 `summary.missingSelectedPagePlan=true`，由 UI 引导用户先生成。

This flow uses the independent `/application-development-planning/run` AG-UI endpoint and its own thread id. It never enters or resumes the primary LangGraph workflow. A normal generation requires one model call; if the model returns genuine blocking questions, the answers are supplied to a second generation call. Confirmation is deterministic and model-free.

## Reference Architecture Mapping

- **learn-coding-agent:** the loop stays narrow: gather the current bounded application configuration, apply the explicit existing-foundation constraint, reason once, validate the result, wait for user confirmation, then persist. The model receives no repository scan or terminal history.
- **OpenCode:** the planning UI is a human-in-the-loop boundary. Model JSON is untrusted; Pydantic and deterministic DAG checks reject newly invented shared modules, missing menus, duplicate or dangling task ids, dependency cycles, and invalid execution order before the plan is displayed or written.
- **Deep Agents:** context is progressively disclosed and durable state remains filesystem-backed. Only relevant `application.json` product metadata and the fixed foundation boundary enter the model context; AG-UI progress and text deltas keep long model work observable; the confirmed plan is atomically written to the workspace.

XCodeAgent intentionally uses a graph-free action agent because this feature has one bounded reasoning action and one deterministic confirmation action. Clarification is a discriminated result of the same planning action, not a separate speculative workflow phase.

## Context Budget

The backend reads the fixed `<workspaceRoot>/.xcodeagent/application.json` and sends only application identity, scenario, terminal, layout, datasource, auth, menus, APIs, and at most five short clarification answers. It never sends source files, repository trees, workflow history, tool logs, chat history, or secrets. The output is bounded by existing menu count, twenty tasks per menu, two to six acceptance criteria per generated task, and short field limits. This remains far below the 128k model context budget.

## Task and Persistence Contract

- The selected page receives a non-empty, ordered `developmentTasks` array; other pages are preserved and may remain unplanned. Array order is the visible 1, 2, 3 task order. Each task has a globally unique id, concise title and scope, `todo`/`in_progress`/`completed` status, direct `dependsOn`, derived `blocks`, covered feature names, and a separate acceptance-criteria list. Model-generated tasks always start as `todo`; the broader status enum allows later task completion updates without changing the storage shape.
- Routing, API-call infrastructure, navigation, and layout are treated as existing project capabilities. Generation must return `sharedModules: []`, and deterministic validation rejects newly proposed shared modules. The field remains in schema version 1 only for payload-shape compatibility.
- `menus.developmentPlan` stores the plan summary, schema version, and global topological `executionOrder`.
- Generation and confirmation carry the selected page key through the AG-UI payload. Confirmation rereads the current workspace file, derives missing `menus`, `apis`, `schemas`, and `dataSources` from the confirmed ProjectPlan when necessary, validates that the plan covers exactly the selected page, derives reverse blockers, checks dependency existence and acyclicity, preserves other page plans, then writes through a sibling temporary file and atomic replacement.
- Existing page purposes, features, interactions, APIs, and unrelated application configuration are preserved.

## AG-UI Lifecycle

Generation and confirmation both emit run start, assistant message start, structured progress custom events, state snapshots, assistant text, a completed or failed custom result, message end, and run finish. Generation forwards model chunks as `TEXT_MESSAGE_CONTENT`; the frontend consumes the endpoint through `@ag-ui/client` and `@ag-ui/core` without handwritten SSE parsing.
