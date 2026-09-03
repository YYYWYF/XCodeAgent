# Application Development Planning

## Scope

Workbench 读取 `.xcodeagent/plans/technical-plan.json`，以 ProductPlan `pages` 作为页面事实，并按 `pageId` 合并 TechnicalPlan `pages[].references`；API 大纲从 `api_contracts` 投射 endpoint 基本信息，但接口“已设计”只由对应非空 `.xcodeagent/plans/endpoints/endpoint--<contractId>--<endpointId>.md` 是否真实产出决定，TechnicalPlan 声明本身不能放行该状态；实体大纲投射 TechnicalPlan 顶层 `entities` 和独立数据源绑定状态。页面当前不读取独立详设文件。

点击大纲只选择本次开发目标；从入口开始页面或接口开发后，在当前通用历史会话中创建独立 execution thread，并通过 `/workflow/run` 进入实体数据源绑定前置检查。会话不归属于页面、接口或实体，已有 Workflow 消息及用户显式打开的历史会话继续展示运行结果。

页面视觉、组件、交互入口和状态呈现以已确认 React UI 稿为权威；UI 阶段被跳过时依据 ProductPlan、TechnicalPlan 和模板技能实现。`PageImplementationContract` 仍由 ProductPlan、UiManifest 和 TechnicalPlan 在运行时确定性编译，不写入独立页面详设。

选择页面或 API 后，主 `/workflow/run` 首先进入 `development_readiness_gate`。门禁按页面的 `requiredEndpointIds` 或选中的 API Endpoint 收集 `entity_ids`，并校验所有关联实体是否已有已确认 EntitySourceBinding：通过后进入 `inspect_workspace`；缺失时返回 `entity_source_binding_required`、`missing_entities` 和当前 `development_target`，同时由后端把原开发 execution 登记为一次性 continuation。用户可在当前通用历史会话中启动使用独立 thread 的 EntitySourceBinding execution；确认完成且服务端复检原目标 ready 后，后端签发绑定原 thread、原 run、目标与当前 TechnicalPlan 哈希的一次性 token，前端仅持久化该正式合同的续接卡。用户显式点击后，后端消费 token、恢复原开发 thread 并重新执行页面/API开发门禁。

EntitySourceBinding 只负责实体的数据源类型与物理来源绑定，保留 database、external API、static 三类方案、AI 映射辅助、建表/补列以及高危 DDL 审批。确认后只写入 `.xcodeagent/plans/entities/entity--<entityId>.json/.md`，实体运行独立结束；续接卡不会自动执行页面/API任务，用户点击后才以原目标发起新的正式运行。`prepare_build_tasks` 在生成 Build DAG 前再次执行同一实体绑定复检，防止恢复旧 checkpoint 或绕过入口门禁。

外部 API 绑定使用实体级共享连接与多操作设计器。当前唯一契约是 `external_api_design.connection + operations[]`：共享连接保存无鉴权 HTTP(S) Base URL、生成代码配置键、默认超时与非敏感 Header；每个操作保存稳定 `operation_id`、名称、一个或多个 `api_contract_id + endpoint_id` 引用、可选连接覆盖、method/path/Path/Query 参数、操作 Header、请求与返回样例、响应语义及字段映射。每个已配置操作必须且只能关联一个或多个本系统 Endpoint，同一操作可被多个 Endpoint 复用；实体设计允许按 Endpoint 分批确认，未覆盖的其它 Endpoint 不阻塞当前操作，后续开发目标仍会单独检查自身是否已绑定。`entity_payload=false` 的状态响应不允许配置载荷、分页和映射。后端在提交与开发就绪边界执行 URL、占位符、参数、敏感 Header、响应路径、分页引用和逐操作必填映射校验，并将当前非敏感集合契约写入 EntitySourceBinding Markdown/JSON；构建单 Endpoint 时只投射其关联操作，页面构建时投射去重后的相关操作。正式设计完整保存请求与响应样例，但任务规划和 DatasourceAgent 只接收无样例值的 `request_shape`、`response_shape`、完整字段映射和由映射共同数组前缀确定的 `mapped_entity_path`；请求样例标量只描述字段类型，不得被生成代码硬编码。任务规划的 `upstream` 阶段必须把当前 Spring Boot 配置文件纳入任务写入范围，并将确认 Base URL 作为普通 YAML/Properties 值直接写入 `base_url_config_key`，不得生成 `${ENV_NAME:default}` 或其它环境变量占位符；Java 代码仍只能通过配置键读取，不能写入 Java 常量。`mapped_entity_path` 负责实体记录提取，响应根对象和 `cardinality=object` 不会覆盖明确的 `list[]` 等映射路径。不发起真实外部请求，也不保存鉴权凭据，不读取或迁移旧的单 API 结构。

页面、API 和实体的 execution 使用独立 AG-UI `threadId`，消息连续保存在当前通用历史会话。实体绑定每轮操作从自身 checkpoint 保留原开发 continuation ID；内部 ID 不接受客户端快照补写。确认后仍缺实体时，当前对话列出剩余项继续设计；仅全部前置满足时提供原目标的继续开发卡，不清空会话、不自动开发。

生命周期边界签发的 `developmentContinuation` 是协议控制结果，必须完整保留到节点事件、StateSnapshot 和 RUN_FINISHED，不能被最终 Graph checkpoint 覆盖。前端仅从当前公开 summary 读取，运行完成时将续接卡和确认结果一起持久化，不依赖当前 UI 选择或挂载 effect。续接请求解析只读校验，token 消费和原 execution 的接替在同一生命周期锁及同一次写盘内完成；新运行未登记成功时 token 保持可重试。页面/API开发、EntitySourceBinding、Build DAG 确认以及后续执行都使用 `/workflow/run` 的完整 AG-UI 生命周期；普通协作继续使用独立 `/conversation/run`。

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
