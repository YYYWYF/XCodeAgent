# 模型转发与模型调用观测服务

code8 是以 code7.1 为基础的独立 Python 工程。提供 direct/gateway 转发、官方 AG-UI/SSE、取消和观测输出，使用 LangSmith SDK 的本地追踪对象采集模型用量与时间。调用方传入完整上下文，每个通过校验的请求独立执行，包括相同 runId 的重复请求。业务上下文由调用方明确提供，Studio 在业务入口绑定操作名称后随模型请求传入。

每次被接纳的模型推理在成功、失败或取消收尾时生成统计快照；输出开关开启后，交给共享线程池异步输出到控制台或 ES。控制台输出以下嵌套 JSON；ES 写入时将同份快照的各级字段名转成小驼峰，再用下划线连接路径，例如 `identity.user_id` → `identity_userId`、`data.model.input_tokens` → `data_model_inputTokens`：

```text
{"identity":{"user_id":null,"project_id":null,"conversation_id":null,"thread_id":"thread-1","run_id":"run-1","operation_id":null,"workflow_scope":null},"data":{"trace_id":"019a267d-6000-7000-8000-000000000001","span_id":"019a267d-6000-7000-8000-000000000001","parent_span_id":null,"span_kind":"model","run_name":"model-inference","started_at":"2026-10-27 14:00:00","ended_at":"2026-10-27 14:00:01","duration_ms":1500,"status":"completed","model":{"provider":"direct","name":"configured-model","input_tokens":284,"output_tokens":45,"total_tokens":329,"usage_source":null,"usage_status":null,"ttft_ms":250,"output_duration_ms":750},"error":null},"context":{"business_operation":"generate_technical_plan"}}
```

ID、数字和时间为示例；运行时 ID 由本地 LangSmith SDK 创建，追踪对象创建失败时对应 ID 为 null。响应体用于统计输出，不加入或替换原有 AG-UI 返回结果。`context.business_operation` 仅使用调用方传入的字符串原值，不归一化或根据提示词补值；started_at/ended_at 使用北京时间（东八区 UTC+08:00），格式为 `yyyy-MM-dd HH:mm:ss`，例如 `2026-10-22 14:30:00`，不带微秒或时区后缀。耗时仍由单调时钟计算，保留毫秒精度，不受显示格式影响。

服务只统计模型调用：`span_kind` 固定为 `model`，`run_name` 固定为 `model-inference`。工具定义、工具指令和工具结果保持原有转发协议，不识别或额外打印工具观察记录。

可通过 `forwardedProps.modelInference.metadata.extensions.context` 提供业务上下文，输出到与 `identity`、`data` 同级的可选 `context`。`business_stage`、`business_operation`、`node_name`、`mode`、`target` 及 `target.type/id/api_contract_id` 全部可省略或为 null，允许只传部分字段；endpoint 也不强制要求接口契约 ID。未传或传 null 时省略这段上下文，类型或枚举格式无效时仅忽略本次 context，不影响推理和其他统计。`mode` 使用调用方明确传入的 ultra/pro/fast，不从网关配置推断。ES 继续平铺，例如 `context.business_stage` → `context_businessStage`、`context.target.api_contract_id` → `context_target_apiContractId`。

## 异步输出与统一开关

在 code8 的 `.env` 中设置：

```dotenv
MODEL_OBSERVATION_OUTPUT_ENABLED=true
```

默认开启，修改后重启服务生效。`false` 时不投递统计响应体输出任务；模型推理与本地 LangSmith 采集继续运行，服务运行日志仍按原设置输出。观测快照仅在内存中构建，关闭输出后不会保存统计记录。

应用使用单个工作线程的共享 `ThreadPoolExecutor`，最多接纳 256 个待处理任务（包含执行中的任务）。请求收尾先冻结完整 JSON，再非阻塞提交，主流程不等待控制台 `write/flush` 或 ES 网络请求。排队不会修改已记录的北京时间、耗时、Token 或英文操作标识。队列满、序列化失败、关闭后提交和输出失败只记录计数，不阻塞或重试模型调用；运行计数可通过本地 `app.state.observation_output` 读取，不增加 HTTP 接口。

统一输出入口是 `app/model_call_logging.py` 的 `deliver_record`。应用通过 `ModelCallWriter` 注入自己的官方 Elasticsearch 客户端和索引；`ES_URL` 留空时执行 `print_record_json`，填写后执行 `write_record_to_es`，不再打印同份 JSON。两种输出共用 `MODEL_OBSERVATION_OUTPUT_ENABLED`，没有额外 ES 开关。

当前集群版本为 ES 7.6.0，`requirements.txt` 使用 `elasticsearch>=7.17.9,<8`，保持客户端为同一主版本。更新代码后安装依赖，再填写本工程 `.env` 并重启服务，例如：

```dotenv
MODEL_OBSERVATION_OUTPUT_ENABLED=true
ES_URL=99.1.1.1:8020,99.1.1.2:8020
ES_INDEX=model-call-observations
ES_USERNAME=replace-with-es-username
ES_PASSWORD=replace-with-es-password
ES_REQUEST_TIMEOUT_SECONDS=5
```

`ES_URL` 支持单节点或逗号分隔的多个集群节点；可以只填 IP，例如 `99.1.1.1,99.1.1.2`，默认使用 HTTP 和 9200 端口。实际端口非 9200 时填写 `IP:端口`，例如 `99.1.1.1:8020`。有统一入口或负载均衡地址时，也可只填写该地址。HTTPS 节点须显式填写 `https://`，未指定端口时使用 443。先拆分节点，再逐个解析并生成官方客户端接收的节点配置列表，不将整个多节点字符串交给 `urlsplit`。兼容中文逗号，节点两侧的空白会去除、重复地址会去重，禁止空节点、URL 内嵌凭证、内部空白、查询参数和片段；每个地址均可带反向代理路径前缀。

使用用户名密码认证时填写 `ES_USERNAME` 和 `ES_PASSWORD`，两项必须同时配置。没有启用认证的集群可以将这两项全部留空。索引名采用小写字母或数字开头、其余字符为小写字母、数字、点、下划线或连字符，最多 255 个字符。

写入使用官方 7.x 客户端的 `client.index(index=..., id=..., body=es_document)`，由客户端处理序列化、HTTP 请求和路径编码。目标应为普通索引或可写索引别名；服务不主动创建索引或 mapping，索引不存在时是否自动创建由 ES 配置和账号权限决定。文档 ID 优先取 `data.span_id`，缺失时取 `identity.run_id`；点路径身份及超过 ES 512 字节上限的身份改用稳定 SHA-256 摘要，原始身份值保留在平铺后的正文中。`build_es_document` 在后台将冻结快照转换为平铺字典，例如 `data.started_at` → `data_startedAt`、`data.model.output_duration_ms` → `data_model_outputDurationMs`、`data.error.code` → `data_error_code`。无异常时错误子字段为 null，不生成 `data_error` 父字段。转换只调整结构和字段名，英文操作标识等字符串值、数值、null 和北京时间格式保持不变，不重新计算统计字段。

客户端只在首个后台 ES 输出任务中创建，同一应用复用连接池；关闭统一输出开关后不创建客户端。多个节点由官方连接池轮询选择并管理故障节点，仅使用配置的节点，不开启节点嗅探。认证使用 7.x 的 `http_auth`，HTTPS 使用默认信任库校验证书。请求不读取系统代理、不跟随重定向；`ES_REQUEST_TIMEOUT_SECONDS` 默认 5 秒且必须为正有限数，控制单次请求的等待时间。连接错误、超时及 HTTP 429/502/503/504 由客户端最多重试 2 次，重试可能切换节点，因此整条输出的耗时可能超过此值。所有尝试复用相同的文档 ID 和冻结 JSON，不重新调用模型；最终失败、无效响应和失败分片计入输出失败，组件不回显异常原文，也不回退控制台。

正常停止服务会等待已提交任务排空、关闭线程池，再关闭 ES 连接池。队列满、输出失败或强制终止可能丢失观测记录；后台输出是尽力交付，没有本地持久化副本或自动补发。

## 安装与启动

推荐 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 code8 自己的 .env，填写上游密钥及独立 SERVICE_TOKEN。
.\scripts\start.ps1
```

默认监听 `127.0.0.1:8100`，启动脚本使用单 worker。如果 7.1 正占用该端口，先停止原服务，或使用 `.\scripts\start.ps1 -Port 8200`。

本工程不复制私有 `.env`、真实密钥或虚拟环境。服务不创建或读写本地历史数据库，已有数据库文件可保留归档。旧 `HISTORY_DB_PATH` 与 `MOCK_ANALYTICS_ENABLED` 配置不再生效。消费者的 Bearer token 必须与 code8 `SERVICE_TOKEN` 一致。若使用其他已有 Python 环境，可传 `-PythonPath <python.exe>`；运行时模块和配置仍来自 code8。

已接入旧 AG-UI 转发合同的消费者可使用相同路径。仍然直接调用模型的 Studio 不会自动经过本服务，本工程不会修改 Studio 配置或代码、拦截其直连请求。

## 无真实模型的控制台演示

```powershell
.\.venv\Scripts\python.exe -X utf8 -B scripts/demo_observation.py
```

演示使用真实 FastAPI 路由、OpenAI SDK 和本地 LangSmith RunTree，但 HTTP 被内存模拟传输拦截。无需 `.env`、真实模型、LangSmith 账号或常驻服务，不创建本地数据库。调用方显式传入的 context.business_operation 为 generate_technical_plan，usage 为示例 284/45/329。

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

流式请求统一补充 `stream_options.include_usage=true`，保留其他 stream_options；本地追踪对象使用实际发送的开关。上游不支持此选项时正常报告错误，不静默重放模型调用。

输出保留 `model-inference.chunk/result/error` 和标准 AG-UI 文本/工具事件。Studio 原有消费方式不变。流建立前错误返回 HTTP 401/413/422。

服务不执行请求防重，相同 `runId` 的顺序或并发请求均独立调用上游、构建各自的观测；每次调用的追踪身份由本地 SDK 独立生成。调用方负责重复提交和重试策略。输出 `identity.thread_id/run_id` **原样取请求 envelope 的 threadId/runId**，不使用 metadata.sessionId/requestId 替换，也不在服务中重新解释业务身份。

## 上游配置

默认 `MODEL_UPSTREAM=direct`：配置 `DIRECT_BASE_URL`、`DIRECT_MODEL_NAME`、`DIRECT_API_KEY`。

`MODEL_UPSTREAM=gateway`：配置 `GATEWAY_TIER=fast/pro/ultra`，所选档位的 `GATEWAY_<TIER>_BASE_URL/MODEL_NAME`，以及共用 `GATEWAY_API_KEY/GATEWAY_KEY`。pro/ultra 的真实地址和模型未知时保持为空，不推测地址。只校验已启用配置，切换后重启；失败不自动换档。

gateway 的 `x-spaid` 按 `GATEWAY_SPAID_SOURCE=request/fixed` 读取 metadata.spaid 或固定配置；`x-session-id/x-request-id` 取已有元数据，缺省取 envelope 身份。地址和认证头不进入埋点。上游禁止跟随重定向，默认忽略系统代理。

当前上游协议继承 7.1 的 OpenAI 兼容 Chat Completions。`model.provider` 表示 direct/gateway 模式，不能理解成原生 Anthropic 协议支持。本工程没有移植最新 Studio 的其他模型协议或修改它的模型工厂。

## 观测数据

完整字段来源、时间定义、业务上下文约定与限制见 [OBSERVABILITY.md](docs/OBSERVABILITY.md)。核心约定：

- 全部埋点字段可省略或为 null；标准输出保留已定义字段，模型记录省略 active_intervals。
- 除 span_kind/run_name 按模型服务合同固定外，data 优先读取本地 LangSmith RunTree 的字段与 metadata，缺失有效值时由本次真实观测补充；不读取远端追踪。
- input/output/total_tokens 优先读取 RunTree.metadata.usage_metadata；该数据由真实上游 usage 写入，不是 LangSmith 自行计算。未知为 null，真实 0 保留，不估算、不补算、不逐片相加，也不跨快照拼接字段。
- usage_source、usage_status 固定为 null。
- 请求模式与模型来自服务配置；关联字段仅取现有请求，不要求 Studio 补参数。
- context.business_operation 只读取 metadata.extensions.context.business_operation，字符串原样保留；缺失时不输出该字段，显式 null 保留 null，不根据提示词、节点名或其他字段推断，也不补 unknown。
- 每次调用始终尝试本地创建模型 RunTree，无需 LangSmith 账号或 API key；不创建上传 Client，不调用 post/patch，也不向 LangSmith 上报。SDK 对象缺失时追踪 ID 为 null，固定的 model/model-inference 标签与已取得的真实模型用量仍保留。
- 首末输出计时发生在 SDK 接收边界；非流式的 ttft/output_duration 为 null。
- 开关开启时通过共享线程池输出冻结快照：控制台使用嵌套 JSON，ES 使用小驼峰字段名和下划线路径的平铺 JSON；请求仅非阻塞投递，输出故障及排队不改变模型统计。

埋点快照通过共享输出组件提交到控制台或 ES。完整提示词和回答仅用于本次转发和内存中的本地追踪，不写入本地历史，也不加入统计摘要。服务不提供历史查询接口。

本地追踪先完成状态、输出、用量和时间的收尾，再组装快照并提交后台输出。开关关闭只跳过输出投递。`LANGSMITH_PROJECT` 只标记本地追踪项目；旧 `LANGSMITH_ENABLED`、`LANGSMITH_API_KEY`、`LANGSMITH_ENDPOINT` 即使保留在私有配置中，也不参与本地追踪开关或远端上传。健康信息 `langsmithEnabled=false` 表示远端上报关闭，`langsmithMode=local` 表示本地 SDK 追踪。

上游真实 Token 统计不等同于完整账单：响应丢失的 SDK 重试尝试可能无法取得用量。进程强杀无法保证输出最后日志；重启后无法恢复未完成调用或部分输出，不伪造其结束观测。

## 验证

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s tests -v
Invoke-RestMethod http://127.0.0.1:8100/health
```

测试使用隔离配置、真实 SDK 与 MockTransport，不访问真实模型、LangSmith 远端或 ES。覆盖继承的转发合同、三档网关、用量尾帧、未知/部分/零值/冲突统计、时间边界、取消、并发、SDK 重试及日志故障，并验证相同 runId 的顺序和并发请求独立执行、本地追踪优先读取、输出开关、慢输出不阻塞收尾、队列容量、冻结快照和正常排空关闭。工程职责见 [CODEBASE_INDEX.md](docs/CODEBASE_INDEX.md)。
