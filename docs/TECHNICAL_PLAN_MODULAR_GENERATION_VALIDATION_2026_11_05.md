# 技术规划分段生成验证（2026-11-05）

本文件记录 11 月 5 日首版的验证快照；其中持久化、既有失败和 Electron 验证缺口已在后续处理，当前结果见 [11 月 6 日修复与完整专项验证](TECHNICAL_PLAN_MODULAR_GENERATION_VALIDATION_2026_11_06.md)。

首版生成在原 TechnicalPlan 节点内部按共享目录、Contract、页面拆分；最终正式文档、AG-UI 接口和用户确认门保持原合同。当时串行生成，校验失败复用已完成片段；模型连接异常或进程退出前尚未提交的片段不保证持久化。

## 已通过

从 `Backend` 执行以下命令，67 项测试通过，其中新增分段生成测试 13 项：

```sh
.venv/bin/python -m unittest \
  tests.test_technical_plan_modular_generation \
  tests.test_technical_plan_modular_workflow \
  tests.test_technical_plan_fragment_editor \
  tests.test_technical_plan_fragment_workflow \
  tests.test_technical_plan_repair_feedback \
  tests.test_technical_plan_json_recovery \
  tests.test_technical_planning_stage_retry \
  tests.test_technical_planning_recovery_journey
```

覆盖多实体、多 Contract、多页面共享接口、页面内同名 action、组合业务步骤、契约本地 Schema、截断与错误结构、身份和路由冲突、失败片段复用、输入变化失效、模块间取消、完整候选局部修复，以及真实 Graph/AG-UI/checkpoint 的组装、落盘和确认门。模型响应由夹具控制，不代表在线模型验收。

生产 Graph 测试验证只生成一份待确认 Markdown/JSON、不写半份正式计划、成功和失败的公开 AG-UI 状态都不含片段缓存。重新打开 checkpoint 后，同输入的局部校验失败可以从未完成模块继续。

恢复夹具同步了当前 `confirmed` 上游状态、新增生成参数和唯一 `state` 公开投影；没有修改生产 UI 跳过或恢复判定行为。

13 个变更 Python 文件通过 `py_compile.compile(..., doraise=True)`；`git diff --check` 通过；`curl -fsS http://127.0.0.1:8000/health` 返回 `status=ok`。

## 扩展回归中的既有失败

以下两个扩展命令仍有失败：

```sh
.venv/bin/python -m unittest tests.test_technical_plan_modular_generation tests.test_technical_plan_modular_workflow tests.test_technical_plan_fragment_editor tests.test_technical_plan_fragment_workflow tests.test_technical_plan_repair_feedback tests.test_technical_plan_json_recovery tests.test_product_planning_retry tests.test_technical_planning_stage_retry

.venv/bin/python -m unittest tests.test_product_technical_planning tests.test_authorization_manifest tests.test_workflow_projection tests.test_workflow_ag_ui tests.test_application_planning_recovery_projection
```

第一组 90 项中有 1 项失败；第二组 142 项中有 2 项断言失败和 1 项异常。以下四项均在未修改的 HEAD `4ac7bbd63663aa1d66a24569449751f8f22fa159` 的独立源码快照中复现，未在本次变更扩大修复范围：

| 测试 | 当前失败原因 |
| --- | --- |
| `tests.test_product_planning_retry.ProductPlanningRetryTests.test_ui_design_can_be_skipped_and_returns_completed` | 用例期望 `completed`，当前 UI 节点返回 `requires_user_input`。 |
| `tests.test_product_technical_planning.ProductTechnicalPlanningTests.test_skipped_ui_uses_product_expected_results_for_local_bindings` | 用例期望跳过 UI 后从产品结果补充 `localEffect`，当前结果为空。 |
| `tests.test_workflow_projection.WorkflowProjectionTests.test_build_retry_hint_does_not_hide_explicit_build_error` | 构建失败提示与用例期望的重试提示不同。 |
| `tests.test_application_planning_recovery_projection.ApplicationPlanningRecoveryProjectionTests.test_user_ended_execution_reopens_as_separate_end_receipt` | 当前回执缺少用例读取的 `summary.planControl`。 |

## 真实模型补充验证（11 月 5 日晚运行，11 月 6 日完成核对）

已重启本地 8000 后端加载本次源码，使用原配置的 `deepseek-v4.1-flash`。在独立目录 `/Users/yifei/Coding/xcodeagent_test/technical-modular-live-20261105/` 准备订单列表、订单详情、客户列表、客户详情四页面上游，通过已安装的官方 `@ag-ui/client` `HttpAgent` 调用真实 `/application-page-planning/run`，没有替换模型、校验器或工作流节点，也没有自行解析 SSE。

需求、ProductPlan 和 UI Manifest 是确定性准备的已确认测试输入，不包含真实业务数据；本次在线范围从技术规划生成开始，未覆盖上游文档或 UI 设计稿的模型生成。

运行入口为该目录的 `prepare.py` 和 `run-live.mjs`。实际模型请求开始于北京时间 `2026-11-05 23:50:03`，完整 AG-UI 调用耗时 **36.689 秒**，得到 **2 个实体、2 个 Contract、8 个 Endpoint、4 个页面引用**。真实响应按阶段拆分如下：

| 顺序 | 模型输出 | 响应字符数 | 结果 |
| --- | --- | ---: | --- |
| 1 | 共享实体与接口目录 | 3550 | 通过 |
| 2 | `order_contract` | 2874 | 通过 |
| 3 | `customer_contract` | 5427 | 通过 |
| 4 | `orders` 页面绑定 | 685 | Endpoint 依赖重复，局部校验拒绝 |
| 5 | `orders` 页面绑定 | 565 | 通过；没有重生成目录或两个 Contract |
| 6 | `order_detail` 页面绑定 | 442 | 通过 |
| 7 | `customers` 页面绑定 | 457 | 通过 |
| 8 | `customer_detail` 页面绑定 | 458 | 通过 |

这轮真实模型自然产生了一次错误，验证了只重试失败页面的行为，不是人工注入错误。完整候选组装后没有进入额外的文件编辑修复。

独立重新读盘调用 `_project_plan_validation_errors`，返回空错误列表。另外明确检查：

- 四页面完整覆盖，两个分页响应只包含 `total/pageSize/current/list`，参数包含 `current/pageSize`。
- `confirm_order` 无请求体，`rename_customer` 引用存在的非空请求 Schema。
- 新增订单 sequence 恰好绑定 `save_order/reload_orders` 两个业务步骤，不把本地提示步骤编为 API。
- 修复后的页面没有重复 Endpoint 依赖；公开 AG-UI 快照不包含内部片段缓存。
- 原生 checkpoint 的下一节点为 `technical_planning_review` 且存在 interrupt，产物为 `pending_user_confirmation`；成功后内部片段缓存为空，没有模板或开发续接。
- 完整 Markdown 为 6331 字节，JSON 为 19147 字节；没有逐模块正式文件。

随后再次经官方 AG-UI 客户端调用只读 `applicationPlanningRecovery.get`，仍返回 `awaiting_user` 与 `technical_plan_confirmation`，没有重新调用模型或消费确认。

证据保存在隔离测试目录：`live-evidence.json`（SDK 解析后的事件）、`model-tokens.txt`、`model-fragments.json`（逐次模型响应）、`recovery-evidence.json`、`verification.json`（完整校验、数量、文档摘要与确认门检查），以及 `workspace/.devagentstudio/plans/technical-plan.md|json`。

Electron 中直接添加并打开了该隔离工作区。测试夹具的应用身份已对齐 Electron 添加项目时分配的 ID，未更改技术规划内容。界面停在计划阶段，开发阶段不可进入。由于生成请求由 SDK 脚本发起，没有 Electron 本地聊天记录，界面显示“暂无会话记录”；因此这只验证 Electron 入场和阶段门禁，**没有验证确认卡片显示、按钮操作或 Electron 点击发起整轮生成**。未使用浏览器中的 Vite 页面替代 Electron。

## 当前未验证范围

- 未覆盖需求、ProductPlan、UI 设计稿生成，以及技术规划确认后的模板、代码开发和完整应用运行。
- Electron 确认卡片交互和完整界面发起流程尚未验证；本次没有前端源码变更，未运行前端构建。
- 未验证 Windows、其他模型或并发生成；本次仅证明上述单轮真实场景，不与旧整份生成比较速度或费用。
- 当前后端已加载新代码且 `/health` 健康；未提交或推送 Git 变更。
