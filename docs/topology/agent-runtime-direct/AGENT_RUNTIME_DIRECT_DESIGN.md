# Agent Runtime Direct 整体设计

> 拓扑枚举：`agent_runtime_direct`  
> 状态：设计已确认，实现已接入当前工作树，待聚焦验证  
> 当前行为：已注册、可自动匹配、可编译三阶段蓝图，已接入 TechnicalPlan/Bootstrap/Build/Launch  
> 目标工程：`frontend/ + agent-runtime/`，不生成 Java Backend 或 Gateway

## 1. 设计结论

`agent_runtime_direct` 面向只有 Agent 应用能力、没有独立 Java 业务后端需求的生成应用。Frontend 通过公开 AG-UI Endpoint 直接访问 Agent Runtime；Runtime 同时拥有 Agent 执行、会话状态、认证终止和用户资源归属校验。

创建应用时不要求用户每次手动选择模式。平台根据已确认产品事实和 `.xcodeagent/application.json` 自动匹配唯一拓扑：

1. 只有 `agent_runtime_direct` 满足时自动选择；
2. 没有已注册拓扑满足时继续使用尚未迁移的当前流程；
3. 多个拓扑同时满足且结果存在真实工程差异时，只在 TechnicalPlan 设计阶段澄清一次；
4. 用户明确要求“纯 Agent、不要 Java 后端”时只形成设计约束，仍必须通过确定性不变量校验；
5. 拓扑确认后写入 TechnicalPlan，下游不得再根据目录、Agent 数量或开关重新猜测。

拓扑不是应用开关，不在 `application.json` 增加 `topology.type`、`backend.enabled` 或 `gateway.enabled`。`application.json` 继续只拥有 `auth.enable`、`authorization.enabled` 等应用级能力事实。

## 2. 适用范围

本拓扑适用于：

- ProductPlan 至少包含一个启用的 Agent Surface；
- 应用的主要业务能力由 Agent 对话、Agent 交互和 Runtime 原生能力完成；
- 页面只需要 Agent UI、登录 UI、导航和本地展示状态；
- 不需要 Java 业务 Endpoint、Java Adapter、内部 RPC 或 Gateway；
- 不需要业务实体 CRUD、MySQL 业务数据模型或数据库事务；
- Agent Tool 只使用 Runtime 原生工具、知识、MCP 或外部服务适配器，不绑定 Java Backend Endpoint；
- `authorization.enabled=false`；
- `auth.enable` 可以为 `true` 或 `false`。

Runtime 自身的 user、session、thread、run、checkpoint、memory、interaction 和审计元数据不属于业务后端实体，不会因此退出本拓扑。

## 3. 禁止匹配条件

出现以下任一事实时，平台不得选择本拓扑：

- `authorization.enabled=true`；
- ProductPlan 存在必须由服务端业务 API 实现的非 Agent action 或 business step；
- TechnicalPlan 需要业务实体、业务 API Contract、数据源映射或数据库变更；
- Agent Tool 依赖 Java Endpoint、Java Domain Service、Gateway Executor 或 Backend Adapter；
- 需要多个公开后端服务、内部服务身份传播或 Java/Python 双向 RPC；
- 需要 Agent RBAC、页面/操作资源目录或角色授权管理；
- 需要 Runtime 绕过受控 Tool Adapter 直接访问业务数据库；
- Runtime 模板不能提供当前 Auth 模式要求的公开入口和 ownership 能力。

平台不得为了进入本拓扑删除业务 Endpoint、忽略实体、关闭 RBAC 或把 Java Tool Binding 改写成外部调用。事实不满足时必须选择其他拓扑或停留在澄清阶段。

## 4. 目标架构

### 4.1 Auth 开启

```text
Browser / Electron Preview
        │
        ├── login capability ───────────────┐
        │                                   ▼
        └── AG-UI SSE ───────────────> Agent Runtime
                                         ├── Authentication termination
                                         ├── Trusted Principal
                                         ├── Agent execution
                                         ├── Thread / Run / Checkpoint
                                         ├── Memory / Interaction
                                         └── Runtime-native Tool Adapters
```

### 4.2 Auth 关闭

```text
Browser / Electron Preview
        │
        ├── server-issued anonymous session
        └── AG-UI SSE ───────────────> Agent Runtime
                                         ├── Anonymous Principal
                                         ├── Agent execution
                                         └── owner-scoped runtime state
```

Frontend 不持有 Runtime 内部凭据，不伪造 `userId`、`tenantId`、role 或 scope。公开请求中的 Principal 只能由 Runtime 的认证或匿名会话中间件建立。

## 5. 服务与目录边界

生成工作区固定包含：

```text
<workspace>/
├── frontend/
├── agent-runtime/
└── .xcodeagent/
```

不得生成：

```text
backend/
gateway/
backend-common/
Java Maven modules
Java Agent Gateway
Backend Endpoint adapters
```

服务所有权：

| 边界 | 所有者 |
| --- | --- |
| 页面、Agent Surface、登录界面、AG-UI Client | Frontend |
| Public Edge、Auth 终止、Principal、CORS | Agent Runtime 模板 |
| Agent 装配、Prompt、Memory、Tools、Skills、Knowledge | Agent Runtime + Agent Generator |
| thread/run/checkpoint/memory/interaction | Agent Runtime |
| topology、TemplateState、Build DAG、启动和验收 | XCodeAgent 平台 |
| 模型与外部服务密钥 | 部署环境 |

## 6. 自动选型时机

拓扑选择属于 TechnicalPlan 设计阶段，不属于应用创建表单，也不属于 Build 阶段。

```text
Confirmed ProductPlan
        +
Canonical application.json
        ↓
平台提取 Topology Design Facts
        ↓
Registry 中的 Definition.matches
        ↓
唯一匹配 / 无匹配 / 冲突澄清
        ↓
Topology Definition.compile_design
        ↓
TechnicalPlan candidate + topology projection
        ↓
用户确认 TechnicalPlan
```

`Topology Design Facts` 只包含匹配所需事实：启用的 Agent、Surface、产品 action/step 分类、服务端业务能力需求、Auth/Authorization 能力和用户明确的架构约束。它不是第五种正式产物，也不单独落盘。

如果 ProductPlan 无法确定某项业务行为是否需要独立业务后端，TechnicalPlan 设计阶段只澄清该差异，不向用户展示内部枚举列表让其盲选。

## 7. 三阶段策略

### 7.1 Design

`compile_design` 负责：

- 校验直接拓扑不变量；
- 声明 `agent-runtime` 为唯一 API Public Edge；
- 声明认证终止点；
- 生成 TechnicalPlan 的最小拓扑投影；
- 生成只包含 `frontend`、`agent_runtime`、`data` 的架构摘要；
- 选择拓扑对应的 Agent Invocation 合同。

Design 不决定端口、进程 PID、模板 commit 或 Build Task。

### 7.2 Planning

`compile_planning` 负责：

- managed roots；
- Template capabilities；
- Build Unit/Edge Blueprint；
- Generator owner；
- required checks；
- 不允许出现的 Backend/Gateway Unit。

Planning 只提供差异蓝图，继续复用现有 Pending/Formal Build DAG、用户确认、调度、修复和失效传播。

### 7.3 Development

`compile_development` 负责：

- Frontend 与 Agent Generator 的执行绑定；
- Runtime → Frontend 启动图；
- 普通预览与独立调试 Profile；
- Testing、Code Review、Launch 和 Acceptance 检查；
- 失败时的 owner 与修复边界。

Development 不建立第二套 Workflow 或第二套 DAG。

## 8. Auth、Authorization 与多用户结论

- `auth.enable=true`：Runtime 是公开认证终止点，并复用应用现有 `login` capability 的用户体验和会话语义；
- `auth.enable=false`：Runtime 签发不可由客户端自选 subject 的匿名会话；
- `authorization.enabled` 必须为 `false`；
- 不生成 role、permission、resource key、policy、authorization management 页面或 Agent RBAC；
- ownership、数据隔离、Tool 风险确认和管理员运维不是 RBAC，仍然必须实现；
- 所有 thread、run、message、file、memory、checkpoint 和 pending interaction 都绑定可信 Principal；
- 不同 subject 之间的读取、恢复、取消、重放和删除必须拒绝。

详细规则见 [安全与本地调试附录](./AGENT_RUNTIME_DIRECT_SECURITY_DEBUG.md)。

## 9. Runtime Tool 边界

第一版允许：

- Runtime 内置只读工具；
- 当前 Agent Contract 明确声明的外部 HTTP Adapter；
- 当前 Agent Contract 明确声明的 MCP Server；
- Knowledge/RAG Adapter；
- 需要显式用户确认的 Runtime 写操作。

第一版禁止：

- `backend_endpoint` Tool Binding；
- Java 内部 RPC；
- 直接业务数据库访问；
- 从 Prompt、Skill 或请求体动态扩大网络、文件或命令权限；
- 客户端提交任意 Tool schema 覆盖正式 Contract。

任何外部 Adapter 都必须由正式 Agent Contract、环境凭据和 Runtime allowlist 三者共同授权。

## 10. 与当前 Agent Sidecar 的关系

当前 Sidecar 设计固定为：

```text
Frontend -> Java Agent Gateway -> Python Runtime
```

`agent_runtime_direct` 是独立拓扑，不是删掉 Java 目录后的 Sidecar。它需要新的 Public Edge、Auth、Principal、CORS、ownership、Template capability、Invocation Contract 和 Launch 验收。

当前迁移边界：

- `agent_runtime_direct` 已进入 Registry，不再使用旧 Gateway/Sidecar 路径作为该拓扑的运行回退；
- 不复用 `AGENT_RUNTIME_GATEWAY_TOKEN` 作为浏览器凭据；
- 不把 `/internal/agents/...` 暴露为公开 API；
- 只能通过已注册 Definition 的正式事实自动匹配，不根据 `backend/` 缺失猜测拓扑；
- 不将当前调试 Header 身份机制带入生产入口。

## 11. 设计文档组成

- 本文：目标、自动选型、架构和阶段边界；
- [正式合同](./AGENT_RUNTIME_DIRECT_CONTRACTS.md)：TechnicalPlan、Agent Contract、TemplateState 和失效；
- [构建与运行](./AGENT_RUNTIME_DIRECT_BUILD_RUNTIME.md)：Bootstrap、DAG、生成、测试、启动和验收；
- [安全与本地调试](./AGENT_RUNTIME_DIRECT_SECURITY_DEBUG.md)：Auth、Principal、ownership、匿名模式和 Debug Profile。

## 12. 审核结论边界

本文已确认并进入实现。Registry 与主流程接入本身不代表：

- Runtime 模板与 Frontend 直连已完成真实环境验证；
- Bootstrap、Build DAG、Launcher 和 Acceptance 的端到端证据已齐全；
- 当前工作树已可标记为完成迁移；
- 现有生成应用会自动迁移。

实施只支持新当前合同，不增加 `gatewayEndpointId` fallback、旧 TechnicalPlan reader、双写或历史工作区迁移。
