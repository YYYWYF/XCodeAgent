# Agent Runtime Direct 安全与本地调试设计

> 状态：待用户审核  
> 核心边界：可以认证和支持多用户，但不启用 RBAC

## 1. 安全模型

本拓扑把四类概念明确分开：

| 概念 | 是否支持 | 含义 |
| --- | --- | --- |
| Authentication | 可选 | 证明当前请求对应哪个 subject |
| Multi-user ownership | 必需 | 每个 Runtime 资源属于唯一可信 Principal |
| RBAC Authorization | 禁止 | 不生成角色、资源目录、策略和授权管理 |
| Tool confirmation | 按风险必需 | 用户确认一次具体写操作，不是角色授权 |

`authorization.enabled=false` 只关闭 RBAC，不能关闭认证、ownership、Tool allowlist、写操作确认、速率限制或审计。

## 2. Auth 开启模式

当 `application.json.auth.enable=true`：

- Runtime 是公开认证终止点；
- Frontend 复用现有 `login` capability 的页面和会话体验；
- Runtime 模板提供与 login capability 对齐的认证 Adapter；
- Token、Cookie 或外部 IdP assertion 只能由认证中间件验证；
- 验证成功后建立不可由请求体覆盖的 `TrustedPrincipal`；
- AG-UI、thread、run、memory 和 Tool 都消费同一个 Principal；
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

所有 Runtime 资源必须以以下逻辑 owner key 隔离：

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

Repository 查询必须把 owner key 放入查询条件，不能先按 `threadId` 读取后在业务层比较。不存在和不属于当前 Principal 的资源统一返回不泄露存在性的结果。

## 6. 并发与重放

- `runId` 在 owner + thread 范围内唯一；
- 同一 `runId` 重复提交不能重复执行 Tool；
- 同一 thread 的并发写入使用 revision/CAS 或串行锁；
- cancel 只能取消当前 owner 的活动 run；
- interaction resume 同时校验 owner、agentId、threadId、interactionId 和 revision；
- 旧 interaction、其他用户 interaction 和已完成 interaction 必须拒绝；
- Runtime 重启后仍执行相同 ownership 校验。

## 7. RBAC 禁止项

本拓扑不得生成或启用：

- role / permission 表；
- resource key；
- policy engine；
- authorization manifest；
- `/authorization/*` 管理 API；
- 角色管理页面；
- Agent route RBAC；
- 基于用户可控 scope 的 Tool 授权。

如果产品出现“管理员可查看所有用户会话”“不同角色可使用不同 Agent/Tool”等需求，已属于 Authorization，必须退出本拓扑或重新设计一个支持 Authorization 的拓扑。

## 8. Tool 安全

Tool 执行同时满足：

```text
Agent Contract 声明
  ∩ Runtime capability allowlist
  ∩ 部署环境凭据
  ∩ 当前交互确认状态
```

约束：

- 客户端 AG-UI `tools` 字段不能新增服务端 Tool；
- Prompt、Skill、Knowledge 和 Tool 输出不能扩大权限；
- 外部 HTTP 只允许配置域名、协议和操作；
- 阻断 loopback、link-local、metadata endpoint 和任意重定向逃逸；
- 响应大小、超时、重试和日志裁剪由平台固定；
- 写操作默认需要 confirmation；
- secret 只在 Tool Adapter 执行边界注入。

## 9. Public Edge

生产 Public Edge 最少公开：

```text
GET  /health
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

调试身份由 XCodeAgent 和 Runtime 共同固定，例如：

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
.xcodeagent/runtime/launch/agent-runtime-debug.json
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

### Debug

- 非 loopback 拒绝；
- 旧 credential 重启后失效；
- Debug Principal 不能访问 preview namespace；
- 人工 Header 不能切换 subject；
- 状态文件停止后不保留 credential。

任何 cross-principal isolation、Auth bypass 或 secret 泄露测试失败都不可跳过，必须阻断 Acceptance。
