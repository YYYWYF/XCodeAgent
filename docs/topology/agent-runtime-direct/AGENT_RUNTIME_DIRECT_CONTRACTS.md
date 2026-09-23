# Agent Runtime Direct 正式合同

> 适用拓扑：`agent_runtime_direct`  
> 状态：目标合同，代码待实现

## 1. 事实源

| 事实 | 权威来源 |
| --- | --- |
| 页面、Action、信息项和 Agent 产品语义 | 已确认 ProductPlan |
| Entity、API Contract、Action/Endpoint、Agent 设置 | TechnicalPlan Core |
| 用户选择的拓扑与实现 owner | 最终已确认 TechnicalPlan |
| Endpoint 字段来源和业务处理 | 已确认 Endpoint API Design |
| Auth/Authorization 能力 | `.xcodeagent/application.json` |
| 模板能力和路径策略 | TemplateState + capability manifest |
| Build Units 和任务 | 已确认 BuildTaskPlan |

Topology Definition 只能投影实现归属，不得改写选择前的业务事实。

## 2. TechnicalPlan 拓扑投影

最终 TechnicalPlan 增加最小稳定投影：

```json
{
  "topology": {
    "type": "agent_runtime_direct",
    "publicEdgeServiceId": "agent-runtime",
    "serviceIds": ["agent-runtime"],
    "authenticationTermination": "agent-runtime",
    "implementationOwners": {
      "businessApi": "agent-runtime",
      "businessData": "agent-runtime",
      "agent": "agent-runtime"
    },
    "sourceFactsSha256": "sha256:..."
  }
}
```

不保存端口、PID、模板 commit、Task、运行状态或数据库凭据。

## 3. Entity 与 API Contract

- `entities` 保持 TechnicalPlan Core 的业务字段事实；
- `api_contracts` 保持 method、path、Schema、`entity_ids` 和 Endpoint 身份；
- Contract 不写 Java/Python 文件路径，也不因拓扑选择改变业务字段；
- Endpoint Design 继续独立保存字段来源、业务说明和 source snapshot；
- Build Context 根据 topology projection 把 Endpoint 实现 owner 绑定为 Python Runtime。

业务 Endpoint 默认生成：

```text
FastAPI Route
  -> Request/Response DTO
  -> Application Service
  -> Domain Entity / Policy
  -> Repository or External Adapter
```

## 4. Agent Contract

Agent Contract 保留七模块：

```text
prompt / model / memory / tools / skills / knowledge / context
```

公开 Invocation：

```json
{
  "protocol": "ag-ui",
  "transport": "sse",
  "serviceId": "agent-runtime",
  "endpointId": "agent.<agentId>.run",
  "method": "POST",
  "path": "/agents/<agentId>/run",
  "exposure": "public"
}
```

## 5. Tool Binding

Direct 模式允许以下 Tool source：

- `application_service`：调用同进程受控 Application Service；
- `repository_query`：只允许通过审核的只读 Repository facade；
- `external_adapter`：调用 allowlisted 外部服务 Adapter；
- `runtime_native`：文件、知识、Memory 等 Runtime 原生能力。

禁止：

- `backend_endpoint` 或 Java Gateway Endpoint；
- loopback HTTP 调用同一 Runtime；
- Tool 直接执行任意 SQL；
- Tool 绕过 Application Service 写业务数据；
- 从用户消息读取 Principal 或扩大数据范围。

写操作必须经过 pending interaction confirmation，并复用 Application Service 的事务、授权和幂等规则。

## 6. 数据与迁移合同

业务数据库和 Runtime 状态必须具有独立命名空间：

```text
business migrations / business repositories
runtime migrations / checkpoint and memory repositories
```

TechnicalPlan Entity 变化使相关 Python Entity、DTO、Repository、Service、Endpoint、测试和 Build 证据失效。Runtime checkpoint schema 变化走模板 capability 升级，不由业务 Entity 生成器修改。

## 7. RequestedConfig 与 TemplateState

至少需要：

- `python_application_runtime`；
- `python_business_api`；
- `python_domain_model`；
- `python_repository`；
- `agent_runtime_public_edge`；
- `agent_runtime_principal_ownership`；
- `agent_runtime_local_debug`；
- 对应 Auth/Authorization、数据库和外部 Adapter capability。

缺少任一当前计划必需 capability 时，`is_available` 或 `validate_selection` 必须失败。

## 8. Build Unit Blueprint

```text
application:root
python:bootstrap
python:entity:<entityId>
python:repository:<entityId>
python:service:<serviceId>
python:endpoint:<contractId>:<endpointId>
agent:runtime
agent:<agentId>
frontend:shell
frontend:api-client
frontend:agent-surface:<pageId>
page:<pageId>
app:integration
```

依赖原则：

- Entity → Repository → Application Service → Endpoint；
- Application Service → Agent Tool；
- Endpoint 与 Agent Invocation → Frontend client/surface；
- 所有可执行叶子 → `app:integration`。

## 9. Hash 与失效

Topology hash 至少绑定：

- selected topology type；
- Entity/API/Agent 合同摘要；
- application config revision；
- implementation owner projection；
- Template capability/revision；
- Endpoint Design fingerprints。

拓扑、Entity、API、数据源、Auth/Authorization、Tool Binding 或模板能力变化必须精确失效相关 DAG、测试、启动和验收证据。

## 10. Formal Revision

拓扑变更或重大业务合同变化必须重新生成 TechnicalPlan Core、重新选择拓扑并确认最终 TechnicalPlan。平台不得自动保留不属于新拓扑的目录，也不得读取历史 schema 兜底。
