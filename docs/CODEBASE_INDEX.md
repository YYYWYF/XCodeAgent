# code8 代码索引

| 文件 | 职责 |
| --- | --- |
| `app/main.py` | FastAPI factory、鉴权、大小限制、官方 AG-UI 路由；lifespan 管理共享输出组件，正常退出排空后关闭 ES 连接；健康和模型信息不产生推理埋点 |
| `app/contracts.py` | 现有 request/metadata 输入边界，禁止连接覆盖，检查 stream_options 类型 |
| `app/config.py` | code8 独立环境文件、direct/gateway 三档、超时重试、本地追踪项目名、ES 单节点/多节点地址解析及索引/用户名密码/超时和统一 MODEL_OBSERVATION_OUTPUT_ENABLED 开关；旧远端追踪参数不启用上传 |
| `app/upstream.py` | request_body 统一实际请求；官方 OpenAI SDK；采集真实请求及响应时间；include_usage 开关 |
| `app/aggregation.py` | 原始响应、文本及工具参数聚合，不承担日志展示 |
| `app/protocol.py` | 原 AG-UI 生命周期、标准文本/工具与 custom 模型事件 |
| `app/inference.py` | 每个请求独立转发、推理生命周期、取消；本地追踪收尾后构建快照并非阻塞提交后台输出 |
| `app/observation_models.py` | 全字段可选的 record_id/type/occurred_at 及 identity/context/data 合同；业务上下文及开发目标全部可省略或为 null，无跨字段必填约束；context.business_operation 为仅取调用方原值的可选字符串，usage_source/status 只能为 null |
| `app/model_observation.py` | 每次调用以 uuid4().hex 固定 record_id，终态快照冻结北京时间 occurred_at，type 固定 execution_trace；本次真实 usage/SDK 边界时间写入本地 RunTree；data 优先读取追踪字段，缺失值本地观测兜底；从 metadata.extensions.context 原样读取可选业务上下文，业务操作不推断、不归一化或补默认值，格式故障隔离；固定安全错误摘要 |
| `app/trace_projection.py` | 读取本地追踪身份、状态、用量及耗时；实际边界时间仅在输出时转换为北京时间 +08:00，不访问 SDK 客户端 |
| `app/observation_output.py` | 服务级单工作线程池、256 个待处理容量、统一开关、冻结 JSON 非阻塞投递、计数及排空关闭 |
| `app/model_call_logging.py` | deliver_record 统一输出入口；未配置 ES 时打印单行 JSON；build_es_document 将快照转换为各级小驼峰字段名、下划线连接路径的平铺字典；ModelCallWriter 复用官方 7.x Elasticsearch 客户端并关闭连接池，节点选择及重试由客户端处理，write_record_to_es 优先以 record_id 作为稳定文档 ID 写入平铺快照 |
| `app/tracing.py` | 始终尝试创建本地 LangSmith RunTree；只修改内存对象，不创建 Client、不调用 post/patch、不上报 |
| `scripts/start.ps1` | UTF-8、固定单 worker；默认本机 8100 |
| `scripts/demo_observation.py` | 无配置和外网的完整模型日志演示 |
| `scripts/backfill_trace_fields.py` | 历史 ES Trace 身份补齐和时间校正；只读取 ES 配置，默认只读扫描预览；通过官方客户端显式 Scroll 分页，以数组格式清理游标，清理失败单独报告；查询和更新统一使用配置索引（--index 优先于 ES_INDEX），返回的日期索引名仅用于日志；--apply 后单次扫描，在 Python 中按 ES 日期毫秒值比较，保留命中的 _type/_id/_routing，以 doc 局部更新补缺失 recordId/type，仅在 occurredAt 缺失或早于 data_endedAt 时更新时间；无需 ES 脚本，使用序列号和主分片任期保护并发修改，输出更新目标及失败原因，单列版本冲突和文档缺失数量，支持重复执行 |
| `tests/test_model_observation.py` | 可选合同、确定性时钟、Token 语义及多线程单行日志原子性 |
| `tests/test_observation_service.py` | SDK/HTTP/AG-UI 集成、取消并发、重试、相同 runId 独立执行与观测、故障隔离 |
| `tests/test_local_trace.py` | 真实 RunTree 的字段优先级、父子身份、默认本地追踪与零上传、收尾先于打印 |
| `tests/test_observation_output.py` | 异步投递、冻结快照、容量限制、输出故障、统一开关及慢打印不阻塞模型收尾 |
| `tests/test_output_submit_failure.py` | 线程创建/任务提交失败后的撤销与单次结算、故障池回收、旧任务排空及后续输出恢复 |
| `tests/test_service.py`、`test_gateway_tiers.py`、`test_config_safety.py` | 继承的转发、网关三档、完整上下文透传、重复请求独立执行与配置安全回归 |

全部产品接口保持官方 AG-UI；日志不是新响应协议。不在本服务补建 Studio 会话/轮次、不执行 Studio 工具、不根据历史猜测当前请求身份。当前仅实现 OpenAI 兼容上游协议。

模型统计固定使用 span_kind=model、run_name=model-inference；不创建或打印工具观察记录，工具相关请求与响应只沿用原转发协议。

本地 LangSmith 追踪不需要账号、API key 或联网。健康信息 `langsmithEnabled=false` 表示远端上报关闭，`langsmithMode=local` 表示使用本地 SDK 追踪对象。
