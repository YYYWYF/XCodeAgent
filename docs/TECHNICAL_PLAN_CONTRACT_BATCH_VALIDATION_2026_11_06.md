# 大 Contract 内部分批生成验证（2026-11-06）

## 实现结果

Contract 仍按业务能力划分。接口较多或估算输出过大时，在同一 Contract 内生成子片段，由代码拼回完整 Contract；最终产物结构、Markdown 编辑同步和显式用户确认门不变。

当前策略：

| 判断项 | 规则 |
| --- | --- |
| 整份生成 | 接口数不超过 12，且估算输出不超过 `min(6000, 输出预算 × 40%)` |
| 触发分批 | 上述任一条件超限，或整份生成被 SDK 明确报告长度截断 |
| 批次目标 | `min(3500, 输出预算 × 25%)`，接口每批最多 6 个 |
| 大对象 | 冻结完整字段/必填清单，最多 24 字段一组且受批次目标约束 |
| 截断恢复 | 只二分失败接口/字段组，在下一次现有外层尝试执行 |
| 普通错误 | JSON/类型/引用错误重试原任务；网络异常交回原恢复流程 |

当前 `Settings.from_env().default_max_tokens=16384`，所以整份目标 6000、批次目标 3500。此处是输出预算及保守体量估算，不是模型输入窗口或供应商真实 token 计数。策略值仍需随真实业务样本校准。

小 Contract 不增加 Schema 目录请求。大 Contract 先冻结唯一 Schema 名称、根属性、字段与必填集合、引用及接口请求/响应引用；每个定义只生成一次。接口批次没有 Schema 写权限，携带当前动作与 Schema 引用闭包。完整定义按目录顺序组装，执行原 Contract 与 TechnicalPlan 校验，不请求模型重写整个合并结果。

成功片段先落盘，再推进下一批；内容摘要键不依赖批次序号。目录修复同时失效受影响 Contract 的 Schema 目录、字段、接口及缩批标记，未变化模块保留。所有重试仍受原三轮预算约束。单个字段、单个接口或 Schema 目录仍超出输出上限时显式失败，不提交半成品。

## 本轮文件

- 新增 `Backend/app/agents/main/technical_plan_contract_budget.py`：预算估算、阈值、装箱及稳定片段身份。
- 新增 `technical_plan_contract_batches.py`：Schema/接口子片段编排、同身份拼接及明确截断后的二分。
- 新增 `technical_plan_contract_batch_prompts.py`、`technical_plan_contract_batch_validation.py`：冻结目录、字段所有权、批次动作裁剪、引用闭包与严格局部校验。
- 新增 `technical_plan_fragment_output.py`：保留 SDK 长度终止信号，拒绝内部批次重复 JSON 键。
- 更新 `technical_plan_generation.py`、`planner.py`、`technical_plan_catalog_repair.py`：接入实际输出预算、流式/非流式终止信号及依赖失效。
- 新增 `Backend/tests/test_technical_plan_contract_batches.py`、`test_technical_plan_contract_batch_recovery.py`；更新 `test_technical_plan_catalog_repair.py`。
- 更新 `docs/CODEBASE_INDEX.md`、`docs/PRODUCT_UI_TECHNICAL_PLANNING.md` 及本记录。

## 自动验证

技术规划前次 31 个专项模块，加上两个批次模块，共 **33 模块、405 项测试通过，执行器退出码 0**。

本轮新增 16 项测试，覆盖 12/13 数量阈值、体量与低预算阈值、小 Contract 一次调用、共享 Schema 单次定义、70 字段大对象分组、循环引用闭合、错误引用与重复 JSON 键拒绝、失败批次精确重试、三轮预算不膨胀、小 Contract 截断转分批、字段/接口二分、网络失败磁盘恢复、目录修复撤销全部受影响子片段，以及 SDK 流式/非流式终止原因。

进程退出用例在第一接口批次持久化后，于第二请求前执行 `os._exit(73)`；父进程重新调用生成，跳过目录、Schema 及第一批接口，只生成剩余接口。此前不产生正式 TechnicalPlan 文件。

最终执行命令（在 `Backend`）：

```bash
PYTHONPATH=. .venv/bin/python \
  /Users/yifei/Coding/xcodeagent_test/technical-contract-batch-live-20261106-final/run-focused.py
```

该脚本运行真实 unittest suite，结束后显式关闭测试进程的全局 checkpoint 连接。直接运行 suite 时，405 项断言通过后，仍有测试持有的 SQLite worker 阻止进程退出；显式清理后命令正常退出，不需要强制成功退出或修改产品逻辑。最终日志：`/Users/yifei/Coding/xcodeagent_test/technical-contract-batch-live-20261106-final/focused-tests.log`。

另外通过：本轮变更 Python 文件的 `py_compile`、`git diff --check`。运行中的 8000 后端已重启加载新代码，`curl -fsS http://127.0.0.1:8000/health` 返回 `status=ok`，模型为 `deepseek-v4.1-flash`；健康证据保存为测试目录的 `main-health.json`。

## 真实模型与 AG-UI 验证

最终隔离目录：

`/Users/yifei/Coding/xcodeagent_test/technical-contract-batch-live-20261106-final`

输入是一个订单页面、十八个已确认业务查询视图。RequirementSpec/ProductPlan/UiManifest 作为确认过的测试上游，技术节点通过官方 `@ag-ui/client` 的 `HttpAgent` 请求 `/application-page-planning/run`。没有替换模型、完整校验器或持久化实现，没有预置目录、Schema、接口或页面响应。

真实模型共返回七个完整阶段响应：共享实体/接口目录、Schema 目录、字段组、三次接口批次、单页技术引用。核对结果：

| 结果 | 实际证据 |
| --- | --- |
| 模型 | deepseek-v4.1-flash |
| 耗时 | 约 51.7 秒，仅本次样本，不作性能对比结论 |
| 目录 | 1 个业务 Contract，18 个 Endpoint |
| 接口批次 | 6 + 6 + 6 |
| Schema | 共享 `order_view`，字段只生成一次 |
| 最终产物 | 同一个 Contract 身份，18 个 Endpoint，单份 Markdown/内部 JSON |
| 全量校验 | `_project_plan_validation_errors=[]` |
| 确认门 | next=`technical_planning_review`，存在 interrupt，状态 `pending_user_confirmation` |
| 候选目录 | 完整正式提交后清理 |
| AG-UI | RUN_STARTED → 状态/进度 → 待确认 → RUN_FINISHED |

可复核证据：`manifest.json`、`live-evidence.json`、`model-tokens.txt`、`model-fragments.json`、`verification.json`，及隔离工作区的正式产物与 checkpoint。`llmTokenEvents` 是 SDK 文本事件数量，不能作为模型 token 用量。

首次真实运行保留在 `technical-contract-batch-live-20261106`：虽然目标批次只有六个接口，提示仍携带整页十八个动作，模型连续三轮返回全部接口，校验正确拒绝，未到产物确认。修复为按当前接口裁剪业务动作，并在提示前后明确列出本批 ID，同时使错误明确反馈目标集合。补充复现回归后，重新创建隔离工作区并完成上述成功验证。失败运行不计入成功证据。

## 证明边界

- 真实模型证明的是该十八接口样本完整经过 AG-UI、分批、全量校验、正式保存及原用户确认门。
- 截断、连接失败及硬退出的注入测试证明对应编排和持久化行为；本次没有强迫远端供应商实际输出到长度上限。
- 本轮只修改后端生成逻辑，没有新增公开接口或前端变更，因此未重复执行前端构建或 Electron UI 验证。上游 UI 使用确认夹具，不将其当作真实页面渲染证据。
- 全应用实体/精简接口目录及大 Contract 的 Schema 目录仍是单次请求；极端目录本身过大时显式失败，本轮未增加目录级递归生成。
