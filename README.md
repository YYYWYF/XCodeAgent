# 模型转发服务

独立 Python 服务，集中保存上游连接配置，转发模型请求并保存本次实际调用历史。所有业务提示词、上下文构造、工具执行、正式聊天保存和会话恢复继续由 Studio 负责。

## 启动

建议 Python 3.12。首次部署创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填写 DIRECT_API_KEY 和独立 SERVICE_TOKEN。
.\scripts\start.ps1
```

默认监听 `127.0.0.1:8100`。Studio 的 `MODEL_TRANSMIT_TOKEN` 必须与服务端 `SERVICE_TOKEN` 一致；该凭证与上游密钥独立。示例 token 仅供本机开发，部署时设置独立值。

也可从本目录运行：

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8100 --workers 1
```

**当前 SQLite 运行形态只支持单服务进程、单 worker**，同一数据库不能被多个服务实例共用。启动时会将遗留 `running` 调用标记为 `interrupted`；不得使用多 worker 或在有活跃请求时启动第二个实例。变更配置需要停止并重新启动服务。

`GET /health` 不需要凭证，不访问模型和 LangSmith，不返回地址、密钥或历史内容。所有模型产品接口要求 `Authorization: Bearer <SERVICE_TOKEN>`。API 文档位于 `/docs`。

## 上游配置

默认 `MODEL_UPSTREAM=direct`，实际地址、模型名称和密钥由 `DIRECT_BASE_URL`、`DIRECT_MODEL_NAME`、`DIRECT_API_KEY` 配置。未启用的 `gateway` 凭证可以为空，也不校验其未完成地址。调用方不能选择上游、覆盖真实模型名称、认证头或连接地址。

`MODEL_UPSTREAM=gateway` 支持 `fast / pro / ultra` 三档。档位由服务端 `GATEWAY_TIER` 统一选择，所有 Studio 场景共用所选档位，调用方不能选择或覆盖档位；错误不会触发自动换档。启用前填写所选档位的地址、模型标识，以及三档共用的 `GATEWAY_API_KEY` 和 `GATEWAY_KEY`：

```dotenv
MODEL_UPSTREAM=gateway
GATEWAY_TIER=fast
GATEWAY_FAST_BASE_URL=http://lt5404-agent-gateway.paas.cmbchina.cn/pattern/llm/fast/v1
GATEWAY_FAST_MODEL_NAME=fast
GATEWAY_PRO_BASE_URL=
GATEWAY_PRO_MODEL_NAME=
GATEWAY_ULTRA_BASE_URL=
GATEWAY_ULTRA_MODEL_NAME=
```

pro、ultra 的真实地址和请求体 `model` 尚未提供，配置预留为空；不能通过替换 fast 路径来推断真实接口。拿到实际参数后，填写对应的 `GATEWAY_PRO_*` 或 `GATEWAY_ULTRA_*` 并切换 `GATEWAY_TIER` 即可。基础地址不包含 `/chat/completions`，SDK 会追加。所选档位缺地址、模型标识或共享凭据时，启动明确失败；未选档位可留空或保留未完成地址，direct 模式不校验网关地址和凭据。三档目前沿用共用凭据与请求头规则，真实联调时需要确认该前提。

旧的单组 `GATEWAY_BASE_URL`、`GATEWAY_MODEL_NAME` 已由三组明确配置替代，运行时不读取旧字段。已有私有 `.env` 请保留原凭据，把原地址与模型名称填写到相应档位，再设置 `GATEWAY_TIER`，无需覆盖整个 `.env`。

切换方式或档位后停止并重新启动本服务。Studio 无需修改代码或上游配置；建议同步重启 Studio 后端，刷新客户端缓存的公开模型信息。按所选网关配置发送：

| 上游字段 | 来源 |
| --- | --- |
| `Authorization` | `GATEWAY_API_KEY`，服务端生成 Bearer 头 |
| `x-gateway-key` | `GATEWAY_KEY` |
| `x-spaid` | `GATEWAY_SPAID_SOURCE=request` 时取 `metadata.spaid`；`fixed` 时只取 `GATEWAY_SPAID` |
| `x-session-id` | `metadata.sessionId`，缺省取 AG-UI `threadId` |
| `x-request-id` | `metadata.requestId`，缺省取 AG-UI `runId` |
| `model` | `GATEWAY_<选中档位>_MODEL_NAME`，覆盖调用方传入值 |

网关目前通过模拟 HTTP 验证，真实网关凭证和 spaid 规则尚未提供，需要补齐后联调。服务默认不信任系统代理；只有设置 `UPSTREAM_TRUST_ENV=true` 才使用系统代理环境。上游请求不跟随重定向，避免向跳转目标发送认证信息。

## AG-UI 合同

两个产品接口均接收官方 `RunAgentInput`，使用官方编码器输出 SSE，并完整发送运行开始、助手消息、custom 结果或错误、最终状态与运行结束。输入校验、鉴权、请求大小和重复身份等流建立前错误分别返回 HTTP 422、401、413、409。

`POST /model-info/run` 的 `model-info.result` 值为：

```json
{"model":"deepseek-v4p1-flash-w8a8","provider":"openai","upstream":"direct"}
```

gateway 模式额外返回服务端确定的 `gatewayTier`，例如 `{"model":"fast","provider":"openai","upstream":"gateway","gatewayTier":"fast"}`；真实 `model` 取所选档位的配置值，不要求与档位名称相同。`GET /health` 同样在 gateway 模式显示 `gatewayTier`，direct 模式不返回该字段；均不公开上游地址或凭据。

`POST /model-inference/run` 输入示例：

```json
{
  "threadId": "studio-thread-id",
  "runId": "unique-logical-call-id",
  "messages": [],
  "tools": [],
  "context": [],
  "forwardedProps": {
    "modelInference": {
      "request": {
        "messages": [{"role":"user","content":"你好"}],
        "stream": true,
        "temperature": 0.2
      },
      "timeoutSeconds": 300,
      "maxRetries": 0,
      "metadata": {
        "sessionId": "studio-session-id",
        "requestId": "request-id",
        "extensions": {}
      }
    }
  }
}
```

实际消息、工具定义和生成参数均放在 `request` 中。服务只替换 `model`，不追加、裁剪或总结消息，不添加业务默认参数。`thinking` 等 SDK 未知字段以 `extra_body` 发送给上游，是否支持由上游决定。

| 事件 | 载荷 |
| --- | --- |
| `model-inference.chunk` | 原始 OpenAI SDK chunk 字典；仅流式调用发送 |
| `model-inference.result` | 非流式完整结果，或从 chunk 聚合的完整结果 |
| `model-inference.error` | `code`、`message`、`retryable`，上游 HTTP 错误另含 `statusCode` |

同时发出标准 AG-UI 文本和工具调用事件，供可视化消费者使用。Studio 模型适配器只消费 custom 原始模型结果，避免重复拼接。工具调用只作为模型输出转发，不在服务执行工具。

`runId` 是全局唯一的逻辑模型调用身份。一经登记即不允许同 ID 重放，包括失败、取消和进程中断后的调用；重复请求返回 409，由 Studio 决定新调用身份。

服务端默认上限为每次网络操作 1800 秒、SDK 重试 2 次；超限请求直接拒绝。调用方可以传 0 次重试，SDK 是唯一执行网络重试的层；Studio 不自动重试同一 HTTP 调用。开始产生内容后，流读取故障不自动重播。结束原因缺失时标记失败并保留部分输出。客户端断开会关闭上游连接，并尽力终止上游执行。

## 历史、追踪及扩展

SQLite 默认保存到 `data/model_history.sqlite3`，也可通过 `HISTORY_DB_PATH` 指定绝对路径。`model_calls` 保存本次实际输入及完整或部分模型输出、状态、用量、结束原因、耗时、会话和请求身份；不保存认证请求头。支持 `completed`、`failed`、`cancelled`、`interrupted`。流式期间约每 0.5 秒提交部分输出，正常及异常结束时原子提交最终状态；强制结束进程可能丢失最后一个检查点之后的片段。

`attempt_id` 表示本次转发执行身份；SDK 内部自动网络重试归属于同一执行，不伪造逐次重试明细。预留 `user_id`、`project_id`、`studio_data_ref`、`analysis`、`extensions`，不采集项目文档、源码、附件或其他 Studio 业务数据。`extensions.gatewayTier` 是服务端保留字段：gateway 调用记录实际所选档位，direct 调用移除该字段，客户端不能伪造；其他扩展信息保留。不修改 SQLite 表结构。没有新增历史 HTTP 查询接口，开发分析可通过 SQLite 或 `HistoryStore.get(run_id)` 读取。服务不会从历史中补拼下一轮上下文。

`LANGSMITH_ENABLED=false` 时不创建客户端，也不会产生追踪网络请求。开启时填写专用 API Key、endpoint 和 project；按会话关联请求、输出与调用状态，gateway 调用的追踪 metadata 同时记录 `gatewayTier` 和实际 `model`，并将 `langsmith_run_id` 保存到 SQLite。追踪客户端初始化、开始和结束故障均隔离，不影响模型结果及本地历史；LangSmith 不是本服务历史的唯一存储。正式启用前应为追踪部署确认数据访问权限和保留期限。

`MOCK_ANALYTICS_ENABLED=false` 默认关闭。开启后调用 `app.tracing.mock_analytics(record)`，生成 `wouldInsert` 载荷和 `inserted=false` 回执，保存在该调用的 `analysis` 字段。载荷通过 runId 引用产出，`extensions` 保留历史中已确定的档位及扩展关联信息，不连接真实分析数据库。后续替换该独立入口即可接入具体埋点逻辑。

历史包含完整提示词和模型产出，应将 `data/` 及 LangSmith 项目权限限制在授权开发人员范围。`.env`、运行数据库和虚拟环境已加入忽略规则。

## 验证

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
Invoke-RestMethod http://127.0.0.1:8100/health
```

离线测试覆盖官方 SDK 的流和非流响应、工具参数分片、两种认证头、三档网关地址与模型匹配、所选配置校验、失败不换档、档位历史与追踪、防重、鉴权、取消、重启中断、LangSmith 故障隔离和 mock 埋点。不使用真实模型密钥，也不访问外部服务。

三档配置的实施结果与跨项目回归记录见 [网关档位验证记录](docs/GATEWAY_TIERS_VALIDATION.md)。
