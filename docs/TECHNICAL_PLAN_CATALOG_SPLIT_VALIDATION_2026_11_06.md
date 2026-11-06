# 全局技术目录分段生成验证（2026-11-06）

## 变更

全应用的共享实体字段、接口目录与动作映射不再由一个模型请求同时输出。当前流程：

1. `catalog_registry`：一次冻结全局实体和业务 Contract 的轻量身份，以及实体/页面归属；不生成字段、接口或绑定。
2. `catalog_entity`：每个实体独立返回完整语义字段，共享实体只展开一次。
3. `catalog_endpoints`：每个业务 Contract 独立返回精简接口目录；每个目录提交前检查与已完成目录的全局 ID、规范化 HTTP 路由冲突。
4. `catalog_bindings`：每页独立返回业务动作/步骤到现有接口的映射，逐页完整覆盖；静态页跳过该请求。接口能力不足可立即报告 `catalog_issue`。
5. 代码组装并完整校验全局目录，一次原子发布后，继续既有 Contract 详情/大 Contract 分批、逐页技术引用、完整计划提交和用户确认。

目录子片段先校验后同步持久化，下一请求等持久化完成才开始。部分目录不作为完整 `catalog` 发布，也不触发完整 Contract 生成。最终发布同时撤下 `catalog-part:` 子引用；发布失败时保留已完成目录子片段供恢复。

修复只重新生成相关实体字段、业务能力目录和页面映射，其他已生成事实在校验后复用。若修复过程中再次报告缺口，内部 `catalog_repair` 同时保存最新候选 `catalog`、最初已发布基线 `baseline` 和当前 `issue`；最终失效始终对照基线，防止第一轮改动影响的旧 Contract/页面被遗漏。未实际修复的目录不发布，撤下本轮子候选，下一次原有外层尝试必须真正重新请求。维持三轮总预算。

没有新增公开接口、确认门或前端状态。正式 TechnicalPlan JSON/Markdown 结构不变，没有旧整目录模型包络的兼容分支。

## 文件范围

- 新增 `Backend/app/agents/main/technical_plan_catalog_parts.py`：分段编排、修复复用、原子完整目录发布。
- 新增 `technical_plan_catalog_parts_prompts.py`：全局身份、单实体、单 Contract、单页的提示与上下文裁剪。
- 新增 `technical_plan_catalog_parts_validation.py`：严格响应、冻结归属、局部/全局约束。
- 更新 `technical_plan_generation.py`：接入新目录编排与严格子片段 JSON 解析；`technical_plan_generation_prompts.py` 移除旧整目录生成提示。
- 更新 `technical_plan_catalog_repair.py`：使用最初发布基线计算下游失效。
- 新增 `Backend/tests/technical_plan_catalog_fixture.py`、`test_technical_plan_catalog_parts.py`；更新模块生成、持久化、目录修复、真实 Graph 及 Contract 分批测试夹具。
- 更新 `docs/CODEBASE_INDEX.md`、`docs/PRODUCT_UI_TECHNICAL_PLANNING.md` 和本记录。

## 自动验证

沿用前次 33 个专项测试模块，新增 `tests.test_technical_plan_catalog_parts`，共 34 个模块。本轮新增九项测试，覆盖：

- 全局响应没有字段/接口展开，实体、接口、映射按所有权分别生成；不向提示投射应用凭据。
- 当前身份协议拒绝旧整目录包络、空业务目录、未知实体/页面归属。
- 第二 Contract 与首 Contract 的占位参数路由冲突，拒绝缓存错误目录；重试保留首目录。
- 实体字段重复或页面映射漏步骤时，只重试当前对象，保留其他已完成片段。
- 子进程在全局身份与首实体提交后 `os._exit(73)`，新进程从第二实体续跑，之前没有完整目录或正式计划。
- 完整目录原子发布失败后，已验证的八个子片段仍可恢复，不重新请求模型生成目录。
- 连续目录缺口相对于最初已发布基线失效，前一次改动影响的缓存不被遗漏。
- 在目录映射阶段提前发现缺口，先修目录再生成完整 Contract；未受影响实体和目录复用。

执行入口（`Backend`）：

```bash
PYTHONPATH=. .venv/bin/python \
  /Users/yifei/Coding/xcodeagent_test/technical-catalog-split-live-20261106/run-focused.py
```

脚本在正常 unittest suite 结束后关闭测试持有的进程级 checkpoint 连接，避免 SQLite worker 阻止测试进程退出。首次主回归 413 项通过，补充前置目录缺口用例后再次执行，最终 **414 项全部通过，执行器退出码 0**；日志见证据目录 `focused-tests.log`。首次升级夹具时发现并修正了旧响应包络、页面映射次序和无效修复重试数量的测试预期，未新增产品兼容分支。

另外执行本轮 Python 源文件 `py_compile`、`git diff --check` 和运行后端 `/health` 检查。运行中的 8000 后端在收尾时重新加载当前代码，健康证据保存为 `main-health.json`。

## 真实模型、AG-UI 与正式产物

证据目录：

`/Users/yifei/Coding/xcodeagent_test/technical-catalog-split-live-20261106`

从已确认 RequirementSpec/ProductPlan/UiManifest 测试上游出发，以官方 `@ag-ui/client` 的 `HttpAgent` 调用真实 `/application-page-planning/run`。没有替换模型、目录内容、完整校验器或存储实现。模型为 `deepseek-v4.1-flash`。

| 检查项 | 实际结果 |
| --- | --- |
| 页面 | 订单列表/详情、客户列表/详情，共 4 页 |
| 目录响应 | 1 个身份、2 个实体字段、2 个接口目录、4 个页面映射 |
| 全局身份响应 | 433 个紧凑 JSON 字符，没有 fields/endpoints/action_bindings |
| 九个目录响应体量 | 433、434、300、503、534、352、233、253、252 字符 |
| 全流程模型响应 | 15 次：上述 9 次，加 2 个完整小 Contract、4 页技术引用 |
| 最终目录 | 2 个实体、2 个业务 Contract、8 个 Endpoint |
| 耗时 | 约 74.6 秒，仅本次样本，不作加速结论 |
| 全量校验 | `_project_plan_validation_errors=[]` |
| 正式文件 | 单份技术规划 Markdown 与内部 JSON 已保存 |
| 确认门 | `next=technical_planning_review`，存在 interrupt，`pending_user_confirmation` |
| 候选目录 | 完整正式提交后清理 |
| AG-UI 生命周期 | RUN_STARTED、状态/进度、待确认、RUN_FINISHED 完整 |

字符数不是供应商 token 数；增加串行请求是换取单次输出更小和失败局部恢复，不承诺整体延迟下降。`llmTokenEvents` 同样只是 SDK 文本事件数量。

可复核 `manifest.json`、`live-evidence.json`、`model-tokens.txt`、`model-fragments.json`、`verification.json`、`focused-tests.log`，以及隔离工作区的正式产物和 checkpoint。

## 验证边界

- 本轮真实模型证明四页面样本完整经过新目录生成、AG-UI、完整校验、正式保存和原用户确认门；故障注入验证局部恢复、进程退出和原子发布失败。
- 轻量全局身份、单个实体字段、单个 Contract 的精简接口目录各仍一次生成；本轮没有增加它们内部的递归分批。极端单对象仍受模型输出预算约束，失败不会提交半份正式产物。
- 只修改后端生成逻辑与测试/文档，没有前端改动，未重复运行前端构建和 Electron UI 验证。上游 UI 是确认夹具，不作为页面渲染证据。
