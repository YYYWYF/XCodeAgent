# TechnicalPlan 目录 P0 修复与验证（2026-11-06）

## 本次修复

原问题：共享目录通过结构校验但漏接口时，后续页面不能新增接口；原有缓存始终复用目录，三次预算可能全部耗在无法修好的页面。

现在分两层处理：

1. 目录根对象增加内部 `action_bindings`，逐一声明 `(pageId, actionId, stepId) → endpointId`。确定性检查完整业务覆盖、重复、未知动作/步骤/接口以及 Contract 页面归属；业务页不能再用空目录进入后续生成，纯静态页仍可为空。
2. 页面独立核对接口能力。有效的 `catalog_issue` 撤下旧 catalog，保存前目录与缺口；原三轮预算下一轮先修目录。按 Contract、实体和页面实际引用计算失效，新目录与保留片段同批原子发布。普通绑定或格式错误仍只修当前页面，未知身份、非业务步骤或混合输出不能触发目录失效。

没有增加模型调用的嵌套重试、公开 AG-UI 接口或新的用户确认门。覆盖声明与待修复上下文不写入正式 TechnicalPlan。完整候选仍组装、编译、完整校验后保存单份 Markdown/内部 JSON，等待原生明确确认。

目录修复后会清除已解决的系统诊断，保留原始用户要求；修复输出未改变报告页面依赖时继续保留缺口，不把原错误目录再次当成成功结果。挂起的目录修复、已完成片段和失效结果均可在连接异常后由新调用从唯一候选目录恢复。

## 文件范围

- 新增 `Backend/app/agents/main/technical_plan_catalog_repair.py`：覆盖与缺口模型、覆盖校验、依赖失效计算。
- 修改 `technical_plan_generation.py`、`technical_plan_generation_prompts.py`、`technical_plan_generation_validation.py`：接入目录修复、隔离已解决诊断、区分报告与正常页面结果。
- 修改 `Backend/app/services/technical_plan_fragments.py`：同输入的整批原子替换，拒绝旧输入迟到写入。
- 新增 `Backend/tests/test_technical_plan_catalog_repair.py`；更新模块生成、真实 Graph 和持久化夹具三个测试文件。
- 同步 `docs/CODEBASE_INDEX.md` 与 `docs/PRODUCT_UI_TECHNICAL_PLANNING.md` 的当前内部合同。

## 自动验证

新增 8 项目录专项测试，覆盖：精确业务 action/step 覆盖、原空目录故障、语义缺口回到目录、无关模块复用、修复前后连接异常恢复、原样修复不清缺口及三轮预算、伪造/混合报告拒绝、原子替换失败、共享实体影响范围。真实 Graph 另增加一项，验证修复后仍提交完整文档并停在原确认门。

最终执行前次专项报告中的 30 个后端模块，再加 `tests.test_technical_plan_catalog_repair`，合计 **389 项通过**。完整模块清单见 [前次报告的自动验证命令](TECHNICAL_PLAN_MODULAR_GENERATION_VALIDATION_2026_11_06.md#自动验证)。测试日志为 `/tmp/xcodeagent-p0-final-tests.log`，另复制到本次真实测试证据目录。

本次 9 个变更 Python 文件通过 `py_compile.compile(..., doraise=True)`；`git diff --check` 通过。8000 后端已重启加载本次源码，`curl -fsS http://127.0.0.1:8000/health` 返回 `status=ok`。

## 真实模型故障场景

模型为 `deepseek-v4.1-flash`，通过官方 `@ag-ui/client` `HttpAgent` 和原 `/application-page-planning/run` 执行。上游为隔离的已确认四页测试输入；没有替换正式运行的模型、节点、解析器或校验器。

为了主动验证 P0，在内部候选目录人为删除“确认订单”接口，并将对应覆盖声明错误指向 GET；已完成订单列表、客户 Contract 与两页缓存，订单详情尚未生成。这是受控缺口候选，不能称为模型自然漏接口。

最终真实响应依次为：

| 顺序 | 实际模型输出 |
| --- | --- |
| 1 | 订单详情回报 `catalog_issue`：现有查询/创建接口无法确认已有订单 |
| 2 | 修复后的共享目录，新增 `confirm_order` |
| 3 | 重新生成订单 Contract |
| 4 | 重新生成订单列表绑定 |
| 5 | 生成订单详情绑定 |

耗时 **56.281 秒**。客户 Contract 与两页没有模型重生成，读盘结果与缓存完全相同。最终 `confirm_order` 绑定 `POST /api/orders/{orderNumber}/confirm`；2 实体、2 Contract、8 Endpoint、4 页面。独立重读 checkpoint 并执行 `_project_plan_validation_errors` 返回空错误，状态为 `pending_user_confirmation`，下一节点为 `technical_planning_review` 且存在原生 interrupt；候选目录已清理，没有模板或开发续接。

证据目录：`/Users/yifei/Coding/xcodeagent_test/technical-p0-catalog-live-20261106-final/`，含 `prepare-seed.py`、`seed-before-run.json`、官方 SDK 事件、逐次模型正文、`model-fragments.json`、`verification.json` 及正式文档。

## 正常首版与排障记录

另跑一轮未植入故障的四页首版：**42.010 秒**，实际依次生成目录、2 Contract、4 页面，内部覆盖声明通过校验，完整生产校验为空，仍停在原确认门。证据位于 `/Users/yifei/Coding/xcodeagent_test/technical-p0-normal-live-20261106/`。

故障场景前几轮真实试验没有记作通过：模型最初照抄错误覆盖声明，将查询接口绑定到确认操作；随后虽报告并补出了接口，但旧缺口诊断继续回灌页面，引发了重复误报。因此最终版本让页面独立核对接口用途，不提供未验证的覆盖声明作为绑定答案，并在目录修复后隔离旧诊断。对应过程证据保留在同级 `technical-p0-catalog-live-20261106`、`-recheck` 和 `-independent-review` 目录；最终五阶段通过结果才是本报告的故障验收证据。

## 验证边界

本次确定性保证的是业务覆盖、报告身份、预算、缓存一致性和确认门；“接口业务语义是否足够”仍依赖模型判断，不能据单轮成功声称所有自然语言业务错误都会被发现。本次验证了上述具体缺接口场景。

本次只改后端与文档，未重跑 Electron 界面或前端构建；没有新增前端行为或公开传输合同。未测试其他模型、Windows、并发修改同一工作区或断电。隔离测试后端已停止，正式 8000 后端保持运行；本仓库未提交或推送。
