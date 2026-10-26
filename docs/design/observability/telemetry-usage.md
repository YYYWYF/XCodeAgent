# 后端一期埋点调用说明

本期提供后端调用入口，不自动接入业务节点，也不新增前端或产品 HTTP 端点。字段结构以同目录的 `telemetry-phase1-v1.jsonc` 为准；完整版 `telemetry-batch.schema.json` 不用于校验本期精简记录。

## 配置

在后端运行环境设置 `DEVAGENTSTUDIO_TELEMETRY_URL` 为第三方接收接口的完整 HTTP(S) POST URL。留空时不发送埋点。当前不发送鉴权信息。

## 在业务完成后调用

例如，应用创建**成功持久化之后**，在后端业务代码中提交一次埋点；同步和异步调用处都可使用：

```python
from app.observability.telemetry import emit_telemetry_event

emit_telemetry_event(
    {
        "identity": {"project_id": application_id},
        "data": {
            "event_name": "application_created",
            "payload": {"creation_source": "new_app"},
        },
    }
)
```

执行片段使用 `ExecutionTraceRecord(data=ExecutionTraceData(...))`，调用 `emit_telemetry_trace(record)`，同样不加 `await`。调用位置应在业务事实成立之后，例如保存成功后记录创建事件，执行终态确定后记录 Trace。本期没有把入口接入任何具体业务节点。

入口接受上面的字典或已构造的 `TelemetryEventRecord`／`ExecutionTraceRecord`。推荐直接传字典，这样字段校验、读取地址、序列化和 POST 都在埋点专用线程中完成；字段无效时只记录日志，不会在业务调用处抛出校验异常。若调用处自行构造 Pydantic 对象，构造时的校验仍发生在调用线程。传入字典后请不要再修改它，因为线程稍后才读取内容。`telemetry_models.py` 中每个字段都可省略，但提供的值仍会按字段类型检查。

公共字段在发送任务中自动补齐，直接新建模型不会补齐这些字段。只补未提供的键，调用方提供的值（包括空字符串和显式 `None`）保留；显式 `producer=None` 或 `identity=None` 时不补对应对象。其余未提供的业务字段仍省略。

| 字段 | 未提供时的默认值 |
| --- | --- |
| `type` | 根据 Event／Trace 入口确定 |
| `record_id` | 自动生成 UUID |
| `occurred_at` | 调用入口捕获的时间，不包含线程池排队时间 |
| `producer.platform` | 当前系统对应的 `windows`／`macos`／`linux`；未知系统省略 |
| `producer.xcodeagent_version` | 从 `Frontend/package.json` 读取产品版本；打包时将同一文件复制到 `app/product_metadata/package.json`；无法读取时记日志并省略 |
| `identity.user_id` | 暂时默认 `Hupy/test`，可通过调用参数覆盖 |

`producer` 只自动补齐 `platform` 和 `xcodeagent_version`；`source`、`environment` 由调用方按需提供，未提供则省略。

本次请求另补 `schema_version=1`、`batch_id`、`sent_at`。时间使用 UTC RFC3339 `Z` 后缀；`sent_at` 在工作线程组装发送请求时生成。若业务事实发生在更早时刻，请提供对应的 `occurred_at`。Trace 的开始时间、结束时间、耗时和 Token 用量仍由调用方采集。同一事实若由业务层再次上报，请复用稳定的 `record_id` 和原 `occurred_at`。

`emit_telemetry_event(...)` 是普通函数，只向独立于业务执行器的埋点线程池提交任务，随后立即继续业务流程；**调用时不要加 `await`，也无需接收返回值或运行中的事件循环**。线程池最多同时执行 2 个任务，最多容纳 16 个执行中或待执行任务；满载时直接丢弃新埋点并记日志，避免无限积压。URL 未配置时任务不发送；地址无效或字段校验失败时只记录日志。第三方收到的请求体为 `{"schema_version":1,"batch_id":"...","sent_at":"...Z","records":[一条记录]}`，需接受省略的业务字段。

## 失败处理

每次业务触发只发送一次。HTTP 非 2xx、网络异常或 5 秒超时仅写状态日志；不抛回业务流程，不保存本地持久队列，也不自动重试。Python 正常退出时会等待已提交的线程任务，可能延长关闭时间；进程被强制结束时，未完成的埋点可能丢失。专用线程池避免占用业务事件循环和业务默认线程池，但仍共享进程的 CPU、内存与网络资源。需要更强的送达保证时，再单独扩展持久化机制。

构造错误摘要前应先脱敏，不上传凭证、完整 Prompt 或源码。发送日志不记录请求体或完整 URL。
