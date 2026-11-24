# XCodeAgent 模型 TTFT 实施记录

## 结果与边界

已在当前 HEAD `0ec754adb651555fac921e5e97d588e645223c2a` 的现有实现上补齐模型 TTFT 与首增量后耗时。实施前工作区仅有用户提供的未跟踪实施方案；该方案未改动。本地不存在方案基准提交 `8b9ef786de34123b107e0ecc402e77563ea20873`，无法核对两提交间差异。

已知事实：确定性单测和离线 SDK fixture 共 42 项通过，后端健康检查返回 `ok`。尚未执行真实模型请求或真实 Build 性能采样，不能据此推断当前模型 TTFT 或优化收益。

反向检验：真实增量能够建立客户端时间证据，但最终完整结果、Usage 和结束帧不能证明首输出时间；这些路径保持 null。客户端 TTFT 包含网络、排队、Prefill 等多种耗时，不能直接归因为服务端排队；后续耗时含尾帧及收尾，不能直接解释为 GPU 解码耗时。

## 实际修改文件

以下路径均相对于仓库根目录。

运行实现：

- `Backend/app/observability/model_stream_timing.py`（新增）：明确字段判定与单次调用时钟。
- `Backend/app/observability/model_usage_callback.py`：在途状态、独立首增量采集和唯一终态。
- `Backend/app/observability/build_instrumentation.py`：`finish(..., ended_ns=None)` 共享结束时钟。
- `Backend/app/observability/agent_execution_trace.py`：独立模型时钟、结束/错误字段和清理。
- `Backend/app/observability/build_model_timing_report.py`（新增）：有效配对与统计，避免继续扩张主报告职责。
- `Backend/app/observability/build_baseline_report.py`：统计区、覆盖率及明细列。
- `Backend/app/agents/transmit_model_messages.py`：仅最终完整结果的合成 Chunk 增加最小观测标记。

测试：

- `Backend/tests/test_model_stream_timing.py`（新增）。
- `Backend/tests/test_model_stream_adapters.py`（新增）。
- `Backend/tests/test_build_observability.py`。
- `Backend/tests/test_frontend_agent_trace.py`。
- `Backend/tests/test_model_factory.py`：隔离测试环境的 Telemetry 开关，增加与输出日志独立注册的断言。
- `Backend/tests/test_telemetry_pipeline.py`。

文档：

- `docs/CODEBASE_INDEX.md`。
- `docs/design/observability/telemetry-batch.schema.json`（只修改字段说明）。
- `docs/design/observability/telemetry-contract.txt`。
- `docs/design/observability/telemetry-fields.jsonc`。
- `docs/design/observability/telemetry-phase1-v1.jsonc`。
- `docs/design/observability/telemetry-upload-v1.jsonc`。
- `docs/design/observability/metrics-calculation-v1.md`。
- 本实施记录。

## 首增量判定与实现依据

`has_effective_model_delta()` 只检查明确的文本、thinking/reasoning、OpenAI reasoning summary，以及工具名称/原始参数字符串；真实空白文本有效。未知内容块、role、ID、index、signature、Usage、finish reason、空块均不触发。没有把对象转为字符串后判空，也不保存增量正文。

核对并使用本地实际安装的 OpenAI/Anthropic SDK 转换器和 LangChain 正式回调入口建立 fixture。OpenAI 完整补帧只含空正文及结果元数据，不算增量；Transmit `final_stream_chunk()` 在没有真实 chunk 时可能产生含全文的合成 Chunk，而且官方 `stream/astream` 会对它派发 token 回调，因此在 `generation_info` 增加 `_ttft_synthetic`，公共判定器优先排除它。内容、输出顺序和聚合方式保持原逻辑。

Telemetry 复用 `Span.started`，终态共享一次结束时间，毫秒向下取整；Trace 在原输入引用准备后建立模型起点，保留 round 取整。两者均先计算总耗时与 TTFT，再用差值计算后续耗时。未观测到首增量时两项均 null，零毫秒允许保留。

Usage 原快照优先级、Anthropic SDK 聚合、1024 条在途上限、Span 归属及原状态规则保留。新增观测异常与 Usage 路径隔离；锁内不发送 Telemetry，token 回调不写详细日志。工具日志不添加模型字段，关闭 Trace 时清理模型在途状态。

Baseline 仅对三个非负整数、排除 bool 且加和成立的模型片段统计配对；P50 使用中位数，P95 使用 nearest-rank。失败/取消且已有首增量的片段可配对，明细保留状态。按 BuildRun 计算后再沿既有报告展示平均数（中位数）；覆盖率不同不计算耗时变化率。TTFT 不参与原质量门禁。

## 验证命令与结果

在 `Backend` 目录执行：

```sh
.venv/bin/python -m unittest tests.test_model_stream_timing tests.test_build_observability tests.test_frontend_agent_trace tests.test_model_factory tests.test_model_stream_adapters tests.test_telemetry_pipeline
```

结果：`Ran 42 tests ... OK`。覆盖文本、推理、工具、控制帧、首次观测与取整、失败/取消、重复终态、并发、淘汰、观测错误隔离、两套开关、消息引用、无 token I/O、OpenAI/Anthropic/Transmit 同步异步流式与非流式、最终合成结果、本地/HTTP 字段传递和历史缺失字段。

在仓库根目录执行：

```sh
Backend/.venv/bin/python -m py_compile Backend/app/observability/model_stream_timing.py Backend/app/observability/model_usage_callback.py Backend/app/observability/build_instrumentation.py Backend/app/observability/agent_execution_trace.py Backend/app/observability/build_model_timing_report.py Backend/app/observability/build_baseline_report.py Backend/app/agents/transmit_model_messages.py
git diff --check
curl -sS http://127.0.0.1:8000/health
```

结果：语法检查、diff 检查通过；健康检查成功，`status=ok`。沙箱内 curl 最初无法连接，使用获准的本地端口访问后成功。

首次使用系统 `python3 -m unittest ...` 时因缺少 `json_repair` 导入失败；改用仓库已有 `.venv/bin/python` 后解决，未安装或修改依赖。随后模型工厂测试暴露了测试环境的 Telemetry 开关依赖；在该测试的 setUp 显式隔离开关，并增加启用情况下的独立回调注册断言，最终全部通过。测试输出中的日志创建/关闭失败警告来自既有故障注入用例；Starlette 的 httpx 弃用警告不影响通过结果。

未运行前端构建或 Electron UI 验证：没有前端代码或 UI 修改。未访问真实模型、未运行真实 Build：本次使用确定性/离线验证，真实性能对比需要同项目、模型、任务与初始代码状态的后续采样。SDK 丢弃或不公开的私有推理字段无法补采，未知输出类型不猜测时间。

## 两种日志与 Baseline 示例

以下均为模拟 fixture 的字段节选，不是历史或真实模型测量，也不是完整上传对象。

Telemetry：

```json
{"span_kind":"model","status":"completed","duration_ms":10,"model":{"provider":"openai","name":"test","total_tokens":6,"ttft_ms":2,"output_duration_ms":8}}
```

详细 Trace：

```json
{"event":"model_end","runId":"a","durationMs":11,"ttftMs":3,"outputDurationMs":8,"outputRefs":["m1"]}
```

这两条来自不同取整口径的确定性 fixture，不能要求彼此数值一致。

用已有 `write_sample()` fixture 加入配对数据，再实际调用 `render_report()` 的 Baseline 节选：

| 指标 | Baseline 平均数（中位数） |
| --- | ---: |
| 有效配对 / 总调用 | 1 / 1 |
| 模型累计耗时 ms | 120（120） |
| TTFT 累计 ms | 100（100） |
| 首 Token 后累计 ms | 20（20） |
| TTFT 平均 / P50 / P95 ms | 100 / 100 / 100 |
| TTFT 覆盖率 | 1（1） |
| 配对样本 TTFT / 总耗时 | 0.833（0.833） |

累计模型耗时可能包含并发调用，不能等同 Build wall time。历史日志没有首增量时间，不能反推出真实 TTFT。
