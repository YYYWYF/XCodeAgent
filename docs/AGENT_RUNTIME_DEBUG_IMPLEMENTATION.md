# Agent Runtime 托管启动与临时调试实施记录

实施日期：2026-09-10

## 1. 实施结果

本次实现让 XCodeAgent 可以在不进入 Agent 代码生成流程的情况下，独立启动生成应用工作区中的 `agent-runtime/`，并为 Runtime 注入运行所需的模型 fallback、工作区稳定端口和临时内部认证 Token。端口首次动态分配，后续重启优先复用；只有被其他真实服务占用时才更换。

工作台 Agent 详情中的“启动 Runtime”按钮位于“开始开发智能体”之前，仅用于临时调试。启动成功后，用户可以获得当前 loopback 地址和本次启动专用的 Bearer Token，并直接使用 `curl` 验证内置 Chat Agent。

本次没有修改 Agent 代码生成逻辑、Build DAG 或 CodeRunner。

## 2. 当前启动流程

```text
工作台“启动 Runtime”
  -> /agent-runtime-debug/run（独立 AG-UI 动作）
  -> 校验 XCodeAgent 受管工作区
  -> 安全停止该工作区的旧 Runtime
  -> uv sync --frozen
  -> 优先复用工作区上次端口；被其他服务占用时分配新端口
  -> 生成本次启动专用随机 Token
  -> 注入模型 fallback、端口和 AGENT_RUNTIME_GATEWAY_TOKEN
  -> uv --directory <agent-runtime> run agent-runtime
  -> 调用 /health 等待 readiness
  -> 写入工作区调试状态文件
  -> 前端展示可复制的 Runtime 地址和 Bearer Token
```

该流程独立于主 Workflow，不执行需求、产品规划、技术规划或 Agent Build。

## 3. 模板模型配置 fallback

模板仓库：`/Users/frank/agent-runtime-template`

模板的模型配置按以下优先级读取：

```text
工作区 .env 中的 MODEL_* / AGENT_*
  -> XCodeAgent 注入的 XCODEAGENT_FALLBACK_*
  -> 模板数值默认值
```

XCodeAgent 托管启动时注入：

- `XCODEAGENT_FALLBACK_MODEL_BASE_URL`
- `XCODEAGENT_FALLBACK_MODEL_API_KEY`
- `XCODEAGENT_FALLBACK_MODEL_NAME`
- `XCODEAGENT_FALLBACK_MODEL_TIMEOUT_SECONDS`
- `XCODEAGENT_FALLBACK_MODEL_MAX_RETRIES`
- `XCODEAGENT_FALLBACK_AGENT_TEMPERATURE`
- `XCODEAGENT_FALLBACK_AGENT_MAX_TOKENS`

启动器会先移除从 XCodeAgent 父进程继承的普通 `MODEL_*`、`AGENT_*` 模型变量，再注入 fallback 名称。这样模板根目录 `.env` 中的显式项目配置保持最高优先级，同时没有 `.env` 的工作区可以直接复用 XCodeAgent 当前模型配置。

模型 API Key 不会写入工作区文件、启动结果、调试状态文件或 Runtime 日志。

## 4. 动态端口与临时认证

XCodeAgent 托管启动不固定使用模板默认的 `8010`。首次调试启动会分配一个可用的 loopback 端口；后续启动先安全停止该工作区由 XCodeAgent 管理的旧 Runtime，再优先复用状态文件中的上次端口。端口探测启用 `SO_REUSEADDR`，允许复用旧 Runtime 关闭连接后留下的 `TIME_WAIT`；如果端口仍存在真实监听占用，绑定仍会失败，启动器才申请新端口并更新状态文件。随后注入：

```text
AGENT_RUNTIME_HOST=127.0.0.1
AGENT_RUNTIME_PORT=<动态端口>
```

每次启动还会生成一枚新的随机 Token，并注入：

```text
AGENT_RUNTIME_GATEWAY_TOKEN=<本次启动专用随机值>
```

Token 在当前 Runtime 进程生命周期内保持不变。重新启动 Runtime 后会生成新 Token，旧 Token 随即失效。

普通项目预览结果不会返回该 Token。只有用户显式点击“启动 Runtime”的临时调试动作，才会向当前 XCodeAgent Renderer 返回 loopback 地址和调试 Token，方便本机联调。

## 5. 工作区调试状态文件

显式调试启动成功后写入：

```text
<workspace>/.xcodeagent/runtime/launch/agent-runtime-debug.json
```

当前结构：

```json
{
  "status": "running",
  "service": "agent-runtime",
  "host": "127.0.0.1",
  "port": 61245,
  "health": {
    "status": "healthy"
  },
  "reused": false,
  "errorCode": null,
  "message": "Agent Runtime 已启动",
  "debugToken": "<本次启动专用随机 Token>"
}
```

文件行为：

- 使用同目录临时文件原子覆盖，避免读取到半写入 JSON；
- macOS/Linux 权限固定为 `0600`；
- 正常重新启动复用当前工作区端口并生成新 Token；
- 上次端口被其他服务占用时分配新端口并覆盖记录；
- Runtime 停止后，`status` 和 `health.status` 更新为 `stopped`；
- Runtime 停止后，`debugToken` 更新为 `null`；
- 文件不属于生成应用源码或正式部署配置。

## 6. 旧进程安全恢复

XCodeAgent 后端重启后，内存中的 `subprocess.Popen` 登记会丢失，因此启动器需要通过工作区 PID 文件恢复并清理旧 Runtime。

原实现要求进程命令行同时包含 `agent-runtime` 和工作区绝对路径，但旧启动命令可能只有：

```text
uv run agent-runtime
```

工作区路径仅存在于进程当前工作目录中，导致合法 Runtime 被误判为其他进程并拒绝终止。

本次调整后：

- 优先校验进程命令行是否包含 Runtime 标识和当前 Runtime 根目录；
- 命令行没有根目录时，在 POSIX 上读取进程实际工作目录继续核验；
- Linux 优先读取 `/proc/<pid>/cwd`；
- macOS 使用 `lsof` 读取进程工作目录；
- 后续启动使用 `uv --directory <agent-runtime> run agent-runtime`，让工作区路径显式进入命令行；
- 同名进程位于其他工作区时仍拒绝终止；
- PID 无效、复用、身份不匹配或终止失败时继续 fail-closed，避免误杀。

POSIX 上恢复 PID 文件中的进程时必须同时满足两项独立证据：命令行包含 `agent-runtime` 标识，并且进程实际工作目录精确等于当前工作区的 `agent-runtime/`。Windows 无法可靠读取其他进程工作目录，因此要求命令行同时包含 `agent-runtime` 标识和当前工作区 Runtime 绝对路径。任何证据缺失、读取失败或不匹配都拒绝终止。

每个工作区还使用独立启动锁串行化 Runtime 启动。每次启动必须先完成该工作区旧进程清理；只要清理失败，本次启动立即失败，不会继续创建第二个受管 Runtime。因此端口复用负责保持调试地址稳定，启动锁与 fail-closed 清理负责保证一个工作区只有一个由 XCodeAgent 管理的 Runtime。端口探测只决定复用旧端口还是申请新端口，绝不根据端口占用直接终止进程或其他服务。

调试 AG-UI 会把经过换行和长度裁剪的清理失败原因返回前端，不再只显示笼统的“无法安全停止上一次 Agent Runtime 进程”。

## 7. AG-UI 调试契约

Endpoint：

```text
POST /agent-runtime-debug/run
```

动作输入位于：

```text
forwardedProps.agentRuntimeDebug
```

当前动作：

```json
{
  "action": "start",
  "workspaceRoot": "<受管工作区绝对路径>"
}
```

该动作发送完整 AG-UI 生命周期，包括运行开始、进度消息、结构化结果或错误、状态快照和运行结束。

成功结果中的 Runtime 调试字段包括：

- `status`
- `message`
- `ready`
- `pid`
- `managedModelFallbackAvailable`
- `runtimeUrl`
- `debugGatewayToken`

物理工作区路径和模型配置不会进入 Renderer。

## 8. Chat curl 验证

先从调试弹窗或状态文件取得当前地址和 Token，然后调用：

```bash
curl --no-buffer \
  http://127.0.0.1:<动态端口>/internal/agents/chat/run \
  -H 'Accept: text/event-stream' \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <debugToken>' \
  -H 'X-Agent-User-Id: local-user' \
  -H 'X-Agent-Tenant-Id: local-tenant' \
  -H 'X-Agent-Scopes: chat' \
  --data-binary '{
    "threadId": "curl-debug-thread",
    "runId": "curl-debug-run-1",
    "messages": [
      {
        "id": "message-1",
        "role": "user",
        "content": "四川省成都市有多大？请介绍其行政区域面积、人口规模和下辖行政区划。"
      }
    ],
    "state": {},
    "tools": [],
    "context": [],
    "forwardedProps": {}
  }'
```

成功响应是 AG-UI SSE 流，至少应包含：

```text
RUN_STARTED
TEXT_MESSAGE_START
TEXT_MESSAGE_CONTENT
TEXT_MESSAGE_END
agent_runtime_result.v1
RUN_FINISHED
```

常见失败判断：

- 无法连接：端口已变化、Runtime 已停止，或状态文件过期；
- HTTP 401：Bearer Token 不属于当前启动实例；
- HTTP 422：AG-UI 请求体或可信上下文 Header 不完整；
- `RUN_ERROR / AGENT_RUNTIME_INTERNAL_ERROR`：已进入 Runtime，但模型初始化或模型请求失败，应查看 Runtime stderr 日志。

重复请求时应使用新的 `runId`。

## 9. 主要代码位置

XCodeAgent Backend：

- `Backend/app/services/agent_runtime_launch_support.py`
- `Backend/app/services/agent_runtime_process_registry.py`
- `Backend/app/services/agent_runtime_project_launcher.py`
- `Backend/app/services/agent_runtime_debug_state.py`
- `Backend/app/protocols/agent_runtime_debug.py`
- `Backend/app/main.py`

XCodeAgent Frontend：

- `Frontend/src/renderer/src/service/agentRuntimeDebug.ts`
- `Frontend/src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/index.tsx`
- `Frontend/src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail/AgentRuntimeDebugButton.less`

Agent Runtime Template：

- `src/app/settings.py`
- `src/app/main.py`
- `src/app/server/app.py`
- `src/app/server/agui.py`
- `src/app/agent/context.py`

测试：

- `Backend/tests/test_agent_runtime_project_launcher.py`
- `Backend/tests/test_agent_runtime_debug_protocol.py`
- 模板仓库 `tests/test_runtime.py`

## 10. 验证记录

已通过：

- XCodeAgent Backend Runtime 启动、端口复用、安全清理与调试协议聚焦测试：13 项通过；
- XCodeAgent Backend `/health` 返回 `status=ok`；
- 生成工作区 Runtime `/health` 返回 HTTP 200；
- 使用动态端口和本次启动 Token 已完成真实 Chat curl 链路验证。

未完成：

- Frontend `pnpm build` 尚未通过环境级启动检查。当前全局 `pnpm 11.5.1` 要求 Node.js `>=22.13`，而执行环境是 Node.js `20.20.2`，因此在进入项目 TypeScript 编译前报 `ERR_UNKNOWN_BUILTIN_MODULE: node:sqlite`；
- 尚未完成 Java Agent Gateway 与 Runtime 的正式双向地址、内部凭据和业务 Tool 联调；
- 尚未实现 Runtime 意外退出后的持续健康状态回写，当前状态文件在受管启动和受管停止边界更新。

## 11. 安全边界

- Runtime 只监听 `127.0.0.1`；
- 模型 API Key 仅进入 Runtime 子进程环境；
- 模型 fallback 不写入工作区或调试结果；
- 调试 Token 每次启动随机生成，不复用旧实例 Token；
- 调试 Token 仅写入权限受限的调试状态文件，并只通过显式调试动作返回；
- 停止 Runtime 后清空状态文件中的 Token；
- 普通项目启动不会向 Renderer 返回 Runtime 内部 Token；
- 端口探测只决定复用或更换端口，绝不直接终止端口占用者；
- POSIX PID 恢复同时要求 Runtime 命令标识和精确工作目录匹配；
- Windows PID 恢复同时要求 Runtime 命令标识和命令行中的精确工作区路径匹配；
- 任一身份依据缺失、读取失败或不匹配时，拒绝终止旧进程并停止新启动。
