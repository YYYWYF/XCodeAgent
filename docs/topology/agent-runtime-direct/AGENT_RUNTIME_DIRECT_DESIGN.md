# Agent Runtime Direct 整体设计

> 拓扑枚举：`agent_runtime_direct`  
> 状态：目标设计已重构；现有模板、Definition 和生成器不满足本合同，待重新实现  
> 目标工程：`frontend/ + agent-runtime/`

## 1. 设计结论

`agent_runtime_direct` 是 Frontend + Python Application Runtime 的直连拓扑。Python Runtime 同时承载：

- 业务 Entity、DTO、Repository、Application Service 和事务；
- FastAPI 业务 REST Endpoint；
- Agent Prompt、Model、Memory、Tools、Skills、Knowledge 和 Context；
- AG-UI Public Endpoint、Thread、Run、Checkpoint 和 Pending Interaction；
- Auth/匿名会话适配、可信 Principal、资源 ownership 和审计；
- 数据库、外部服务和 Runtime-native Adapter。

它不是删掉 Java Backend 后只剩 Agent 的 Sidecar，也不要求 `entities=[]` 或 `api_contracts=[]`。

## 2. 选择与适用范围

用户在计划阶段显式选择 `agent_runtime_direct`。平台只验证该选择是否已经实现以及能否完整承载当前业务事实，不得自动匹配或改选。

适用场景包括：

- 应用希望使用单一 Python 服务承载业务 API 和 Agent；
- TechnicalPlan Core 可以包含业务 Entity、API Contract、数据源和数据库变更；
- 页面可以同时调用 REST API 和 AG-UI；
- Agent Tool 可以调用本地 Application Service、Repository 封装或受控外部 Adapter；
- 不需要 Java Backend 或独立 Gateway；
- 部署规模和组织边界允许业务 API 与 Agent Runtime 同进程或同服务发布单元。

以下情况必须拒绝选择：

- Python Application Runtime 模板或生成器尚未达到 required capabilities；
- 用户要求独立 Java Backend、独立 Gateway 或 Java 生态专属组件；
- 当前 Auth/Authorization 能力无法由 Python 模板实现；
- 数据库、外部协议或部署约束超出 Python Runtime 已审核能力；
- 选择会导致任何已确认业务 Entity、Endpoint 或 Agent 能力被删除。

## 3. 目标架构

```text
Browser / Electron Preview
  ├── REST JSON ------------------------------┐
  └── AG-UI SSE ------------------------------┤
                                               v
                                  Python Application Runtime
                                  ├── Public Auth / Anonymous Session
                                  ├── Trusted Principal / Ownership
                                  ├── FastAPI Business Endpoints
                                  ├── Application Services / Transactions
                                  ├── Domain Entities / Repositories
                                  ├── Agent Runtime / Tool Execution
                                  ├── Thread / Run / Checkpoint / Memory
                                  └── Database / External Adapters
```

REST Endpoint 与 Agent Tool 必须复用同一个 Application Service：

```text
REST Endpoint ─┐
               ├── Application Service ── Domain / Repository / External Adapter
Agent Tool ────┘
```

Agent Tool 不得通过 loopback HTTP 调用同一 Python 进程，也不得复制 Endpoint 的业务逻辑。

## 4. 工程目录

```text
<workspace>/
├── frontend/
├── agent-runtime/
│   ├── pyproject.toml
│   ├── src/app/
│   │   ├── api/
│   │   ├── application/
│   │   ├── domain/
│   │   ├── infrastructure/
│   │   ├── agents/
│   │   └── runtime/
│   └── tests/
└── .xcodeagent/
```

不得生成 `backend/`、`gateway/`、Java Maven modules 或 Java Agent Gateway。

## 5. 数据边界

必须区分两类数据：

| 数据类别 | 示例 | 生命周期 |
| --- | --- | --- |
| 业务数据 | Order、Customer、审批记录、业务事务 | 由 Entity/API Contract、Endpoint Design 和业务迁移控制 |
| Agent 运行状态 | thread、run、checkpoint、memory、interaction | 由 Runtime 合同和 ownership 控制 |

二者可以使用同一种数据库产品，但必须使用独立模型、Repository、迁移和清理规则。Runtime 状态不能伪装成业务 Entity，业务 Repository 也不能绕过 Principal 与数据范围规则。

## 6. TechnicalPlan 编译

TechnicalPlan Core 先生成完整的：

- `entities`；
- `api_contracts`；
- `pages` 和 Action/Endpoint bindings；
- `agent_contracts` 与 Tool 业务需求。

用户选择 Direct 后，Definition 编译：

- `publicEdgeServiceId=agent-runtime`；
- Entity/API/Agent/Persistence owner 均为 `agent-runtime`；
- 业务 Endpoint 映射到 Python FastAPI；
- Agent Tool 映射到本地 Application Service 或受控 Adapter；
- managed roots 为 `frontend`、`agent-runtime`；
- 禁止 Backend/Gateway Build Unit。

## 7. 三阶段策略

### Design

- 校验用户选择和模板能力；
- 编译服务边界、Public Edge、认证终止点和实现 owner；
- 保留完整 Entity/API/Agent 业务事实；
- 生成最终 TechnicalPlan 的最小 topology projection。

### Planning

- 编译 Python Entity、Repository、Service、Endpoint、Agent 和 Frontend Units；
- 绑定 Endpoint Design 到 Python Build Context；
- 声明模板 capabilities、路径策略和 required checks；
- 复用统一 Pending/Formal Build DAG。

### Development

- 绑定 Python business、Agent 与 Frontend Generator；
- 执行单元测试、REST/AG-UI 集成、数据迁移和 ownership 检查；
- 按 database → application runtime → frontend → integration probe 启动；
- 复用统一 Code Review、Acceptance 和 Formal Revision。

## 8. Auth 与 Authorization

`auth.enable` 与 `authorization.enabled` 仍来自 `.xcodeagent/application.json`。Direct Definition 只能在 Python 模板具有对应 capability 时接受选择。

- Auth 开启：Python Runtime 是公开认证终止点；
- Auth 关闭：Runtime 签发服务端匿名会话，客户端不能自选 subject；
- Authorization 开启：页面、操作、Endpoint 和 Agent 权限必须由 Python 授权编译器与中间件实现；
- 在 Python Authorization capability 完成前，`authorization.enabled=true` 必须使 Direct 选择失败，不能静默关闭权限。

## 9. 实施状态与迁移边界

现有实现仅生成 Agent 七模块，并要求 Entity/API 为空，因此属于废弃原型。迁移不得在旧实现上增加兼容分支，而应：

1. 重建 Python Application Runtime 模板；
2. 新增 Python 业务 Build Units 和 Generator；
3. 让 Endpoint API Design 生成 Python Build Context；
4. 修改 Tool Binding 复用 Application Service；
5. 删除旧 direct candidate 和自动匹配；
6. 完成 Build、Testing、Launch 和 Acceptance 后再把 UI 选项标记为可用。

本项目不迁移历史 TechnicalPlan 或旧生成工作区；已有应用需重新规划并确认当前合同。
