# Application Development Planning

## Scope

After the workbench reads its workspace `application.json`, it recursively checks every `menus.items` entry. A newly created application may initially contain only confirmed `planning.requirementSpec` and `planning.projectPlan`; in that case this stage derives `menus`, `apis`, `schemas`, and `dataSources` from the confirmed ProjectPlan before planning tasks, then persists those derived fields together with the confirmed task plan. If any menu item has no non-empty `developmentTasks`, the normal workbench shell remains visible. Only the application-outline section receives a translucent local interaction lock, while the workspace/home entry and footer navigation remain visible and usable. In the chat view, the conversation header, history, messages, composer, and preview actions are not rendered; that content area shows only the development-planning UI. Skills, files, and settings opened from the footer remain available. The user reviews a numbered, status-aware task list with separate acceptance checklists, answers focused clarification questions only when necessary, and explicitly confirms before derived fields and tasks are persisted.

This flow uses the independent `/application-development-planning/run` AG-UI endpoint and its own thread id. It never enters or resumes the primary LangGraph workflow. A normal generation requires one model call; if the model returns genuine blocking questions, the answers are supplied to a second generation call. Confirmation is deterministic and model-free.

## Reference Architecture Mapping

- **learn-coding-agent:** the loop stays narrow: gather the current bounded application configuration, apply the explicit existing-foundation constraint, reason once, validate the result, wait for user confirmation, then persist. The model receives no repository scan or terminal history.
- **OpenCode:** the planning UI is a human-in-the-loop boundary. Model JSON is untrusted; Pydantic and deterministic DAG checks reject newly invented shared modules, missing menus, duplicate or dangling task ids, dependency cycles, and invalid execution order before the plan is displayed or written.
- **Deep Agents:** context is progressively disclosed and durable state remains filesystem-backed. Only relevant `application.json` product metadata and the fixed foundation boundary enter the model context; AG-UI progress and text deltas keep long model work observable; the confirmed plan is atomically written to the workspace.

XCodeAgent intentionally uses a graph-free action agent because this feature has one bounded reasoning action and one deterministic confirmation action. Clarification is a discriminated result of the same planning action, not a separate speculative workflow phase.

## Context Budget

The backend reads the fixed `<workspaceRoot>/.xcodeagent/application.json` and sends only application identity, scenario, terminal, layout, datasource, auth, menus, APIs, and at most five short clarification answers. It never sends source files, repository trees, workflow history, tool logs, chat history, or secrets. The output is bounded by existing menu count, twenty tasks per menu, two to six acceptance criteria per generated task, and short field limits. This remains far below the 128k model context budget.

## Task and Persistence Contract

- Every recursive menu item receives a non-empty, ordered `developmentTasks` array. Array order is the visible 1, 2, 3 task order. Each task has a globally unique id, concise title and scope, `todo`/`in_progress`/`completed` status, direct `dependsOn`, derived `blocks`, covered feature names, and a separate acceptance-criteria list. Model-generated tasks always start as `todo`; the broader status enum allows later task completion updates without changing the storage shape.
- Routing, API-call infrastructure, navigation, and layout are treated as existing project capabilities. Generation must return `sharedModules: []`, and deterministic validation rejects newly proposed shared modules. The field remains in schema version 1 only for payload-shape compatibility.
- `menus.developmentPlan` stores the plan summary, schema version, and global topological `executionOrder`.
- Confirmation rereads the current workspace file, derives missing `menus`, `apis`, `schemas`, and `dataSources` from the confirmed ProjectPlan when necessary, validates that the plan still covers exactly its menu keys, derives reverse blockers, checks dependency existence and acyclicity, then writes through a sibling temporary file and atomic replacement.
- Existing page purposes, features, interactions, APIs, and unrelated application configuration are preserved.

## AG-UI Lifecycle

Generation and confirmation both emit run start, assistant message start, structured progress custom events, state snapshots, assistant text, a completed or failed custom result, message end, and run finish. Generation forwards model chunks as `TEXT_MESSAGE_CONTENT`; the frontend consumes the endpoint through `@ag-ui/client` and `@ag-ui/core` without handwritten SSE parsing.
