# 模型转发与模型调用观测服务

code8 是以 code7.1 为基础的独立 Python 工程。保留 direct/gateway 转发、官方 AG-UI/SSE、取消、防重、SQLite 历史及 mock 分析，使用 LangSmith SDK 的本地追踪对象采集模型用量与时间。所有改动仅在 code8，Studio 与 code7.1 不需要源码变更。

每次被接纳的模型推理在成功、失败或取消收尾时生成统计快照；输出开关开启后，交给共享线程池异步向控制台输出一行完整 JSON：

```text
{"identity":{"user_id":null,"project_id":null,"conversation_id":null,"thread_id":"thread-1","run_id":"run-1","operation_id":null,"workflow_scope":null,"current_operation":"generate_technical_plan"},"data":{"trace_id":"019a267d-6000-7000-8000-000000000001","span_id":"019a267d-6000-7000-8000-000000000001","parent_span_id":null,"span_kind":"model","run_name":"model-inference","started_at":"2026-10-27T14:00:00+08:00","ended_at":"2026-10-27T14:00:01.500000+08:00","duration_ms":1500,"status":"completed","model":{"provider":"direct","name":"configured-model","input_tokens":284,"output_tokens":45,"total_tokens":329,"usage_source":null,"usage_status":null,"ttft_ms":250,"output_duration_ms":750},"error":null}}
```

ID、数字和时间为示例；运行时 ID 由本地 LangSmith SDK 创建，追踪对象创建失败时对应 ID 为 null。响应体只用于控制台与本地历史，不加入或替换原有 AG-UI 返回结果。`identity.current_operation` 使用英文 snake_case 标识，不再输出独立动作行；started_at/ended_at 使用北京时间（东八区 +08:00）的 ISO8601 字符串，时分秒和时区内部不插入空格。耗时仍由单调时钟计算，不受时区显示转换影响。

服务只统计模型调用：`span_kind` 固定为 `model`，`run_name` 固定为 `model-inference`。工具定义、工具指令和工具结果保持原有转发协议，不识别或额外打印工具观察记录。

## 异步输出与统一开关

在 code8 的 `.env` 中设置：

```dotenv
MODEL_OBSERVATION_OUTPUT_ENABLED=true
```

默认开启，修改后重启服务生效。`false` 时不投递统计响应体输出任务；模型推理、本地 LangSmith 采集和 SQLite 的 `analysis.execution_trace` 继续正常保存，服务运行日志仍按原设置输出。

应用使用单个工作线程的共享 `ThreadPoolExecutor`，最多接纳 256 个待处理任务（包含执行中的任务）。请求收尾先冻结完整 JSON，再非阻塞提交，主流程不等待 `write/flush`。排队不会修改已记录的北京时间、耗时、Token 或英文操作标识。队列满、序列化失败、关闭后提交和输出失败只记录计数，不阻塞或重试模型调用；运行计数可通过本地 `app.state.observation_output` 读取，不增加 HTTP 接口。

统一输出入口是 `app/model_call_logging.py` 的 `deliver_record(payload_json)`。当前执行 `print_record_json(payload_json)`；`write_record_to_es` 方法骨架和调用行均以注释预留，没有 ES 依赖、客户端或网络请求。正式接入 ES 时补齐实现，再注释控制台调用、启用 ES 调用，仍由同一个开关控制任务投递。

正常停止服务会等待已提交任务排空并关闭线程池。强制终止不能保证内存任务输出完成，同份快照已沿原流程保存在 SQLite；当前后台输出是尽力交付，不自动补发旧历史。

## 安装与启动

推荐 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 code8 自己的 .env，填写上游密钥及独立 SERVICE_TOKEN。
.\scripts\start.ps1
```

默认监听 `127.0.0.1:8100`，固定单 worker。如果 7.1 正占用该端口，先停止原服务，或使用 `.\scripts\start.ps1 -Port 8200`。不要对正在使用的数据库启动第二个实例。

本工程不复制私有 `.env`、真实密钥、原数据库或虚拟环境。默认数据库为 **code8 自己的** `data/model_history.sqlite3`。消费者的 Bearer token 必须与 code8 `SERVICE_TOKEN` 一致。若使用其他已有 Python 环境，可传 `-PythonPath <python.exe>`；运行时模块和配置仍来自 code8。

已接入旧 AG-UI 转发合同的消费者可使用相同路径。仍然直接调用模型的 Studio 不会自动经过本服务，本工程不会修改 Studio 配置或代码、拦截其直连请求。

## 无真实模型的控制台演示

```powershell
.\.venv\Scripts\python.exe -X utf8 -B scripts/demo_observation.py
```

演示使用真实 FastAPI 路由、OpenAI SDK 和本地 LangSmith RunTree，但 HTTP 被内存模拟传输拦截。无需 `.env`、真实模型、LangSmith 账号或常驻服务；临时数据库自动清理。输出的 identity.current_operation 为 generate_technical_plan，usage 为示例 284/45/329。

## 接口及现有请求合同

| 路径 | 鉴权与行为 |
| --- | --- |
| `GET /health` | 无需认证；不访问上游，不产生模型统计 |
| `POST /model-info/run` | Bearer 认证，AG-UI 模型公开信息，不产生模型统计 |
| `POST /model-inference/run` | Bearer 认证，AG-UI 单次推理与统一收尾统计 |

API 文档在 `/docs`。示例推理输入：

```json
{
  "threadId": "studio-thread-id",
  "runId": "unique-model-request-id",
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
        "sessionId": "optional-session",
        "requestId": "optional-request",
        "userId": null,
        "projectId": null,
        "extensions": {}
      }
    }
  }
}
```

输入中的原始模型消息在 `request.messages`。服务不补拼历史、不裁剪消息、不执行工具。实际模型固定取服务配置，禁止请求覆盖地址、密钥或认证头。SDK 未识别生成字段仍以 `extra_body` 传给上游。

流式请求统一补充 `stream_options.include_usage=true`，保留其他 stream_options；历史和追踪记录同样保存实际发送的开关。上游不支持此选项时正常报告错误，不静默重放模型调用。

输出保留 `model-inference.chunk/result/error` 和标准 AG-UI 文本/工具事件。Studio 原有消费方式不变。流建立前错误返回 HTTP 401/413/422/409。

`runId` 保留既有全局唯一防重合同：登记后禁止重复执行，包括失败、取消和重启中断。输出 `identity.thread_id/run_id` **原样取请求 envelope 的 threadId/runId**，不使用 metadata.sessionId/requestId 替换，也不在服务中重新解释业务身份。

## 上游配置

默认 `MODEL_UPSTREAM=direct`：配置 `DIRECT_BASE_URL`、`DIRECT_MODEL_NAME`、`DIRECT_API_KEY`。

`MODEL_UPSTREAM=gateway`：配置 `GATEWAY_TIER=fast/pro/ultra`，所选档位的 `GATEWAY_<TIER>_BASE_URL/MODEL_NAME`，以及共用 `GATEWAY_API_KEY/GATEWAY_KEY`。pro/ultra 的真实地址和模型未知时保持为空，不推测地址。只校验已启用配置，切换后重启；失败不自动换档。

gateway 的 `x-spaid` 按 `GATEWAY_SPAID_SOURCE=request/fixed` 读取 metadata.spaid 或固定配置；`x-session-id/x-request-id` 取已有元数据，缺省取 envelope 身份。地址和认证头不进入埋点。上游禁止跟随重定向，默认忽略系统代理。

当前上游协议继承 7.1 的 OpenAI 兼容 Chat Completions。`model.provider` 表示 direct/gateway 模式，不能理解成原生 Anthropic 协议支持。本工程没有移植最新 Studio 的其他模型协议或修改它的模型工厂。

## 观测数据

完整字段来源、时间定义、动作规则与限制见 [OBSERVABILITY.md](docs/OBSERVABILITY.md)。核心约定：

- 全部埋点字段可省略或为 null；标准输出保留已定义字段，模型记录省略 active_intervals。
- 除 span_kind/run_name 按模型服务合同固定外，data 优先读取本地 LangSmith RunTree 的字段与 metadata，缺失有效值时由本次真实观测补充；不读取远端追踪。
- input/output/total_tokens 优先读取 RunTree.metadata.usage_metadata；该数据由真实上游 usage 写入，不是 LangSmith 自行计算。未知为 null，真实 0 保留，不估算、不补算、不逐片相加，也不跨快照拼接字段。
- usage_source、usage_status 固定为 null。
- 请求模式与模型来自服务配置；关联字段仅取现有请求，不要求 Studio 补参数。
- identity.current_operation 优先归一化已有 metadata.extensions.current_operation，再匹配已核对的 Studio 提示词入口；覆盖主流程与权限判定、权限提取、UI 修复/续写、设计路由、文档同步等辅助调用。统一英文标识，未知/冲突为 unknown，不额外调用模型。
- 每次调用始终尝试本地创建模型 RunTree，无需 LangSmith 账号或 API key；不创建上传 Client，不调用 post/patch，也不向 LangSmith 上报。SDK 对象缺失时追踪 ID 为 null，固定的 model/model-inference 标签与已取得的真实模型用量仍保留。
- 首末输出计时发生在 SDK 接收边界；非流式的 ttft/output_duration 为 null。
- 开关开启时通过共享线程池输出一行 UTF-8 JSON；请求仅非阻塞投递冻结快照，输出故障及排队不改变模型统计。

埋点快照保存在同一条 SQLite 历史的 `analysis.execution_trace`，其中 identity.current_operation 和控制台保持一致；不增加表、不添加 HTTP 查询接口。原始模型历史仍可能包含完整提示词和回答，应限制本地数据目录访问权限。

本地追踪先完成状态、输出、用量和时间的收尾，再组装快照、保存 SQLite，并在保存步骤的 finally 中提交后台输出。开关关闭只跳过输出投递。`LANGSMITH_PROJECT` 只标记本地追踪项目；旧 `LANGSMITH_ENABLED`、`LANGSMITH_API_KEY`、`LANGSMITH_ENDPOINT` 即使保留在私有配置中，也不参与本地追踪开关或远端上传。健康信息 `langsmithEnabled=false` 表示远端上报关闭，`langsmithMode=local` 表示本地 SDK 追踪。

上游真实 Token 统计不等同于完整账单：响应丢失的 SDK 重试尝试可能无法取得用量。进程强杀无法保证输出最后日志；启动时仅将遗留 running 历史标记为 interrupted，不伪造其结束观测。

## 验证

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -v
Invoke-RestMethod http://127.0.0.1:8100/health
```

测试使用隔离临时数据库、真实 SDK 与 MockTransport，不访问真实模型、LangSmith 远端或 ES。覆盖继承的转发合同、三档网关、用量尾帧、未知/部分/零值/冲突统计、时间边界、取消、并发、SDK 重试及日志故障，并验证本地追踪优先读取、输出开关、慢输出不阻塞收尾、队列容量、冻结快照和正常排空关闭。工程职责见 [CODEBASE_INDEX.md](docs/CODEBASE_INDEX.md)。
