# Agent Runtime 模板仓库与初始化流程设计

> 状态：阶段 A 独立模板与阶段 B 主流程初始化已实现；Testing、Launch 与完整端到端尚未接入
> 确认日期：2026-09-04
> 适用范围：包含业务智能体的生成应用、Agent Runtime 模板仓库、模板初始化、AG-UI Chat、Build、启动与健康检查

## 1. 文档目的

本文定义包含业务智能体的生成应用如何在现有 `frontend/`、`backend/` 之外，条件式初始化同级 `agent-runtime/` 模板工程，并明确模板预置代码、Build 生成代码、运行时配置、AG-UI Chat、模型、交互、持久化、启动和安全边界。

本文同时记录目标设计、独立模板仓库和当前主流程初始化实现。模板仓库已能独立安装、测试、启动，并通过内置 `chat` Deep Agent 提供基础 AG-UI Chat；XCodeAgent 已在 TechnicalPlan 确认后条件式下载并校验该模板。Testing、Project Launch、Java Gateway 和 Electron 完整端到端仍待后续接入。后续实施必须复用现有 RequirementSpec、ProductPlan、TechnicalPlan、应用生命周期、模板初始化、Build DAG、Testing、Code Review、Launch 和 Acceptance，不新增平行顶层工作流。

## 2. 核心决策

1. 业务智能体使用生成应用根目录下独立的 `agent-runtime/`，与 `frontend/`、`backend/` 同级。
2. `agent-runtime/` 从独立 Git 模板仓库初始化，不再由模型从空目录生成完整运行时。
3. 只有已确认 TechnicalPlan 的 `agent_contracts[]` 非空时才要求 Agent Runtime 模板；普通应用不创建空运行时工程。
4. 模板必须是最小可独立运行的 Chat Runtime，至少具备 Python 3.12、由 `create_deep_agent` 创建的内置 `chat` Agent、基于 `init_chat_model` 的项目默认模型工厂、AG-UI SSE、用户交互恢复、checkpoint 和健康检查。
5. 模板只拥有稳定基础设施。Agent 装配统一位于 `src/app/agent/`，工具统一位于 `src/app/tools/`；不得在 Runtime 根目录再建立平行的 `agents/` 或 `tools/`。具体业务能力、工具适配器和对应测试继续由已确认 Build DAG 生成。
6. 浏览器不得直连 Python sidecar。调用链固定为 `Frontend -> Java Agent Gateway -> Python agent-runtime`，两段对话传输均使用 AG-UI SSE。
7. 认证、业务 API 和面向客户端的 Agent 网关继续由 Java 8 + Spring Boot 后端负责；Agent Runtime 固定使用独立仓库的 `master` 分支，不复制 Java 模板的 `auth` 分支。
8. 模板不携带真实 `.env`、密钥、业务 Prompt、业务 Agent 配置、业务工具、知识内容或用户数据。
9. 模板初始化、Build 生成、测试、启动和验收各自承担独立职责，目录存在不能替代正式状态和证据。
10. 实施时只支持新的当前契约：同步更新生产者、消费者、类型、测试和文档，不增加旧 manifest、旧目录或旧生成方式的兼容分支。

## 3. 与当前实现的关系

当前生产实现已经具备以下基础：

- RequirementSpec `agent_requirements[]`；
- ProductPlan `agents[]`；
- TechnicalPlan `agent_contracts[]`；
- 固定 Python 3.12 + DeepAgents sidecar；
- 固定 `ag-ui-sse` 与 `/internal/agents/<agentId>/run`；
- `agent:runtime`、`agent:<agentId>` Build Unit；
- `agent` owner 和 `/agent-runtime/**` 写边界；
- 业务 Agent、工具适配器和测试使用 `src/app/agent/`、`src/app/tools/` 与 `tests/` 下的确定性路径。

当前模板初始化已条件式处理 `frontend`、`backend` 和 `agent-runtime`，并在进入开发前执行 manifest 与真实 Git 来源门禁。项目启动、测试和代码审查尚未完整覆盖 Agent Runtime，不能据此把初始化完成误记为运行闭环。

## 4. 模板、平台与 Build 的所有权

| 内容 | 所有者 | 创建时机 | 是否允许模型修改 |
| --- | --- | --- | --- |
| Runtime 入口、AG-UI 适配、健康检查 | Agent Runtime 模板 | TechnicalPlan 确认后的模板初始化 | 否 |
| 模型工厂、可信上下文、交互恢复、checkpoint | Agent Runtime 模板 | TechnicalPlan 确认后的模板初始化 | 否 |
| `src/app/agent/factory.py` 内置 Chat 与业务 Agent 动态装配 | Agent Runtime 模板 | TechnicalPlan 确认后的模板初始化 | 否 |
| 模板仓库 URL、分支、commit、readiness | XCodeAgent 平台 | 模板初始化 | 否 |
| `src/app/agent/<agentId>.py` | Agent Build CodeRunner | Build DAG 确认后 | 仅对应 `agent:<agentId>` 任务 |
| `src/app/tools/<agentId>_tools.py` | Agent Build CodeRunner | Build DAG 确认后 | 仅对应 `agent:<agentId>` 任务 |
| `tests/test_<agentId>.py` | Agent Build CodeRunner | Build DAG 确认后 | 仅对应 `agent:<agentId>` 任务 |
| Java Agent Gateway | Backend/Data Source Build Agent | Build DAG 确认后 | 仅对应 Java 后端任务 |
| 页面 Agent 入口 | Frontend Build Agent | Build DAG 确认后 | 仅对应前端页面任务 |
| 模型密钥和内部网关凭证 | 部署环境 | 启动时 | 否，且不得落盘到正式产物 |

模板基础设施属于平台。业务 Agent CodeRunner 只能写入明确授权的 `src/app/agent/<agentId>.py`、`src/app/tools/<agentId>_tools.py` 和对应测试，不得修改 Server、Models、Interaction、Persistence、依赖基线、启动入口或安全边界。需要改变模板基础设施时，应升级独立模板仓库并重新走模板设计、验证和发布流程。

## 5. 最小模板仓库

### 5.1 目录结构

```text
agent-runtime/
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
│
├── src/
│   └── app/
│       ├── __init__.py
│       ├── main.py
│       ├── settings.py
│       │
│       ├── server/
│       │   ├── __init__.py
│       │   ├── app.py
│       │   ├── health.py
│       │   └── agui.py
│       │
│       ├── agent/
│       │   ├── __init__.py
│       │   ├── factory.py
│       │   └── context.py
│       │
│       ├── models/
│       │   ├── __init__.py
│       │   └── factory.py
│       │
│       ├── tools/
│       │   └── __init__.py
│       │
│       ├── interaction/
│       │   ├── __init__.py
│       │   ├── service.py
│       │   └── schemas.py
│       │
│       └── persistence/
│           ├── __init__.py
│           └── checkpointer.py
│
└── tests/
    └── test_runtime.py
```

`src/app/agent/` 是唯一 Agent 装配目录，`src/app/tools/` 是唯一工具目录。当前最小 `chat` Agent 直接在 `agent/factory.py` 中调用 `create_deep_agent`，不再为了默认 Agent 单独建立文件或根目录代码树；它只负责证明模型、Deep Agents、checkpoint 与 Chat 传输能够连通，不能作为业务 Agent 验收证据。对于其他合法 `lower_snake_case` ID，Factory 动态导入 `app.agent.<agent_id>`，并调用该模块的固定 `create_agent(*, model, runtime_context, checkpointer)` 入口；不存在的模块和缺少入口的模块分别返回稳定的 `agent_not_found` 与 `invalid_agent_module` 错误。

模板自身的基础设施契约测试可以保存在 `tests/`；它们只验证健康检查、内部认证、AG-UI 生命周期、流式文本和 checkpoint 交互恢复，不声明任何业务 Agent 已完成。

### 5.2 最小文件职责

| 文件 | 最小职责 |
| --- | --- |
| `README.md` | 开发启动、环境变量、调用链、禁止直连、安全边界和验证命令 |
| `pyproject.toml` | Python 3.12、运行依赖、开发依赖和命令入口 |
| `uv.lock` | 与 `pyproject.toml` 同步的可复现依赖锁 |
| `.python-version` | 固定 `3.12`，具体 patch 版本由模板验证矩阵确定 |
| `.env.example` | 只包含无密钥占位符和必要运行变量 |
| `main.py` | 进程入口；加载设置并暴露 ASGI app，不包含业务 Agent |
| `settings.py` | 唯一环境变量解析点；严格校验模型、内部认证、Java Backend HTTP(S) Origin 和 Tool Gateway 凭据且不打印密钥 |
| `server/app.py` | FastAPI 应用工厂和稳定路由注册 |
| `server/health.py` | `/health`；返回有界运行状态，不泄露配置和凭证 |
| `server/agui.py` | `/internal/agents/{agent_id}/run`；AG-UI 输入校验与事件流输出 |
| `agent/factory.py` | 校验 Agent ID；直接创建内置 `chat`，或动态加载 `app.agent.<agent_id>.create_agent` 并注入模型、可信上下文和 checkpointer |
| `agent/context.py` | Java 网关注入的可信用户、租户、权限和 trace 上下文 |
| `models/factory.py` | 使用 `init_chat_model` 根据项目默认配置创建支持 streaming 的 ChatModel |
| `tools/__init__.py` | 保留原设计中的应用内工具扩展包；最小模板不预置业务工具实现 |
| `interaction/service.py` | 普通消息、澄清、确认、中断和恢复的统一编排 |
| `interaction/schemas.py` | 待交互状态和恢复输入的最小严格 schema |
| `persistence/checkpointer.py` | 按 `threadId` 隔离 checkpoint；不提供独立 session CRUD API |

## 6. 不进入最小模板的旧设计内容

| 旧设计内容 | 本轮处理 | 原因或未来归属 |
| --- | --- | --- |
| `Dockerfile`、`compose.dev.yaml` | 删除 | 当前桌面端本地启动不依赖容器；部署方案确认后再加入 |
| `config.yaml` | 删除 | 最小运行参数统一由 `settings.py` 从环境读取，避免双配置源 |
| `agent.json` | 删除 | 不能把规划 JSON 直接当运行时事实源；如需注册清单应由确认产物确定性编译 |
| `prompts/AGENTS.md`、`SOUL.md`、`PROFILE.md` | 删除 | 属于业务 Agent 设计或用户内容，不应由通用模板预置 |
| `server/session.py` | 删除 | 多轮状态由 checkpoint 支撑，session 列表/历史/删除不属于基础 Chat |
| `agent/state.py` | 暂不预置 | 具体状态随 Agent 能力生成；通用待交互状态归 `interaction/schemas.py` |
| `agent/prompt_segments.py` | 删除 | 业务 Prompt 由正式 Agent Design/Build 生成，模板不拥有语义 |
| `models/client.py`、`registry.py`、`env.py` | 收敛为 `models/factory.py` | 初期只支持项目默认模型，不提前建设多 Provider 插件系统 |
| knowledge、MCP、plugin、workflow、sub-agent 工具 | 删除 | 由已确认 tool/knowledge/capability binding 决定并生成 |
| `human_reply.py`、skill loader | 删除 | 不预置未确认工具；基础交互走 Runtime 通用中断/恢复 |
| 15 个 middleware slot 及实现 | 删除 | 当前没有稳定、可验证的模板契约，不保留空实现 |
| permissions、approvals、secrets、rules | 不预置具体引擎 | Java 网关负责客户端认证；Runtime 只保留可信上下文与内部调用校验 |
| Store、OSS、session repository | 删除 | 最小版本只保留当前线程 checkpoint |
| A2UI、动态表单 | 删除 | 基础 Chat 先使用版本化待交互 state；UI 协议另行确认 |
| LocalSandbox | 删除 | 模板 Runtime 不默认获得文件或命令执行能力 |
| tracing、audit、AI label 完整体系 | 删除 | 先保留 trace 透传和安全日志边界；完整可观测性另行设计 |
| 大量预置测试分类 | 删除 | 模板仓库只验证基础设施；业务测试由 Build 生成并进入统一 Testing |

被删除的目录和文件可以保留在设计路线图中，但不得使用空类、`pass`、伪成功响应或永不调用的 stub 让模板看起来已经具备能力。

## 7. 基础 Chat AG-UI SSE 契约

### 7.1 调用拓扑

```text
Frontend Renderer
  -> Java Agent Gateway
  -> Python agent-runtime
  -> 具体业务 Agent

具体业务 Agent
  -> 声明过的 Python Tool Adapter
  -> Java Business API Endpoint
```

Frontend 只能访问 TechnicalPlan 中确认的 Java `gatewayEndpointId`。Java 网关使用内部 Runtime 地址调用 Python sidecar，并注入可信上下文；Tool Adapter 再通过声明过的 Java API Endpoint 获取业务能力。禁止形成浏览器直连 sidecar、Python 绕过 Java 直接访问数据库、或 Java 内重复实现业务 Agent 的路径。

### 7.2 Runtime 入口

```http
POST /internal/agents/{agent_id}/run
Content-Type: application/json
Accept: text/event-stream
```

约束：

- `agent_id` 必须符合当前 `lower_snake_case` 契约；
- `agent_id` 必须存在于当前已构建 Agent 集合；
- 请求使用 AG-UI `RunAgentInput`，不定义第二套 Chat 请求体；
- `threadId` 标识稳定会话，`runId` 标识单次执行；
- Java 网关注入的可信上下文与 Renderer 消息分离，不能从用户可控 `forwardedProps` 接受身份；
- 响应必须为 AG-UI SSE，禁止自定义 `data: {text: ...}` 文本协议；
- sidecar 监听地址、端口和内部调用凭证来自运行环境，不写进业务 Agent 文件。

### 7.3 最小事件生命周期

普通对话至少产生：

```text
RUN_STARTED
TEXT_MESSAGE_START
TEXT_MESSAGE_CONTENT × N
TEXT_MESSAGE_END
STATE_SNAPSHOT
CUSTOM(agent_runtime_result.v1)
RUN_FINISHED
```

按需增加标准 Tool Call 事件。成功结果 `agent_runtime_result.v1` 至少包含 `schemaVersion`、`agentId`、`threadId`、`runId` 和 `status=completed|awaiting_user`，不得包含完整 Prompt、密钥、工具响应正文或内部绝对路径。

处理后的业务失败使用版本化 `agent_runtime_error.v1`，包含稳定错误码、安全消息、是否可恢复和建议动作；未捕获异常使用标准 Run Error 终态。所有路径都只能产生一个终态，不能先成功再错误或遗漏结束事件。

### 7.4 澄清、确认与恢复

基础交互状态使用严格、版本化的 `pendingInteraction`，至少区分：

- `clarification`：补充缺失信息；
- `confirmation`：确认将产生后续动作的候选；
- `approval_required`：只表示需要上游审批，不在 Runtime 内自行批准。

流程固定为：

```text
Agent 产生待交互状态
  -> checkpoint 保存当前线程和 interactionId
  -> STATE_SNAPSHOT 发布 pendingInteraction
  -> result.status = awaiting_user
  -> 本轮正常结束
  -> 用户通过同一 agentId + threadId 发起下一次 AG-UI Run
  -> 校验 interactionId、状态和可信上下文
  -> 恢复 checkpoint 后继续执行
```

不使用长时间挂起的 HTTP 请求或进程内 `asyncio.Future` 等待用户。澄清回答只补充信息，不自动等同于需要显式确认的业务动作。

## 8. 模型层最小契约

`models/factory.py` 是模板唯一模型创建入口，负责：

1. 读取 `settings.py` 已验证的项目默认模型配置；
2. 使用 LangChain `init_chat_model` 创建 Deep Agents 可消费且支持 streaming 的 ChatModel；
3. 为每次调用绑定 timeout、输出预算和 trace metadata；
4. 在缺少 Provider、模型名或凭证时 fail fast；
5. 对日志和异常进行凭证脱敏；
6. 拒绝业务 Agent 通过请求体覆盖 Base URL、API Key 或任意模型 Provider。

初期不提供多模型注册市场、成本路由、自动降级链或业务 Agent 从请求体选择 Provider。项目环境可使用 `deepseek:`、`openai:`、`anthropic:`，以及映射到 OpenAI 兼容接口的 `qwen:`；`gpt:` 和 `claude:` 可作为便于阅读的别名。TechnicalPlan 当前只允许 `agentSettings.model.selection=project_default` 和 `modelRef=project_default`，因此一次运行仍只使用项目配置的默认模型；需要让业务 Agent 动态选模时必须先修改并确认正式 Agent Contract。

`.env.example` 只提供 `provider:model` 模型名、可选 Base URL、API Key 占位符、host、port、工作目录和内部网关认证变量。模板仓库、生成产物、manifest、日志和 AG-UI 状态中都不能出现真实密钥。

## 9. 交互层与 checkpoint

### 9.1 交互层职责

`interaction/service.py` 只负责：

- 把 AG-UI 输入、可信 RuntimeContext 和 Agent 实例组合成一次运行；
- 把模型输出、工具活动和待交互状态投射为 AG-UI 事件；
- 保存和恢复 checkpoint；
- 防止旧 `interactionId`、重复提交或其他用户/租户恢复当前线程；
- 统一完成、等待用户、业务错误和系统错误终态。

它不拥有具体业务问题、表单字段、审批决策或业务状态机。上述内容来自已确认的业务 Agent 设计和生成代码。

### 9.2 checkpoint 最小要求

- 默认使用本地、workspace 内隔离的持久化 checkpointer；
- key 至少绑定 `agentId + tenantId + userId + threadId`；
- 写入原子，进程重启后可以恢复当前契约状态；
- 不保存密钥、完整凭证或未裁剪工具输出；
- 不提供历史格式探测、迁移或 fallback reader；
- session 列表、历史浏览、删除和跨设备同步不属于本模板批次。

具体存储库和文件路径在模板实施计划中确定，并在当期 schema、清理策略和并发测试中一次闭合。

## 10. 模板初始化流程

### 10.1 触发条件

只有以下条件同时满足时，Agent Runtime 模板才是 required：

1. RequirementSpec 已确认；
2. ProductPlan 已确认且包含业务 Agent；
3. UiDesign 已确认或明确跳过；
4. TechnicalPlan 已确认且 `agent_contracts[]` 非空；
5. 生命周期由 TechnicalPlan 确认动作进入既有 `generating_application_template_files`。

普通应用 `agent_contracts=[]` 时，manifest 仍记录 `agentRuntime.required=false` 和 `status=skipped`，但工作区不创建 `agent-runtime/`。不得通过目录是否存在反向推断应用是否包含智能体。

### 10.2 下载和复用

```text
确认 TechnicalPlan
  -> 读取正式 TechnicalPlan 判断 agentRuntime.required
  -> 检查 frontend/backend/agent-runtime 目标目录
  -> 复用来源、分支、commit 和入口均有效的现有模板
  -> 依次下载缺失的 required 模板，每个最多尝试 3 次
  -> GitHub HTTPS 首次连接超时时尝试等价 SSH 传输
  -> 任一 required 模板失败则停止后续初始化
  -> 写入 manifest
  -> 执行真实文件门禁
  -> ready_for_workbench 或既有终止失败状态
```

Agent Runtime 模板的规范仓库固定为 `https://github.com/Bettetman/agent-runtime-template.git`，分支固定为 `master`。实际拉取可使用指向同一 GitHub 仓库的 `git@github.com:...` SSH 地址，manifest 记录真实传输地址，完成门禁按规范化后的仓库身份校验。`frontend` 与 `backend` 继续根据权限开关选择配套的 `main` 或 `auth`；Agent Runtime 不参与二者的分支一致性判断。

已存在但来源、分支、commit 或入口不可验证的 `agent-runtime/` 必须报错，不能覆盖、合并、删除或退回空目录生成。模板下载仍由 Electron Main 执行，Renderer 只通过现有受控 bridge 发起，不直接运行 Git。

### 10.3 manifest 目标结构

现有 `.xcodeagent/template-generation-manifest.json` 的 `download.targets` 扩展为当前三目标结构：

```json
{
  "frontend": {
    "required": true,
    "status": "succeeded",
    "path": "frontend",
    "repositoryUrl": "<frontend-template-repository>",
    "branch": "main",
    "commitSha": "<commit>",
    "attempt": 1,
    "error": null
  },
  "backend": {
    "required": true,
    "status": "succeeded",
    "path": "backend",
    "repositoryUrl": "<backend-template-repository>",
    "branch": "main",
    "commitSha": "<commit>",
    "attempt": 1,
    "error": null
  },
  "agentRuntime": {
    "required": true,
    "status": "succeeded",
    "path": "agent-runtime",
    "repositoryUrl": "https://github.com/Bettetman/agent-runtime-template.git",
    "branch": "master",
    "commitSha": "<commit>",
    "attempt": 1,
    "error": null
  }
}
```

无业务 Agent 时 `agentRuntime` 使用 `required=false`、`status=skipped`，其仓库、分支、commit、attempt 和 error 使用当期严格 schema 规定的空值，不省略整个目标。实施时同步替换前后端协议类型和全部消费者，不保留只接受两目标的兼容解析。

### 10.4 Agent Runtime 模板门禁

初始化完成门禁至少检查：

- manifest 中 `agentRuntime.required` 与最新已确认 TechnicalPlan `agent_contracts[]` 一致；
- required 时目标为 `succeeded`，仓库来源、`master` 分支和 commit 可验证；
- `agent-runtime/` 是真实目录且不为符号链接；
- `README.md`、`pyproject.toml`、`uv.lock`、`.python-version`、`.env.example` 存在；
- `src/app/main.py`、`settings.py`、`server/app.py`、`server/health.py`、`server/agui.py` 存在；
- `agent/factory.py`、`agent/context.py`、`models/factory.py`、`tools/__init__.py`、`interaction/service.py`、`interaction/schemas.py`、`persistence/checkpointer.py` 存在；
- `src/app/agent/`、`src/app/tools/` 和 `tests/` 是真实目录；
- 模板不包含真实 `.env` 或其他已知敏感文件。

模板门禁不要求任何 `src/app/agent/<agentId>.py`、`src/app/tools/<agentId>_tools.py` 或 `tests/test_<agentId>.py`，因为这些业务文件只能在 Build DAG 确认后生成。

## 11. Build DAG 调整

### 11.1 `agent:runtime`

当前 `agent:runtime` 从“模型生成共享 sidecar 基础设施”收敛为“平台确定性验证和准备已下载模板”：

- 验证模板来源、commit、入口和依赖基线；
- 验证 TechnicalPlan Runtime、AG-UI 路径和模板公开扩展契约一致；
- 不让模型修改 Server、Models、Interaction、Persistence 等共享 Runtime 基础设施；
- 不生成 `agent.json` 或复制整个 `agent_contracts[]` 到运行目录；
- 产出可归因的 `agent.runtime` readiness 证据。

如果未来需要生成运行时注册清单，必须由平台根据已确认正式产物确定性编译，并另行确认 schema、路径、hash、失效与安全边界；不能由模型自由生成。

### 11.2 `agent:<agentId>`

每个业务 Agent Unit 继续只生成：

```text
agent-runtime/src/app/agent/<agent_id>.py
agent-runtime/src/app/tools/<agent_id>_tools.py
agent-runtime/tests/test_<agent_id>.py
```

Backend TechnicalPlan/Build 契约与第三模板统一使用上述应用包内路径，不保留根级 `agents/`、`tools/` 旧路径兼容。

约束：

- Agent 模块暴露固定的 `create_agent(*, model, runtime_context, checkpointer)` 构造入口，复用模板通过 `init_chat_model` 创建并注入的项目默认模型，不得二次初始化模型；
- Tool Adapter 只能调用同一 TechnicalPlan 中声明的 Java API Endpoint；
- Tool Adapter 通过模板 Settings 读取 `AGENT_RUNTIME_BACKEND_BASE_URL` 与 `AGENT_RUNTIME_TOOL_GATEWAY_TOKEN`，前者只能是无账号、密码、query、fragment 和业务路径的 HTTP(S) Origin，后者不得进入源码、日志或模型上下文；
- 测试覆盖 Agent 构造、能力绑定、工具输入输出边界和至少一个 AG-UI 对话路径；
- 任务不修改 Runtime 模板、Java 网关、Frontend 或正式规划产物；
- 真实 Diff、allowed paths、required checks 和 task result 继续由现有 Build Subgraph 验证。

## 12. 启动、健康检查与停止

### 12.1 启动条件

Project Launch 在存在业务 Agent 时必须同时验证：

- Agent Runtime 模板 readiness 已通过；
- 所有 required Agent Build Unit 已成功；
- 依赖安装与 Runtime 基础检查已成功；
- Java Agent Gateway 与 Python internal path 的契约一致；
- 运行时必要环境变量存在，但不把值写入启动证据。

### 12.2 启动顺序

```text
分配 Java backend 和 agent-runtime 本地端口
  -> 向两侧注入彼此内部地址及网关认证配置
  -> 启动 Java backend 与 Python agent-runtime
  -> 分别轮询健康检查
  -> 两者都健康后启动/展示 frontend preview
```

Java backend 与 Agent Runtime 互相提供运行能力，健康检查不得通过同步调用对方形成启动死锁。基础 `/health` 只判断本进程与必要本地依赖；跨服务连通性由 Launch/Integration Testing 单独检查。

任一 required 服务启动失败时不启动 Frontend preview，不把部分启动伪装成成功。停止时先停止 Frontend preview，再停止 Agent Runtime 和 Java backend，并清理本轮进程句柄；不删除工作区、checkpoint 或用户产物。

### 12.3 健康返回

`GET /health` 返回有界、稳定、无敏感信息的当前状态，至少包含服务名和 `status`。它不返回模型 Base URL、API Key、内部凭证、完整环境变量、Prompt、Agent 配置或物理工作区路径。

## 13. 安全边界

1. Agent Runtime 默认仅监听 loopback 或受控内部网络，不对 Renderer 暴露地址。
2. Java 网关到 sidecar 必须使用独立内部认证；身份字段只在认证通过后可信。
3. `RuntimeContext` 与用户消息分离，绑定 user、tenant、scope、thread、run 和 trace。
4. 用户可控输入不能覆盖模型密钥、Provider、内部 URL、allowed tools 或权限范围。
5. Tool Adapter 只获得声明过的 Java Endpoint 和最小用户上下文，不直接读取 Java 后端凭证。
6. 模板默认不提供 shell、任意文件、数据库、外网或动态插件工具。
7. 日志只记录有界元数据；模型内容、工具结果和异常栈必须按环境策略裁剪和脱敏。
8. `.env`、凭证、私钥和用户数据不能进入模板仓库、Build Diff、manifest、AG-UI state 或安装包。
9. 依赖安装、网络访问、发布和部署继续使用外层现有审批边界，业务 Agent 不能自行授权。

## 14. 生命周期与 AG-UI 集成

本设计不增加新的顶层生命周期枚举。模板初始化继续使用：

```text
generating_application_template_files
  -> ready_for_workbench
  -> application_template_generation_failed
       -> generating_application_template_files  # 用户显式重试，不重跑已确认 TechnicalPlan
```

现有 `/application-lifecycle/run` 的 `prepare_template_generation` 和 `complete_template_generation` 扩展为条件处理 `agentRuntime` 目标，并继续发出完整 AG-UI 生命周期、结构化结果/错误和状态快照。

Runtime Chat 自身使用独立的 Java gateway Endpoint 和 Python internal path，不进入 XCodeAgent 的 `/workflow/run`。它是生成应用的业务运行流，不是 XCodeAgent 规划或 Build 流；但仍必须遵守 AG-UI 完整生命周期与 thread/run 隔离。

## 15. 实施影响面

实施阶段至少需要同步修改并验证以下边界：

| 范围 | 主要影响 |
| --- | --- |
| Agent Runtime 模板仓库 | 创建本文最小目录、依赖、AG-UI Chat、模型、交互、checkpoint 和健康检查 |
| Electron Main | 条件克隆/复用第三模板，校验来源和入口，返回三目标结果 |
| Renderer template service | 提交 Agent Runtime 模板 URL，展示第三目标失败摘要 |
| Frontend 类型/Preload | `TemplateDownloadResult` 增加严格 `agentRuntime` 目标 |
| Backend lifecycle protocol | 校验三目标 download result 与 required/skipped 语义 |
| Template manifest/service | 根据 TechnicalPlan 判断 required，写入并验证第三目标 |
| Build Unit/Task Planner | 把 `agent:runtime` 收敛为模板 readiness，保留 per-Agent 生成任务 |
| Workspace inspection | 识别并投射 Agent Runtime 目录，但不把模板源码交给业务模型任意修改 |
| Testing/Code Review | 增加 Python 依赖、compile、pytest、AG-UI contract 和安全检查范围 |
| Project Launcher | 启停 Agent Runtime、轮询健康、注入 Java/Runtime 内部地址 |
| Acceptance | 绑定真实 Runtime 进程、AG-UI 对话、工具调用、Java 网关和页面入口证据 |

任何实施批次都必须先列出准确文件、协议字段、事件、状态和测试，获得确认后再修改；不得一次把所有开放能力塞进模板。

## 16. 分阶段实施建议

### 阶段 A：独立模板仓库

- 建立最小目录与 Python 3.12 依赖基线；
- 实现 `/health` 和无业务 Agent 时可启动的 app；
- 实现模型工厂、Agent loader、基础 AG-UI Run 和 checkpoint；
- 提供模板级单元、契约和安全测试；
- 固定正式仓库 URL、`master` 分支和发布 commit。

### 阶段 B：XCodeAgent 模板初始化

- 扩展 Electron IPC、Preload、Renderer types 和 Backend protocol；
- 扩展 manifest、readiness 和生命周期门禁；
- 验证无 Agent 应用不创建目录、有 Agent 应用必须成功下载模板；
- 保持页面、API、实体和权限模板路径回归不变。

### 阶段 C：Build 与质量链

- 收敛 `agent:runtime` 任务职责；
- 让 per-Agent 生成代码符合模板 loader 契约；
- 把 Python checks 纳入现有 Testing 和 Code Review；
- 验证真实 Diff 与越界写拒绝。

### 阶段 D：启动与端到端验收

- 扩展 Project Launcher；
- 验证 Frontend -> Java Gateway -> Agent Runtime -> Java Tool Endpoint；
- 验证普通 Chat、流式输出、工具调用、澄清/确认恢复和错误终态；
- 在 Electron 中完成真实运行、控制台、网络和失败恢复验收。

## 17. 验收标准

### 17.1 模板仓库

- 干净 clone 后使用 Python 3.12 和锁定依赖可以启动；
- `/health` 返回成功且不泄露敏感信息；
- 未生成业务 Agent 时 Chat 返回稳定、可恢复的 Agent Not Found 错误；
- 放入符合契约的 Agent 模块后，factory 可以按 `agentId` 加载；
- 模型 streaming 能转换为合法 AG-UI 文本事件；
- checkpoint 支持进程重启后的同线程恢复；
- 非法 Agent ID、缺失内部认证、伪造身份和跨租户恢复被拒绝；
- 模板中不存在真实 `.env`、示例业务 Agent、示例业务工具或伪测试结果。

### 17.2 初始化

- `agent_contracts=[]` 时 `agentRuntime=skipped` 且不创建目录；
- `agent_contracts` 非空时第三模板为 required；
- 下载、复用、三次失败、非空未知目录和来源不匹配均有确定结果；
- manifest、Backend protocol、Frontend types 和真实目录使用同一当前契约；
- required Agent Runtime 未就绪时不能进入开发；
- 初始化不预建具体 Agent、工具或业务测试文件。

### 17.3 Build 与运行

- `agent:runtime` 不再由模型改写模板基础设施；
- 每个 `agent:<agentId>` 只写三个确定性业务路径；
- Python compile、pytest、AG-UI contract 和内部网关检查提供真实命令证据；
- 浏览器不能发现或直连 sidecar；
- Java gateway 与 Runtime 均健康后才展示可用 Preview；
- 普通消息、流式文本、工具调用、澄清、确认、恢复和错误各只有一个完整 AG-UI 终态；
- 无 Agent 的既有应用开发路径和启动行为保持不变。

## 18. 非目标与后续决策

本文不决定：

- 独立十部分 Agent Design Markdown/JSON 的最终 schema、revision 和 hash；
- active/draft/candidate 配置版本、晋升和回滚；
- Skill、知识库、MCP、插件和子 Agent 的运行时实现；
- 完整审批、沙箱、审计、追踪、成本限制和模型降级体系；
- 容器、远程部署、扩缩容和生产服务发现；
- 会话管理 UI、历史浏览、删除、跨设备同步和长期记忆；
- Agent Runtime 模板仓库的发布流程和 commit pin 策略；
- checkpoint 的最终存储实现和清理周期。

上述能力必须分别形成最小闭合设计并获得确认。它们不能作为空目录、空类、占位 JSON 或默认放行逻辑提前进入模板。

## 19. 阶段 A 独立模板与阶段 B 初始化实现记录

2026-09-04 完成独立模板仓库 `https://github.com/Bettetman/agent-runtime-template.git` 的 `master` 分支，并接入 XCodeAgent 条件式模板初始化：

- 使用 Python 3.12、`pyproject.toml` 和 `uv.lock` 固定可复现依赖；
- `/health` 无需模型密钥即可用于模板 readiness；
- `/internal/agents/{agent_id}/run` 使用官方 AG-UI `RunAgentInput` 和 `EventEncoder`；
- 内部 Chat 强制 Bearer token、可信 user/tenant/scopes 上下文，浏览器身份不从 `forwardedProps` 获取；
- 项目默认模型工厂通过 `init_chat_model` 支持 DeepSeek、GPT、Claude、Qwen 等模型并启用 streaming；
- `src/app/agent/factory.py` 直接使用 `create_deep_agent` 创建最小 `chat` Agent，配置模型后无需先生成业务 Agent 即可独立对话；
- 根目录不建立 `agents/` 或 `tools/`，Agent 与工具代码边界回到原设计的 `src/app/agent/` 和 `src/app/tools/`；
- checkpoint 线程键绑定 agent、tenant、user、thread 并进行 SHA-256 收敛；
- 待交互恢复校验当前 `interactionId`，使用 LangGraph `Command(resume=...)`；
- handled failure 发送版本化错误事件、StateSnapshot 和 RunFinished；未捕获异常使用 RunError 终态；
- 除 `factory.py` 中的最小 `chat` 装配和基础系统提示词外，不预置业务 Agent、业务工具、知识库、MCP、Docker、session CRUD 或复杂审批。

已完成的本地验证：

- `uv lock`：成功，生成当前 `uv.lock`；
- `uv sync --frozen`：成功，按当前 82 个锁定包同步本地模板环境；
- `uv run python -m compileall -q src tests`：成功；
- `uv run pytest`：12 项通过，存在 1 条来自 Starlette TestClient/AnyIO 的第三方弃用警告；
- 使用无效测试密钥完成离线构造验证：`deepseek:` 生成 `ChatDeepSeek`，`openai:` 和 `qwen:` 生成 `ChatOpenAI`，`anthropic:` 生成 `ChatAnthropic`；
- `src/app/agent/factory.py` 使用真实 `create_deep_agent` 成功构造 `CompiledStateGraph`，未发起远程模型请求；
- 实际启动 `uv run agent-runtime`：成功监听 `127.0.0.1:8010`；
- `GET /health`：HTTP 200，返回 `status=ok` 和 `transport=ag-ui-sse`；
- 无内部认证的 Chat：HTTP 401；
- 已认证但 Agent 不存在的 Chat：完整发送 RunStarted、Text Message、`agent_runtime_error.v1`、StateSnapshot 和 RunFinished。

当前未完成：

- 尚未使用真实模型密钥验证内置 Chat 或生成一个真实业务 Agent；
- 尚未接入 Java Agent Gateway、Python Tool Adapter、Testing、Code Review、Project Launch 和 Electron；
- 尚未完成真实业务 Chat、工具调用、澄清/确认和重启恢复的端到端验收。

## 20. Agent Contract 到业务 Runtime 生成实现记录

2026-09-06 完成阶段 C 的首个最小切片，使完整 Agent Contract 能够进入模板约束下的业务 Agent 代码生成：

- 模板 Factory 保留可独立运行的内置 `chat`，并按合法 Agent ID 动态加载 `app.agent.<agent_id>`；业务模块缺失或接口不合法时返回稳定错误，不修改 Factory 注册表；
- 业务模块固定公开 `create_agent(*, model, runtime_context, checkpointer)`，模型、可信上下文和 SQLite checkpointer 均由模板注入；业务生成代码不得再次调用 `init_chat_model`；
- Runtime Settings 增加 `AGENT_RUNTIME_BACKEND_BASE_URL` 与 `AGENT_RUNTIME_TOOL_GATEWAY_TOKEN`，Java 地址只接受安全 HTTP(S) Origin，凭据缺失时 fail fast；
- XCodeAgent 新增内置 `agent-runtime-generate` Skill，强制业务 CodeRunner 先读取模板接口，再按需读取业务 Agent 与 Java Tool Adapter 规则；
- Generator 只把当前任务对应的完整 Agent Contract，以及 Tool `apiContractId` 实际引用的 Java API Contract/Schema 投射给 CodeRunner，不泄露无关 API；
- 每个业务 Agent 仍只拥有 Agent 模块、Tool Adapter 和聚焦测试三个确定性路径；Tool 身份来自可信 `RuntimeContext`，只能调用 Contract 声明的 Java Endpoint。

本切片闭合的是“正式 Contract → 受控业务 Python 文件生成接口”，不代表 Java Gateway、真实业务 Agent E2E、Testing、Code Review 或 Project Launch 已完成。

发布与验证记录：

- 独立模板已提交并推送到 `Bettetman/agent-runtime-template@master`，发布提交为 `c3362eda04d19941e20f4702e693566f6e0d23aa`；
- 模板使用自身 Python 3.12 执行 `compileall` 通过，`git diff --check` 通过；
- 模板完整 `pytest` 未执行到测试阶段：全新环境同步依赖时下载 `anthropic==1.3.0` 超时，本轮不把网络失败记作测试通过或代码失败；
- XCodeAgent 的 Agent Runtime Runner、TechnicalPlan 与 Build Unit 聚焦测试 25 项通过，新增内置 Skill 完整性测试 1 项通过，变更 Python 文件 `py_compile` 与 `git diff --check` 通过；
- XCodeAgent 全量 `test_builtin_skills` 仍有 1 项当前 HEAD 已存在的 Spring Boot Skill 文案断言差异，与本切片新增 Runtime Skill 无关；正式 Backend 未运行，因此 `/health` 本轮无法连接。
