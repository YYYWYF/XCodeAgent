# 生成应用 Gateway 正式合同

> 状态：主设计的规范性附录  
> 适用范围：Application Config、TechnicalPlan、TemplateState、GatewayPlan、Route Contract、GatewayExecutionSlice、Hash 与失效规则  
> 主文档：[`GENERATED_APPLICATION_GATEWAY_DESIGN.md`](./GENERATED_APPLICATION_GATEWAY_DESIGN.md)

> 本附录只适用于 `gateway_composed` 拓扑；全局拓扑框架见 [`../topology/APPLICATION_TOPOLOGY_DESIGN.md`](../topology/APPLICATION_TOPOLOGY_DESIGN.md)。

## 1. 合同原则

- `.xcodeagent/application.json` 是应用级能力开关的唯一事实源；
- TechnicalPlan 是服务归属、公开 API、Gateway 架构选择和 Decision Record 的唯一事实源；
- Endpoint Design 是单个 Endpoint 字段映射、外部 Operation snapshot 和实现说明的唯一事实源；
- TemplateState 是 Backend 模板能力、Gateway 模块入口、行内统一认证 Profile、Claim Mapping 和策略目录的唯一模板事实源；
- GatewayPlan 和 GatewayExecutionSlice 都是确定性只读投影，不是用户可编辑事实源；
- Build Plan 只消费 GatewayExecutionSlice，不能反向改变 GatewayPlan；
- 不读取历史字段、旧路由、模型原始输出或旧 Build 结果补全当前合同。

Gateway V1 不消费页面、动作或普通业务 Endpoint 的 Authorization Manifest 切片。只有 `auth.enable=true && authorization.enabled=true` 且应用存在 Agent 时，Gateway 才消费 Agent 资源与策略切片，确定性投影 Agent RBAC 合同、Hash 和 Build Unit。Agent `allowedRpcOperationIds` 仍是拓扑能力白名单，不属于 RBAC 授权合同。

## 2. Application Config 与拓扑事实

`.xcodeagent/application.json` 不增加 `gateway.enabled`。Gateway 是拓扑的组成服务，是否生成由已确认 `TechnicalPlan.topology.type` 唯一决定。

规则：

- `TechnicalPlan.topology.type=gateway_composed` 是 Gateway 存在的唯一持久化事实；
- `gateway_composed` 固定为 Frontend + Gateway + Backend + Agent Runtime，不允许缺少 Backend 或 Agent Runtime；
- `auth.enable` 仍是是否要求公开用户认证的唯一应用级事实；Application Config 不新增可由低代码用户选择的认证提供方或签名算法字段；
- `authorization.enabled` 是 Gateway Agent RBAC 的唯一应用级开关，不新增第二个 Gateway 授权开关；Backend 业务 RBAC 和数据权限仍属 Backend 事实；
- `auth.enable=true` 时认证提供方固定由 TemplateState 的行内统一认证 Profile 决定；`auth.enable=false` 时只允许正式声明的匿名 Endpoint；
- `authorization.enabled=true` 必须同时满足 `auth.enable=true`；不满足时正式产物确认、GatewayPlan 编译、Build 和 Launch 全部失败；
- Agent RBAC 仅在两个开关同时为 `true` 时存在；任一开关为 `false` 时不得生成 Agent 资源键、权限 Filter、策略客户端或授权缓存；
- 纯 Agent 需求进入 `agent_runtime_direct`，纯 Backend 需求进入 `backend_direct`；两者都不生成 GatewayPlan；
- 从 `gateway_composed` 移除 Backend 或 Agent Runtime 必须通过 Formal Revision 切换拓扑，并重新确认 TechnicalPlan；
- Application Config 不保存路由、模块路径、Application 类名、upstream、策略参数、Origin 或 Secret；
- 创建、修订、确认、删除、前后端类型和校验器必须同批支持当前合同，不增加旧字段别名。

## 3. TechnicalPlan Gateway 合同

TechnicalPlan 必须显式描述服务、Endpoint owner、内部路径、Route 类型、策略和架构 Decision：

```json
{
  "topology": {
    "type": "gateway_composed",
    "public_edge": "gateway"
  },
  "services": [
    {
      "id": "order-service",
      "kind": "business_backend",
      "required": true
    },
    {
      "id": "recheck-agent-runtime",
      "kind": "agent_runtime",
      "required": true
    },
    {
      "id": "shipping-provider",
      "kind": "external_service",
      "required": false
    }
  ],
  "gateway": {
    "publicBasePath": "/api",
    "defaultPolicyProfile": "standard",
    "decisions": []
  },
  "api_contracts": [
    {
      "id": "orders_api",
      "base_path": "/api/orders",
      "endpoints": [
        {
          "id": "orders.list",
          "method": "GET",
          "path": "/api/orders",
          "owner_service_id": "order-service",
          "upstream_path": "/internal/orders",
          "gateway": {
            "route_kind": "business_http",
            "policy_profile": "standard_read",
            "anonymous": false,
            "criticality": "required"
          }
        }
      ]
    },
    {
      "id": "recheck_agent_api",
      "base_path": "/api/agents/recheck",
      "endpoints": [
        {
          "id": "recheck.run",
          "method": "POST",
          "path": "/api/agents/recheck/run",
          "owner_service_id": "recheck-agent-runtime",
          "upstream_path": "/internal/agents/recheck/run",
          "gateway": {
            "route_kind": "agent_stream",
            "policy_profile": "streaming",
            "anonymous": false,
            "criticality": "required"
          }
        }
      ]
    }
  ],
  "internal_rpc_contracts": [
    {
      "id": "order_agent_rpc",
      "owner_service_id": "order-service",
      "visibility": "internal",
      "operations": [
        {
          "id": "order.lookup_for_recheck",
          "method": "LookupOrder",
          "request_schema_ref": "orders.LookupOrderRequest",
          "response_schema_ref": "orders.OrderDetail",
          "timeout_policy": "standard_read",
          "idempotency_policy": "none"
        }
      ]
    }
  ],
  "agent_contracts": [
    {
      "agent_id": "recheck-assistant",
      "runtime_service_id": "recheck-agent-runtime",
      "tool_rpc_bindings": [
        {
          "tool_id": "lookup_order",
          "rpc_contract_id": "order_agent_rpc",
          "rpc_operation_id": "order.lookup_for_recheck"
        }
      ]
    }
  ],
  "backend_adapters": [
    {
      "id": "shipping-adapter",
      "owner_service_id": "order-service",
      "external_service_id": "shipping-provider",
      "operation_snapshot_ref": "endpoint-design:shipping.quote"
    }
  ]
}
```

规则：

- `services[].id` 是应用内稳定服务身份；
- `topology.type` 必须为 `gateway_composed`，`public_edge` 必须为 `gateway`；
- `services[].kind` 只允许 `business_backend`、`agent_runtime`、`external_service`；Gateway 不进入 `services[]`，也不能成为 Endpoint owner；
- `services[]` 必须至少包含一个 `business_backend` 和一个 `agent_runtime`；任一缺失都不能通过拓扑校验；
- 每个公开 Endpoint 必须有且只有一个 `owner_service_id`；
- `business_http`、`streaming_sse`、`websocket`、`file_transfer` 的 owner 必须是 `business_backend`；
- `agent_stream`、`agent_control` 的 owner 必须是 `agent_runtime`，并且必须解析到 `agent_contracts[].runtime_service_id`；
- `external_service` 不能成为公开 Endpoint owner 或 Gateway upstream，只能由 `backend_adapters[]` 中的 `business_backend` 调用；
- `internal_rpc_contracts[]` 只描述 Agent Runtime 可调用的 Backend 内部 RPC；owner 必须是 `business_backend`、`visibility` 必须是 `internal`，且不得生成公开 Route Contract；
- `agent_contracts[].tool_rpc_bindings[]` 必须同时解析到当前 Agent、一个 `internal_rpc_contracts[]` 和其中唯一 operation；模型、Frontend 和 Runtime 不能动态增加绑定；
- `backend_adapters[]` 的 owner 必须是 `business_backend`，外部 Operation 只能引用已确认 Endpoint Design snapshot；Gateway 不生成第三方 Client 或持有第三方凭据；
- Endpoint 现有 `path` 是规范公开路径，不维护第二份 public path；
- `upstream_path` 必须显式存在，不允许从公开路径隐式补全；
- Gateway 允许正式 Route Contract 声明的路径改写，Frontend Public Origin 只能指向 Gateway；
- `route_kind`、`policy_profile`、`anonymous`、`criticality` 全部进入 TechnicalPlan 确认门禁；
- 缺少 Backend 或 Agent Runtime 的候选无效；平台必须改用匹配的直连拓扑，不得删减路由后继续编译 GatewayPlan；
- Schema、Markdown 渲染与同步、模型输出、确定性校验、前后端类型和 Build Context 必须同批更新。

## 4. Decision Record

TechnicalPlan `gateway.decisions` 是架构 Decision 的唯一正式承载位置：

```json
{
  "decisionId": "GW-CORE-06",
  "selection": "independent_backend_bff"
}
```

规则：

- 影响公开 API、安全边界、部署拓扑、存储 owner 或多实例一致性的 Decision 必须在 TechnicalPlan 确认前完成；
- 每条记录只保存 `decisionId` 和 `selection`；确认身份、确认人和确认时间由 TechnicalPlan 确认生命周期统一审计；
- 合法选项由当前设计合同和 TemplateState 能力共同限制；
- 模板安全默认值不要求用户逐项确认；只有改变架构或事实 owner 的选择进入 Decision Record；
- TechnicalPlan 修订后重新校验所有受影响 Decision；
- GatewayPlan 只能投影已确认选择，不能生成、修改或覆盖 Decision。

## 5. 权威来源表

| 内容 | 唯一来源 |
| --- | --- |
| Gateway 是否存在 | TechnicalPlan `topology.type=gateway_composed` |
| 是否要求公开用户认证 | Application Config `auth.enable` |
| 是否启用 Agent RBAC | Application Config `authorization.enabled`；同时要求 `auth.enable=true` 且存在正式 Agent |
| Agent 资源、角色和用户-Agent策略 | Authorization Manifest 的 Agent 切片 |
| 服务、Endpoint owner、公开路径、upstream path、route kind、criticality | TechnicalPlan |
| Agent Runtime、公开 Agent Endpoint、Tool 到 RPC 的绑定 | TechnicalPlan `services[]`、`api_contracts[]`、`agent_contracts[]` |
| Backend 内部 RPC operation、Schema、超时和幂等语义 | TechnicalPlan `internal_rpc_contracts[]` |
| Backend Adapter 与外部服务依赖 | TechnicalPlan `backend_adapters[]`；外部 Operation 细节来自已确认 Endpoint Design snapshot |
| 字段映射、外部 Operation snapshot、实现说明 | 已确认 Endpoint Design |
| 行内统一认证 Profile、Claim Mapping、可用策略 Profile、模块路径、Application 入口、协议与框架能力 | Backend Template Manifest / TemplateState |
| 选中的策略 Profile 和架构 Decision | 已确认 TechnicalPlan |
| Origin 环境值、Secret 值、证书 | 部署环境或 Secret Store |
| Build Scope、任务依赖、路径权限 | Build Plan；只消费 GatewayExecutionSlice |

Template Manifest 为 Profile 提供受版本控制的安全参数。TechnicalPlan 只能选择当前 TemplateState 支持的 Profile；没有已确认选择且不存在唯一安全默认值时，必须阻止确认。

## 6. GatewayPlan

平台从当前正式产物确定性编译完整只读 GatewayPlan：

```json
{
  "contractVersion": "application-gateway.v1",
  "topologyType": "gateway_composed",
  "exposureMode": "gateway",
  "sourceLayout": "backend_submodule",
  "runtimeTopology": "independent_service",
  "module": {
    "artifactId": "<app>-gateway",
    "applicationClass": "GatewayApplication"
  },
  "publicOrigin": {
    "schemePolicy": "https_required_in_production",
    "basePath": "/api"
  },
  "routes": [],
  "upstreams": [],
  "authentication": {
    "profile": "enterprise_unified_jwt.v1",
    "verification": "local_public_key",
    "logoutCheck": "enterprise_logout_store",
    "claimMapping": "xcode_enterprise_principal.v1",
    "credentialForwarding": "forbidden"
  },
  "agentAuthorization": {
    "enabled": true,
    "profile": "agent_rbac.v1",
    "policySource": "authorization_manifest_agent_slice",
    "failureMode": "fail_closed",
    "resourceKeysByAgentId": {}
  },
  "internalIdentity": {
    "profile": "xcode_user_info_jws.v1",
    "headerName": "Xcode-User-Info",
    "issuer": "xcode-generated-gateway",
    "audience": "application-internal-services",
    "directRequestTtlSeconds": 60,
    "directRequestMaxTtlSeconds": 120,
    "agentTtlSeconds": 21600,
    "agentMaxTtlSeconds": 21600,
    "supportsRefresh": false,
    "supportsReauthorization": true,
    "reauthorizationMode": "frontend_via_gateway",
    "reauthorizationWindowSeconds": 600,
    "reauthorizationGraceSeconds": 1800,
    "activeStreamLogoutRecheckSeconds": 60,
    "logoutCheckCacheMaxSeconds": 60,
    "sourceLogoutBinding": "token_digest"
  },
  "cors": {},
  "csrf": {},
  "limits": {},
  "resilience": {},
  "streaming": {},
  "agentCompatibility": {
    "enabled": false,
    "agentIds": [],
    "rpcContractIds": [],
    "rpcOperationIds": []
  },
  "backendIntegrations": {
    "adapterIds": [],
    "externalServiceIds": []
  },
  "observability": {},
  "health": {},
  "requiredChecks": [],
  "sourceHashes": {
    "applicationGatewayAuthHash": "sha256:...",
    "technicalPlanGatewaySliceHash": "sha256:...",
    "templateGatewayCapabilityHash": "sha256:...",
    "topologyHash": "sha256:...",
    "publicOriginHash": "sha256:...",
    "authenticationProfileHash": "sha256:...",
    "internalIdentityProfileHash": "sha256:...",
    "agentAuthorizationHash": "sha256:...",
    "policyCatalogHash": "sha256:...",
    "routeHashes": {},
    "serviceIdentityHashes": {},
    "rpcContractHashes": {},
    "backendAdapterHashes": {}
  },
  "planSha256": "sha256:..."
}
```

`gateway_composed` 之外不编译 GatewayPlan。直连拓扑的 Public Edge、Frontend Origin、认证适配和 Launch 合同由对应 TopologyDefinition 编译，不得使用禁用态 GatewayPlan 伪装。

输入变化后旧 GatewayPlan 不能启动新的 PlanningRun。平台必须重新编译并生成新的 `planSha256`；已完成 Unit 是否复用，由该 Unit 实际消费的分片 Hash 和文件证据决定。

编译 GatewayPlan 前必须校验 `TechnicalPlan.topology.type=gateway_composed`，且正式服务图同时存在 Backend 和 Agent Runtime。不满足时不生成 GatewayPlan，也不允许通过忽略 Route 或生成空壳服务继续规划。

`authentication` 和 `internalIdentity` 是企业模板固定安全 Profile，不进入低代码用户 Decision。`auth.enable=true` 时必须使用 `enterprise_unified_jwt.v1`；它引用统一认证公钥、登出 Store 和 Claim Mapping 的配置名，不包含运行值。

`applicationGatewayAuthHash` 覆盖 `topology.type`、`auth.enable` 及 Gateway 实际消费的公开认证配置。`agentAuthorizationHash` 覆盖 `authorization.enabled`、Agent ID/Route 绑定和 Authorization Manifest 的 Agent 资源与策略切片；页面、动作、普通业务 Endpoint 与 Backend 数据授权投影不得进入该 Hash。两个开关任一关闭时 `agentAuthorization.enabled=false`，不保留空资源键或策略依赖。

`Xcode-User-Info` 是普通 Gateway -> Backend 和 Agent 委托的唯一内部用户上下文格式。普通请求只携带当前公开业务 Endpoint 的 `allowedEndpointIds`；Agent 委托只携带由 `agent_contracts[].tool_rpc_bindings[]` 确定性投射的 `allowedRpcOperationIds`。两类白名单不能混用，也不持久化到 GatewayPlan 运行值中。直连拓扑的身份合同由各自 Public Edge 负责，不属于 GatewayPlan。

Agent 委托票据是最长 6 小时的授权租约，不是登录 Session，也不是可刷新的 Refresh Token。每张票据必须包含 `grantVersion`；首次签发为 `1`。进入到期前 10 分钟窗口或已经过期后，Frontend 才能通过 Gateway 的 Agent control Endpoint 为同一 `agentId + runId` 发起重新授权。Gateway 必须重新验证统一认证 JWT、登出状态、线程权限和当前正式 Tool/RPC binding，然后签发 `grantVersion + 1` 的新票据。Runtime 只能原子替换为更高版本票据，不能自行续期、修改或请求扩大 `allowedRpcOperationIds`。

票据到期时 Runtime 必须把 run 转为 `authorization_paused`，停止模型继续执行、停止发起新 Backend RPC，并发出 `reauthorization_required` 事件。30 分钟重新授权宽限期内成功换票后可以从 checkpoint 恢复；宽限期结束仍未重新授权时，run 转为 `authorization_expired` 并释放执行资源。重新授权不会创建新 run，也不能改变 thread/run owner。

GatewayPlan 的 `upstreams[]` 只允许投影 `business_backend` 和 `agent_runtime`。`external_service`、`internal_rpc_contracts[]` 和 Tool/RPC binding 不生成 Gateway route；它们分别进入 Backend Adapter 和 Agent Runtime/Backend RPC 的受控 Build slice。

GatewayPlan 不得包含统一认证公钥、Redis 地址/凭据、原 JWT、内部签名私钥、验签公钥内容或任何真实用户 Claims；它只保存 Profile 和运行配置引用。

## 7. GatewayExecutionSlice

GatewayExecutionSlice 是当前 Build Scope 对 GatewayPlan 的只读投影：

```json
{
  "scopeId": "endpoint:orders_api:orders.list",
  "planSha256": "sha256:...",
  "routeIds": ["orders_api:orders.list"],
  "serviceIds": ["order-service"],
  "agentIds": [],
  "rpcContractIds": [],
  "rpcOperationIds": [],
  "backendAdapterIds": ["shipping-adapter"],
  "requiredUnitIds": [],
  "sourceSliceHashes": {},
  "requiredChecks": []
}
```

- 页面 Scope 包含页面全部正式 Endpoint 对应的 route；
- Endpoint Scope 只包含自身复合身份；
- Agent Scope 包含公开 Agent route、对应 runtime、Tool/RPC binding 和所需内部 RPC Operation；
- Backend Adapter Scope 包含 owner Backend、外部服务引用和已确认 Operation snapshot，不产生第三方 Gateway upstream；
- application Scope 包含全部 route 和共享 Gateway 能力；
- 切片不持久化为正式产物，不接受客户端或模型修改；
- 切片只缩小本轮生成范围，不能改变完整公开合同。

## 8. Route Contract

```json
{
  "routeId": "orders_api:orders.list",
  "routeKind": "business_http",
  "public": {
    "method": "GET",
    "path": "/api/orders"
  },
  "upstream": {
    "serviceId": "order-service",
    "method": "GET",
    "path": "/internal/orders"
  },
  "requestSchemaRef": null,
  "responseSchemaRef": "orders_api.OrderPage",
  "authenticationRequired": true,
  "agentAuthorization": null,
  "timeoutPolicy": "standard_read",
  "retryPolicy": "idempotent_read",
  "rateLimitPolicy": "authenticated_default",
  "cachePolicy": "none",
  "streamingMode": "none",
  "criticality": "required"
}
```

允许的 `routeKind`：

- `business_http`；
- `streaming_sse`；
- `websocket`；
- `agent_stream`；
- `agent_control`；
- `file_transfer`。

编译规则：

- `routeId` 固定为 `<apiContractId>:<endpointId>`；
- method + public path 在应用内唯一；
- public path 来自 Endpoint `path`；upstream service 来自 `owner_service_id`；upstream path 来自显式 `upstream_path`；
- `business_http`、`streaming_sse`、`websocket`、`file_transfer` 只能指向 `business_backend`；`agent_stream`、`agent_control` 只能指向 `agent_runtime`；
- `agent_stream`、`agent_control` 在 `auth.enable=true && authorization.enabled=true` 时必须投影 `agentAuthorization.required=true`、唯一 `agentId` 和确定性 `resourceKey`；其他 Route 的该字段必须为 `null`；
- 任一授权前置开关关闭时 Agent Route 不得残留资源键、角色、策略引用或授权 Filter，只保留非授权入口治理；
- Gateway 不能成为 owner，`external_service` 不能出现在 Route Contract upstream；
- 只转发 Endpoint Contract 声明的 Path、Query、Header 和 Body 字段；
- 未知 Query/Header 默认拒绝或丢弃；
- `Authorization`、`Xcode-User-Info`、`Claw-User-Info`、`X-User-Id` 和其他身份 Header 是模板保留字段，Endpoint Contract 不得声明透传或覆盖；
- Gateway 不改变业务 Schema；聚合和转换必须生成在明确的 Backend Adapter/BFF；
- 策略只能引用 TechnicalPlan 已确认且 TemplateState 支持的 Profile；
- 使用外部 API 的公开 Endpoint 仍编译为指向 Backend 的业务 Route；第三方 Operation snapshot 只供 Backend Adapter 使用，Gateway 不能接受外部 URL 或第三方凭据。

计划确认前必须拒绝：

- 相同 method/path 指向不同 Endpoint；
- 参数路径与通配路径优先级不确定；
- 路径改写丢失必需 Path 参数；
- 前端依赖 Endpoint 没有公开 route；
- route 指向不存在的 service/upstream；
- route kind 与 owner service kind 不匹配；
- 公开 Agent Endpoint 未指向对应 `agent_runtime`，或公开业务 Endpoint 未指向 `business_backend`；
- `internal_rpc_contracts[]` operation 被生成为公开 route，或 Tool/RPC binding 指向不存在的 operation；
- `external_service` 被设置为公开 Endpoint owner、Gateway upstream，或第三方凭据进入 Gateway 配置；
- 写操作套用只读重试或缓存；
- 流式 route 使用普通响应缓冲；
- Agent route 的拓扑、身份或故障策略未确认；
- 认证开启时存在无依据匿名业务 route。
- `auth.enable=true` 但 TemplateState 缺少统一认证、登出检查、Claim Mapping 或 `Xcode-User-Info` 能力。
- 存在 Agent 且 `authorization.enabled=true`，但 `auth.enable=false`、Agent Authorization Manifest 切片缺失、Agent resourceKey 不唯一或策略 Profile 不受模板支持；
- `topology.type != gateway_composed`；
- 服务图缺少 `business_backend` 或 `agent_runtime`；
- Frontend Public Origin 未唯一解析到 Gateway，或 Frontend 发现了 Backend/Runtime 内部地址。

## 9. 合同变化与运行值变化

必须重新编译 GatewayPlan：

- `topology.type` 或 `auth.enable`、统一认证 Profile/Claim Mapping 配置改变；
- 存在 Agent 时 `authorization.enabled`、Agent Authorization Manifest 切片或用户-Agent资源绑定改变；
- Endpoint method/path/Schema/service owner 改变；
- Agent Contract、Tool/RPC binding 或 Internal RPC Contract 改变；
- Backend Adapter owner、外部服务引用或 Operation snapshot 改变；
- 页面 Endpoint 依赖改变；
- 已确认 Endpoint Design snapshot、实现说明或字段映射改变；
- 配置键、Secret reference 身份、策略 Profile 或协议合同改变；
- Backend template revision 或 Gateway capability metadata 改变。

`authorization.enabled` 和 Authorization Manifest 的 Agent 切片只重新编译 `agentAuthorization`、相关 Agent Route Contract、Hash、RBAC Unit 与测试；普通业务 Route 和无关 Backend 授权投影不失效。

不重新生成 GatewayPlan 或代码：

- 同一配置键对应的 Origin 值改变；
- Secret 值、证书内容或签名密钥轮换；
- DNS、实例 IP、副本数和端口改变；
- 不改变配置 Schema 的观测后端地址改变。

运行值变化只触发配置原子校验以及模板支持的热更新或受管重启。Secret 值不进入 Hash、GatewayPlan、Build Diff 或报告。

## 10. 精确失效

| 变化 | 失效范围 |
| --- | --- |
| `topology.type` 离开或进入 `gateway_composed` | 整个拓扑的 managed roots、GatewayPlan、全部 Gateway Unit、Frontend public-origin、集成测试和 launch graph |
| 单个 `routeHash` | 对应 Route、registry、相关测试；公开 Schema/path 改变时再失效对应 API client 贡献 |
| 单个 `rpcContractHash` | 对应 Backend RPC service、Agent Runtime typed client、`allowedRpcOperationIds` 投影和 RPC 集成测试；不生成 Gateway route |
| 单个 `backendAdapterHash` | 对应 Backend Adapter 和外部服务集成测试；只有公开 Endpoint Schema/path 同时改变时才失效 Gateway route/API client |
| `serviceIdentityHash` | 目标 internal-auth、指向该 service 的 route 和集成测试 |
| `authenticationProfileHash` | Gateway/Backend 公开统一认证 binding、认证合同测试和 launch verification |
| `agentAuthorizationHash` | Agent RBAC binding、受保护 Agent Route、策略客户端/缓存、授权合同与集成测试；不失效普通业务 Route |
| `internalIdentityProfileHash` | Gateway `Xcode-User-Info` 签发、Backend/Runtime 验签和相关集成测试 |
| `publicOriginHash` | frontend public-origin、Gateway public config、集成/launch；不失效后端业务实现 |
| Profile 或 `policyCatalogHash` | 使用该 Profile 的 route/config/tests |
| TemplateState revision 但 Gateway capability 未变化 | 重新核对文件证据，只失效消费变化模板切片的 Unit |
| Gateway capability contract 改变 | 全部 Gateway Unit、internal auth、集成和 launch |
| Secret/Origin 运行值轮换 | 不失效代码 Unit，只执行配置校验、重启和健康验证 |

全局 `planSha256` 只证明本轮切片来自当前完整 GatewayPlan，不是全量重建条件。
