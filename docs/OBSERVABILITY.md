# 模型埋点采集和统计输出

## 范围

一个接纳后的 `/model-inference/run` 对应一次模型调用观测。成功、失败、取消收尾时构建一份快照，输出开关开启后提交一次后台任务，不逐分片打印。SDK 内部网络重试仍为一组；相同 runId 的顺序或并发请求均独立执行，各自创建追踪对象和模型观测。健康、模型信息、鉴权/参数/大小拒绝不打印模型统计。

本服务只记录模型调用，span_kind 固定为 model，run_name 固定为 model-inference。请求和响应中的工具相关数据沿用原协议透传，不额外识别、记录或打印工具调用。

每次埋点只输出一份完整快照（record_id/type/occurred_at、identity/data，以及调用方提供的可选 context），业务操作位于 context.business_operation；不再打印独立动作行。record_id 在本次模型观测初始化时使用 uuid4().hex 生成一次，type 固定为 execution_trace，occurred_at 在终态快照首次形成时按北京时间（UTC+08:00）冻结；后台排队和 ES 写入重试复用这三个值。未配置 ES 时，实际 write/flush 在后台工作线程中执行，JSON 内的换行会转义，避免并发日志交错或字段内容破坏行边界；配置 ES 后，后台线程将同份快照转换为平铺字段写入索引。输出失败由组件记录失败计数，不保存本地持久化副本。

服务不修改 Studio 的请求合同、业务流程、提示词或 AG-UI 返回事件。采集仅作用于实际经过服务的推理请求。

每次推理始终尝试使用 LangSmith SDK 在本地创建 RunTree。服务不初始化上传 Client，不调用 RunTree.post/patch，不向 LangSmith 上报，也不查询远端追踪；无需 LangSmith 账号或 API key。旧 LANGSMITH_ENABLED/API_KEY/ENDPOINT 配置不控制当前本地采集、不启用上传，LANGSMITH_PROJECT 仅作为本地项目标记。健康信息中 langsmithEnabled=false 表示远端上报关闭，langsmithMode=local 表示本地追踪。

## 异步输出开关与生命周期

`MODEL_OBSERVATION_OUTPUT_ENABLED=true` 为默认值，在启动时读取，修改后重启服务生效。`false` 只停止统计响应体投递，不关闭模型推理、内存中的观测采集或普通运行日志。关闭输出后不会保存统计记录。控制台和 ES 共用这个开关，不设置第二个目标开关。

每个应用持有一个 `ObservationOutput`，通过 FastAPI lifespan 管理共享单工作线程的 `ThreadPoolExecutor`；`app.state.observation_output` 可用于本地运行状态检查。待处理容量为 256，包含正在执行和等待执行的任务，完成后释放容量，不按请求创建线程池，也不新增查询接口。

请求协程先完成本地 RunTree、构建 `build_observation` 快照，再提交输出。提交前将快照序列化为独立、不可变的 JSON 字符串，线程任务不持有可变 RunTree、模型响应流或原始上下文，也不重新获取时间、计算耗时或识别当前操作。主流程只进行非阻塞投递，不等待输出完成。

统一输出方法 `deliver_record` 已接入 `write_record_to_es`。`ES_URL` 留空时调用 `print_record_json`；填写地址后，由每个应用独立持有的 `ModelCallWriter` 提供官方 Elasticsearch 客户端和索引，改为写入 ES，不重复打印也不在 ES 失败时回退控制台。ES 集群为 7.6.0，客户端依赖使用 `elasticsearch>=7.17.9,<8`，采用 7.x 的 `http_auth` 和 `index(..., body=...)` 接口。

ES 配置均在本工程环境文件读取，修改后重启生效：

| 配置 | 含义 |
| --- | --- |
| `ES_URL` | 单节点或逗号分隔的多个集群节点；只填 IP 时默认 HTTP、9200 端口，非默认端口使用 `IP:端口`。HTTPS 须显式指定，未填端口时使用 443。可带代理路径前缀，留空使用控制台；兼容中文逗号，去除节点两侧空白并去重，禁止空节点、内嵌凭证、内部空白、查询参数和片段 |
| `ES_INDEX` | 普通索引或可写索引别名，默认 `model-call-observations`；使用小写字母/数字开头，其余为小写字母、数字、点、下划线或连字符，最多 255 字符 |
| `ES_USERNAME` / `ES_PASSWORD` | Basic 认证，两项必须同时配置；无认证时两项均留空 |
| `ES_REQUEST_TIMEOUT_SECONDS` | 官方客户端单次请求的等待超时参数，默认 5 秒，必须为正有限数；重试可能使整条输出的耗时超过此值 |

配置校验先拆分多节点，再通过 urllib3 的单地址解析器逐个处理，最终直接将节点配置字典列表交给官方客户端。ES 地址不再经过共享的 `urlsplit` 校验；非法地址的错误会指出 `ES_URL` 和具体节点序号。模型上游地址仍单独校验，错误会标明 `DIRECT_BASE_URL` 或所选网关地址字段。

首个后台 ES 输出任务才创建官方客户端，同一应用复用连接池；应用构造、健康/模型信息查询和禁用输出均不连接 ES。客户端负责节点轮询、故障节点管理与连接复用；仅使用配置的节点，禁用启动和失败后的节点嗅探。只填一个节点或统一负载均衡入口也可使用。HTTPS 使用默认信任库校验证书。默认 urllib3 连接不读取系统代理、不跟随重定向。正常停机先排空输出，再关闭客户端及其全部节点连接。

写入前由 `build_es_document` 将冻结快照的各级字段名从 snake_case 转为小驼峰，再用下划线连接路径。转换后的字典通过 `client.index(index=..., id=..., body=es_document)` 写入，由官方客户端序列化并生成 `PUT /<index>/_doc/<id>` 请求。优先以转换前的 `record_id` 作为文档 ID，未提供时依次取 `data.span_id`、`identity.run_id`；特殊路径字符由客户端编码，点路径身份及超过 [512 字节上限](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/mapping-id-field) 的身份使用稳定 SHA-256 摘要，原始身份值保留在平铺正文中。重复写入同一 record_id 覆盖同一文档，不重新计算统计字段；不同模型调用分别生成 record_id，即使外层 runId 相同，也各自保留一条事实记录。服务不主动创建索引或 mapping，自动建索引取决于集群配置和账号权限，不支持将目标设为 data stream。

ES mapping 字段示例：

| 快照字段路径 | ES 字段名 |
| --- | --- |
| record_id | recordId |
| type | type |
| occurred_at | occurredAt |
| identity.user_id | identity_userId |
| context.business_operation | context_businessOperation |
| context.business_stage | context_businessStage |
| context.node_name | context_nodeName |
| context.mode | context_mode |
| context.target.type / id | context_target_type / context_target_id |
| context.target.api_contract_id | context_target_apiContractId |
| data.parent_span_id | data_parentSpanId |
| data.started_at / data.ended_at | data_startedAt / data_endedAt |
| data.model.input_tokens | data_model_inputTokens |
| data.model.output_duration_ms | data_model_outputDurationMs |
| data.error.layer / code / message | data_error_layer / data_error_code / data_error_message |

无异常时仍输出三个错误子字段，值均为 null，不生成 `data_error` 父字段。业务 context 字段沿用通用平铺规则：缺失字段不补齐，显式 null 保留在对应路径，例如 `context.target=null` 输出为 `context_target=null`，不补出目标子字段。标量 null、数值、字符串值及北京时间格式均保持原样，例如 `generate_technical_plan` 不改成小驼峰。控制台继续使用原始嵌套结构；下文字段来源表使用快照中的字段路径。

连接错误、超时以及 HTTP 429/502/503/504 由官方客户端最多重试 2 次；客户端可选择其他可用节点，所有尝试使用相同的文档 ID 和冻结正文。鉴权或参数等其他 HTTP 错误不会按该状态列表重试。客户端最终抛错、非预期写入响应或 `_shards.failed` 非零均作为一次输出失败；组件只记录计数，不回显 ES 异常正文、认证或完整文档，不重新调用模型或补发旧记录。

队列满、序列化失败、关闭后提交、线程输出失败分别记录运行计数，不能阻塞请求或重新调用模型；这些计数不是响应体字段。正常关闭时停止接收任务，在请求处理协程之外等待已提交任务排空，再关闭输出线程池。队列满、输出失败或强制终止进程可能丢失观测记录，当前没有本地持久化副本，不承诺可靠投递或自动补发。

## 字段来源

| 字段 | 来源或未知规则 |
| --- | --- |
| record_id | 每次模型观测初始化时通过 uuid4().hex 生成的 32 位唯一 ID；同一事实的重复快照和 ES 写入重试保持不变 |
| type | 固定为 execution_trace |
| occurred_at | 本地追踪收尾后、首次构建终态快照时读取的北京时间（UTC+08:00），格式 yyyy-MM-dd HH:mm:ss；后台上传时间不参与生成，重复快照沿用冻结值 |
| identity.thread_id | 原样 envelope.thread_id，不使用 metadata.sessionId 或 Graph 推断 |
| identity.run_id | 原样 envelope.run_id，不用 SDK/Span 身份替换 |
| identity.user_id/project_id | 既有 metadata.userId/projectId，没有则 null |
| identity.conversation_id/operation_id/workflow_scope | 既有 metadata.extensions 中同名 snake_case 键，没有则 null；不搜索别名或猜测 |
| context.business_operation | 只读取 metadata.extensions.context.business_operation，字符串原样保留；缺失则省略，显式 null 保留 null |
| context.business_stage/node_name | 原样读取 metadata.extensions.context 中同名字段，不根据操作标识或固定模型 Span 名推断 |
| context.mode | 读取 metadata.extensions.context.mode，支持 ultra/pro/fast，不使用 GATEWAY_TIER 兜底 |
| context.target.type/id/api_contract_id | 读取 metadata.extensions.context.target 中同名字段，均可独立省略或为 null，不按目标类型要求其他字段齐全 |
| data.trace_id/span_id/parent_span_id | 真实 RunTree.trace_id/id/parent_run_id；本地 SDK 对象创建失败时 null，不生成替代 ID |
| data.span_kind | 固定为 model，表示本服务实际执行的模型调用 |
| data.run_name | 固定为 model-inference，不随提示词职责、工具指令或追踪对象名称变化 |
| data.started_at/ended_at | 优先读取 RunTree.start_time/end_time 的实际请求边界，缺失时取本次观测；统一按北京时间输出，格式为 yyyy-MM-dd HH:mm:ss |
| data.duration_ms | 优先读取 RunTree.metadata.duration_ms 中的单调时钟实测值，缺失时使用本次观测 |
| data.status | 优先读取 RunTree.metadata.status，缺失时取本次执行结果，通常为 completed/failed/cancelled |
| data.active_intervals | 模型 Span 不输出；合同保留可选类型供后续用途 |
| data.model.provider/name | 优先读取 RunTree.metadata.ls_provider/ls_model_name；缺失时使用本次实际配置/请求，不采用上游响应别名 |
| data.model.input_tokens/output_tokens/total_tokens | 优先读取 RunTree.metadata.usage_metadata；由真实上游 usage.prompt_tokens/completion_tokens/total_tokens 映射写入，不由 LangSmith 估算 |
| data.model.usage_source/usage_status | 显式 null，不计算枚举值 |
| data.model.ttft_ms/output_duration_ms | 优先读取 RunTree.metadata 中对应实测值，缺失时由本次观测补充；非流式均 null，时间规则见下文 |
| data.error | 优先读取追踪对象的结构化 execution_error 等错误事实；无技术异常或主动取消为 null；异常统一转换为固定安全摘要，不打印 RunTree.error 中的原始敏感正文 |

字段模型全部可省略/可空。标准日志为了对比保留已定义字段，未知不填空字符串或虚构零值。模型用量字段只接受非负整数，拒绝布尔、浮点、负数和字符串。

业务 context 从 `forwardedProps.modelInference.metadata.extensions.context` 读取，独立于 AG-UI 请求最外层的 `context` 数组。context 及其所有内部字段均非必填，也允许显式 null；target.type 支持 application/page/endpoint/entity，但不要求同时提供 id 或 api_contract_id，endpoint 缺少 api_contract_id 也可接收。未提供 context 或显式为 null 时不输出该段；部分对象只输出明确提供的字段。business_operation 与其他业务字段一样仅来自调用方，不转换大小写、不翻译、不读取提示词补值，也不补 unknown。格式无效时记录固定诊断并忽略本次 context，推理与其他观测字段继续处理，不回显无效原文。

例如只传节点名和目标 ID 也可接收：

```json
{
  "metadata": {
    "extensions": {
      "context": {
        "node_name": "generate_page",
        "target": {"id": "page-001"}
      }
    }
  }
}
```

除 span_kind/run_name 按当前模型服务合同固定外，data 优先读取本地追踪对象中的有效字段；对象或字段缺失时，仅使用当前调用已经取得的真实事实兜底，未知保持 null。追踪对象没有 SDK 原生字段的状态、实测单调时钟耗时、TTFT、输出区间及结构化错误，由服务写入 RunTree.metadata 后统一读取。usage_source/status 仍固定为 null，不受其他追踪元数据覆盖。identity 继续原样来自请求，不用追踪 ID 替换。

LangSmith 身份由 SDK 本地创建对象时生成，不需要网络。当前请求不继承外部追踪上下文，模型追踪是独立根，trace_id=span_id、parent_span_id=null。此身份不等同于 Studio 的请求 runId；不能仅根据同 threadId/runId 伪造调用树。模型响应出现 tool_calls 仍为模型 Span；Studio 真正执行的工具或 RAG 没有经过本服务，不会补建它们的追踪记录。

## Token 统计

流式请求补充 include_usage=true，继续读取内容结束后可能出现的 choices=[] 用量尾帧。最新包含有效 Token 的 usage 对象整体替换旧快照，并将对应 input_tokens/output_tokens/total_tokens 写入本地 RunTree.metadata.usage_metadata；不叠加累计值、不跨快照补字段。打印优先选择追踪对象中的有效用量快照，不能用其他旧快照补齐该快照缺失的字段。后续不含用量的分片不会清空统计。

上游真实 0 保留。缺失/非法字段 null；不由字符数估算，也不根据输入加输出补算缺失总数。三项有效但总数冲突时摘要 total_tokens=null 并产生无原文的诊断；返回客户端的原始输出不改写。

## 时间边界

根字段 occurred_at 表示终态记录形成时刻，按北京时间（固定 UTC+08:00）输出，格式为 `yyyy-MM-dd HH:mm:ss`。它与模型执行结束时间 data.ended_at 分别采集，在首次快照形成后固定；不在工作线程中生成，也不由 ES 上传时刻替代。ES 平铺字段名为 occurredAt。此变更仅作用于后续新记录，不会自动补齐已有 ES 文档的 recordId/type/occurredAt，也不会改写之前按 UTC 输出的 occurredAt；使用 type=execution_trace 过滤时，缺少 type 的历史文档不会命中。

历史数据可使用 `scripts/backfill_trace_fields.py` 单独补齐和校正时间。默认读取 `.env` 中 ES_URL/ES_INDEX/ES_USERNAME/ES_PASSWORD，也可通过 `--es-url`、`--index` 覆盖；无需启动模型服务。直接运行只读扫描预览，添加 `--apply` 后写入，`--project-id` 可限定应用，`--limit` 限制本次扫描记录数（包含无需修改的记录）。脚本扫描 data_spanKind 为 workflow/node/model/tool 的文档，读取三个根字段和 data_endedAt，并通过 docvalue_fields 获取两个日期字段按 mapping 解析后的 epoch 毫秒值，在 Python 中比较先后，避免遗漏字段齐全但时间错误的记录。recordId/type 仅在缺失、null 或空字符串时补充，分别使用 uuid4().hex 和 execution_trace；data_endedAt 有值时，缺失的 occurredAt 取其原值，已有 occurredAt 仅在小于 data_endedAt 时更新为其原值，相等或更晚均保留，不转换时区，也不使用当前或上传时间。缺少结束时间时，仅补可生成的 recordId/type 并计数，保留原 occurredAt。历史 data_endedAt 若为北京时间文本，校正的 occurredAt 与新记录保持相同显示口径。无需修改的记录不会发送更新；写入时以原 `_id` 进行 `doc` 局部批量更新，只提交需要修改的字段，不调用 Painless 或其他 ES 脚本，无需开放脚本权限。扫描时请求 `seq_no_primary_term=true`，写入时携带 `if_seq_no`/`if_primary_term`，防止覆盖扫描后已被其他请求修改的文档；版本信息缺失时停止，409 版本冲突时跳过并计入失败及 version_conflicts，不使用旧值重试冲突。可重新执行以读取最新数据继续处理；普通网络或限流重试复用同一局部文档和 UUID。预览输出待更新数量、已有时间早于结束时间的数量和缺失结束时间数量；正式执行单次扫描并在完成后刷新索引，按实际 ES 响应报告更新、无需修改、失败和跳过数量，支持重复执行。

刷库分页仍使用官方客户端的 Scroll API，直接调用 search/scroll，续页的 `scroll_id` 按字符串发送；每次使用最新返回的游标，超时 5 分钟。清理游标时显式发送 `{"scroll_id": ["..."]}` 数组格式，避免 scan helper 在部分旧 ES 环境中发送字符串造成 400。正常结束、达到扫描上限或异常中断都会尝试清理本次游标；清理请求失败仅单独打印异常类型和状态码，由服务端按超时释放，不遮盖原始读取或写入异常。查询超时或任意分片失败时停止处理，不使用该页不完整结果。

查询和更新统一使用配置的索引或别名：`--index` 优先于 `ES_INDEX`，未配置时沿用默认索引。更新动作的 `_index` 固定为该配置值，不取搜索结果中附带日期的 `_index`，也不通过字符串截取猜测索引名；搜索返回的名称保留为诊断日志的 `search_index`，日志 `_index` 表示实际提交的更新目标。`_id` 仍为原文档 ID，同时保留返回的 `_type`。`_routing` 优先读取命中顶层，否则读取 `fields._routing` 的单值；不从业务字段推断或重算路由。业务 `type=execution_trace` 和 `recordId` 只是待补充字段，不用于替换文档的 `_type` 或 `_id`。预览样本和正式执行的前三条更新目标包含这些定位信息；失败输出包含 ES 返回的 `_index`、`_type`、`_id`、状态码、异常类型和 reason，并在统计中单列 document_missing 数量，便于排查 `document_missing_exception`。未找到文档时保留失败结果，不使用 upsert 创建只有补充字段的新文档。

控制台和 ES 的 started_at/ended_at 统一显示北京时间（固定 UTC+08:00），格式为 `yyyy-MM-dd HH:mm:ss`。例如 `2026-10-27T01:20:01.602528+00:00` 输出为 `2026-10-27 09:20:01`；日期和时间之间为空格，不带微秒或时区后缀。显示截取到秒，不修改内部 UTC 采集、LangSmith 对象或上游原始时间字段，耗时指标仍保留毫秒精度。未知时间仍为 null。线程池只输出提交前已经冻结的快照，排队及 write/flush 时间不计入模型时间字段。

SDK 请求开始前记录单调时钟和 UTC 起点，并同步到本地 RunTree，不包括鉴权、本地追踪对象创建等准备时间；包含本次 SDK 网络重试和退避。开始时间不采用 RunTree 提前创建时的默认时间来扩大模型执行区间。

每次 SDK 返回分片，先采集接收时间再交给聚合/输出：文本、推理、拒绝、工具名称或参数的非空字符串算生成输出。空格换行有效；角色、工具 ID、结束原因和纯用量无效。多候选时按整次请求中任一候选的首末有效输出观测。

ttft_ms=首个有效输出时间-请求开始时间。output_duration_ms=最后一个有效输出时间-首个有效输出时间。没有有效输出时两者 null；只有一个有效片段或没有正的可观测间隔时 output_duration_ms=null。有效正间隔不足一毫秒时整数值可能为 0，并非缺失值。

上游结束或关闭时冻结结束时刻并写入 RunTree；非流式在完整响应到达后、交付客户端前冻结。模型结果交付和本地追踪收尾不延长该时间。流式度量是服务本地接收过程，也可能受到网络、下游背压及流内本地处理影响，不是模型服务器纯计算时间。

## 业务操作字段

`context.business_operation` 仅由调用方通过 `forwardedProps.modelInference.metadata.extensions.context.business_operation` 传入。模型服务只校验其类型并记录原值，不读取提示词、节点名或其他字段推断操作，也不进行英文转换、大小写转换或默认值填充。

- 调用方传入 `B`，记录及 ES 字段值均为 `B`；中文操作名称同样原样保留。
- 未提供该字段时，快照和 ES 文档不补出该字段。
- 显式传入 null 时保留 null；未提供整个 context 或传入 null 时省略 context。
- XcodeAgent 的业务入口负责定义操作标识，沿现有逐调用上下文传递；`operation_id` 继续表示一次业务操作的身份，不用作操作名称。

ES 仍按通用规则将字段名平铺为 `context_businessOperation`；OpsQuery 的 `businessOperation` 按实际存储值精确筛选。

## 保存与故障

调用结束时先在内存中完成 RunTree 的输出、状态、错误和时间收尾，再构建 data 快照并按开关提交输出。输出线程使用同份快照的冻结 JSON，输出失败不更改模型状态、结果或用量。配置 ES 地址后向该集群写入统计摘要，不创建本地历史数据库或查询 API。原始提示词仅用于当前请求和本地追踪内存，不输出到统计摘要，也不上传 LangSmith。

埋点采集、构造、打印异常都与推理隔离。进程强杀或机器断电时不能保证执行 finally，最后日志和部分输出可能丢失；重启后不恢复未完成调用，不伪造未知结束时间、Span 或 Token。服务不保留请求防重状态，启动脚本仍默认单 worker。
