# Agent Runtime Direct 正式合同设计

> 状态：待用户审核  
> 适用拓扑：`agent_runtime_direct`  
> 原则：只支持新当前合同，不读旧字段、不双写、不迁移历史计划

## 1. 事实源

| 事实 | 唯一来源 |
| --- | --- |
| Auth 是否开启 | `.xcodeagent/application.json.auth.enable` |
| Authorization 是否开启 | `.xcodeagent/application.json.authorization.enabled` |
| Agent 产品语义和 Surface | 已确认 ProductPlan |
| 拓扑身份、工程架构和 Agent 执行合同 | 已确认 TechnicalPlan |
| 模板实际能力 | `.xcodeagent/template-state.json.effective` |
| Endpoint 字段映射 | 本拓扑不生成业务 Endpoint，因此不存在 |
| Build Unit 和任务 | 已确认 BuildTaskPlan |
| 运行端口、进程和临时凭据 | `.xcodeagent/runtime/`，不进入正式产物 |

TechnicalPlan 可以保存 Auth 终止点的派生投影，但不能复制 `auth.enable` 或 `authorization.enabled` 作为第二份开关。

## 2. TechnicalPlan 拓扑投影

正式 TechnicalPlan 增加：

```json
{
  "topology": {
    "type": "agent_runtime_direct",
    "publicEdgeServiceId": "agent-runtime",
    "serviceIds": ["agent-runtime"],
    "authenticationTermination": "agent-runtime",
    "sourceFactsSha256": "sha256:..."
  }
}
```

当 `auth.enable=false` 时：

```json
{
  "authenticationTermination": "anonymous-session"
}
```

字段约束：

- `type` 只能来自 Registry；
- `serviceIds` 不包含 Frontend 静态资产，也不包含 Backend/Gateway；
- `sourceFactsSha256` 绑定本次选型消费的 ProductPlan 事实和 canonical application config revision；
- 拓扑投影不保存 managed roots、端口、模板分支、Task 或运行状态；
- Auth/Authorization 配置变化会使旧 TechnicalPlan 失效并重新确认。

## 3. TechnicalPlan 架构约束

`architecture` 必须且只能包含：

```json
{
  "frontend": "React 客户端通过 AG-UI 访问 Agent Runtime Public Edge。",
  "agent_runtime": "Python 3.12 + DeepAgents，承载公开 AG-UI、Agent 执行、认证和会话状态。",
  "data": "Runtime 内部持久化只保存认证及 Agent 运行状态，不承载通用业务实体。"
}
```

正式计划必须满足：

- `entities=[]`；
- 不生成业务 `api_contracts`；
- 页面不存在 Backend Endpoint dependency；
- `agent_contracts[]` 非空；
- 页面实现只依赖 Agent Surface、Auth 和本地 UI 行为。

Agent Invocation 是 Runtime 公开执行合同，不作为普通业务 `api_contracts` 伪装成 Java Endpoint。

## 4. Agent Invocation 当前合同

当前 `gatewayEndpointId` 与该拓扑冲突，实施时直接替换，不保留 fallback：

```json
{
  "invocation": {
    "protocol": "ag-ui",
    "transport": "sse",
    "serviceId": "agent-runtime",
    "endpointId": "agent.inventory_assistant.run",
    "method": "POST",
    "path": "/agents/inventory_assistant/run",
    "exposure": "public",
    "authMode": "application"
  }
}
```

约束：

- `endpointId` 由平台根据 `agentId` 确定性生成；
- `path` 不由模型自由生成；
- `authMode=application` 表示运行时读取 canonical Auth capability，不复制开关；
- Frontend 只能调用已确认 Agent Contract 中的公开 path；
- 不存在 `gatewayEndpointId`、`internalRuntimeEndpointId` 或 Java route；
- 多 Agent 使用独立 `agentId` path，共享同一 Runtime Public Edge。

## 5. Runtime Tool Binding 当前合同

`agentSettings.tools.bindings[]` 的来源类型固定为：

```text
runtime_builtin
external_http
mcp
knowledge
```

示例：

```json
{
  "toolId": "search_public_documents",
  "source": {
    "type": "external_http",
    "adapterId": "public_document_search",
    "operationId": "search"
  },
  "accessMode": "read",
  "requiresConfirmation": false
}
```

写操作必须声明：

```json
{
  "accessMode": "write",
  "requiresConfirmation": true
}
```

禁止字段和值：

- `source.type=backend_endpoint`；
- Java API Contract / Endpoint 引用；
- 物理 Base URL、API Key 或 secret；
- 请求体提供的临时 Tool schema；
- Prompt 或 Skill 扩大的权限。

外部 Tool Adapter 的 schema、允许域名、超时、最大响应和凭据引用由平台编译，用户只确认业务语义和风险。

## 6. Runtime 与 Security 投影

Agent Contract 的 `runtime` 至少包含：

```json
{
  "language": "python",
  "version": "3.12",
  "framework": "deepagents",
  "serviceId": "agent-runtime",
  "publicProtocol": "ag-ui-sse",
  "stateOwner": "agent-runtime"
}
```

`security` 至少包含：

```json
{
  "principalSource": "runtime_authentication",
  "resourceIsolation": "principal_owner",
  "acceptsClientIdentityHeaders": false,
  "rbacEnabled": false,
  "toolAuthorization": "contract_allowlist",
  "writeToolConfirmationRequired": true
}
```

当 Auth 关闭时，`principalSource` 为 `runtime_anonymous_session`。`rbacEnabled` 只是一条派生安全事实，不是可由模型或 TechnicalPlan 修改的应用开关。

## 7. RequestedConfig 与 TemplateState

`application.json` 仍按现有规则编译基础能力：

| Application Config | Requested capability |
| --- | --- |
| `auth.enable=true` | `login` |
| `auth.enable=false` | 不请求 `login` |
| `authorization.enabled=false` | 不请求 `authorization` |

Topology Planning 另外请求结构能力：

| 条件 | Requested capability |
| --- | --- |
| 所有 direct 应用 | `agent_runtime_public_edge` |
| `auth.enable=true` | `agent_runtime_public_auth` |
| `auth.enable=false` | `agent_runtime_anonymous_session` |
| 所有 direct 应用 | `agent_runtime_principal_ownership` |
| 开发环境 | `agent_runtime_local_debug` |

Template Engine 负责能力依赖解析。示例：`agent_runtime_public_auth` 可以依赖 `login`，但 XCodeAgent 不通过修改 `application.json` 自动补开登录。

TemplateState 的 `effective` 必须完整包含当前模式要求的能力。Git 模板模式不能把 Requested capability 原样复制成 Effective；Runtime 模板必须提供可验证的 capability manifest 和真实入口证据。

## 8. Template capability 证据

每个 Runtime capability 必须至少关联：

- capability id；
- template revision；
- 入口文件；
- 路由或中间件证据；
- 配置 schema；
- 对应 contract test id；
- 是否允许生产环境启用。

仅存在目录、README、环境变量或空路由不算 Effective。Bootstrap Readiness 必须验证 capability 证据与当前模板 commit 一致。

## 9. Build Unit Blueprint

Planning 输出固定 Unit：

```text
application:root
frontend:shell
frontend:api-client
frontend:auth-guard          # 仅 auth.enable=true 时参与任务
frontend:agent-surface:<pageId>
agent:runtime
agent:<agentId>
app:integration
```

禁止 Unit：

```text
backend:bootstrap
backend:endpoint:*
gateway:*
database:*
authorization:*
```

`frontend:api-client` 在本拓扑中是 AG-UI Public Edge Adapter，不是 REST Backend Client。

## 10. Hash 与失效传播

Topology hash 至少绑定：

- topology type；
- ProductPlan Agent/Surface/behavior facts；
- application config revision；
- Agent Invocation projection；
- Runtime Tool source kinds；
- required template capabilities。

以下变化使 TechnicalPlan、BuildTaskPlan 和下游证据失效：

- Auth 或 Authorization 配置变化；
- ProductPlan 新增需要业务后端的 action/step；
- Agent 新增 Backend Endpoint Tool；
- Agent Invocation、Runtime 或 Security 合同变化；
- Runtime Template capability/revision 变化；
- 拓扑变更。

Prompt、Temperature 等不改变拓扑的 Agent 配置修订只定向失效对应 Agent Unit、Runtime tests、Integration 和 Acceptance，不重新选择拓扑。

## 11. Formal Revision

自然语言提出“增加数据库”“增加后台管理”“开启 RBAC”“Agent 调用 Java 业务接口”等变化时：

1. 进入现有 Formal Revision；
2. 生成 ProductPlan/TechnicalPlan 候选；
3. 重新运行 topology matcher；
4. 若退出 `agent_runtime_direct`，必须展示服务和目录变化；
5. 用户确认新的 TechnicalPlan 后再废弃旧 Build DAG；
6. 不在旧工作区原地保留未使用的 Backend/Gateway 兼容目录。

本项目不支持历史合同读取。已有旧计划需要重新规划，不做 JSON 字段迁移。
