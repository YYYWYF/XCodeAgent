# 二次正式修订业务骨架移除：实施与验证记录

日期：2026-10-29。分支：`dev_agent`，基线 HEAD：`fe9d47ce`。实施依据为同目录的 `xcodeagent_remove_revision_backend_scaffold_codex_plan.md`；该文件是用户原有未跟踪文件，未修改。

## 修改文件

| 操作 | 文件 | 目的 |
| --- | --- | --- |
| 修改 | `Backend/app/graph/application_planning_workflow.py` | 删除注入调用、完整包装函数、专属 import、误导进度和注释；保留确认 checkpoint、CAS、模板能力更新、异常恢复与续接签发 |
| 删除 | `Backend/app/services/template_scaffold_injection.py` | 移除全部业务骨架渲染、写入和推算预置路径逻辑 |
| 修改 | `Backend/app/graph/nodes/tasks.py` | 应用级 Build Context 移除废弃字段及 import |
| 修改 | `Backend/app/services/build_context_resolver.py` | Page/Endpoint Build Context 移除废弃字段及 import |
| 修改 | `Backend/app/agents/data_source/prompt_context.py` | Bootstrap/Endpoint 实现契约移除废弃字段，保留技能、授权路径及权限约束 |
| 修改 | `Backend/app/builtin_skills/springboot-template-modification-boundary/SKILL.md` | 按真实文件、当前 Task、授权路径创建或最小修改，不再假定业务文件已生成 |
| 修改 | 上述 Skill 的 `references/module-layout.md` | 目录树改为按需参考，区分数据库与纯外部 API 职责 |
| 修改 | 上述 Skill 的 `references/{entity,repository,dto,controller}-template.md` | 清理业务代码已提前生成的前提，保留代码示例及受管边界 |
| 修改 | `Backend/app/builtin_skills/springboot-backend-generate/references/database/layer-implementation.md` | 额外检索发现两处平台已创建 Mapper XML 的说法，改为授权任务创建或复用，保留占位 XML 与自定义 SQL 门槛 |
| 删除 | `Backend/tests/test_template_scaffold_injection.py` | 删除废弃模块专属测试 |
| 修改 | `Backend/tests/test_application_page_planning.py` | 移除旧 mock；增加两个修订分支 × NO_CHANGE/CHANGED 的业务 Java/XML 字节保护、无新增业务文件及真实续接签发/消费测试 |
| 修改 | `Backend/tests/test_data_source_generation_prompt.py` | 验证实现契约与执行任务包无废弃字段，并保留 allowed_paths/change_scope |
| 修改 | `Backend/tests/test_page_build_context_resolver.py` | 验证 Page、Endpoint、Application Context 无废弃字段，接口映射及 Unit 信息保留 |
| 修改 | `docs/CODEBASE_INDEX.md` | 同步当前职责与调用链 |
| 修改 | `docs/DAG_TASK_GENERATION_UNIT_CANDIDATE_PLAN.md` | 工作区文件事实来自 WorkspaceSnapshot，不再引用推算清单 |
| 修改 | `docs/FRONTEND_SHELL_PREREQUISITE.md`、`Backend/tests/DAG_PLANNING_BASELINE.md` | 仅移除已删除生成器的测试命令引用 |
| 新增 | 本记录 | 保存命令、验证事实及未覆盖边界 |

## 调用链与边界

当前链路：`TechnicalPlan 确认 → template_reconcile → _reconcile_revision_template_capabilities → issue_revision_continuation`。

`Backend/app` 对旧模块、两个注入入口、预置路径函数和 `prebuilt_files` 的精确检索无匹配。目标修改边界 Skill 对旧预置声明的指定检索无匹配。测试保留字段名仅用于“不包含”断言；实施方案保留旧符号用于描述删除目标，不是运行合同。

`git diff --exit-code` 检查下列边界未修改：`services/template_reconcile/`、`services/workspace_bootstrap/`、`build_unit_skeleton.py`、`unit_generation_requirements.py`、`backend_task_manifest.py`、`agents/data_source/{agent,generator}.py`、`graph/subgraphs/build.py`、`Frontend/`。无替代注入器、兼容字段或工作区历史文件清理。

## 已执行检查

以下 Python 命令均从 `Backend` 执行，使用项目 `.venv/bin/python`。后续测试设置 `LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false`，避免测试观测数据外发。

### 方案主回归：147 项，6 失败、2 错误

```bash
.venv/bin/python -m unittest \
  tests.test_application_page_planning \
  tests.test_template_reconcile_finalization \
  tests.test_data_source_generation_prompt \
  tests.test_page_build_context_resolver \
  tests.test_unit_generation_requirements \
  tests.test_backend_unit_prompt_merge \
  tests.test_build_subgraph_scheduler \
  tests.test_code_graph_agent_scope \
  tests.test_unit_task_prompt
```

在 `/private/tmp/revision-scaffold-baseline-hjqld12o` 通过 `git archive HEAD Backend` 隔离提取未修改基线，使用同一虚拟环境重跑同组：145 项，同样 6 失败、2 错误。增加的两项测试均通过。

| 基线同样失败的用例 | 实际原因 |
| --- | --- |
| `test_creation_requirements_expose_clarification_tool` | 断言期望需求提示，实际捕获授权证据分类提示 |
| `test_product_confirmation_projects_only_draft_markdown` | 旧夹具未投影出预期确认产物 |
| `test_requirement_confirmation_status_drives_lifecycle_mapping` | 预期 awaiting 状态，实际为 generating 状态 |
| `test_routes_cover_design_and_independent_planning_stages` | 预期 requirements_review，当前路由为 product_planning |
| `test_stale_requirements_resume_is_rejected_during_technical_planning` | 预期拒绝，但当前明确重入规则未抛异常 |
| `test_technical_planning_cannot_run_before_stage_entry` | 预期文案“规划阶段”，当前为“计划阶段” |
| `test_streaming_requirements_merge_chunked_ask_user_tool_call` | FakeStreamingModel 无授权分类需要的 invoke 方法 |
| `test_frontend_execution_prompt_requires_exact_resources_import` | TemplateState 夹具 revision=r1 不符合当前格式 |

这些失败已调查并在原始 HEAD 复现，未通过修改无关生产行为或旧测试来扩大补丁范围。基线日志：`/private/tmp/revision-scaffold-baseline-hjqld12o/baseline-tests.log`。

### 补充回归：30 项，4 错误

```bash
.venv/bin/python -m unittest \
  tests.test_build_task_planner tests.test_build_unit_skeleton \
  tests.test_prepare_build_tasks_guard tests.test_build_task_reuse_workspace \
  tests.test_builtin_skills tests.test_application_template_generation \
  tests.test_generation_workspace_paths tests.test_auth_deterministic_execution_integration
```

同样在隔离 HEAD 基线重跑，结果相同：

- `test_build_task_planner` 导入已不存在的 `app.agents.main.task_preparer`。
- `test_prepare_build_tasks_guard` 导入已不存在的 `_handle_build_task_plan_confirmation`。
- `tests.test_application_template_generation` 模块不存在。
- `test_data_source_agent_receives_scoped_bundle_without_host_path_in_prompt` 的旧任务夹具缺少当前必需的唯一确认 Endpoint API 设计。

### 聚焦回归：146 项全部通过

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m unittest \
  tests.test_application_page_planning.ApplicationPagePlanningTests.test_technical_planning_confirmation_validates_without_detail_node \
  tests.test_application_page_planning.ApplicationPagePlanningTests.test_design_revision_confirmation_commits_before_template_reconcile \
  tests.test_application_page_planning.ApplicationPagePlanningTests.test_revision_reconcile_preserves_business_sources_and_issues_continuation \
  tests.test_application_page_planning.ApplicationPagePlanningTests.test_reconcile_retry_reuses_confirmed_plan_without_confirm_interaction \
  tests.test_application_page_planning.ApplicationPagePlanningTests.test_template_reconcile_retry_rebuilds_server_owned_thread_and_request \
  tests.test_template_reconcile_finalization tests.test_data_source_generation_prompt \
  tests.test_page_build_context_resolver tests.test_unit_generation_requirements \
  tests.test_backend_unit_prompt_merge tests.test_build_subgraph_scheduler \
  tests.test_unit_task_prompt tests.test_build_unit_skeleton \
  tests.test_build_task_reuse_workspace tests.test_builtin_skills \
  tests.test_auth_deterministic_execution_integration
```

### 当前模板回归：21 项全部通过

旧文档引用的 `test_application_template_generation` 已不存在，使用当前 Bootstrap/V4/权限交付测试验证保留边界：

```bash
LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m unittest \
  tests.test_workspace_bootstrap_service tests.test_template_update_v4 \
  tests.test_template_update_v4_transaction tests.test_authorization_template_delivery
```

### 语法、服务及差异检查：通过

```bash
.venv/bin/python -m py_compile \
  app/graph/application_planning_workflow.py app/graph/nodes/tasks.py \
  app/services/build_context_resolver.py app/agents/data_source/prompt_context.py \
  tests/test_application_page_planning.py tests/test_page_build_context_resolver.py \
  tests/test_data_source_generation_prompt.py
curl -sS http://127.0.0.1:8000/health
git diff --check
```

health 在沙箱内首次连接失败，沙箱外重试及最终复查均返回 `status=ok`，内置修改边界 Skill 仍注册。无前端代码变更，未运行 pnpm build、Vite 或 Electron 验证。

## 真实环境覆盖与限制

源码保护测试使用真实本地文件、CAS 与续接签发/消费，模板引擎写入由 mock 隔离；不能据此宣称真实 V4 引擎安装通过。Authorization 确定性执行集成测试、来源分流与调度测试通过；真实模板服务 Generate/Login/Authorization 安装、数据库/外部系统连通及完整分层 Build 未验收。

### 真实模型对象阶段 smoke：数据库、外部 API 均通过

执行命令（从 `Backend`）：

```bash
.venv/bin/python /private/tmp/revision_scaffold_agent_smoke.py
```

沙箱内首次两种来源均遇到 `APIConnectionError`；按网络限制流程在沙箱外重跑，使用当前配置的真实模型与未改动的 `generate_data_sources_with_deep_agent()` 执行器。未 mock 模型或写文件工具。所有业务输入为合成夹具，工作区位于 `/private/tmp`，未修改用户业务工作区。

| 来源 | 模型终态 | 实际结果 |
| --- | --- | --- |
| database | completed | 在原本缺少 category 模块的工作区创建 CategoryInput.java、CategoryValue.java；全部写入在两个授权路径内，相邻 Existing.java 字节不变 |
| external_api | completed | 同样创建两个契约 DTO，全部写入在授权路径内，相邻 Existing.java 字节不变；未生成 PO/Mapper/Repository |

证据及文件：`/private/tmp/revision-agent-smoke-fxzk9sjb/report.json`、同目录 `database/backend/`、`external_api/backend/`。合成夹具使用现有测试中的当前 Endpoint Task/API Design，并通过实际任务包加载更新后的内置 Skill。

这验证了取消提前注入后，真实 Agent 能按授权创建缺失的对象层文件。没有执行生成 Java 的 Maven 编译、真实数据库操作、真实外部 HTTP 调用、完整 repository/upstream/service/controller 链或真实 V4 服务安装，因此 AC-06/07/08 的完整端到端验收仍未完成。当前 smoke 未准备真实应用模板、数据库绑定和可调用的上游服务；静态及 mock 回归不能替代这些验收。
