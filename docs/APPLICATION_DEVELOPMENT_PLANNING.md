# Application Development Planning

## Scope

Workbench 只读取 `.xcodeagent/plans/technical-plan.json`。当前 TechnicalPlan 的左侧 `Pages` 大纲来自 ProductPlan `pages`，再按 `pageId` 合并 TechnicalPlan `pages[].references`；API 大纲投射 `api_contracts`。页面开发就绪状态来自已确认的页面 references，不再检查 `.xcodeagent/plans/pages/`，也不再用 PageDetail 蒙层锁住页面对话。

选择页面后，主 `/workflow/run` 从 ProductPlan、UiManifest 和 TechnicalPlan references 按需编译对应 `PageImplementationContract`：若 `requiredEndpointIds` 为空，直接进入工作区检查与任务规划；若存在接口依赖，只为缺失或失效的 EndpointDetail 生成批量开发确认，确认后继续。选择具体接口仍提交 `detailTargetType=endpoint`、`selectedApiContractId` 和 `selectedEndpointId`，并只生成、审核该接口详情。页面视觉、组件、交互入口和状态呈现以已确认 React UI 稿为唯一权威；若 UI 阶段被跳过，则依据 ProductPlan、TechnicalPlan 和模板技能实现，不再生成新的 `plans/pages/page--<pageId>.*`。

无目标的“自由对话”以及页面或 endpoint 进入“已设计”状态后的底部普通输入，使用独立 `/conversation/run` AG-UI Graph；前端只发送 `forwardedProps.conversation.workspaceRoot/selectedSkillNames` 和当前标准 AG-UI user message，不发送左侧 target。页面/API 目标下的输入区提供“设计修改 / 自由协作”显式模式，默认使用“设计修改”，并按目标保留用户的模式选择；设计修改模式发送到 `/workflow/run`，自由协作模式发送到 `/conversation/run`。左侧选择仍只决定本地会话归属和是否允许进入自由对话通道。后端先区分常规对话、只读工作区问答、局部工作区修改、正式 Workflow 和待澄清输入，再为需要修改的请求识别 frontend、backend、fullstack 或 workspace owner。正式详情确认、调试恢复、计划控制及尚未设计目标仍使用 `/workflow/run`。自由对话实时展示助手正文增量、当前节点、工具调用和必要的过程详情，同时保留代码 diff/撤销和预览；如果分类结果需要正式计划，先在对话中展示确认卡，只有用户确认后才切换到 `/workflow/run`。

页面统一使用 `pageId`，接口使用 `apiContractId + endpointId`。每个页面仍可拥有多个独立本地会话；会话归属、自由对话和计划执行保持原行为。EndpointDetail 继续独立保存接口用途、处理逻辑、数据来源、字段差异和数据库操作；页面任务上下文由 TechnicalPlan 切片、PageImplementationContract、React UI 路径及相关 EndpointDetail 共同组成。

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
