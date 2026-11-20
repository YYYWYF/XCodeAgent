# 统一分析埋点与 Build Baseline

## 配置与输出

在后端的当前环境文件中配置，重启后端生效。Electron 在启动时复用现有 `/health` 响应里的 `tools.telemetry.enabled`，重新启动客户端后读取新的开关。

| 模式 | 配置与行为 |
| --- | --- |
| `off` | 默认。前端跳过 `/telemetry/run` 提交，后端不创建分析队列、线程、客户端或文件，Build 不注册 Usage 回调，也不扫描 Prompt 或工具输出。 |
| `local` | 设置 `DEVAGENTSTUDIO_OBSERVABILITY_MODE=local`。后台将嵌套 snake_case JSONL 写到 `~/<Studio 环境目录>/observability/<experiment>/events-*.jsonl`。开发环境目录是 `.devagentstudio_dev`。 |
| `http` | 设置 `DEVAGENTSTUDIO_OBSERVABILITY_MODE=http` 和 `DEVAGENTSTUDIO_OBSERVABILITY_URL`，使用完整 HTTP(S) POST 地址。记录从内存直接进入 camelCase 批次。HTTP 失败只增加进程内计数。 |

显式 MODE 优先。未设置 MODE 且 `DEVAGENTSTUDIO_TELEMETRY_URL` 非空时，按旧设置使用 HTTP，并保留旧接收端的缺省 `userId`。显式 `http` 使用新 URL；仅在新 URL 变量未设置时读取旧 URL。任一启用模式只建立一条队列与一个 Sink。已有 `backend.log` 继续独立用于本地排障。

前端保持独立标准 AG-UI `/telemetry/run` 的 `emit_event` / `emit_trace` 动作。后端业务事实或片段终态可调用 `emit_telemetry_event(record)`、`emit_telemetry_trace(record)` 或 `publish(record, record_type)`，入口同步返回，无需 `await`。普通字典会在入队时冻结，字段校验和编码在后台完成。AG-UI 路由在边界校验外部结构，返回完整生命周期，提交成功表示调用完成。

当前严格字段以 [telemetry_models.py](../../../Backend/app/observability/telemetry_models.py) 为准。共有 `execution_trace`、`telemetry_event` 两类记录，省略值和显式 `null` 分开保留。默认 `occurred_at`、HTTP 批次 `sentAt` 采用固定 UTC+08:00 的 `YYYY-MM-DD HH:mm:ss`。片段耗时取单调时钟。HTTP 批次沿用 `{schemaVersion, batchId, sentAt, records}`，每批最多 16 条。字段平铺仅属于模型服务的 ES 写入方式。

每进程队列最多积压 128 条，每个提交限制为 2048 个结构成员和 16384 个字符。满载时丢弃最新提交。内存诊断可通过 `telemetry.diagnostics()` 读取 `accepted/written/sent/dropped/validation_failed/local_write_failed/http_failed/pending`。后台不重试 HTTP。正常进程退出最多等待 250 毫秒清空，强制退出可能丢失在途数据。只有稳定标识、时间、大小、状态及真实用量属于 Build 分析数据。

## 采集口径

- Build 根片段采用实际 `build_run_id` 和确认 DAG 摘要。每次调度调用有自己的 `trace_id`。任务按实际执行分配 `attempt_id`，重试和修复都有标记。工作流缺少独立 `operation_id` 时保留 `null`。现行调度器逐任务调用 Agent，`task_ids` 留给未来确有多任务同批调用的生产者。
- 前端记录实际注入的任务级 ProjectPlan 视图及任务列表文本，范围无法确定时记录所注入的完整 ProjectPlan 的大小。后端记录实际 WorkspaceSnapshot 投影和序列化任务包，其中包含分型实现契约。两边都记录拼接完成的初始 Prompt。每项分别统计字符和 UTF-8 字节。
- 模型耗时覆盖一次 LangChain 调用。用量来自 Provider Usage 或 LangChain 的标准用量字段，累计流式尾帧采用最后一个有效快照。每个模型调用只有一个终态，SDK 内部重发若未产出独立用量则无法单列。失败和取消有片段，缺失的用量保留 `null`。工具统计已返回的文本字节，未知结构保留 `null`，工具次数仍计入。
- 任务完成率分母为首次执行切片中的原 DAG 任务数，分子分别取首轮完成和最终完成任务。验收通过率同样使用原 DAG 任务作分母，另列验收覆盖率，跳过和无证据任务不计通过。修复规划、修复执行和重试的模型用量可沿父片段追溯，重试与修复子集允许重叠。多个调度调用组成一个 BuildRun 时，报告显示调度耗时合计，跨暂停墙钟保留 `N/A`。

## 记录和复跑

1. 选择可从同一起点恢复的真实项目。冻结已确认 DAG、需求版本、起始工程快照、Provider/模型、参数、模板、权限与工具环境、冷热缓存、验收套件和质量阈值。为每次运行恢复一份相同快照，冷热缓存分别设 Scenario。建议每个 Scenario 重复 3–5 次。
2. 配置 `DEVAGENTSTUDIO_OBSERVABILITY_MODE=local`、`DEVAGENTSTUDIO_OBSERVABILITY_EXPERIMENT=baseline`、`DEVAGENTSTUDIO_OBSERVABILITY_SCENARIO=crud-query`，沿现有 Build 路径运行。根据根片段的真实 `build_run_id` 填写实验目录的 `manifest.json`。每次使用独立 BuildRun ID，每个进程会生成独立 JSONL 文件。
3. 在实验目录记录清单。下面的值是填写示例，质量门禁先于实验确定。复测时换实验标签和代码版本，验证其余字段与原始条件相同。模型中记录真实名称、供应商、转发方式和有效参数。工具环境可用稳定的配置指纹，验收套件可列实际命令与门禁。清单只供离线比较，存放于对应的分析目录。

```json
[
  {
    "build_run_id": "<根片段的 build_run_id>",
    "experiment_tag": "baseline",
    "scenario_id": "crud-query",
    "project_id": "<真实应用标识>",
    "requirement_revision": "<确认需求的版本或摘要>",
    "workspace_snapshot": "<相同起始工程的快照或提交摘要>",
    "task_plan_sha256": "<根片段的 task_plan_sha256>",
    "model": {
      "name": "<模型>",
      "provider": "<供应商>",
      "transport": "direct",
      "parameters": {"temperature": 0, "max_tokens": 16384}
    },
    "template_version": "<模板版本>",
    "code_revision": "<XCodeAgent 代码提交>",
    "tool_environment": "<工具版本、依赖和权限的可核对记录>",
    "validation_suite": ["<同一验收套件及命令>"],
    "cache_condition": "cold",
    "repetitions": 1,
    "quality_gate": {
      "minimum_final_rate": 1.0,
      "minimum_acceptance_rate": 0.9,
      "wall_time_tolerance": 0.05,
      "repair_count_tolerance": 0
    }
  }
]
```

从 `Backend` 运行：

```bash
.venv/bin/python -m app.observability.build_baseline_report summary \
  --input ~/.devagentstudio_dev/observability/baseline --output baseline-summary.md

.venv/bin/python -m app.observability.build_baseline_report compare \
  --baseline ~/.devagentstudio_dev/observability/baseline \
  --after ~/.devagentstudio_dev/observability/optimized-v1 \
  --output build-baseline-comparison.md
```

报告固定分为 Prompt、整体 Token、交付效率与质量三部分。数值按 BuildRun 展示样本数、均值和中位数，逐片段展示 Task/Attempt 与 Span 父链。After 缺失时显示 `N/A`。数据损坏、Span 数目不符、模型用量缺失或条件不同会限制比较结论。质量通过需要最终完成、验收通过及覆盖率至少保持 Baseline 水平，并达到预设阈值；耗时和修复成本也须在预设容差内，才可根据 Token 下降判定该场景有效。

## 开发单测对照回放

开发单测采用同一观测开关。按 `context.business_stage=unit_test` 和 `identity.run_id` 聚合 `unit_test`、`unit_test_repair` 根片段，并沿父链关联 `generate_unit_tests`、`unit_test.mapping_cache_hit`、`repair_planning`、`unit_test_repair_task` 及平台命令。模型片段的 `attempt_id` 和 `model_round` 关联本次 Agent 调用。首轮记录实际文字消息的长度及字节数，模型用量取供应商回包。命令片段记录实际子进程耗时，未跨入命令的环境错误仍由检查报告提供。

对照测量从同一工程快照开始，冷生成、缓存命中和失败修复各自配对。按唯一模型片段统计输入和输出 token、轮次及用量缺失数。阶段时间取根片段区间并集，跨确认的等待单列。把同一失败类型的修复规划、Agent 执行和平台命令耗时分别比较，并同时核对实际运行用例、门禁及重新确认结果。

## 集成修复对照回放

- 启用 `DEVAGENTSTUDIO_OBSERVABILITY_MODE=local`，用独立实验标签记录两批运行。在对照清单中为每个固定失败样本登记版本、同一起始工作区快照、实际模型及参数、缓存条件、检查命令、Run ID。对 `be2140e6` 基线从原模型服务取得逐次用量和调用状态，从原 AG-UI 运行与真实检查结果取得时间和质量门禁。前后的同一个样本应从两份相同快照各执行一次。
- 后端本地 `integration_test` Trace 按 `identity.run_id` 聚合初次检查、规划、修复和复测。按 `context.extra.attempt_id` 区分节点内的独立计划或任务，`repair_round` 关联修复轮次，`model_round` 记录该尝试实际开始的第几次模型调用。首轮 Prompt 大小来自 `initial_prompt`，工具返回大小来自工具片段，`repair_close_reason` 记录固定收尾原因。统计数据只含大小、状态和标识。
- 两个操作的 Token 和模型耗时只聚合 `span_kind=model` 的唯一片段。阶段墙钟用外层集成节点及不重叠的修复节点区间，跨确认的 Run 用清单关联，等待用户的时间单列。最终通过以平台检查和 `metrics.quality_gate_passed=true` 为据。缺失模型用量单列。至少完成 30 对可比运行后再判定合计 Token 降低 70%、修复链路墙钟 P50 降低 50% 和复测通过率保持。
- 后端执行扩展以变更前的提交为基线，按同一模型、参数、命令和起始快照配对。单独统计 `repair_backend_integration_code` 的编码与收尾调用、Token、墙钟 P50/P95，以及平台实际复测通过率和确认行为。至少完成 30 对后再核验 Token 和墙钟是否下降、既有可修复样本及确认行为是否有回退。


## 前端步骤收敛复测

同一份待 Build 工程、未改动的源码及正式任务计划分别复制到两个隔离工作区。在上一版和本版后端上分别执行同一批准任务，保持模型、模板、输入、技能和观测配置一致。每轮从初始快照恢复，按上面的 Baseline compare 命令比较模型用量、Prompt/工具返回字节、耗时、完成率和 Repair。使用 `FRONTEND_AGENT_TRACE_ENABLED=true` 记录相同 Task ID 的步骤明细，并按文件与步骤汇总以下字段。

| 事件 | 对照字段 |
| --- | --- |
| `frontend_file_mutation` | `frontend_step_edit_count`、`frontend_step_no_change_edit_count`、`frontend_step_revert_count` |
| `frontend_tool_permission` | `duplicate_read`、`duplicate_read_kind`、`frontend_read_repeated_covered_count` |
| `frontend_acceptance_rule_result` | `rule_id`、`status`、`file_path`、`line`、`file_version` |

本地同快照工具对照从 `Backend` 运行 `.venv/bin/python -m unittest -q tests.test_frontend_step_convergence.FrontendStepConvergenceTests.test_same_snapshot_write_comparison`。两边使用相同四次工具调用与相同初始字节，检查旧写入路径落盘四次，收敛路径落盘两次，最终字节相同。模型侧用量、耗时与成功率由真实 Build 对照独立衡量。

API 模块默认采用任务级执行，不再设置快速路径开关。前端本地日志继续记录 `frontend_tool_permission`、`frontend_file_mutation` 和模型/工具消息；API 权限事件含当前 `run_id`、`task_id`、`task_kind`，`step_id` 为 `null`，不产生 `frontend_step_transition`。页面仍记录四步切换，`source` 为 `agent`。通过 BuildRun、Task ID 和 Attempt 关联标准 Build 验收及 Repair 决策。对固定样本统计初始 Prompt 字节、累计输入/输出 Token、模型轮次、步骤工具次数、业务工具次数及返回量、总耗时、Repair 次数、验收通过率和最终完成率。离线脚本只能验证时序、权限和验收；真实模型用量、耗时及质量必须在相同项目、需求、模型和模板条件下独立复测。
