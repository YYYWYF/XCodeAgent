# Runtime 启动与前端代理合同（1028）

这是 Studio 当前消费合同。模板端由用户同步，不能把本文或消费端代码视为真实模板已经实现。保留当前 Generate/V3 State/V4 更新协议，不新增历史键、dotenv 回退或 Git 补模板。

## 当前 Studio 接入

入口仍为现有 `/preview-runtime/run` AG-UI 的手动 restart/stop/leave，以及验收的同一个 Project Launcher。只读状态订阅不自动启动。显式、已确认的 Runtime 服务图才会启动 Python；不按 Agent 数量或目录存在推断。Java 流程沿原入口。

`agent_runtime_launcher.py` 复核当前 TemplateState、capabilities、源码入口及模块策略；在现有工作区锁内停止旧 Runtime、同步冻结依赖、取密、启动、健康检查。模型和数据库凭据仅传给 Runtime 子进程，不写工作区或公开快照。依赖同步没有模型和数据库凭据，不安装缺失 uv、不修改锁文件、不自动修复或换端口重试。

预览快照和启动结果增加独立 `agent_runtime` 层，三类服务分别为 frontend/backend/agent_runtime。对应日志为 agent-runtime-install.stdout/stderr.log 和 agent-runtime.stdout/stderr.log。运行日志先按环境中秘密值及 Header 值脱敏再落盘。普通预览用真实匿名会话，不注入 Debug Principal 或内部万能令牌。

## Runtime 模板读取的环境键

| 范围 | 唯一键与含义 |
| --- | --- |
| 监听 | `AGENT_RUNTIME_HOST=127.0.0.1`；`AGENT_RUNTIME_PORT` 为本次随机可用端口；`AGENT_RUNTIME_LAUNCH_ID` 为本次启动身份 |
| 场景 | `AGENT_RUNTIME_PROFILE=preview` 或临时启动检查的 `probe`；`AGENT_RUNTIME_NAMESPACE` 隔离 preview/probe 的 checkpoint、会话与 memory |
| 身份 | `AGENT_RUNTIME_AUTH_ENABLED=false`；`AGENT_RUNTIME_AUTH_MODE=anonymous-session`；模板必须实际建立可信匿名 Principal 并做数据归属隔离 |
| Cookie | `AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET` 仅在子进程内，重启轮换；`AGENT_RUNTIME_ANONYMOUS_COOKIE_NAME` 按工作区隔离；旧 Cookie 无效时必须重新建立匿名会话，不让前端永久卡在无效凭据 |
| CORS | `AGENT_RUNTIME_ALLOWED_ORIGINS=[]` 为 JSON 数组。普通前端通过同源代理调用，不使用 `*` 或任意 Origin 回显；CORS 不代替匿名会话鉴权 |
| 模型 | `MODEL_PROVIDER`（openai/anthropic）、`MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`（API 名，不加 provider 前缀）、`MODEL_TIMEOUT_SECONDS`、`MODEL_MAX_RETRIES`、`MODEL_TRUST_ENV`、`MODEL_CUSTOM_HEADERS`（JSON 对象）、`ANTHROPIC_API_VERSION`、`AGENT_TEMPERATURE`、`AGENT_MAX_TOKENS` |
| 数据库 | `AGENT_RUNTIME_DATABASE_ENABLED`；启用时额外给出 `AGENT_RUNTIME_DATABASE_DRIVER=pymysql`、`HOST`、`PORT`、`USERNAME`、`PASSWORD`、`NAME`、`SSL_MODE`，均使用 `AGENT_RUNTIME_DATABASE_` 前缀；`AGENT_RUNTIME_DATABASE_ALLOW_DDL=false` |

模型沿 Studio 当前主模型 Settings 注入，不读取目标模板默认模型，不创建第二套配置权威。服务商余额和权限沿实际 Key 账户计算，注入不会制造额度。模板必须把模型 401/403、余额及超时错误保留为清晰业务失败，不能转成数据库错误或吞掉成通用失败。

业务连接共用 1028 `select_runtime_database`：内置模式共用 SaaS 全实体结构检查与一次 runtime-connection 取密，验证 datasource 身份及当前设计摘要；外部数据源沿当前目录和解密/DBID 合同，不从旧 application 连接字段回退。不存在数据库数据源时明确禁用。

模板必须提供 PyMySQL 驱动、事务和连接实现，读取分离字段，不拼接未转义的 URL。SSL_MODE 原样保持 SaaS 的 DISABLED/PREFERRED/REQUIRED/VERIFY_CA/VERIFY_IDENTITY：REQUIRED 必须加密；VERIFY_CA 验证证书链；VERIFY_IDENTITY 同时验证主机名。不得静默降级或通过关闭证书校验绕过失败。应用启动不执行 DDL、迁移或 seed；表缺失由现有实体建表/重建流程处理。

## 健康合同

`uv run --no-sync --no-env-file agent-runtime` 调用真实 project.scripts 入口，读取上表。Runtime 正常 `/health` 必须返回 HTTP 200：

```json
{
  "service": "agent-runtime",
  "launchId": "原样返回 AGENT_RUNTIME_LAUNCH_ID",
  "status": "ok",
  "checks": {
    "database": "ready",
    "businessApi": "ready",
    "agUi": "ready",
    "anonymousSession": "ready"
  }
}
```

未配置数据库时 database 必须为 `not_required`。ready 必须基于真实数据库连接、业务路由和已装配 Agent AG-UI 路由，不得写常量伪造成功；健康探测本身不调用模型或写业务数据。响应不含环境、模型/数据库密钥、Principal 或调试凭据。Studio 同时确认当前 PID/launchId、连续稳定就绪，健康只表明工程就绪，不代表会话业务和全部语义验收通过。

## 前端模板代理

Studio 只向前端开发服务器注入公开的 `AGENT_RUNTIME_PUBLIC_URL=http://127.0.0.1:<本次端口>`，不注入模型或数据库凭据。当前共享模板实际使用 Rsbuild；其开发服务器配置必须使用这个服务端环境变量设置代理，不能在浏览器写死 Runtime 端口：

- `/__runtime/health` 转发至 Runtime `/health`，用于验证前端连接的确为本次进程。
- 正式业务 Endpoint 路径（通常 `/api/*`）、匿名会话路径和 `/agents/*/run` 转发至同一 Runtime；保留 Cookie/Set-Cookie，流式 AG-UI 不缓冲、不改事件形状。
- 前端通过现有业务访问层调用接口，Agent 会话使用 `@ag-ui/client`；不新增手写 SSE 或手动事件解析。
- Cookie 失效时沿模板匿名会话握手重建，不伪造 Principal 或静默重发有副作用的业务消息。

前端启动后 Studio 从前端 origin 探测 `/__runtime/health` 并核对 launchId；未配置代理会明确失败并清理本轮服务。不能用前后端进程都存活作为联通结论。

## 接口调试与验收边界

服务抽屉新增第三张 Runtime 卡片及独立日志。只有当前 Runtime running 时可在独立 Electron 窗口打开 `/docs`，这只是当前普通预览的接口文档调试，不是独立 Debug Principal/Profile，不传递 secret。模板必须在本地预览提供该接口文档；正常会话在前端预览进行。

Python 启动检查使用同一启动器的临时模式，独立端口、日志和 probe namespace，完成即回收，不覆盖预览 PID 或停止原预览。源码、依赖、当前应用/技术规划/TemplateState、数据源及模型摘要变化会使旧启动缓存失效。

Python/Agent 静态业务验收现已在后续批次接入，能力与阻断边界见 [业务验收合同](BUSINESS_ACCEPTANCE_1028.md)；它不替代动态行为和真实会话证据。仍待完成：独立的 Debug Profile/短期凭据流程、模板端同步及真实会话联调。本批遵循用户要求，没有执行测试、构建、health、语法检查或 Electron 验证。
