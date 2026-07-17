# Application Development Planning

## Scope

Workbench entry renders the original full-area selection overlay and the left `Pages` outline with page choices projected from ProjectPlan `frontend_pages`. The desktop reader prefers `.xcodeagent/project_plan.json` for this page list and falls back to the confirmed `.xcodeagent/plans/project-plan.json`; neither UI uses `application.json`, `menus.items`, `developmentTasks`, or planning-readiness conditions to decide which pages appear. Selecting a page in the entry overlay submits `selectedPageId` through the primary `/workflow/run` AG-UI endpoint and starts `detail_confirmation`. The page-level development planner remains an independent AG-UI action for later explicit use and is not this workbench entry action.

The overlay remains while the selected-page generation request runs. After that request returns, the workbench switches to the normal chat view so generated artifacts and the detail-review card are displayed through the existing Workflow message UI.

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
