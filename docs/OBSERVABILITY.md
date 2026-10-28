# 模型埋点采集和控制台输出

## 范围

一个接纳后的 `/model-inference/run` 对应一次模型调用观测。成功、失败、取消收尾时构建一份快照，输出开关开启后提交一次后台任务，不逐分片打印。SDK 内部网络重试仍为一组；同 runId 重复登记直接 409，不增加模型日志。健康、模型信息、鉴权/参数/大小拒绝不打印模型统计。

本服务只记录模型调用，span_kind 固定为 model，run_name 固定为 model-inference。请求和响应中的工具相关数据沿用原协议透传，不额外识别、记录或打印工具调用。

每次埋点只输出一行完整 JSON（identity/data），英文操作标识位于 identity.current_operation；不再打印独立动作行。实际 write/flush 在后台工作线程中执行，JSON 内的换行会转义，避免并发日志交错或字段内容破坏行边界。控制台写入失败由输出组件记录失败计数，同份本地摘要仍按原流程保存。

服务不修改 Studio 的请求合同、业务流程、提示词或 AG-UI 返回事件。采集仅作用于实际经过服务的推理请求。

每次推理始终尝试使用 LangSmith SDK 在本地创建 RunTree。服务不初始化上传 Client，不调用 RunTree.post/patch，不向 LangSmith 上报，也不查询远端追踪；无需 LangSmith 账号或 API key。旧 LANGSMITH_ENABLED/API_KEY/ENDPOINT 配置不控制当前本地采集、不启用上传，LANGSMITH_PROJECT 仅作为本地项目标记。健康信息中 langsmithEnabled=false 表示远端上报关闭，langsmithMode=local 表示本地追踪。

## 异步输出开关与生命周期

`MODEL_OBSERVATION_OUTPUT_ENABLED=true` 为默认值，在启动时读取，修改后重启服务生效。`false` 只停止统计响应体投递，不关闭模型推理、观测采集、现有 SQLite 历史或普通运行日志。未来输出方式改为 ES 后，这个开关继续控制 ES 输出任务，无需新增第二个目标开关。

每个应用持有一个 `ObservationOutput`，通过 FastAPI lifespan 管理共享单工作线程的 `ThreadPoolExecutor`；`app.state.observation_output` 可用于本地运行状态检查。待处理容量为 256，包含正在执行和等待执行的任务，完成后释放容量，不按请求创建线程池，也不新增查询接口。

请求协程先完成本地 RunTree、构建 `build_observation` 快照并保存历史，再在保存步骤的 finally 中提交输出。提交前将快照序列化为独立、不可变的 JSON 字符串，线程任务不持有可变 RunTree、模型响应流或原始上下文，也不重新获取时间、计算耗时或识别当前操作。主流程只进行非阻塞投递，不等待输出完成。

统一输出方法 `deliver_record(payload_json)` 当前调用 `print_record_json(payload_json)`。`write_record_to_es` 方法骨架及调用均以注释保留；本次不安装 ES 依赖、不创建客户端、不连接 ES。正式接入时实现 ES 写入，注释控制台调用并启用 ES 调用，统一开关仍位于投递入口。ES 地址、索引、认证、超时及补发策略属于正式接入时的配置内容，不在当前响应体增加字段。

队列满、序列化失败、关闭后提交、线程输出失败分别记录运行计数，不能阻塞请求或重新调用模型；这些计数不是响应体字段。正常关闭时停止接收任务，在请求处理协程之外等待已提交任务排空，再关闭输出线程池。强制终止进程可能使内存任务尚未输出，SQLite 中已保存的模型快照不因此删除；当前输出不承诺可靠投递或自动补发。

## 字段来源

| 字段 | 来源或未知规则 |
| --- | --- |
| identity.thread_id | 原样 envelope.thread_id，不使用 metadata.sessionId 或 Graph 推断 |
| identity.run_id | 原样 envelope.run_id，不用 SDK/Span 身份替换 |
| identity.user_id/project_id | 既有 metadata.userId/projectId，没有则 null |
| identity.conversation_id/operation_id/workflow_scope | 既有 metadata.extensions 中同名 snake_case 键，没有则 null；不搜索别名或猜测 |
| identity.current_operation | 已有明确操作说明归一化，或根据当前 Studio 平台提示词入口识别；英文 snake_case，未知/冲突为 unknown |
| data.trace_id/span_id/parent_span_id | 真实 RunTree.trace_id/id/parent_run_id；本地 SDK 对象创建失败时 null，不生成替代 ID |
| data.span_kind | 固定为 model，表示本服务实际执行的模型调用 |
| data.run_name | 固定为 model-inference，不随提示词职责、工具指令或追踪对象名称变化 |
| data.started_at/ended_at | 优先读取 RunTree.start_time/end_time 的实际请求边界，缺失时取本次观测；输出统一转换为北京时间 +08:00 |
| data.duration_ms | 优先读取 RunTree.metadata.duration_ms 中的单调时钟实测值，缺失时使用本次观测 |
| data.status | 优先读取 RunTree.metadata.status，缺失时取本次执行结果，通常为 completed/failed/cancelled |
| data.active_intervals | 模型 Span 不输出；合同保留可选类型供后续用途 |
| data.model.provider/name | 优先读取 RunTree.metadata.ls_provider/ls_model_name；缺失时使用本次实际配置/请求，不采用上游响应别名 |
| data.model.input_tokens/output_tokens/total_tokens | 优先读取 RunTree.metadata.usage_metadata；由真实上游 usage.prompt_tokens/completion_tokens/total_tokens 映射写入，不由 LangSmith 估算 |
| data.model.usage_source/usage_status | 显式 null，不计算枚举值 |
| data.model.ttft_ms/output_duration_ms | 优先读取 RunTree.metadata 中对应实测值，缺失时由本次观测补充；非流式均 null，时间规则见下文 |
| data.error | 优先读取追踪对象的结构化 execution_error 等错误事实；无技术异常或主动取消为 null；异常统一转换为固定安全摘要，不打印 RunTree.error 中的原始敏感正文 |

字段模型全部可省略/可空。标准日志为了对比保留已定义字段，未知不填空字符串或虚构零值。模型用量字段只接受非负整数，拒绝布尔、浮点、负数和字符串。

除 span_kind/run_name 按当前模型服务合同固定外，data 优先读取本地追踪对象中的有效字段；对象或字段缺失时，仅使用当前调用已经取得的真实事实兜底，未知保持 null。追踪对象没有 SDK 原生字段的状态、实测单调时钟耗时、TTFT、输出区间及结构化错误，由服务写入 RunTree.metadata 后统一读取。usage_source/status 仍固定为 null，不受其他追踪元数据覆盖。identity 继续原样来自请求，不用追踪 ID 替换。

LangSmith 身份由 SDK 本地创建对象时生成，不需要网络。当前请求不继承外部追踪上下文，模型追踪是独立根，trace_id=span_id、parent_span_id=null。此身份不等同于 Studio 的请求 runId；不能仅根据同 threadId/runId 伪造调用树。模型响应出现 tool_calls 仍为模型 Span；Studio 真正执行的工具或 RAG 没有经过本服务，不会补建它们的追踪记录。

## Token 统计

流式请求补充 include_usage=true，继续读取内容结束后可能出现的 choices=[] 用量尾帧。最新包含有效 Token 的 usage 对象整体替换旧快照，并将对应 input_tokens/output_tokens/total_tokens 写入本地 RunTree.metadata.usage_metadata；不叠加累计值、不跨快照补字段。打印优先选择追踪对象中的有效用量快照，不能用其他旧快照补齐该快照缺失的字段。后续不含用量的分片不会清空统计。

上游真实 0 保留。缺失/非法字段 null；不由字符数估算，也不根据输入加输出补算缺失总数。三项有效但总数冲突时摘要 total_tokens=null 并产生无原文的诊断；原始输出历史不改写。

## 时间边界

控制台 started_at/ended_at 统一显示北京时间（固定 UTC+08:00），同份 analysis.execution_trace 快照使用相同表示。例如 2026-10-27T01:20:01.602528+00:00 输出为 2026-10-27T09:20:01.602528+08:00；表示的是同一时刻，微秒保留。仅做带时区 datetime 的转换，不修改内部 UTC 采集、LangSmith 对象或原历史表时间列，也不修改上游原始时间字段。未知时间仍为 null。线程池只输出提交前已经冻结的快照，排队及 write/flush 时间不计入模型时间字段。

SDK 请求开始前记录单调时钟和 UTC 起点，并同步到本地 RunTree，不包括鉴权、数据库登记、本地追踪对象创建等准备时间；包含本次 SDK 网络重试和退避。开始时间不采用 RunTree 提前创建时的默认时间来扩大模型执行区间。

每次 SDK 返回分片，先采集接收时间再交给聚合/输出：文本、推理、拒绝、工具名称或参数的非空字符串算生成输出。空格换行有效；角色、工具 ID、结束原因和纯用量无效。多候选时按整次请求中任一候选的首末有效输出观测。

ttft_ms=首个有效输出时间-请求开始时间。output_duration_ms=最后一个有效输出时间-首个有效输出时间。没有有效输出时两者 null；只有一个有效片段或没有正的可观测间隔时 output_duration_ms=null。有效正间隔不足一毫秒时整数值可能为 0，并非缺失值。

上游结束或关闭时冻结结束时刻并写入 RunTree；非流式在完整响应到达后、交付客户端前冻结。模型结果交付、最终 SQLite 提交和本地追踪收尾不延长该时间。流式度量是服务本地接收过程，也可能受到网络、下游背压及流内本地处理影响，不是模型服务器纯计算时间。

## 当前操作字段

identity.current_operation 是可省略、可空的字符串，服务生成的标准记录填写最长 80 字符的英文 snake_case 标识，并随整份快照保存到 analysis.execution_trace。不再输出第二行，不加入原 AG-UI 业务事件。span_kind/run_name 仍固定为 model/model-inference。

取值顺序：

1. 已有 metadata.extensions.current_operation 为英文标识或英文短语时，统一大小写及分隔符；例如 Generate Technical Plan 或 GenerateTechnicalPlan 转成 generate_technical_plan。已知中文说明（例如生成技术规划）通过明确映射转换，不做任意拼音或机械翻译。
2. 未知中文、非法文本、链接、超长值、包含服务凭证的值或显式 unknown 不直接输出，继续检查本次已收到的提示词。
3. 优先匹配 system/developer 指令开头；没有匹配时只检查首条用户模板。识别前统一空白、大小写和全半角，并支持标准文本块。多种角色冲突、普通聊天或缺少匹配证据时为 unknown。

只比对已核对的平台模板入口，不根据任意业务关键词、后续用户消息、历史回答、工具定义/结果或引用代码推断动作。规则见 [operation_catalog.py](../app/operation_catalog.py)；不要求 Studio 增加字段、不修改提示词、不额外调用模型。任务身份没有显式传入时，规则只表示可确认的模型职责，不能替代真实工作流节点状态。

主要操作标识：

| 请求职责 | 英文标识 |
| --- | --- |
| 需求分析 | analyze_requirements |
| 权限要求判定 | classify_authorization_requirements |
| 权限业务事实提取 | extract_authorization_facts |
| 产品规划 | generate_product_plan |
| 技术规划 | generate_technical_plan |
| 技术动作绑定修复 / API 契约修复 | repair_technical_action_bindings / repair_technical_api_contracts |
| 设计意图路由 | classify_design_intent |
| 技术规划会话处理 | handle_technical_planning_conversation |
| 需求 / 产品 / 技术文档同步 | sync_requirement_spec / sync_product_plan / sync_technical_plan |
| UI 生成 / 修复 / 续写 / 调整 | generate_ui_design / repair_ui_design / continue_ui_design / adjust_ui_design |
| UI 调整页面选择 | select_ui_design_targets |
| Unit 开发任务规划 | generate_unit_tasks |
| 前端 / 后端代码生成 | generate_frontend_code / generate_backend_code |
| 测试生成 / 代码审查 / 审查修复 | generate_tests / review_code / repair_code_review |
| 修复规划 / 局部代码修改 | plan_repairs / modify_code |
| 工作区问答 / 普通对话 | answer_workspace_question / answer_conversation |
| 变更影响分析 | analyze_change_impact |
| 接口字段映射辅助 | assist_endpoint_field_mapping |
| 数据库操作判定 / 查询映射 / 返回映射 | identify_database_operation / map_database_query / map_database_return_fields |
| 外部 API 字段映射 / 静态返回映射 / Mock 生成 | map_external_api_fields / map_static_return_fields / generate_static_mock_data |
| 未能确认 | unknown |

## 保存与故障

调用结束时先在内存中完成 RunTree 的输出、状态、错误和时间收尾，再构建 data 快照、保存到 analysis.execution_trace，并在保存步骤的 finally 中按开关提交输出；mock 分析开启时继续保留原有 mock 字段。输出线程使用同份快照的冻结 JSON，输出失败不更改模型状态、历史结果或用量。不开启 ES 调用，不新增数据库表、查询 API 或远程埋点请求。原始提示词只保留于本地追踪内存与既有本地历史，不打印到摘要，也不上传 LangSmith。

埋点采集、构造、打印异常都与推理隔离。进程强杀或机器断电时不能保证执行 finally，最后日志/未提交片段可能丢失；现有启动对账标记 interrupted，但不伪造未知结束时间、Span 或 Token。当前数据库仍限单实例、单 worker。
