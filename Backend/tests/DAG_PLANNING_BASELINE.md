# T0.1 DAG Planning Regression Baseline

本基线区分“后续重构必须保持的正确行为”和“旧实现现状回归”。只修改测试；
不宣称已经实现全局 G1–G14，也不以当前实现反向定义新 Contract。

## 新增正式 Contract 基线

共享夹具：`dag_planning_baseline_fixtures.py`。使用当前 TechnicalPlan 的 `pages`、
API Contract 和已确认实体绑定，`project_plan` 是带 PageImplementationContract 的运行时投影。
正式 TechnicalPlan 门禁夹具不持久化派生的 PageImplementationContract。
所有文件写入临时工作区；模型调用与模板工程准备在节点集成测试中隔离，
Unit Skeleton、Build Context、acceptance、Task Graph 和 Artifact Gate 使用真实实现。

| 场景 | 测试入口 | 必须保持的行为 |
| --- | --- | --- |
| empty baseline | `test_empty_baseline_compiles_complete_graph_without_mutating_inputs`、`test_empty_workspace_has_no_planning_baseline` | 不虚构历史 DAG；按当前 scope 完整编译，不改写输入 |
| existing confirmed baseline | `test_confirmed_baseline_preserves_tasks_evidence_and_skeleton_on_reuse`、`test_workspace_revision_refresh_keeps_confirmed_task_contracts`、`test_persisted_confirmed_dag_reaches_skeleton_and_build_context` | 确认 DAG 能从正式文件进入骨架，保留任务、状态、验收和来源 |
| page / endpoint scope | `test_context_and_skeleton_agree_on_direct_target_units` | 只投射直接 Endpoint 与实体；后端 bootstrap 前置；不创建普通 database Unit |
| static scope | 同上及 acceptance、execution 测试 | 静态页面依赖 frontend data Unit；不生成 backend / API adapter 任务 |
| authorization scope | `test_authorization_compile_keeps_platform_slices_and_exact_any_of` | 平台权限切片覆盖候选伪造字段；页面操作资源与 Controller 精确 Endpoint/ANY-OF 对齐 |
| Task Graph | `test_task_graph_preserves_local_order_and_parallel_frontend_backend`、`test_invalid_local_dependencies_remain_visible` | 同 Unit 依赖、跨 Unit 编译及前后端并行关系正确；环和缺失依赖阻断且不丢任务 |
| Acceptance Compile | `test_acceptance_compiles_exact_paths_and_formal_endpoint_sources`、`test_business_acceptance_fingerprint_changes_only_with_related_formal_input` | 工程路径、页面结构、API/schema、业务来源和确定性指纹正确；无关正式输入不污染当前检查 |
| retained contract | `test_preserved_confirmed_contract_is_not_recompiled_for_another_scope` | 调用者显式提交完整任务集合并标记保留的任务时，旧合同不被新 scope 覆盖；此测试不定义 Scope Assembly 的替换规则 |
| 正式 Artifact Gate | `test_formal_gate_accepts_confirmed_artifacts_and_explicit_ui_skip`、`test_unconfirmed_or_missing_formal_artifact_blocks_before_model_call` | 四类正式上游逐项校验；UI 可明确跳过；缺失/未确认时不得调用模型 |
| 新 DAG 确认 | `test_prepare_pipeline_stops_at_confirmation_with_empty_or_confirmed_input` | 真实 prepare 链路消费空或 confirmed 基线；新生成 DAG 必须重新等待确认 |
| Build scope | `test_build_execution_scope_excludes_unrelated_tasks_and_reuses_completed_prerequisites` | 切片包含目标与前置闭包，排除无关页面；已完成前置不进入 pending 执行集合 |
| Build authority | `test_build_reads_confirmed_file_instead_of_checkpoint_or_pending_file`、`test_build_blocks_without_formal_plan_even_with_confirmed_checkpoint`、`test_build_gate_rejects_unconfirmed_failed_invalid_and_wrong_scope`、`test_build_run_binds_confirmed_digest_and_rejects_later_drift` | Build 只接受正式 confirmed、ready、合法且 scope 匹配的 DAG；绑定副本及摘要，漂移阻断 |

`static scope` 指静态实体支持的页面构建；没有新增 `type=static` API。
骨架保留 `frontend:shell`，候选不伪造 shell、菜单或路由任务。
权限基线验证现有 Overlay/acceptance，不断言未来 deterministic auth-guard 的生成或写入实现。

## Legacy 现状及未完成的目标

以下仍运行在用户指定的现有回归中，但不属于新增正式 Contract：

| 旧行为 / 测试 | 分类与后续约束 |
| --- | --- |
| `tasks._existing_build_task_plan` 优先有效 checkpoint，且未限定 confirmed | 与 G1 冲突；新增测试仅验证 confirmed 文件可用，不验证 pending/checkpoint 可作 baseline。G1 排他性尚未实现 |
| `_merge_prepared_scope_tasks` / `_replaceable_unit_ids` 的 Scope replacement | 与 G3 append-only 目标不同；不新增删除或替换历史任务的断言 |
| `test_build_task_planner.test_duplicate_task_ids_are_made_unique_and_parallel_batch_is_recorded` | 自动 rename 的现状；不是未来候选合法化规则 |
| `test_prepare_build_tasks_guard.test_page_scope_renames_model_task_ids_that_conflict_with_retained_units` | retained ID 冲突自动 rename 的现状；不是新 Contract |
| `test_build_task_planner.test_exact_duplicate_tasks_merge_dependencies_and_source_refs` | 精确重复合并的现状；与 G9 新路径要求不同 |
| `test_prepare_build_tasks_guard.test_prepare_build_tasks_persists_pending_json_and_does_not_report_code_changes` | 其中 pending 写正式路径的断言属于 legacy；“必须确认”和“不产生代码修改”仍应保留 |
| Planning / Pending / Confirmed 三文件分离、shell capability、deterministic auth-guard 单 writer、UnitCandidate/重试/并发策略 | 后续任务实施范围；T0.1 不添加虚假通过或 expectedFailure 来宣称完成 |

节点集成测试虽经过现有组装/持久化代码，但不对 replacement、rename、精确去重、
pending 输出文件名作新增断言。Build 入口测试对未确认内容的拒绝是安全门禁反例，
并非认可 Planning 将 pending 写入正式文件。

## 修改前已有失败与测试修正

修改前执行指定三组的去重合集：171 tests，1 failure、1 error。

- `test_live_page_path_is_reconciled_without_menu_route_task` 仍断言已经移除的
  `must not add template page entry` 错误。当前实际失败是交付物未包含校对后的页面入口。
  保留图无效、路径校对、无菜单任务等断言，将失败原因改为精确的页面交付物校验。
- `test_data_source_agent_receives_scoped_bundle_without_host_path_in_prompt` 使用已移除的
  `build_task_plan` 参数。按当前调用方改用 `workspace_snapshot.backend.dir_structure`，
  保留宿主路径隔离和任务允许路径的断言。

以上只修正测试，不改变校验器、Agent 签名、Prompt 或其他生产行为。

## 运行方式

沿用项目 `unittest` runner，在 `Backend` 目录执行：

```sh
# T0.1 新增基线
.venv/bin/python -m unittest tests.test_dag_planning_baseline tests.test_dag_planning_baseline_gates

# R-PLAN：全部现有测试
.venv/bin/python -m unittest tests.test_build_task_planner tests.test_build_unit_skeleton tests.test_prepare_build_tasks_guard tests.test_build_dag_v3_contract tests.test_page_build_context_resolver

# R-ACCEPT：全部现有测试（与 R-PLAN 重叠的 planner 仍在组内执行）
.venv/bin/python -m unittest tests.test_engineering_acceptance tests.test_business_acceptance tests.test_build_task_planner

# R-TEMPLATE：全部现有测试
.venv/bin/python -m unittest tests.test_application_template_generation tests.test_template_scaffold_injection tests.test_generation_workspace_paths
```

测试通过只表示 T0.1 回归基线满足当前验收，不表示 legacy 冲突已解决或下一 Task 已开始。
