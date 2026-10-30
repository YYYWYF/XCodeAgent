# Agent Runtime Direct 安全与本地调试设计

> 2026-10-28 搬运裁决：以 `dev_agent_integration_1028` 当前合同为准。应用配置、内置数据库、Template Engine、正式产物确认、恢复及资源锁均沿用新版；本文描述的 Runtime 扩展在完成对应阶段前不代表已经可用。

> 状态：迁入 1028 的目标设计；本文不作为功能交付完成证明。
> 核心边界：业务 API、Agent 与运行状态共享可信 Principal；Authorization 只有在 Python 模板提供正式 capability 时才允许开启

## 1. 安全模型

本拓扑把四类概念明确分开：

| 概念 | 是否支持 | 含义 |
| --- | --- | --- |
| Authentication | 可选 | 证明当前请求对应哪个 subject |
| Multi-user ownership | 必需 | 每个 Runtime 资源属于唯一可信 Principal |
| RBAC Authorization | 条件支持 | 仅在 Python Authorization capability、资源编译器和管理 API 完整实现时启用 |
| Tool confirmation | 按风险必需 | 用户确认一次具体写操作，不是角色授权 |

`authorization.enabled=false` 只关闭 RBAC，不能关闭认证、ownership、Tool allowlist、写操作确认、速率限制或审计。`authorization.enabled=true` 且模板能力缺失时，拓扑选择必须 fail-closed，禁止静默关闭权限。

## 2. Auth 开启模式

当 `application.json.auth.enable=true`：

- Runtime 是公开认证终止点；
- Frontend 复用现有 `login` capability 的页面和会话体验；
- Runtime 模板提供与 login capability 对齐的认证 Adapter；
- Token、Cookie 或外部 IdP assertion 只能由认证中间件验证；
- 验证成功后建立不可由请求体覆盖的 `TrustedPrincipal`；
- REST API、AG-UI、业务事务、thread、run、memory 和 Tool 都消费同一个 Principal；
- 登出、过期、撤销后不能恢复旧 checkpoint。

拓扑不重新定义第二套登录产品协议。具体登录方式由 `login` capability 当前合同决定；Runtime 负责提供等价的 Python 实现或标准 Provider Adapter。模板无法提供时 Bootstrap fail-closed。

## 3. Auth 关闭模式

当 `auth.enable=false`，不能使用用户提交的 `userId` 作为隔离依据。Runtime 必须：

1. 首次访问时生成高熵 anonymous session id；
2. 通过安全、HttpOnly、SameSite Cookie 或等价受保护会话返回；
3. 后续请求只从受保护会话恢复匿名 Principal；
4. 客户端不能指定、枚举或切换其他 anonymous subject；
5. 清理会话后旧资源不可被新会话猜测恢复。

匿名模式提供浏览器会话级隔离，不承诺跨设备身份恢复。需要跨设备、多终端或长期用户身份时必须开启 Auth。

## 4. TrustedPrincipal

Runtime 内部 Principal 最小结构：

```json
{
  "subjectId": "server-derived-subject",
  "tenantId": null,
  "authenticationMode": "authenticated",
  "sessionId": "server-derived-session",
  "issuedAt": "..."
}
```

约束：

- `subjectId` 和 `sessionId` 只能来自认证/匿名中间件；
- `tenantId` 只有当前 Auth Provider 明确提供时才存在；
- 不解析客户端 `X-Agent-User-Id`、`X-Agent-Tenant-Id`、role 或 scope；
- Principal 不进入模型 Prompt，除非 Contract 明确允许一个经过裁剪的业务展示字段；
- 完整 Token、Cookie 和 Provider assertion 不进入 Graph State、checkpoint、日志或 AG-UI 事件。

## 5. Ownership Key

所有 Runtime 状态和需要用户归属的业务资源必须以可信 Principal 建立隔离条件：

```text
applicationId
  + authenticationMode
  + tenantId? 
  + subjectId
  + agentId
```

资源范围：

- thread；
- run；
- message；
- checkpoint；
- pending interaction；
- short-term memory；
- long-term memory（未来启用时）；
- uploaded file；
- generated artifact；
- Tool execution record。
- ProductPlan/TechnicalPlan 声明为 owner-scoped 的业务 Entity；
- 业务上传文件、导出结果和长任务。

Repository 查询必须把 owner key 放入查询条件，不能先按 `threadId` 读取后在业务层比较。不存在和不属于当前 Principal 的资源统一返回不泄露存在性的结果。

## 6. 并发与重放

- `runId` 在 owner + thread 范围内唯一；
- 同一 `runId` 重复提交不能重复执行 Tool；
- 同一 thread 的并发写入使用 revision/CAS 或串行锁；
- cancel 只能取消当前 owner 的活动 run；
- interaction resume 同时校验 owner、agentId、threadId、interactionId 和 revision；
- 旧 interaction、其他用户 interaction 和已完成 interaction 必须拒绝；
- Runtime 重启后仍执行相同 ownership 校验。

## 7. Authorization 边界

Authorization 关闭时不得生成或启用：

- role / permission 表；
- resource key；
- policy engine；
- authorization manifest；
- `/authorization/*` 管理 API；
- 角色管理页面；
- Agent route RBAC；
- 基于用户可控 scope 的 Tool 授权。

Authorization 开启时，页面、操作、业务 Endpoint、Agent 和 Tool 资源必须由当前 Authorization Manifest 确定性编译为 Python 中间件与策略绑定。模型、Frontend 和用户请求不能新增 resource key、role、scope 或 policy。Python capability 尚未完成前，包含这些需求的应用不能选择本拓扑。

## 8. Tool 安全

Tool 执行同时满足：

```text
Agent Contract 声明
  ∩ Runtime capability allowlist
  ∩ Application Service authorization / data scope
  ∩ 部署环境凭据
  ∩ 当前交互确认状态
```

约束：

- 客户端 AG-UI `tools` 字段不能新增服务端 Tool；
- Prompt、Skill、Knowledge 和 Tool 输出不能扩大权限；
- 外部 HTTP 只允许配置域名、协议和操作；
- 阻断 loopback、link-local、metadata endpoint 和任意重定向逃逸；
- 响应大小、超时、重试和日志裁剪由平台固定；
- 写操作默认需要 confirmation，并通过 Application Service 执行业务事务；
- Tool 不得直接访问数据库 session，也不得通过 loopback HTTP 调用同一 Runtime 的 REST API；
- secret 只在 Tool Adapter 执行边界注入。

## 9. Public Edge

生产 Public Edge 最少公开：

```text
GET  /health
业务 REST Endpoint                 # 来自已确认 api_contracts
POST /agents/{agentId}/run        # AG-UI SSE
认证 capability 当前要求的入口   # auth on
匿名会话建立入口                  # auth off
```

禁止公开：

- `/internal/agents/*`；
- Runtime 配置、环境变量或模型信息；
- checkpoint 原始文件；
- 任意 threadId 的通用读取接口；
- Debug token 管理接口；
- 允许客户端设置 Principal 的 Header。
- 未进入正式 API Contract 的数据库或 Repository 通用接口。

CORS 只允许当前 Frontend origin。生产环境不允许 `*`、任意凭据 Origin 或根据请求回显 Origin。

## 10. 普通预览

普通项目预览必须按生产安全路径运行：

- Auth 开启时使用真实 login capability；
- Auth 关闭时使用真实匿名会话；
- 不注入 Debug Principal；
- 不向 Renderer 返回 Runtime secret；
- Frontend 只获得当前 Preview Public Edge URL；
- Preview 数据与独立 Debug 数据使用不同 namespace。

普通预览成功是 Acceptance 证据的一部分。

## 11. 独立本地调试 Profile

现有“启动 Runtime”能力可以复用进程管理，但认证合同必须调整。新调试流程：

```text
用户显式点击启动 Runtime 调试
  -> /agent-runtime-debug/run AG-UI action
  -> 校验受管工作区与 confirmed TechnicalPlan
  -> 确认 topology=agent_runtime_direct
  -> 停止旧 Debug Runtime
  -> 分配 loopback 端口
  -> 生成单次启动 Debug credential
  -> 注入固定 debug Principal 与独立 namespace
  -> 启动 Runtime debug profile
  -> /health readiness
  -> 返回 loopback URL + 临时 credential
```

Debug credential 必须：

- 每次启动随机生成；
- 只接受 loopback；
- audience 固定为当前 workspace + launch id；
- Runtime 停止或重新启动后失效；
- 不能访问普通预览或生产 namespace；
- 不能由 Header 自选 subject/tenant/scope；
- 只通过显式调试动作返回；
- 不进入 Git、正式产物、AG-UI state snapshot 或普通 preview result。

## 12. 调试 Principal

调试身份由 DevAgent Studio 和 Runtime 共同固定，例如：

```json
{
  "authenticationMode": "debug",
  "subjectId": "xcodeagent-local-debug",
  "tenantId": null,
  "namespace": "debug:<launchId>"
}
```

用户不能通过 curl Header 改成其他 subject。需要验证多用户隔离时，由专用安全测试创建受控测试 Principal，不复用人工 Debug credential。

## 13. 调试状态文件

可以继续使用：

```text
.devagentstudio/runtime/launch/agent-runtime-debug.json
```

只允许保存：

- status；
- host=`127.0.0.1`；
- port；
- pid/process evidence；
- launch id；
- health；
- credential 的短期受限副本或 keychain reference；
- sanitized error。

POSIX 权限固定 `0600`。停止后 credential 必须清空。正式源码、TemplateState 和 TechnicalPlan 不保存调试 credential。

## 14. 日志与可观测性

安全日志允许：

- trace id；
- hashed subject/tenant identifier；
- agentId；
- run 状态；
- Tool id、耗时、结果分类；
- Auth/ownership 拒绝码；
- bounded error summary。

禁止记录：

- Token、Cookie、API Key；
- 完整 Prompt 或用户消息；
- Tool 原始响应；
- checkpoint 内容；
- uploaded file 内容；
- Debug credential；
- 物理工作区绝对路径。

## 15. 必需安全测试

### Auth 开启

- 无凭据调用失败；
- 合法登录调用成功；
- 过期/撤销凭据失败；
- 伪造身份 Header 无效；
- 用户 A 无法访问用户 B 资源。

### Auth 关闭

- 首次访问获得服务器匿名会话；
- 客户端不能指定 anonymous subject；
- 两个浏览器会话互相隔离；
- 新匿名会话不能恢复旧会话资源。

### Tool

- 未声明 Tool 拒绝；
- 写 Tool 无 confirmation 拒绝；
- SSRF、重定向逃逸和超限响应拒绝；
- secret 不进入事件、日志和 checkpoint。
- Tool 与 REST Endpoint 对同一 Application Service 执行一致的授权、事务和数据范围规则；
- loopback HTTP 自调用和业务 SQL 直连被拒绝。

### Business API

- REST Endpoint 不能绕过 TrustedPrincipal；
- owner-scoped Entity 查询在 Repository 层带入 owner key；
- 页面 API 与 Agent Tool 不能跨用户读取或修改业务数据；
- 业务表结构操作与 Runtime checkpoint schema 互不覆盖；
- 未确认 Endpoint 和通用 Repository 接口不能暴露为公开路由。

### Debug

- 非 loopback 拒绝；
- 旧 credential 重启后失效；
- Debug Principal 不能访问 preview namespace；
- 人工 Header 不能切换 subject；
- 状态文件停止后不保留 credential。

任何 cross-principal isolation、Auth bypass 或 secret 泄露测试失败都不可跳过，必须阻断 Acceptance。

## 1028 实施补充：数据库、模板与传输

- Studio 新增或实质修改的产品动作采用 AG-UI 完整生命周期，Renderer 使用官方客户端。生成应用的新增产品动作也遵守仓库 AG-UI 规则；既有 Endpoint 的方法、路径和正式 Schema 不得在搬运中被暗改。
- 业务表使用平台内置 MySQL、当前完整物理表名和已有实体表操作；连接复用 prepare_managed_startup／resolve_managed_runtime。只向 Python 子进程注入所需连接，不传 SaaS 服务密钥，连接错误和日志统一脱敏。
- Runtime checkpoint／短期记忆与业务数据库分开；是否更换其存储需独立模板能力与验收，本次不把业务 MySQL 接入等同于已替换 checkpoint SQLite。
- Template Engine Generate 的当前包校验及配置更新事务保持 1028 合同。需要新增 Runtime 根目录与 capability 时同步生产者、消费者和测试，不使用旧 Git 克隆兜底或手造 TemplateState。
- 保留新版实体创建／重建／结构检查／操作记录、版本 SQL 交付、字段映射、配置事务、资源锁、任务结束及恢复语义，不恢复旧 Direct 独立 SQL／API 确认链。

