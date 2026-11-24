# XCodeAgent 模型 TTFT 埋点补全 — Codex 实施方案

## 1. 目标与范围

基于 `YYYWYF/XCodeAgent` 的 `dev_agent` 分支（分析基准提交 `8b9ef786de34123b107e0ecc402e77563ea20873`），在现有统一 Telemetry、Agent 详细 Trace 和 Build Baseline 中补充首 Token 耗时与首 Token 后响应耗时。实施前核对当前 HEAD 与基准的差异，以实际代码为准，不覆盖其他已完成改动。

本次仅增强观测能力，不修改模型调用方式、Agent 执行流程、DAG、权限、Prompt、Skill、Repair、任务验收、重试策略、流式协议或 Token Usage 统计规则。不新增运行开关，不增加逐 Token 日志，不引入新的全局观测框架。

## 2. 现状与统一统计口径

- `Backend/app/observability/model_usage_callback.py`：`BuildModelUsageCallback` 按 LangChain `run_id` 维护 `inflight`，在模型结束时提交单个模型 Span；`on_llm_new_token()` 当前只负责 Usage 快照。
- `Backend/app/observability/build_instrumentation.py`：`Span.started` 使用 `monotonic_ns()`，`finish()` 生成 `duration_ms`。
- `Backend/app/observability/telemetry_models.py`：`TelemetryModelUsage` 已预留 `ttft_ms`、`output_duration_ms`，直接复用。
- `Backend/app/observability/agent_execution_trace.py`：`AgentExecutionTrace` 已有 `model_start`、`model_end`、`model_error` 和 `durationMs`，尚无首 Token 回调；前端及数据源详细日志复用该实现。
- `Backend/app/agents/model_factory.py`：统一注册 Usage Callback；直连 OpenAI/Anthropic 已支持 streaming；`transmit_chat_model.py` 的 `_stream()` / `_astream()` 已提供增量回调。
- `Backend/app/observability/build_baseline_report.py`：已有模型 Span、Usage、耗时与性能对比报告。

### 2.1 指标定义

| 指标 | Telemetry | Agent Trace | 定义 |
|---|---|---|---|
| 总耗时 | `data.duration_ms` | `durationMs` | 保持现有逻辑调用的起止边界 |
| 首 Token 耗时 | `data.model.ttft_ms` | `ttftMs` | 调用开始至首个可观测有效模型输出增量 |
| 首 Token 后响应耗时 | `data.model.output_duration_ms` | `outputDurationMs` | 首个有效增量至调用终态 |

使用单调时钟。同一条记录的总耗时与两个分量共用起止时间和取整口径；先计算总耗时和 TTFT，再以差值计算后续耗时，保证 `duration = ttft + output_duration`。保留详细 Trace 原有 round 毫秒口径与 Telemetry 原有向下取整口径，不要求两套独立 Callback 的数值完全一致。

TTFT 是客户端观测指标，包含连接、网络、服务排队、Prefill、首个增量生成及传输；后续响应耗时包含后续生成、传输、尾帧及回调边界内的收尾。不能解释为纯服务端排队时间或纯 GPU 解码时间。SDK 内部重试仍计入本次逻辑调用，不拆分物理请求。

## 3. 实施步骤一：统一采集能力

### 3.1 新增 `Backend/app/observability/model_stream_timing.py`

提供轻量的首增量判定函数及单次调用状态对象，不依赖全局注册表、不持有消息正文、不产生 I/O。

建议接口：

```python
@dataclass
class ModelStreamTiming:
    started_ns: int
    first_token_ns: int | None = None

    def observe(self, token, chunk=None, *, observed_ns=None): ...
    def finish(self, ended_ns: int, *, duration_ms: int) -> dict: ...


def has_effective_model_delta(token, chunk=None) -> bool: ...
```

具体接口可适配现有代码风格。`observe()` 在回调入口捕获时间，首次有效增量才更新；已记录时直接返回。`finish()` 对未观察到有效增量返回两个 `None`，不使用总耗时或零填充。正常的零毫秒观测值允许保留。并发安全由调用方现有锁负责。

首增量判定要求：

| 内容 | 是否有效 |
|---|---|
| 非空正文文本（包括真实空白字符增量） | 是 |
| 非空 thinking/reasoning 文本 | 是 |
| Tool Call 名称或参数的真实增量 | 是 |
| 空字符串、空列表、空内容块 | 否 |
| 仅 role、tool call ID、index、类型标识 | 否 |
| Usage、finish reason、心跳或协议控制帧 | 否 |
| 非流式最终完整消息、仅最终聚合结果的合成 Chunk | 否 |

核对当前锁定版本的 LangChain OpenAI/Anthropic 和 `agents/transmit_model_messages.py`、`agents/transmit_chat_model.py` 实际增量结构。按明确的正文、推理和工具参数字段判定，例如 `token`（字符串或结构化内容块）、`chunk.message.content`、`tool_call_chunks`、已知的 `additional_kwargs` 推理/工具字段。不对整个对象执行 `str()` 后以非空判断，不把任意非空字典、未知元数据、空工具块误判为首 Token。对 reasoning-only、tool-only 场景建立真实形态的 fixture。

特别回检 Transmit 最终 `final_stream_chunk()` 及 LangChain 可能派发的最终聚合回调：只有真实流式增量才可计时。如果现有回调无法可靠区分某路径的合成结果，则该路径保留 null；仅在确有必要且测试证明时增加最小的观测标识，不改变流式内容、顺序或聚合语义，不为补采 TTFT 强制开启 streaming。

### 3.2 修改 `Backend/app/observability/model_usage_callback.py`

将现有 `inflight[run_id] = (span, stream_usage)` 扩展为清晰的状态对象（或等价的最小结构）：

```python
@dataclass
class ModelCallState:
    span: Span
    stream_usage: dict | None
    timing: ModelStreamTiming
```

- `on_chat_model_start()`：复用 `start_model()` 和 `span.started`，初始化状态；没有 Span 时沿用原逻辑。
- `on_llm_new_token()`：在原 Anthropic / chunk 缺失 / Usage 缺失的提前返回之前，独立采集 TTFT；捕获回调入口时间，按 `run_id` 在原锁保护下只设置一次。不得让 TTFT 判定异常阻止原 Usage 采集。
- 原 Usage 路径保持原样：Anthropic 继续依赖 SDK 聚合；其他 provider 仍取最新标准数字快照，不叠加尾帧，不改变 `_usage()` 的优先级、模型标识、usage_source/status。
- `_finish()`：原子 pop 在途状态，仅产生一个终态 Span；取得一次结束时间，合并 `_usage()` 结果与耗时字段，再调用 `finish()`。保持原 completed/failed/cancelled 状态及异常隔离。
- 保留 `OrderedDict`、1024 条上限、淘汰和重复终态处理；淘汰时使用已有状态和实际淘汰时间计算截至该边界的耗时，不冒充成功完成。原淘汰状态与协议是否兼容不属于本次重构范围。
- 不在锁内发送 Telemetry 或进行文件 I/O；不保存 Token 内容。

### 3.3 修改 `Backend/app/observability/build_instrumentation.py`

为现有 `finish()` 添加可选关键字参数 `ended_ns=None`，默认行为不变：

```python
def finish(span, status, *, metrics=None, model=None,
           timed=True, ended_ns=None):
    ...
    end = monotonic_ns() if ended_ns is None else ended_ns
    duration_ms = (end - span.started) // 1_000_000 if timed else None
    ...
```

模型 Callback 与 `finish()` 使用同一个结束时间。其他 Span 调用方不改动；保留父子关系、业务身份、计数、状态、发布异常隔离及原有 `started_at/ended_at` 语义。不得新增模型终态事件。

### 3.4 数据契约

`TelemetryModelUsage` 已有两个字段，不新增 `generation_ms` 或其他同义字段。核查 `docs/design/observability/telemetry-batch.schema.json`、字段说明和序列化/上传路径是否保留已有字段；仅对实际缺失或口径不一致处做必要补齐，不修改协议版本或上传机制。历史记录缺少新值必须兼容。

## 4. 实施步骤二：同步 Agent 详细日志

修改 `Backend/app/observability/agent_execution_trace.py`：

1. 在 `__init__()` 增加按 `run_id` 隔离的模型耗时状态，保持原有 `_starts` 中工具耗时行为。
2. `on_chat_model_start()` 在原有模型计时边界初始化耗时状态（原逻辑是在输入引用准备后记录起点）；不改变消息去重与上下文引用机制。
3. 新增 `on_llm_new_token()`，使用相同公共增量判定与现有锁；不调用 `_write()`、不进行脱敏、序列化或 flush；首增量后尽快返回。观测异常不得影响业务，也不应因新采集失败禁用原有日志。
4. `on_llm_end()`：沿用原有输出引用、metadata、durationMs，增加 `ttftMs`、`outputDurationMs`。同一次结束时间用于三个指标，保持原有耗时边界和 round 取整方式。
5. `on_llm_error()`：同样输出已有耗时并清理状态；`on_tool_error()` 不增加模型字段；`close()` 清理未完成模型状态。
6. 两套 Callback 独立运行，不依赖另一个开关或全局共享 run_id 状态。复用公共 Trace 的前端、数据源等模块自然获得字段，不单独修改 Agent 业务代码。

示例（模拟数据，不是实际历史测量）：

```json
{
  "event": "model_end",
  "runId": "example-run",
  "durationMs": 88538,
  "ttftMs": 82000,
  "outputDurationMs": 6538,
  "outputRefs": ["m12"],
  "metadata": []
}
```

不新增 `model_token` 事件。保留日志文件格式、权限、脱敏、消息引用、开关和终态行为。

## 5. 实施步骤三：Build Baseline 报告

修改 `Backend/app/observability/build_baseline_report.py`，复用既有 `_sample()`、`_values()`、`_render_tree()`、`render_report()` 等结构，新增独立的模型耗时统计区及模型 Span 明细列。

| 建议指标键 | 统计口径 |
|---|---|
| `model_duration_ms` | 模型 Span 总耗时之和 |
| `ttft_total_ms` | 有效配对样本的 TTFT 之和 |
| `output_duration_total_ms` | 同一批样本的后续响应耗时之和 |
| `ttft_mean_ms` | 有效 TTFT 平均值 |
| `ttft_p50_ms` | 有效 TTFT 中位数 |
| `ttft_p95_ms` | 有效 TTFT P95（采用明确且固定的算法，如 nearest-rank） |
| `ttft_coverage` | 有效配对样本数 / 全部模型 Span 数 |
| `ttft_ratio` | 有效样本 TTFT 之和 / 同一批样本总耗时之和 |

有效配对样本：三个耗时为非负整数，且满足加和关系；排除 bool、缺失或非法字段。无有效样本的耗时统计和比例展示 N/A，覆盖率在有模型调用但无有效样本时为 0；无模型调用时覆盖率 N/A。不要将缺失数据视为 0。失败/取消且已观察首增量的调用可计入观测统计，明细必须保留原状态，说明后续响应耗时截至失败/取消；不得当作完整生成耗时。报告应显示有效样本数/总调用数，明确跨任务并发时累计模型耗时不等于 Build wall time。

保留原有 Baseline 对比与质量门禁，TTFT 不参与 `_conclusion()` 强制判定。历史数据无字段时正常生成报告。模型 Span 明细增加 TTFT 和 Output Duration 两列，非模型 Span 显示 N/A。对比报告展示覆盖率，避免不同覆盖率的耗时直接做无条件结论。

## 6. 边界场景与测试

| 场景 | 预期 |
|---|---|
| 正常文本流、thinking-only、tool-only | 从首次有效增量记录 TTFT |
| 空帧、角色帧、Usage-only、finish-only | 不触发 TTFT |
| 非流式、仅最终合成结果 | 两项 null |
| 首增量前失败或取消 | 两项 null，总耗时保留 |
| 首增量后失败或取消 | TTFT 保留，后续耗时截至终态 |
| 重复增量、重复 end/error、终态后增量 | 首时间不覆盖，不重复发布，不复活状态 |
| 并发不同 run_id、在途淘汰 | 状态隔离，及时清理 |
| 两套开关分别开启、同时开启、均关闭 | 独立运行，原业务行为不变 |
| 旧日志、新旧混合报告、无模型调用 | 兼容，无虚假零值或除零 |

测试文件：

- 新增 `Backend/tests/test_model_stream_timing.py`：有效增量识别、确定性时钟、取整、缺失、首增量只记录一次。
- 修改 `Backend/tests/test_build_observability.py`：模型 Span、真实 Usage 保持、Anthropic 提前返回不影响 TTFT、错误/取消、重复终态、并发、淘汰及报告统计；延续现有 fake model 和 fixture。
- 修改 `Backend/tests/test_frontend_agent_trace.py`：详细日志新增字段、工具字段不变、无逐 Token 日志、消息引用不变、失败清理及开关隔离。
- 回归 `Backend/tests/test_model_factory.py`；按实际现有测试组织补充 OpenAI、Anthropic、Transmit 同步/异步流式与非流式 fixture，特别核查仅最终结果不会误采。优先复用现有测试文件，不新增真实网络依赖。
- 回归 `Backend/tests/test_telemetry_pipeline.py`：确认字段通过校验、序列化及现有 sink，历史记录兼容。

使用可控时钟或 patch，禁止依靠真实 sleep 来判断毫秒数。测试命令按仓库现有环境执行；若采用 unittest，可在 Backend 目录运行：

```bash
python -m unittest discover -s tests -p 'test_model_stream_timing.py'
python -m unittest discover -s tests -p 'test_build_observability.py'
python -m unittest discover -s tests -p 'test_frontend_agent_trace.py'
python -m unittest discover -s tests -p 'test_model_factory.py'
python -m unittest discover -s tests -p 'test_telemetry_pipeline.py'
```

若仓库采用 pytest 或其他入口，则沿用既有入口；不要为了执行测试调整依赖版本或业务代码。无需访问真实模型服务即可完成确定性单测；真实 Build 采样用于后续人工验证，不冒充已执行。

## 7. 最小文件清单

| 文件（相对于 Backend/app） | 操作 |
|---|---|
| `observability/model_stream_timing.py` | 新增 |
| `observability/model_usage_callback.py` | 修改 |
| `observability/build_instrumentation.py` | 小幅修改 |
| `observability/agent_execution_trace.py` | 修改 |
| `observability/build_baseline_report.py` | 修改 |

测试按第 6 节实施。数据契约文档仅在确有必要时补齐。默认不修改 `telemetry_models.py`、`agents/model_factory.py`、`agents/transmit_chat_model.py`、`services/model_output_logger.py`、`agents/frontend/trace.py`、`agents/data_source/trace.py` 或任何 Agent 业务实现；若发现适配器缺少真实增量观测能力，先说明证据，只做必要观测兼容或保留 null，不扩大改造范围。

## 8. 最终验收与交付

- 同一次模型终态记录中，若存在 TTFT，必须满足总耗时 = TTFT + 首 Token 后耗时。
- 原有 Token Usage、模型调用次数、Span 归属、详细日志消息引用、任务结果及开关语义不变。
- 所有已支持的真实流式输出类型能记录首增量，无法观测时保留 null。
- 没有新增逐 Token 日志或网络请求，没有因观测失败导致业务失败。
- Baseline 能展示累计模型耗时、TTFT、后续响应、P50/P95、覆盖率及长耗时调用明细；历史报告兼容，原门禁保持不变。

Codex 完成后输出：实际修改文件清单、关键实现位置、首增量判定依据、测试命令和结果、未覆盖场景及原因、两种日志各一条示例和 Baseline 示例。明确区分模拟 fixture 与真实运行数据。不得将未运行的测试标为通过。

后续人工验证：在原有开关下使用同一项目、模型、任务和初始代码状态重新运行前端 API 模块及页面任务；采集详细日志与统一 Telemetry，检查长耗时调用的首 Token 前/后占比以及覆盖率。历史日志没有首增量时间，不能反推出真实 TTFT，也不能把客户端耗时直接归因于服务端排队或推理速度。
