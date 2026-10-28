# code8 代码索引

| 文件 | 职责 |
| --- | --- |
| `app/main.py` | FastAPI factory、鉴权、大小限制、官方 AG-UI 路由；lifespan 管理共享输出组件，正常退出排空；健康和模型信息不产生推理埋点 |
| `app/contracts.py` | 现有 request/metadata 输入边界，禁止连接覆盖，检查 stream_options 类型 |
| `app/config.py` | code8 独立环境文件、direct/gateway 三档、超时重试、本地追踪项目名和统一 MODEL_OBSERVATION_OUTPUT_ENABLED 开关；旧远端追踪参数不启用上传 |
| `app/upstream.py` | request_body 统一实际请求；官方 OpenAI SDK；采集真实请求及响应时间；include_usage 开关 |
| `app/aggregation.py` | 原始响应、文本及工具参数聚合，不承担日志展示 |
| `app/protocol.py` | 原 AG-UI 生命周期、标准文本/工具与 custom 模型事件 |
| `app/inference.py` | 防重、推理生命周期、取消；本地追踪收尾后构建快照、保存历史，并在 finally 中非阻塞提交后台输出 |
| `app/observation_models.py` | 全字段可选的 identity/data 合同；identity.current_operation 为可选英文标识，usage_source/status 只能为 null |
| `app/model_observation.py` | 本次真实 usage/SDK 边界时间写入本地 RunTree；data 优先读取追踪字段，缺失值本地观测兜底；固定安全错误摘要 |
| `app/trace_projection.py` | 读取本地追踪身份、状态、用量及耗时；实际边界时间仅在输出时转换为北京时间 +08:00，不访问 SDK 客户端 |
| `app/operation_catalog.py` | 当前 Studio 主流程、辅助请求及 Agent 的已核对入口与中文说明到英文操作标识的映射 |
| `app/operation_detection.py` | 英文 current_operation 归一化；明确元数据优先，匹配当前平台模板入口，不搜索历史正文 |
| `app/observation_output.py` | 服务级单工作线程池、256 个待处理容量、统一开关、冻结 JSON 非阻塞投递、计数及排空关闭 |
| `app/model_call_logging.py` | deliver_record 统一输出入口；print_record_json 写入单行 JSON；ES 方法及调用以注释预留，不访问 ES |
| `app/history.py` | 单实例 SQLite；analysis.execution_trace 保存同份模型快照；重启中断对账 |
| `app/tracing.py` | 始终尝试创建本地 LangSmith RunTree；只修改内存对象，不创建 Client、不调用 post/patch、不上报；既有 mock 分析 |
| `scripts/start.ps1` | UTF-8、固定单 worker；默认本机 8100 |
| `scripts/demo_observation.py` | 无配置和外网的完整模型日志演示 |
| `tests/test_model_observation.py` | 可选合同、确定性时钟、Token 语义、英文动作及多线程单行日志原子性 |
| `tests/test_operation_detection.py` | 权限/路由/文档同步/UI 辅助请求、英文归一化、多模态及历史引用防误识别 |
| `tests/test_observation_service.py` | SDK/HTTP/AG-UI 集成、取消并发、重试、防重、持久快照与故障隔离 |
| `tests/test_local_trace.py` | 真实 RunTree 的字段优先级、父子身份、默认本地追踪与零上传、收尾先于打印 |
| `tests/test_observation_output.py` | 异步投递、冻结快照、容量限制、输出故障、统一开关及慢打印不阻塞模型收尾 |
| `tests/test_output_submit_failure.py` | 线程创建/任务提交失败后的撤销与单次结算、故障池回收、旧任务排空及后续输出恢复 |
| `tests/test_service.py`、`test_gateway_tiers.py`、`test_config_safety.py` | 继承的转发、网关三档、历史与配置安全回归 |

全部产品接口保持官方 AG-UI；日志不是新响应协议。不在本服务补建 Studio 会话/轮次、不执行 Studio 工具、不根据历史猜测当前请求身份。当前仅实现 OpenAI 兼容上游协议。

模型统计固定使用 span_kind=model、run_name=model-inference；不创建或打印工具观察记录，工具相关请求与响应只沿用原转发协议。

本地 LangSmith 追踪不需要账号、API key 或联网。健康信息 `langsmithEnabled=false` 表示远端上报关闭，`langsmithMode=local` 表示使用本地 SDK 追踪对象。
