# Agent Runtime Direct 构建与运行设计

> 状态：目标实现设计，当前模板与生成器待重建

## 1. 阶段总览

```text
Confirmed TechnicalPlan Core
  -> User-selected agent_runtime_direct
  -> Final TechnicalPlan
  -> Endpoint API Design
  -> Topology Planning Blueprint
  -> Workspace Bootstrap
  -> TemplateState Readiness
  -> Generic Build DAG
  -> Python Business / Agent / Frontend Generators
  -> Testing / Review / Launch / Acceptance
```

## 2. Workspace Bootstrap

managed roots：

```text
frontend/
agent-runtime/
```

Python 模板必须预置不可由业务生成器修改的基础设施：

- FastAPI app factory 与路由装配；
- AG-UI lifecycle adapter；
- Auth/匿名会话与 TrustedPrincipal；
- ownership、审计和错误处理中间件；
- 数据库连接、事务边界与迁移入口；
- Runtime checkpoint/memory 基础设施；
- 模块路径策略与 contract tests。

Bootstrap 不生成业务 Entity、Endpoint、Agent 或页面代码，只物化并验证模板。

## 3. Build Units

```text
application:root
  ├── python:bootstrap
  ├── frontend:shell
  └── agent:runtime

python:bootstrap
  -> python:entity:<entityId>
  -> python:repository:<entityId>
  -> python:service:<serviceId>
  -> python:endpoint:<contractId>:<endpointId>

python:service:<serviceId>
  -> agent:<agentId>              # Tool 绑定业务服务时

agent:runtime
  -> agent:<agentId>

python:endpoint:* -> frontend:api-client
agent:<agentId> -> frontend:agent-surface:<pageId>
frontend:api-client -> page:<pageId>
frontend:agent-surface:<pageId> -> page:<pageId>
page:<pageId> -> app:integration
python:endpoint:* -> app:integration
agent:<agentId> -> app:integration
```

`python:bootstrap`、`agent:runtime` 和 `frontend:shell` 是模板 readiness Units；业务生成器不得重写其安全基础设施。

## 4. Generator 所有权

| 生成内容 | owner | 写范围 |
| --- | --- | --- |
| Python Entity / DTO | `python-business` | `agent-runtime/src/app/domain/**`、受控 schema 路径 |
| Repository / Migration | `python-business` | `agent-runtime/src/app/infrastructure/**`、业务 migration 路径 |
| Application Service | `python-business` | `agent-runtime/src/app/application/**` |
| FastAPI Endpoint | `python-business` | `agent-runtime/src/app/api/**` |
| Agent 七模块 | `agent` | `agent-runtime/src/app/agents/**` 与授权测试路径 |
| Runtime 安全基础设施 | template/platform | 业务 Agent 不可修改 |
| 页面、REST/AG-UI Client | `frontend` | `frontend/**` 授权范围 |

所有 owner 写路径必须来自模板 policy，不能由模型返回任意目录。

## 5. Endpoint API Design 到 Python

每个确认 Endpoint 的 Build Context 包含：

- API Contract 与 Schema；
- 关联 Entity 快照；
- Request/Response field mappings；
- database/external source snapshot；
- business descriptions；
- topology owner=`agent-runtime`。

生成顺序固定为 DTO/Entity → Repository/Adapter → Application Service → Route → Tests。字段来源设计不因 Python 拓扑改变，只有实现模板和 owner 不同。

## 6. Agent Tool 生成

Agent Tool Binding 指向稳定 Application Service capability，而不是本机 REST URL。生成器必须：

- 注入受控 service interface；
- 传递 TrustedPrincipal、run context 和 idempotency key；
- 对写操作发起 pending interaction；
- 复用业务授权、事务和数据范围；
- 禁止直接访问数据库 session 或认证存储。

## 7. Frontend 生成

Frontend 可以同时生成：

- REST Client：调用 Python FastAPI 业务 Endpoint；
- AG-UI Client：调用同一 Runtime 的 Agent Endpoint；
- Auth/anonymous session adapter；
- 页面与 Agent Surface。

两类 Client 从 topology projection 和正式 Contract 取得 path，不写死开发端口，不手写 SSE parser，不发送伪造身份 Header。

## 8. Testing

### Unit

- Entity/DTO/Schema；
- Repository 与 migration；
- Application Service 事务和授权；
- FastAPI Endpoint；
- 每个 Agent 七模块；
- Tool → Application Service；
- Frontend REST/AG-UI Adapter。

### Integration

- Frontend → REST → Application Service → Database；
- Frontend → AG-UI → Agent → Application Service → Database；
- REST 与 Agent Tool 对同一业务规则结果一致；
- Auth on/off、跨 Principal 隔离和写操作确认；
- Runtime 重启后的业务数据与 checkpoint 恢复；
- 不存在 Backend/Gateway 运行依赖。

### Review

- 无 loopback HTTP 自调用；
- 无业务逻辑在 Route 与 Tool 中复制；
- 无任意 SQL、任意 URL、真实 secret 或客户端身份 Header；
- 业务与 Runtime migration 不互相覆盖；
- 所有失败路径只有一个 AG-UI 或 HTTP 终态。

## 9. Project Launch

```text
structure
  -> database_migration
  -> agent_runtime
  -> frontend
  -> integration_probe
  -> ready
```

`agent_runtime` readiness 必须同时验证 `/health`、业务 API、AG-UI route、数据库连接、TemplateState 和 capability manifest。任一 required check 失败时不得启动 Frontend 进入可验收状态。

## 10. Acceptance

验收必须覆盖产品业务结果，而不仅是 Agent 对话：

- 页面业务 Action 能通过 Python API 完成；
- Agent 能通过同一 Application Service 完成授权业务能力；
- 数据持久化、错误状态和并发行为符合 Contract；
- Frontend 不依赖 Java Backend 或 Gateway；
- 实际启动图、进程和端口与已确认 topology projection 一致。

## 11. 实施顺序

1. Python Application Runtime 模板与 capability manifest；
2. TechnicalPlan topology owner projection；
3. Python Entity/Repository/Service/Endpoint Units；
4. Endpoint Design → Python Build Context；
5. Tool → Application Service Binding；
6. Frontend REST + AG-UI 双 Client；
7. 测试、启动、验收和修复边界；
8. 注册新 Definition 并启用前端选项；
9. 删除旧 Direct 原型和自动匹配。
