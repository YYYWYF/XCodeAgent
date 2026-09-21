# Agent Runtime Direct 构建与运行设计

> 状态：待用户审核  
> 目标：复用现有 Bootstrap、Build DAG、Testing、Launch 和 Acceptance，不建立平行工作流

## 1. 阶段总览

```text
Confirmed TechnicalPlan
  -> Topology Planning Blueprint
  -> Workspace Bootstrap
  -> TemplateState Readiness
  -> Generic Build DAG
  -> Frontend / Agent Generators
  -> Unit Testing
  -> Integration Testing
  -> Code Review
  -> Runtime Launch
  -> Acceptance
```

Topology Definition 只提供差异蓝图；现有 Workflow 继续拥有确认、调度、失败修复、取消、revision 和最终验收。

## 2. Workspace Bootstrap

managed roots 固定为：

```text
frontend/
agent-runtime/
```

Bootstrap 不拉取或创建 `backend/`。事务行为保持现有合同：

- 使用 confirmed TechnicalPlan；
- 编译 RequestedConfig；
- 获取 Frontend 与 Runtime 模板；
- 校验安全 ZIP、roots、TemplateState 和模板来源；
- 原子物化工作区；
- 建立 Git baseline；
- 任一 Readiness 失败时回滚本轮 roots、`.git`、TemplateState 和 staging；
- 不生成业务页面、Prompt、Agent 或 Tool 代码。

Frontend 模板必须与 Runtime Public Auth/Anonymous 模式匹配。不能拉取 Frontend `auth` 模板、却让 Runtime 处于匿名模式，反之亦然。

## 3. Bootstrap Readiness

Readiness 至少验证：

### Frontend

- `frontend/package.json`；
- AG-UI Client 依赖；
- Agent UI 固定组件入口；
- Auth 开启时存在 login capability 入口；
- 不预置指向 Java Backend 的 Agent Gateway URL。

### Agent Runtime

- `agent-runtime/pyproject.toml`；
- Python 3.12；
- DeepAgents 与 AG-UI 依赖；
- Public Edge app factory；
- `/health`；
- public Agent route；
- Auth 或 anonymous-session 中间件；
- principal ownership repository；
- 本地调试 Profile；
- capability manifest 与 contract tests。

### 全局

- TemplateState requested/effective 一致；
- `authorization` capability 不存在；
- 不存在 Backend/Gateway managed root；
- template source commit 与 capability evidence 一致。

目录存在不能替代上述证据。

## 4. Build DAG

### 4.1 Unit 依赖

```text
application:root
  ├── frontend:shell
  ├── frontend:api-client
  ├── frontend:auth-guard          # auth on
  ├── agent:runtime
  └── agent:<agentId>

frontend:shell
  -> frontend:agent-surface:<pageId>

frontend:api-client
  -> frontend:agent-surface:<pageId>

frontend:auth-guard
  -> frontend:agent-surface:<pageId>  # auth on

agent:runtime
  -> agent:<agentId>

agent:<agentId>
  -> app:integration

frontend:agent-surface:<pageId>
  -> app:integration
```

多个 Agent 可以共享 `agent:runtime`，但每个 `agent:<agentId>` 保持独立任务、Diff、测试和 revision 失效范围。

### 4.2 不生成任务的 Unit

`agent:runtime` 默认是模板 readiness Unit，不要求模型从零生成公共服务、安全中间件或持久化基础设施。只有模板 capability revision 明确要求平台升级时，才由模板更新流程处理，不交给业务 Agent CodeRunner。

`frontend:shell` 继续是 prerequisite-only；业务页面和 Surface 由对应 Frontend Unit 生成。

## 5. Generator 所有权

| 生成内容 | owner | 写范围 |
| --- | --- | --- |
| 页面与 Agent Surface 组合 | `frontend` | 当前 Frontend 页面、Adapter 和测试范围 |
| AG-UI Public Edge Client | `frontend` | 共享 Frontend API/Agent adapter |
| Prompt、Agent 装配、Memory/Tool/Skill/Knowledge | `agent` | 当前 Agent 授权的 `agent-runtime/**` 路径 |
| Runtime Public Edge、Auth、ownership 基础设施 | template/platform | 业务模型不可修改 |
| Backend/Gateway/Database | 不存在 | 禁止生成 |

Agent Generator 不能修改认证中间件、Principal 解析、CORS、公开路由装配和 ownership repository。确需改变这些基础设施时，升级 Runtime 模板 capability，而不是生成应用中的业务任务。

## 6. Frontend 生成规则

Frontend 必须：

- 使用 `@ag-ui/client` 和 `@ag-ui/core`；
- 从 topology projection 取得 Public Edge service binding；
- 通过当前 Agent Contract 取得 `agentId` 和 public path；
- Auth 开启时复用 login capability 和安全会话；
- 不设置 `X-Agent-User-Id`、`X-Agent-Tenant-Id` 或内部 Token；
- 不实现手写 SSE parser；
- 不把模型密钥、Runtime secret 或 Tool secret 写入 Renderer；
- 保持 Agent UI 固定组件与 Surface 契约。

开发环境 URL 由 Launch result 注入受控 Preview 配置；生产 URL 由部署环境提供，不写死动态端口。

## 7. Agent Runtime 生成规则

业务 Agent 生成继续使用现有七模块：

```text
prompt
model
memory
tools
skills
knowledge
context
```

差异在于：

- Invocation 直接注册到 Public Edge；
- Tool Binding 不再展开为 Java Endpoint Adapter；
- Runtime-native Adapter 只能消费正式 Contract；
- Principal 从 Runtime Context 注入 Agent，不从用户消息读取；
- Memory namespace 自动包含 owner key；
- 写 Tool 必须经过 pending interaction confirmation；
- Agent 不得访问认证存储或其他用户的 checkpoint。

## 8. Testing

### 8.1 Unit Testing

必需检查：

- Runtime 模板 contract tests；
- 每个 Agent 的 pytest；
- Tool Adapter allowlist、timeout 和响应裁剪测试；
- Frontend Agent Adapter 测试；
- Agent Surface 渲染与状态测试；
- Auth on/off 对应的客户端行为测试。

Runtime 固定命令继续由平台提供，TechnicalPlan 只授权 required check，不允许模型注入任意命令。

### 8.2 Integration Testing

必须覆盖：

- Frontend → Runtime AG-UI 完整生命周期；
- 普通对话、Tool Call、clarification、confirmation、cancel 和 error；
- Auth 开启时未登录拒绝、登录后成功、过期会话拒绝；
- Auth 关闭时匿名会话签发和隔离；
- 用户 A 不能读取、恢复、取消或重放用户 B 的资源；
- Runtime 重启后 checkpoint 恢复；
- Public route 不接受客户端身份 Header；
- 不存在 Backend/Gateway 运行依赖。

### 8.3 Code Review

Code Review 必须检查：

- Frontend 没有自制 SSE/身份 Header；
- Runtime 没有绕过 ownership 的直接资源查询；
- Tool 网络和文件权限没有超出 Contract；
- 没有 Backend/Gateway 目录或调用；
- 没有真实 secret、绝对路径或用户内容进入日志；
- 所有失败路径只有一个 AG-UI 终态。

## 9. Project Launch

启动图固定为：

```text
structure
  -> agent_runtime
  -> frontend
  -> integration_probe
  -> ready
```

不得出现 `backend` 或 `agent_integration` 的 Backend↔Runtime Token 分配阶段。

Launch Executor 负责：

1. 从 confirmed TechnicalPlan 编译 Development Blueprint；
2. 校验 TemplateState capability；
3. 停止当前工作区旧 standard preview；
4. 为 Runtime 分配 loopback 端口；
5. 注入模型 fallback 和 Preview 配置；
6. 启动 Runtime 并等待 `/health`；
7. 启动 Frontend 并注入 Public Edge Preview URL；
8. 执行真实 AG-UI probe；
9. 两端及 probe 全部成功后才返回 `running`。

普通预览不返回任何 Token、模型配置、物理工作区路径或内部环境变量。

## 10. Launch 回滚

| 失败阶段 | 回滚 |
| --- | --- |
| Runtime install/start | 停止 Runtime，Frontend 不启动 |
| Runtime health | 停止 Runtime，返回 Runtime 日志摘要 |
| Frontend install/start | 停止 Frontend 与本轮 Runtime |
| Integration probe | 停止两端，本轮状态不得标记 ready |
| 旧服务清理 | 不启动新服务，保留明确失败证据 |

进程清理继续使用工作区锁、PID 身份和 cwd/command 双证据，禁止按端口占用直接杀进程。

## 11. Acceptance

最终验收至少要求：

- Topology、TemplateState 和 BuildTaskPlan hash 一致；
- managed roots 只有 Frontend 与 Runtime；
- required Unit 全部完成；
- Runtime pytest 与 Frontend build 通过；
- AG-UI integration matrix 通过；
- Auth/Anonymous 对应矩阵通过；
- cross-principal isolation 通过；
- 本地调试 Profile 不进入生产预览；
- 用户确认当前候选。

只有模板下载成功、目录存在、健康检查成功或单次 Chat 成功，都不能单独证明拓扑完成。

## 12. Repair 边界

| 失败 | owner / 处理 |
| --- | --- |
| Agent 业务代码或测试 | `agent` SmallTask Repair |
| Frontend 页面/Adapter | `frontend` SmallTask Repair |
| Runtime Public Edge/Auth/ownership 模板缺陷 | 不允许业务 Repair；升级模板 |
| Topology/Contract 不满足 | 返回 TechnicalPlan revision |
| 环境缺少 uv/Node/端口 | 不自动改业务代码 |
| 跨用户隔离失败 | 阻断 Acceptance，安全缺陷不可跳过 |

## 13. 实施顺序

设计审核通过后按以下批次实施：

1. 当前 TechnicalPlan 与 Agent Contract 字段替换；
2. Runtime Template Public Edge、Auth/Anonymous 和 ownership；
3. Template capability 与 Bootstrap roots；
4. 通用 Build DAG 消费 Planning Blueprint；
5. Frontend/Agent Generator 差异；
6. Testing 与 Code Review；
7. Project Launch 与本地调试；
8. Electron 端到端与 Acceptance；
9. 注册 `AgentRuntimeDirectTopology` 并启用自动匹配。

注册必须是最后的启用步骤。在前置能力未闭合时，枚举存在也不能让平台生成该拓扑。
