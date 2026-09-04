import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agents.main.document_sync import sync_project_plan_from_markdown
from app.agents.main.planner import revise_project_plan_with_chat_model
from app.agents.main.task_preparer import prepare_build_tasks_with_main_agent
from app.agents.main.task_preparer_prompt import planning_context_mode
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.artifact_invalidation import (
    ArtifactInvalidationError,
    assert_confirmed_artifact_closure,
    canonical_sha256,
    stale_artifact_keys,
)
from app.services.build_context_resolver import resolve_target_build_context
from app.services.build_task_confirmation import (
    build_task_confirmation_read_model,
)
from app.services.build_task_reuse import resolve_template_prerequisite_facts
from app.services.template_scaffold_injection import prebuilt_files_for_plan
from app.services.development_readiness import development_readiness
from app.services.build_task_planner import (
    compile_build_task_plan_scope,
    frontend_endpoint_implementation_owners,
    frontend_endpoint_ownership_errors,
    merge_exact_duplicate_tasks,
    strip_platform_owned_candidate_tasks,
    tasks_from_build_task_plan,
)
from app.services.application_template_generation import (
    inspect_template_generation_readiness,
)
from app.services.authorization_overlay import compile_authorization_overlay
from app.services.build_task_progress import (
    build_task_artifacts,
    create_build_task_progress_tracker,
    project_artifact_output,
    project_build_context_output,
    project_candidate_tasks_output,
    project_compiled_tasks_output,
    project_contract_validation_output,
    project_dag_validation_output,
    project_unit_skeleton_output,
)
from app.services.build_unit_skeleton import (
    build_unit_skeleton_input_fingerprint,
    ensure_build_unit_skeleton,
)
from app.services.entity_definitions import entity_design_summaries, plan_data_sources
from app.services.frontend_page_tree import project_plan_page_records
from app.services.page_dependencies import validate_project_plan_dependencies
from app.services.page_implementation_contract import materialize_technical_plan_runtime
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.plan_documents import (
    load_project_plan_json,
    project_plan_json_path,
)
from app.workspace.spec_documents import workspace_root
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_build_task_plan_json,
    load_confirmed_build_task_plan,
    write_build_task_plan_json,
)
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


def _latest_project_plan(
        state: ProjectState,
        *,
        formal_artifacts: dict[str, dict[str, Any]] | None = None,
) -> dict:
    """读取最新正式计划，并为当前 TechnicalPlan 重新物化 Build 运行时投影。"""

    project_plan = state["project_plan"]
    if project_plan.get("confirmation_status") != "confirmed":
        return project_plan
    if not state.get("project_plan_json_path"):
        return project_plan
    path = project_plan_json_path(state)
    if path.is_file():
        latest_plan = load_project_plan_json(path, hydrate_detail_designs=True)
        if latest_plan.get("artifact_type") != "technical-plan":
            return latest_plan
        artifacts = formal_artifacts or {}
        requirement_spec = (
            artifacts.get("requirement_spec") or state.get("requirement_spec")
        )
        product_plan = artifacts.get("product_plan") or state.get("product_plan")
        ui_designs = artifacts.get("ui_designs") or state.get("ui_designs")
        if not all(
                isinstance(artifact, dict) and artifact
                for artifact in (requirement_spec, product_plan, ui_designs)
        ):
            return latest_plan
        # 正式 TechnicalPlan 不持久化 PageImplementationContract；Build 每次都必须
        # 使用最新正式上游重新编译，不能让磁盘重载抹掉运行时派生契约。
        return materialize_technical_plan_runtime(
            latest_plan,
            requirement_spec,
            product_plan,
            ui_designs,
        )
    return project_plan


def prepare_build_tasks(state: ProjectState) -> dict:
    """按应用、页面、数据源或 endpoint 范围编译任务子图并持久化 Build DAG。"""
    workspace = workspace_from_state(state)
    formal_artifacts = _load_formal_artifacts(workspace)
    project_plan = _latest_project_plan(
        state,
        formal_artifacts=formal_artifacts,
    )
    build_execution_scope = _build_execution_scope_from_state(state)
    formal_artifact_state = _formal_artifact_state_update(formal_artifacts)
    prerequisite_errors = _build_prerequisite_errors(
        state,
        project_plan,
        workspace=workspace,
        build_execution_scope=build_execution_scope,
        formal_artifacts=formal_artifacts,
    )
    if prerequisite_errors:
        return {
            **_build_prerequisite_blocked_result(
                project_plan,
                build_execution_scope,
                prerequisite_errors,
            ),
            **formal_artifact_state,
        }

    confirmation_result = _handle_build_task_plan_confirmation(
        state,
        project_plan,
        build_execution_scope,
    )
    if confirmation_result is not None:
        return {**confirmation_result, **formal_artifact_state}

    workspace_snapshot = _workspace_snapshot_from_state(state)
    try:
        existing_build_task_plan = _existing_build_task_plan(state)
    except (OSError, ValueError) as exc:
        return {
            **_build_prerequisite_blocked_result(
                project_plan, build_execution_scope,
                [f"正式 build-task-plan.json 无法作为 ConfirmedPlan：{exc}"],
            ),
            **formal_artifact_state,
        }
    progress = create_build_task_progress_tracker()

    progress.start("unit_skeleton", "正在根据已确认项目计划生成 Unit DAG 骨架。")
    try:
        build_task_plan = ensure_build_unit_skeleton(
            project_plan,
            workspace_snapshot,
            existing_build_task_plan,
        )
    except Exception as exc:
        progress.fail(
            "unit_skeleton",
            f"Unit DAG 骨架生成失败：{exc}",
            output={
                "kind": "unit_graph",
                "schemaVersion": "build-unit-graph.v3",
                "reused": False,
                "units": [],
                "edges": {"items": [], "truncated": False},
                "validation": {"isValid": False, "issues": [str(exc)[:1_000]]},
            },
        )
        raise
    build_units = build_task_plan.get("build_units")
    unit_graph = build_task_plan.get("unit_graph")
    unit_count = len(build_units) if isinstance(build_units, dict) else 0
    unit_edge_count = (
        len(unit_graph.get("edges") or []) if isinstance(unit_graph, dict) else 0
    )
    progress.complete(
        "unit_skeleton",
        f"已生成 {unit_count} 个 Unit、{unit_edge_count} 条 Unit 依赖。",
        build_task_plan=build_task_plan,
        output=project_unit_skeleton_output(build_task_plan),
    )

    progress.start("build_context", "正在解析当前页面或数据源的定向构建上下文。")
    try:
        build_context = _resolve_build_context(
            state,
            project_plan,
            build_execution_scope,
            build_task_plan,
        )
    except ValueError as exc:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
        )
        progress.fail(
            "build_context",
            f"构建上下文解析失败：{exc}",
            build_task_plan=attempt_plan,
            output=project_build_context_output({}, attempt_plan),
        )
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": attempt_plan,
            "build_execution_scope": build_execution_scope,
            "last_persisted_build_execution_scope": (
                build_task_plan.get("build_execution_scope")
                if isinstance(build_task_plan.get("build_execution_scope"), dict)
                else None
            ),
            "build_task_plan_persisted": False,
            "dag_generation_progress": progress.snapshot(),
            "clarification": _build_context_error_payload(
                str(exc),
                build_execution_scope,
            ),
            "timeline": ["prepare_build_tasks"],
            **formal_artifact_state,
        }
    except Exception as exc:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
        )
        progress.fail(
            "build_context",
            f"构建上下文解析异常：{exc}",
            build_task_plan=attempt_plan,
            output=project_build_context_output({}, attempt_plan),
        )
        raise
    target = build_context.get("target")
    target = target if isinstance(target, dict) else {}
    progress.complete(
        "build_context",
        (
            f"已解析 {target.get('type', 'application')}:{target.get('id', 'application')}，"
            f"涉及 {len(build_context.get('required_unit_ids') or [])} 个 Unit、"
            f"{len(build_context.get('endpoint_ids') or [])} 个 Endpoint。"
        ),
        build_task_plan=build_task_plan,
        output=project_build_context_output(build_context, build_task_plan),
    )

    progress.start("authorization_overlay", "正在按当前 Unit 编译只读权限 Overlay。")
    try:
        build_context = compile_authorization_overlay(project_plan, build_context)
    except ValueError as exc:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
        )
        progress.fail(
            "authorization_overlay",
            f"权限 Overlay 编译失败：{exc}",
            build_task_plan=attempt_plan,
            output=project_build_context_output({}, attempt_plan),
        )
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": attempt_plan,
            "build_execution_scope": build_execution_scope,
            "build_task_plan_persisted": False,
            "dag_generation_progress": progress.snapshot(),
            "clarification": _build_context_error_payload(
                str(exc),
                build_execution_scope,
            ),
            "timeline": ["prepare_build_tasks"],
            **formal_artifact_state,
        }
    # 模板分支是 Build 任务边界的唯一事实源，不能仅由权限开关推断。
    template_readiness = inspect_template_generation_readiness(workspace)
    build_context["template_variant"] = template_readiness.get("templateVariant")
    progress.complete(
        "authorization_overlay",
        "已完成当前 Unit 的只读权限切片编译。",
        build_task_plan=build_task_plan,
        output=project_build_context_output(build_context, build_task_plan),
    )

    progress.start("contract_validation", "正在校验页面依赖和 API 契约一致性。")
    try:
        contract_errors = _scoped_contract_errors(
            project_plan,
            build_execution_scope,
            build_context,
        )
    except Exception as exc:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
        )
        progress.fail(
            "contract_validation",
            f"契约校验异常：{exc}",
            build_task_plan=attempt_plan,
            output=project_contract_validation_output(build_context, [str(exc)]),
        )
        raise
    if contract_errors:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
        )
        progress.fail(
            "contract_validation",
            f"契约校验发现 {len(contract_errors)} 个问题：{contract_errors[0]}",
            build_task_plan=attempt_plan,
            output=project_contract_validation_output(build_context, contract_errors),
        )
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": attempt_plan,
            "build_execution_scope": build_execution_scope,
            "last_persisted_build_execution_scope": (
                build_task_plan.get("build_execution_scope")
                if isinstance(build_task_plan.get("build_execution_scope"), dict)
                else None
            ),
            "build_task_plan_persisted": False,
            "dag_generation_progress": progress.snapshot(),
            "clarification": _api_contract_inconsistency_payload(
                contract_errors,
                build_execution_scope,
            ),
            "timeline": ["prepare_build_tasks"],
            **formal_artifact_state,
        }
    progress.complete(
        "contract_validation",
        "页面依赖与 API 契约校验通过。",
        build_task_plan=build_task_plan,
        output=project_contract_validation_output(build_context, []),
    )

    # shell 只使用平台模板证据，历史任务状态和模型工作区摘要均不授予前置能力。
    prerequisite_facts = resolve_template_prerequisite_facts(
        unit_skeleton=build_task_plan, build_context=build_context,
        workspace_snapshot=workspace_snapshot, template_readiness=template_readiness,
    )
    if prerequisite_facts.issues:
        result = _build_prerequisite_blocked_result(
            project_plan, build_execution_scope,
            [issue.message for issue in prerequisite_facts.issues],
        )
        result["clarification"]["issues"] = [
            issue.model_dump(mode="json") for issue in prerequisite_facts.issues
        ]
        return {**result, **formal_artifact_state}
    build_context["external_capabilities"] = [
        capability.model_dump(mode="json")
        for capability in prerequisite_facts.external_capabilities
    ]

    progress.start("model_planning", "正在调用任务规划模型生成候选构建任务。")
    try:
        planning_unit_ids = _replaceable_unit_ids(
            build_task_plan,
            build_context,
            set(build_context.get("required_unit_ids") or []),
        )
        owner_constraints, retained_owner_errors = (
            _retained_frontend_endpoint_owner_constraints(
                build_task_plan,
                planning_unit_ids,
            )
        )
        if retained_owner_errors:
            attempt_plan = _build_task_plan_attempt_view(
                build_task_plan,
                build_execution_scope,
                status="failed",
            )
            progress.fail(
                "model_planning",
                "保留任务中存在前端 Endpoint 多 owner，已停止本轮 DAG 生成。",
                build_task_plan=attempt_plan,
                output=project_candidate_tasks_output(attempt_plan),
            )
            return {
                **_retained_endpoint_owner_blocked_result(
                    project_plan,
                    build_task_plan,
                    progress,
                    retained_owner_errors,
                    build_execution_scope,
                ),
                **formal_artifact_state,
            }
        planning_build_context = {
            **build_context,
            "planning_unit_ids": sorted(planning_unit_ids),
            # 该索引仅约束本轮模型规划，不写入正式 build_context 或 Build DAG。
            "frontend_endpoint_owner_constraints": owner_constraints,
        }
        planning_build_context["planning_context_mode"] = planning_context_mode(
            planning_build_context
        )
        finalized_plan: dict[str, dict[str, Any]] = {}

        def finalize_candidate(candidate_plan: dict[str, Any]) -> dict[str, Any]:
            """把当前候选合并进保留任务，并返回需参与同轮校验的完整 DAG。"""

            merged_plan = _merge_prepared_scope_tasks(
                build_task_plan,
                candidate_plan,
                build_context,
                project_plan=project_plan,
            )
            finalized_plan["value"] = merged_plan
            return merged_plan

        prepared_plan = prepare_build_tasks_with_main_agent(
            _task_preparation_project_plan(
                project_plan,
                planning_build_context,
            ),
            workspace=workspace,
            workspace_snapshot=workspace_snapshot,
            build_context=planning_build_context,
            build_task_plan=build_task_plan,
            build_execution_scope=build_execution_scope,
            candidate_finalizer=finalize_candidate,
        )
    except ValueError as exc:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
            status="failed",
        )
        progress.fail(
            "model_planning",
            f"候选任务生成失败：{exc}",
            build_task_plan=attempt_plan,
            output=project_candidate_tasks_output(attempt_plan),
        )
        return {
            **_build_task_plan_generation_failed_result(
                project_plan,
                build_task_plan,
                progress,
                str(exc),
                build_execution_scope,
            ),
            **formal_artifact_state,
        }
    except Exception as exc:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
            status="failed",
        )
        progress.fail(
            "model_planning",
            f"候选任务生成异常：{exc}",
            build_task_plan=attempt_plan,
            output=project_candidate_tasks_output(attempt_plan),
        )
        raise
    prepared_tasks = tasks_from_build_task_plan(prepared_plan)
    progress.complete(
        "model_planning",
        f"任务规划模型已生成 {len(prepared_tasks)} 个有效候选任务。",
        build_task_plan=prepared_plan,
        output=project_candidate_tasks_output(prepared_plan),
    )

    progress.start("task_compilation", "正在编译任务字段、Unit 与任务依赖。")
    try:
        # 正常运行时最终化已在唯一重试循环内完成；测试替身或旧调用边界未执行
        # callback 时仍在这里合并一次，保证节点边界保持确定性。
        build_task_plan = finalized_plan.get("value") or _merge_prepared_scope_tasks(
            build_task_plan,
            prepared_plan,
            build_context,
            project_plan=project_plan,
        )
    except ValueError as exc:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
            status="failed",
        )
        progress.fail(
            "task_compilation",
            f"任务依赖编译失败：{exc}",
            build_task_plan=attempt_plan,
            output=project_compiled_tasks_output(attempt_plan),
        )
        return {
            **_build_task_plan_generation_failed_result(
                project_plan,
                build_task_plan,
                progress,
                str(exc),
                build_execution_scope,
            ),
            **formal_artifact_state,
        }
    except Exception as exc:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
            status="failed",
        )
        progress.fail(
            "task_compilation",
            f"任务依赖编译异常：{exc}",
            build_task_plan=attempt_plan,
            output=project_compiled_tasks_output(attempt_plan),
        )
        raise
    compiled_tasks = tasks_from_build_task_plan(build_task_plan)
    task_graph = build_task_plan.get("task_graph")
    task_graph = task_graph if isinstance(task_graph, dict) else {}
    progress.complete(
        "task_compilation",
        (
            f"已编译 {len(compiled_tasks)} 个任务、"
            f"{len(task_graph.get('edges') or [])} 条任务依赖。"
        ),
        build_task_plan=build_task_plan,
        output=project_compiled_tasks_output(build_task_plan),
    )

    progress.start("dag_validation", "正在校验任务拓扑、循环依赖和执行批次。")
    dag_errors = (
        build_task_plan.get("task_graph", {})
        .get("validation", {})
        .get("errors", [])
    )
    if dag_errors:
        attempt_plan = _build_task_plan_attempt_view(
            build_task_plan,
            build_execution_scope,
            status="failed",
        )
        progress.fail(
            "dag_validation",
            f"任务 DAG 校验发现 {len(dag_errors)} 个问题：{dag_errors[0]}",
            build_task_plan=attempt_plan,
            output=project_dag_validation_output(attempt_plan),
        )
        return {
            **_build_task_plan_generation_failed_result(
                project_plan,
                build_task_plan,
                progress,
                "；".join(str(error) for error in dag_errors),
                build_execution_scope,
            ),
            **formal_artifact_state,
        }
    execution = build_task_plan.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    progress.complete(
        "dag_validation",
        f"任务 DAG 校验通过，共 {len(execution.get('batches') or [])} 个执行批次。",
        build_task_plan=build_task_plan,
        output=project_dag_validation_output(build_task_plan),
    )

    build_task_plan = {
        **build_task_plan,
        "build_execution_scope": build_execution_scope,
        "status": _build_task_plan_status(build_task_plan),
        "confirmation_status": "pending",
        "confirmed_at": None,
    }
    authorization_constraints = build_context.get("authorization_constraints")
    frontend_projection = (
        authorization_constraints.get("frontendProjection")
        if isinstance(authorization_constraints, dict)
        else None
    )
    if frontend_projection is None:
        build_task_plan.pop("authorization_frontend_projection", None)
    else:
        # 显式业务路由和完整 RESOURCES 均属于平台事实，不能交由 Page Agent 修改。
        build_task_plan["authorization_frontend_projection"] = frontend_projection
    auth_constants_projection = (
        authorization_constraints.get("authConstantsProjection")
        if isinstance(authorization_constraints, dict)
        else None
    )
    if auth_constants_projection is None:
        build_task_plan.pop("authorization_constants_projection", None)
    else:
        # 操作资源常量由平台统一写入模板托管区，Endpoint Agent 只能引用它们。
        build_task_plan["authorization_constants_projection"] = auth_constants_projection
    progress.start("artifact_persistence", "正在保存待确认的 JSON Build Task Plan。")
    try:
        build_task_plan_path = write_build_task_plan_json(state, build_task_plan)
    except Exception as exc:
        progress.fail(
            "artifact_persistence",
            f"DAG 产物保存失败：{exc}",
            build_task_plan=build_task_plan,
            output=project_artifact_output([]),
        )
        raise
    artifacts = build_task_artifacts(build_task_plan)
    progress.complete(
        "artifact_persistence",
        "待确认的 build-task-plan.json 已保存。",
        build_task_plan=build_task_plan,
        artifacts=artifacts,
        output=project_artifact_output(artifacts),
    )
    return {
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "build_task_plan": build_task_plan,
        "dag_generation_progress": progress.snapshot(),
        "build_task_plan_path": build_task_plan_path,
        "build_execution_scope": build_execution_scope,
        "build_task_plan_persisted": True,
        "build_context": build_context,
        "build_units": build_task_plan.get("build_units", {}),
        "unit_graph": build_task_plan.get("unit_graph", {}),
        "task_registry": build_task_plan.get("task_registry", {}),
        "task_graph": build_task_plan.get("task_graph", {}),
        "tasks": tasks_from_build_task_plan(build_task_plan),
        "build_task_plan_confirmation": _build_task_plan_confirmation_payload(
            build_task_plan,
            build_execution_scope,
            project_plan=project_plan,
            build_context=build_context,
        ),
        "clarification": _build_task_plan_confirmation_payload(
            build_task_plan,
            build_execution_scope,
            project_plan=project_plan,
            build_context=build_context,
        ),
        "timeline": ["prepare_build_tasks"],
        **formal_artifact_state,
    }


def _build_prerequisite_errors(
    state: ProjectState,
    project_plan: dict[str, Any],
    *,
    workspace: str | None,
    build_execution_scope: dict[str, str] | None = None,
    formal_artifacts: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """在 DAG 生成前只读校验正式产物、模板 manifest 和当前运行时计划。"""

    errors: list[str] = []
    artifacts = (
        formal_artifacts
        if formal_artifacts is not None
        else _load_formal_artifacts(workspace)
    )
    requirement_spec = artifacts.get("requirement_spec", {})
    product_plan = artifacts.get("product_plan", {})
    ui_designs = artifacts.get("ui_designs", {})
    technical_plan = artifacts.get("technical_plan", {})
    if not requirement_spec or requirement_spec.get("confirmation_status") != "confirmed":
        errors.append("RequirementSpec 未确认。")
    if not product_plan or product_plan.get("confirmation_status") != "confirmed":
        errors.append("ProductPlan 未确认。")
    if not ui_designs or ui_designs.get("confirmation_status") not in {"confirmed", "skipped"}:
        errors.append("UiManifest 未确认或未明确跳过。")
    if (
        not technical_plan
        or technical_plan.get("artifact_type") != "technical-plan"
        or technical_plan.get("confirmation_status") != "confirmed"
    ):
        errors.append("TechnicalPlan 缺失、类型不正确或未确认。")
    errors.extend(
        _formal_artifact_hash_errors(
            workspace,
            {
                "requirement-spec": requirement_spec,
                "product-plan": product_plan,
                "ui-design": ui_designs,
                "technical-plan": technical_plan,
            },
        )
    )
    if not isinstance(project_plan, dict) or project_plan.get("artifact_type") != "technical-plan":
        errors.append("Build 运行时 project_plan 不是当前 TechnicalPlan 的只读投影。")
    scope = build_execution_scope if isinstance(build_execution_scope, dict) else {}
    target_type = str(scope.get("type") or "")
    target_id = str(scope.get("targetId") or "")
    if target_type in {"page", "endpoint"} and target_id:
        try:
            readiness = development_readiness(
                project_plan,
                target_type=target_type,
                target_id=target_id,
                api_contract_id=str(
                    scope.get("apiContractId") or scope.get("api_contract_id") or ""
                ).strip() or None,
            )
            if not readiness.get("ready"):
                missing = "、".join(
                    str(item.get("entity_name") or item.get("entity_id") or "")
                    for item in readiness.get("missing_entities", [])
                    if isinstance(item, dict)
                )
                errors.append(f"EntitySourceBinding 未完成：{missing}。")
        except ValueError as exc:
            errors.append(str(exc))
    if workspace:
        readiness = inspect_template_generation_readiness(workspace)
        authorization_manifest = project_plan.get("authorization_manifest")
        authorization_enabled = (
            isinstance(authorization_manifest, dict)
            and authorization_manifest.get("enabled") is True
        )
        if authorization_enabled and readiness.get("templateVariant") != "auth":
            errors.append("权限已启用，但前后端模板不是配套的 auth 分支。")
        errors.extend(
            f"模板初始化：{error}"
            for error in readiness.get("errors", [])
            if str(error).strip()
        )
        if readiness.get("ready") is not True and not readiness.get("errors"):
            errors.append("模板初始化：模板前置门禁未就绪。")
    else:
        errors.append("缺少 workspace，无法校验模板初始化 manifest。")
    return _dedupe_texts(errors)


def _load_json_object(path: Path) -> dict[str, Any]:
    """严格读取一个正式 JSON 对象，供 Build 前置门禁使用。"""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("正式产物必须是 JSON 对象。")
    return value


def _formal_artifact_hash_errors(
    workspace: str | None,
    artifacts: dict[str, dict[str, Any]],
) -> list[str]:
    """对采用当前 basedOn 合同的正式产物执行 Build 前直接上游哈希门禁。"""

    if not workspace or not any(artifact.get("basedOn") for artifact in artifacts.values()):
        return []
    root = Path(workspace)
    paths = {
        "requirement-spec": root / ".xcodeagent/specs/requirement-spec.json",
        "product-plan": root / ".xcodeagent/plans/product-plan.json",
        "ui-design": root / ".xcodeagent/specs/ui-designs.json",
        "technical-plan": root / ".xcodeagent/plans/technical-plan.json",
    }
    try:
        hashes = {
            artifact_key: canonical_sha256(path)
            for artifact_key, path in paths.items()
            if path.is_file()
        }
        stale = stale_artifact_keys(
            {
                artifact_key: artifact
                for artifact_key, artifact in artifacts.items()
                if artifact.get("basedOn")
            },
            canonical_hashes=hashes,
        )
    except ArtifactInvalidationError as exc:
        return [str(exc)]
    return [f"{artifact_key} 的直接上游哈希不匹配，状态必须重新确认。" for artifact_key in stale]


def _load_formal_artifacts(
    workspace: str | None,
) -> dict[str, dict[str, Any]]:
    """从当前工作区读取 DAG 门禁需要的正式 JSON，不使用 checkpoint 兜底。"""

    return {
        "requirement_spec": _load_formal_artifact(
            workspace,
            ".xcodeagent/specs/requirement-spec.json",
        ),
        "product_plan": _load_formal_artifact(
            workspace,
            ".xcodeagent/plans/product-plan.json",
        ),
        "ui_designs": _load_formal_artifact(
            workspace,
            ".xcodeagent/specs/ui-designs.json",
        ),
        "technical_plan": _load_formal_artifact(
            workspace,
            ".xcodeagent/plans/technical-plan.json",
        ),
    }


def _load_formal_artifact(
    workspace: str | None,
    relative_path: str,
) -> dict[str, Any]:
    """从工作区读取单个正式 JSON；读取失败时返回空对象并让门禁阻断。"""

    if not workspace:
        return {}
    path = Path(workspace).expanduser() / relative_path
    try:
        loaded = load_project_plan_json(path, hydrate_detail_designs=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _formal_artifact_state_update(
    formal_artifacts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """把正式 JSON 的最新读取结果写回 Graph state，清除不可用的旧快照。"""

    return {
        "requirement_spec": formal_artifacts.get("requirement_spec", {}),
        "product_plan": formal_artifacts.get("product_plan", {}),
        "ui_designs": formal_artifacts.get("ui_designs", {}),
        "technical_plan": formal_artifacts.get("technical_plan", {}),
    }


def _dedupe_texts(values: list[str]) -> list[str]:
    """按原始顺序去重阻断原因，避免同一前置条件重复提示。"""

    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _build_prerequisite_blocked_result(
    project_plan: dict[str, Any],
    build_execution_scope: dict[str, str],
    errors: list[str],
) -> dict[str, Any]:
    """将正式产物或模板前置失败投影为可恢复的上游提示。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="Build 前置条件",
                question=(
                    "当前正式产物、模板初始化或运行时上下文尚未就绪，DAG 不会修改上游产物。"
                    "请返回对应的规划、模板初始化或 EntitySourceBinding 流程处理。"
                ),
                type="text",
                placeholder="请按下方具体错误完成上游流程后重新进入 Build。",
            )
        ]
    )
    payload.update(
        {
            "mode": "build_prerequisite_error",
            "code": "build_prerequisite_not_ready",
            "message": "Build DAG 前置条件未满足，已阻止任务生成。",
            "errors": errors,
            "target": build_execution_scope,
            "artifact": (
                "RequirementSpec / ProductPlan / UiManifest / TechnicalPlan / "
                "template-generation-manifest.json / EntitySourceBinding"
            ),
            "recommended_action": "手动完成并确认错误所指向的前置产物后重新发起 DAG 生成。",
            "automatic_routing": False,
            "upstreamStages": [
                "requirements",
                "product_planning",
                "ui_confirmation",
                "technical_planning",
                "application_lifecycle",
                "entity_source_binding",
            ],
            "buildExecutionScope": build_execution_scope,
        }
    )
    return {
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "build_execution_scope": build_execution_scope,
        "clarification": payload,
        "timeline": ["prepare_build_tasks"],
    }


def _latest_build_task_plan_from_workspace(state: ProjectState) -> dict[str, Any]:
    """只从当前工作区的最新 JSON 读取 DAG，避免确认旧 checkpoint 计划。"""

    workspace = workspace_from_state(state)
    if workspace:
        fixed_path = Path(workspace).expanduser() / ".xcodeagent" / "plans" / "build-task-plan.json"
        if fixed_path.is_file():
            try:
                value = load_build_task_plan_json(fixed_path)
                return _fill_missing_build_task_plan_status(value)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return {}
    path = build_task_plan_json_path(state)
    if not path.is_file():
        return {}
    try:
        value = load_build_task_plan_json(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return _fill_missing_build_task_plan_status(value)


def _build_task_plan_confirmation_payload(
    build_task_plan: dict[str, Any],
    build_execution_scope: dict[str, Any] | None,
    *,
    project_plan: dict[str, Any] | None = None,
    build_context: dict[str, Any] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """构造 DAG 确认载荷，并附加不写回累计计划的只读确认投影。"""

    read_model = build_task_confirmation_read_model(
        build_task_plan,
        build_execution_scope,
        project_plan=project_plan,
        build_context=build_context,
    )
    payload: dict[str, Any] = {
        "mode": "build_task_plan_confirmation",
        "status": "requires_user_input",
        "message": "Build DAG 已生成，请确认任务规划后再进入 Build。",
        "actionValues": ["confirm", "abandon"],
        "confirmationStatus": build_task_plan.get("confirmation_status") or "pending",
        "buildExecutionScope": build_execution_scope or build_task_plan.get("build_execution_scope") or {},
        "taskPlan": {
            "version": build_task_plan.get("version"),
            "schemaVersion": build_task_plan.get("schema_version"),
            "status": build_task_plan.get("status"),
            "confirmationStatus": build_task_plan.get("confirmation_status"),
            "summary": build_task_plan.get("summary") or {},
            "scopeTasks": read_model["scopeTasks"],
            "reusedPrerequisites": read_model["reusedPrerequisites"],
            "retainedTaskSummary": read_model["retainedTaskSummary"],
        },
        "targetReview": read_model["targetReview"],
    }
    if errors:
        payload["errors"] = errors
        payload["message"] = "Build DAG 需要处理后才能继续。"
    return payload


def _handle_build_task_plan_confirmation(
    state: ProjectState,
    project_plan: dict[str, Any],
    build_execution_scope: dict[str, str],
) -> dict[str, Any] | None:
    """处理 DAG confirm；放弃由 AG-UI 计划控制流终止，不进入 Graph。"""

    action_payload = state.get("build_task_plan_confirmation")
    if not isinstance(action_payload, dict) or not action_payload.get("action"):
        latest_plan = _latest_build_task_plan_from_workspace(state)
        planned_scope = latest_plan.get("build_execution_scope")
        # build-task-plan.json 是应用级累计产物，但 pending/confirmed 只属于生成它的目标范围。
        # 切换页面或接口后必须继续生成当前范围，不能把上一范围的确认状态直接带入 Build。
        if (
            not isinstance(planned_scope, dict)
            or planned_scope != build_execution_scope
            or not _is_current_build_task_plan(
                state,
                project_plan,
                latest_plan,
                build_execution_scope,
            )
        ):
            return None
        if latest_plan.get("confirmation_status") == "pending":
            return _pending_build_task_plan_result(
                state,
                project_plan,
                latest_plan,
                build_execution_scope,
            )
        if latest_plan.get("confirmation_status") == "confirmed":
            return _confirmed_build_task_plan_result(
                state,
                project_plan,
                latest_plan,
                build_execution_scope,
            )
        return None

    action = str(action_payload.get("action") or "").strip().lower()
    latest_plan = _latest_build_task_plan_from_workspace(state)
    if not latest_plan:
        return _pending_build_task_plan_result(
            state,
            project_plan,
            latest_plan,
            build_execution_scope,
            errors=["工作区中不存在最新 build-task-plan.json，不能确认或修改旧计划。"],
        )
    freshness_errors = (
        []
        if _is_current_build_task_plan(
            state,
            project_plan,
            latest_plan,
            build_execution_scope,
        )
        else ["当前 build-task-plan.json 与项目计划输入、构建范围或 DAG 字段契约不一致，请重新生成。"]
    )
    plan_errors = [
        *freshness_errors,
        *_build_task_plan_gate_errors(latest_plan, build_execution_scope),
    ]
    if action == "confirm":
        if plan_errors:
            return _pending_build_task_plan_result(
                state,
                project_plan,
                latest_plan,
                build_execution_scope,
                errors=plan_errors,
            )
        confirmed_plan = {
            **latest_plan,
            "confirmation_status": "confirmed",
            "confirmed_at": datetime.now(UTC).isoformat(),
            "build_execution_scope": build_execution_scope,
        }
        path = write_build_task_plan_json(state, confirmed_plan)
        return _confirmed_build_task_plan_result(
            state,
            project_plan,
            confirmed_plan,
            build_execution_scope,
            path=path,
        )
    return _pending_build_task_plan_result(
        state,
        project_plan,
        latest_plan,
        build_execution_scope,
        errors=[f"不支持的 Build DAG 动作：{action}"],
    )


def _build_task_plan_gate_errors(
    build_task_plan: dict[str, Any],
    build_execution_scope: dict[str, Any],
) -> list[str]:
    """检查最新 DAG 的 schema、ready 状态、确认前置和当前 scope。"""

    errors: list[str] = []
    if build_task_plan.get("schema_version") != "build-dag.v3":
        errors.append("最新 DAG schema_version 不是 build-dag.v3。")
    if build_task_plan.get("status") != "ready":
        errors.append(f"最新 DAG status={build_task_plan.get('status') or 'unknown'}，不能进入 Build。")
    if build_task_plan.get("confirmation_status") not in {"pending", "confirmed"}:
        errors.append("最新 DAG 缺少有效 confirmation_status。")
    graph = build_task_plan.get("task_graph")
    validation = graph.get("validation") if isinstance(graph, dict) else None
    if not isinstance(validation, dict) or validation.get("is_valid") is not True:
        errors.extend(
            str(error)
            for error in (validation.get("errors") if isinstance(validation, dict) else [])
            if str(error).strip()
        )
    planned_scope = build_task_plan.get("build_execution_scope")
    if isinstance(planned_scope, dict) and planned_scope and planned_scope != build_execution_scope:
        errors.append(
            "DAG scope 与当前 Build scope 不一致："
            f"planned={planned_scope} current={build_execution_scope}。"
        )
    return _dedupe_texts(errors)


def _build_task_plan_status(build_task_plan: dict[str, Any]) -> str:
    """根据任务图校验和执行批次计算 Build DAG 顶层状态。"""

    graph = build_task_plan.get("task_graph")
    validation = graph.get("validation") if isinstance(graph, dict) else None
    execution = build_task_plan.get("execution")
    batches = execution.get("batches") if isinstance(execution, dict) else []
    return (
        "ready"
        if isinstance(validation, dict)
        and validation.get("is_valid") is True
        and isinstance(batches, list)
        and not any(
            isinstance(batch, dict) and batch.get("mode") == "blocked"
            for batch in batches
        )
        else "blocked"
    )


def _fill_missing_build_task_plan_status(value: Any) -> dict[str, Any]:
    """为当前 build-dag.v3 产物补齐生成阶段漏写的顶层 status 字段。"""

    if not isinstance(value, dict):
        return {}
    if value.get("schema_version") != "build-dag.v3" or "status" in value:
        return value
    return {**value, "status": _build_task_plan_status(value)}


def _pending_build_task_plan_result(
    state: ProjectState,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    build_execution_scope: dict[str, str],
    *,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """返回待确认 DAG 的统一节点结果。"""

    path = str(build_task_plan_json_path(state)) if build_task_plan else None
    clarification = _build_task_plan_confirmation_payload(
        build_task_plan,
        build_execution_scope,
        project_plan=project_plan,
        errors=errors,
    )
    return {
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "build_task_plan": build_task_plan,
        "build_task_plan_path": path,
        "build_execution_scope": build_execution_scope,
        # 旧文件存在不等于当前 scope 已持久化；切换页面时必须明确标记为 false。
        "build_task_plan_persisted": bool(path)
        and build_task_plan.get("build_execution_scope") == build_execution_scope,
        "build_task_plan_confirmation": clarification,
        "clarification": clarification,
        "tasks": tasks_from_build_task_plan(build_task_plan),
        "task_registry": build_task_plan.get("task_registry", {}),
        "task_graph": build_task_plan.get("task_graph", {}),
        "timeline": ["prepare_build_tasks"],
    }


def _confirmed_build_task_plan_result(
    state: ProjectState,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    build_execution_scope: dict[str, str],
    *,
    path: str | None = None,
) -> dict[str, Any]:
    """返回已确认 DAG 的结果，让既有主图路由继续进入 Build。"""

    return {
        **_pending_build_task_plan_result(
            state,
            project_plan,
            build_task_plan,
            build_execution_scope,
        ),
        "status": "completed",
        "build_task_plan_path": path or str(build_task_plan_json_path(state)),
        "build_task_plan_confirmation": {
            "mode": "build_task_plan_confirmation",
            "status": "clear",
            "confirmationStatus": "confirmed",
            "message": "Build DAG 已确认，可以进入 Build。",
        },
        "clarification": {},
    }


def _workspace_snapshot_from_state(state: ProjectState) -> dict:
    snapshot = state.get("workspace_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        return snapshot
    snapshot_path = state.get("workspace_snapshot_path")
    if snapshot_path:
        return load_workspace_snapshot_json(snapshot_path)
    return {}


def _build_execution_scope_from_state(state: ProjectState) -> dict[str, str]:
    """读取已在协议边界校验的范围，并为直接节点调用提供默认应用范围。"""

    scope = state.get("build_execution_scope")
    if isinstance(scope, dict):
        target_type = str(scope.get("type") or "").strip()
        target_id = str(scope.get("targetId") or scope.get("target_id") or "").strip()
        if target_type in {"application", "page", "data_source", "endpoint"}:
            return {
                "type": target_type,
                "targetId": target_id or "application",
                **(
                    {"apiContractId": str(scope.get("apiContractId") or scope.get("api_contract_id") or "").strip()}
                    if target_type == "endpoint"
                       and str(scope.get("apiContractId") or scope.get("api_contract_id") or "").strip()
                    else {}
                ),
            }
    selected_page_id = str(state.get("selectedPageId") or "").strip()
    return (
        {"type": "page", "targetId": selected_page_id}
        if selected_page_id
        else {"type": "application", "targetId": "application"}
    )


def _existing_build_task_plan(state: ProjectState) -> dict:
    """正式文件缺失可开始首次规划；文件存在但不合格必须阻断，禁止退化为空基线。"""

    plan = load_confirmed_build_task_plan(workspace_root(state))
    path = build_task_plan_json_path(state)
    if plan is None and (path.exists() or path.is_symlink()):
        raise ValueError("正式文件存在但不是已确认且通过校验的 build-dag.v3。")
    return plan or {}


def _is_valid_build_task_plan(value: object) -> bool:
    """仅接受通过任务图校验的 v3 DAG，避免失败 checkpoint 污染后续重试。"""

    if not isinstance(value, dict) or value.get("schema_version") != "build-dag.v3":
        return False
    if value.get("status") == "failed":
        return False
    task_graph = value.get("task_graph")
    validation = task_graph.get("validation") if isinstance(task_graph, dict) else None
    return isinstance(validation, dict) and validation.get("is_valid") is True


def _is_current_build_task_plan(
    state: ProjectState,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    build_execution_scope: dict[str, str],
) -> bool:
    """判断旧 DAG 是否仍匹配当前输入、范围和任务字段契约，避免新增页面复用旧快照。"""

    if not _is_valid_build_task_plan(build_task_plan):
        return False
    if build_task_plan.get("build_execution_scope") != build_execution_scope:
        return False

    registry = build_task_plan.get("task_registry")
    if not isinstance(registry, dict):
        return False
    tasks = [task for task in registry.values() if isinstance(task, dict)]
    if len(tasks) != len(registry):
        return False
    task_graph = build_task_plan.get("task_graph")
    graph_nodes = (
        {str(task_id) for task_id in task_graph.get("nodes") or []}
        if isinstance(task_graph, dict)
        else set()
    )
    if graph_nodes != {str(task_id) for task_id in registry}:
        return False
    # 空任务图主要用于确认恢复；真实生成计划会带 Unit 骨架指纹，不能以缺失指纹的旧文件冒充当前计划。
    skeleton = build_task_plan.get("unit_skeleton")
    stored_fingerprint = skeleton.get("input_fingerprint") if isinstance(skeleton, dict) else None
    if not tasks and not stored_fingerprint:
        return True
    if not stored_fingerprint:
        return False
    current_fingerprint = build_unit_skeleton_input_fingerprint(
        project_plan,
        _workspace_snapshot_from_state(state),
    )
    if stored_fingerprint != current_fingerprint:
        return False

    required_fields = ("deliverables", "acceptance_checks", "business_acceptance_checks")
    return all(
        all(field in task for field in required_fields)
        for task in tasks
    )


def _resolve_build_context(
        state: ProjectState,
        project_plan: dict,
        build_execution_scope: dict[str, str],
        build_task_plan: dict,
) -> dict:
    """按范围解析详情上下文；应用范围保留全局信息但不伪造单页详情。"""

    target_type = build_execution_scope["type"]
    target_id = build_execution_scope["targetId"]
    if target_type != "application":
        context = resolve_target_build_context(
            project_plan,
            target_type=target_type,
            target_id=target_id,
            api_contract_id=str(
                build_execution_scope.get("apiContractId")
                or build_execution_scope.get("api_contract_id")
                or ""
            ).strip() or None,
            project_plan_path=state.get("project_plan_json_path")
                              or project_plan_json_path(state),
        )
        return context
    return {
        "target": {"type": "application", "id": "application"},
        "page_implementation_contract": None,
        "endpoint_contract": None,
        "direct_endpoint_contracts": [],
        "endpoint_ids": [],
        "entity_ids": [],
        "required_unit_ids": list((build_task_plan.get("build_units") or {}).keys()),
        "source_refs": {},
        "prebuilt_files": prebuilt_files_for_plan(project_plan),
    }


def _task_preparation_project_plan(project_plan: dict, build_context: dict) -> dict:
    """按本轮规划模式构造最小任务拆分视图。"""

    mode = planning_context_mode(build_context)
    executable_details = _executable_details(project_plan, build_context)
    if mode == "endpoint":
        executable_details.pop("page_implementation_contracts", None)

    allowed_unit_ids = [unit_id for unit_id in (
        build_context.get("planning_unit_ids")
        or build_context.get("required_unit_ids")
        or []
    ) if unit_id != "frontend:shell"]
    if mode == "endpoint":
        return {
            "architecture": _scoped_task_architecture(
                project_plan,
                mode,
                build_context,
            ),
            "execution_target": build_context.get("target"),
            "allowed_unit_ids": allowed_unit_ids,
            "executable_details": executable_details,
        }

    skeleton = {
        "pages": _skeleton_pages(project_plan) if mode in {"page", "combined"} else [],
        "data_sources": (
            _skeleton_data_sources(
                project_plan,
                None
                if mode == "combined"
                else build_context.get("entity_ids"),
            )
            if mode in {"endpoint", "combined"}
            else []
        ),
        "api_contracts": _scoped_skeleton_api_contracts(
            project_plan,
            build_context,
            mode,
        ),
        "permission_model": (
            project_plan.get("permission_model")
            if mode in {"page", "combined"}
            else None
        ),
    }
    return {
        "version": project_plan.get("version"),
        "confirmation_status": project_plan.get("confirmation_status"),
        "app": project_plan.get("app"),
        "requirements_overview": (
            project_plan.get("requirements_overview")
            if mode == "combined"
            else None
        ),
        "architecture": _scoped_task_architecture(project_plan, mode, build_context),
        "project_acceptance_criteria": (
            project_plan.get("project_acceptance_criteria")
            if mode == "combined"
            else None
        ),
        "application_skeleton": skeleton,
        "execution_target": build_context.get("target"),
        "allowed_unit_ids": allowed_unit_ids,
        "executable_details": executable_details,
    }


def _scoped_task_architecture(
    project_plan: dict,
    mode: str,
    build_context: dict | None = None,
) -> dict:
    """只投射当前 endpoint/page 模式和数据源需要的架构事实。"""

    architecture = project_plan.get("architecture")
    if not isinstance(architecture, dict) or mode == "combined":
        return architecture if isinstance(architecture, dict) else {}
    if mode == "page":
        return {
            key: value
            for key, value in architecture.items()
            if key in {"frontend", "data_contract", "route_root_path", "menu_enabled"}
        }
    context = build_context if isinstance(build_context, dict) else {}
    endpoint_source_types = {
        str(item.get("data_source_type") or "")
        for item in context.get("entity_designs") or []
        if isinstance(item, dict) and item.get("data_source_type")
    }
    if endpoint_source_types == {"static"}:
        return {
            key: value
            for key, value in architecture.items()
            if key in {"frontend", "data_contract", "route_root_path", "menu_enabled"}
        }
    if endpoint_source_types and endpoint_source_types <= {"database", "external_api"}:
        return {
            key: value
            for key, value in architecture.items()
            if key in {"backend_tech_stack", "data_contract"}
        }
    return {
        key: value
        for key, value in architecture.items()
        if key in {
            "frontend",
            "backend_tech_stack",
            "data_contract",
            "route_root_path",
            "menu_enabled",
        }
    }


def _scoped_skeleton_api_contracts(
    project_plan: dict,
    build_context: dict,
    mode: str,
) -> list[dict]:
    """页面或 endpoint 模式只保留当前范围引用到的 API 契约骨架。"""

    contracts = _skeleton_api_contracts(project_plan)
    if mode == "combined":
        return contracts
    endpoint_ids = {
        str(endpoint_id)
        for endpoint_id in build_context.get("endpoint_ids") or []
        if str(endpoint_id).strip()
    }
    if not endpoint_ids:
        return []
    return [
        contract
        for contract in contracts
        if endpoint_ids.intersection(
            {
                str(endpoint_id)
                for endpoint_id in contract.get("endpoint_ids") or []
                if str(endpoint_id).strip()
            }
        )
    ]


def _skeleton_pages(project_plan: dict) -> list[dict]:
    """提取页面 Unit 骨架摘要，不携带完整页面实现契约。"""

    contract_status = {
        str(contract.get("pageId") or ""): "confirmed"
        for contract in project_plan.get("page_implementation_contracts", [])
        if isinstance(contract, dict) and contract.get("pageId")
    }

    return [
        {
            "pageId": page.get("pageId"),
            "name": page.get("name"),
            "path": page.get("path"),
            "module_id": page.get("module_id"),
            "description": page.get("description"),
            "implementation_contract_status": contract_status.get(
                str(page.get("pageId") or "")
            ),
        }
        for page in project_plan_page_records(project_plan)
        if isinstance(page, dict)
    ]


def _skeleton_data_sources(
    project_plan: dict,
    entity_ids: list[str] | None = None,
) -> list[dict]:
    """提取当前范围的数据源 Unit 骨架摘要，不携带数据源详情正文。"""

    allowed_entity_ids = {
        str(entity_id).strip()
        for entity_id in entity_ids or []
        if str(entity_id).strip()
    }
    scoped_sources: list[dict] = []
    for source in plan_data_sources(project_plan):
        if not isinstance(source, dict):
            continue
        source_entities = [
            entity
            for entity in source.get("entities") or []
            if isinstance(entity, dict)
            and (
                not allowed_entity_ids
                or str(entity.get("id") or "") in allowed_entity_ids
            )
        ]
        if allowed_entity_ids and not source_entities:
            continue
        scoped_sources.append(
            {
                "id": source.get("id"),
                "name": source.get("name"),
                "type": source.get("type"),
                "entities": source_entities,
                "schema_refs": source.get("schema_refs"),
                "detail_status": (
                    source.get("detail_design", {}).get("status")
                    if isinstance(source.get("detail_design"), dict)
                    else None
                ),
            }
        )
    return scoped_sources


def _skeleton_api_contracts(project_plan: dict) -> list[dict]:
    """提取 API 契约骨架摘要，完整字段契约只在 executable_details 中按范围暴露。"""

    return [
        {
            "id": contract.get("id"),
            "entity_ids": contract.get("entity_ids"),
            "base_path": contract.get("base_path"),
            "endpoint_ids": [
                endpoint.get("id")
                for endpoint in contract.get("endpoints", [])
                if isinstance(endpoint, dict) and endpoint.get("id")
            ],
        }
        for contract in project_plan.get("api_contracts", [])
        if isinstance(contract, dict)
    ]


def _executable_details(project_plan: dict, build_context: dict) -> dict:
    """按当前构建目标投射页面实现契约、endpoint 和 API 详情。"""

    endpoint_ids = {str(item) for item in build_context.get("endpoint_ids") or []}
    entity_ids = list(
        dict.fromkeys(
            str(item)
            for item in build_context.get("entity_ids") or []
            if str(item).strip()
        )
    )
    target = build_context.get("target") if isinstance(build_context.get("target"), dict) else {}
    is_application = str(target.get("type") or "") == "application"
    if is_application:
        entity_ids = _confirmed_entity_ids(project_plan)
    context_entity_designs = build_context.get("entity_designs")
    entity_designs = (
        [dict(item) for item in context_entity_designs if isinstance(item, dict)]
        if isinstance(context_entity_designs, list) and context_entity_designs
        else entity_design_summaries(project_plan, entity_ids)
    )
    scoped_contracts = _scoped_contracts(project_plan, build_context, is_application=is_application)
    return {
        "page_implementation_contracts": (
            [build_context["page_implementation_contract"]]
            if build_context.get("page_implementation_contract")
            else list(project_plan.get("page_implementation_contracts") or [])
            if build_context.get("target", {}).get("type") == "application"
            else []
        ),
        "endpoint_contracts": list(
            build_context.get("direct_endpoint_contracts") or []
        ),
        "entity_designs": entity_designs,
        "api_contracts": [
            _scoped_api_contract(contract, endpoint_ids)
            for contract in scoped_contracts
        ],
    }


def _confirmed_entity_ids(project_plan: dict) -> list[str]:
    """返回已确认实体设计的实体 id，供全量构建投射实体上下文。"""

    return [
        str(detail.get("entity_id") or "")
        for detail in project_plan.get("entity_detail_plans") or []
        if isinstance(detail, dict)
                      and str(detail.get("status") or "") == "confirmed"
                      and detail.get("entity_id")
    ]


def _scoped_entity_source_ids(project_plan: dict, entity_ids: set[str]) -> set[str]:
    """按范围内实体设计推导虚拟数据源 id（即实体数据源类型）。"""

    return {
        str(summary.get("data_source_type") or "")
        for summary in entity_design_summaries(project_plan, sorted(entity_ids))
        if summary.get("data_source_type")
    }


def _scoped_contracts(
        project_plan: dict,
        build_context: dict,
        *,
        is_application: bool = False,
) -> list[dict]:
    """按范围内 endpoint/实体/详情契约收敛 API 契约，契约只作 schema 引用。"""

    all_contracts = [
        contract
        for contract in project_plan.get("api_contracts", [])
        if isinstance(contract, dict)
    ]
    if is_application:
        return all_contracts
    endpoint_ids = {str(item) for item in build_context.get("endpoint_ids") or []}
    target_contract_ids = {
        str(endpoint.get("api_contract_id") or "")
        for endpoint in build_context.get("direct_endpoint_contracts") or []
        if isinstance(endpoint, dict) and endpoint.get("api_contract_id")
    }
    return [
        contract
        for contract in project_plan.get("api_contracts") or []
        if isinstance(contract, dict)
                        and (
                                str(contract.get("id") or "") in target_contract_ids
                                or any(
                            isinstance(endpoint, dict)
                            and str(endpoint.get("id") or "") in endpoint_ids
                            for endpoint in contract.get("endpoints") or []
                        )
                        )
    ]


def _scoped_api_contract(contract: dict, endpoint_ids: set[str]) -> dict:
    """保留当前目标 endpoint 及同契约 schemas，避免响应字段引用断裂。"""

    return {
        **contract,
        "endpoints": [
            endpoint
            for endpoint in contract.get("endpoints", [])
            if isinstance(endpoint, dict) and str(endpoint.get("id") or "") in endpoint_ids
        ],
    }


def _scoped_pages(project_plan: dict, target_page_id: str) -> list[dict[str, Any]]:
    """保留当前页面及其直接导航目标壳，忽略其他页面的全局错误。"""

    if not target_page_id:
        return []
    all_pages = [
        page
        for page in project_plan_page_records(project_plan)
        if isinstance(page, dict)
    ]
    target_page = next(
        (
            page
            for page in all_pages
            if str(page.get("pageId") or "") == target_page_id
        ),
        None,
    )
    if target_page is None:
        return []
    references = (
        target_page.get("references")
        if isinstance(target_page.get("references"), dict)
        else {}
    )
    navigation_targets = references.get("navigation_targets") or target_page.get(
        "navigation_targets"
    )
    navigation_ids = {
        str(item.get("targetPageId") or "")
        for item in navigation_targets or []
        if isinstance(item, dict) and item.get("targetPageId")
    }
    navigation_pages = [
        {
            "pageId": page.get("pageId"),
            "path": page.get("path"),
            "references": {
                "endpoint_dependencies": [],
                "navigation_targets": [],
            },
        }
        for page in all_pages
        if str(page.get("pageId") or "") in navigation_ids
    ]
    return [target_page, *navigation_pages]


def _scoped_contract_errors(
        project_plan: dict,
        build_execution_scope: dict[str, str],
        build_context: dict,
) -> list[str]:
    """按范围校验 API 契约：局部构建不受无关页面或数据源的错误阻塞。"""

    if build_execution_scope["type"] == "application":
        return [
            *validate_project_plan_dependencies(project_plan),
            *validate_api_contract_consistency(project_plan),
        ]
    scoped_plan = _scoped_contract_validation_plan(project_plan, build_context)
    return [
        *validate_project_plan_dependencies(scoped_plan),
        *validate_api_contract_consistency(scoped_plan),
    ]


def _scoped_contract_validation_plan(project_plan: dict, build_context: dict) -> dict:
    """投射当前页面、API Contract 与实体 id，排除数据源及范围外设计。"""

    endpoint_ids = {str(item) for item in build_context.get("endpoint_ids") or []}
    entity_ids = [
        str(item).strip()
        for item in build_context.get("entity_ids") or []
        if str(item).strip()
    ]
    target = build_context.get("target") if isinstance(build_context.get("target"), dict) else {}
    target_page_id = str(target.get("id") or "") if target.get("type") == "page" else ""
    pages = _scoped_pages(project_plan, target_page_id)
    contracts = _scoped_contracts(project_plan, build_context)
    page_field = (
        "pages"
        if project_plan.get("artifact_type") == "technical-plan"
        else "frontend_pages"
    )
    return {
        "artifact_type": project_plan.get("artifact_type"),
        page_field: pages,
        "entities": [{"id": entity_id} for entity_id in dict.fromkeys(entity_ids)],
        "api_contracts": [
            _scoped_api_contract(contract, endpoint_ids) for contract in contracts
        ],
        "page_implementation_contracts": (
            [build_context["page_implementation_contract"]]
            if build_context.get("page_implementation_contract")
            else []
        ),
        "endpoint_contracts": list(
            build_context.get("direct_endpoint_contracts") or []
        ),
    }


def _merge_prepared_scope_tasks(
        skeleton_plan: dict,
        prepared_plan: dict,
        build_context: dict,
        *,
        project_plan: dict | None = None,
) -> dict:
    """用本次范围任务替换同 Unit 旧任务，并保留其他已准备 Unit 的任务。"""

    required_unit_ids = set(build_context.get("required_unit_ids") or [])
    generated_tasks = tasks_from_build_task_plan(prepared_plan)
    if not generated_tasks and isinstance(prepared_plan.get("tasks"), list):
        generated_tasks = [task for task in prepared_plan["tasks"] if isinstance(task, dict)]
    if any(task.get("unit_id") == "frontend:shell" for task in generated_tasks):
        raise ValueError("frontend:shell 是 prerequisite_only，禁止产生 Candidate Task。")
    # 资源和路由注册由平台在 Build 启动前确定性执行；模型误输出时丢弃，
    # 并同步移除依赖，避免候选图引用不存在的节点。
    generated_tasks, ignored_platform_task_ids = strip_platform_owned_candidate_tasks(
        generated_tasks
    )
    out_of_scope_unit_ids = sorted(
        {
            str(task.get("unit_id") or "")
            for task in generated_tasks
            if str(task.get("unit_id") or "") not in required_unit_ids
        }
    )
    if out_of_scope_unit_ids:
        raise ValueError(
            "模型返回了当前构建范围以外的 Unit 任务："
            + "、".join(out_of_scope_unit_ids)
        )
    replaceable_unit_ids = _replaceable_unit_ids(
        skeleton_plan,
        build_context,
        required_unit_ids,
    )
    retained_tasks = [
        task
        for task in tasks_from_build_task_plan(skeleton_plan)
        if str(task.get("unit_id") or "") not in replaceable_unit_ids
    ]
    retained_tasks_by_unit = _tasks_by_unit_id(retained_tasks)
    generated_tasks, dropped_dependency_map = _drop_non_replaceable_unit_tasks(
        generated_tasks,
        replaceable_unit_ids=replaceable_unit_ids,
        retained_tasks_by_unit=retained_tasks_by_unit,
    )
    generated_tasks = _rewrite_generated_task_dependencies(
        generated_tasks,
        dropped_dependency_map,
    )
    retained_ids = {str(task.get("id") or "") for task in retained_tasks}
    generated_tasks = _rename_generated_task_id_conflicts(
        generated_tasks,
        reserved_ids=retained_ids,
    )
    replacement_dependency_map = _replacement_dependency_map(
        skeleton_plan,
        generated_tasks,
        replaceable_unit_ids,
    )
    retained_tasks = _rewrite_replaced_unit_dependencies(
        retained_tasks,
        replacement_dependency_map,
    )
    generated_tasks = _rewrite_replaced_unit_dependencies(
        generated_tasks,
        replacement_dependency_map,
    )
    acceptance_context = {
        **build_context,
        "project_plan": project_plan if isinstance(project_plan, dict) else {},
        # 保留任务是上一轮已存在的基线；不猜测其历史业务语义，只允许新编译结果保留结构化空字段。
        "_allow_missing_business_deliverable_task_ids": sorted(retained_ids),
    }
    merged = compile_build_task_plan_scope(
        skeleton_plan,
        merge_exact_duplicate_tasks([*retained_tasks, *generated_tasks]),
        acceptance_context,
        validate_task_scope=False,
        preserve_compiled_task_ids=retained_ids,
    )
    for unit_id, unit in (merged.get("build_units") or {}).items():
        if not isinstance(unit, dict) or unit_id not in replaceable_unit_ids:
            continue
        if unit.get("task_ids"):
            unit["status"] = "prepared"
            continue
        unit["status"] = "not_prepared"
    # 权限共享投影在 Build Run 绑定的计划顶层读取模板变体，不能只保留在调试用构建上下文。
    result = {
        **merged,
        "template_variant": str(build_context.get("template_variant") or ""),
        "prepared_by": {
            **(
                prepared_plan.get("prepared_by", merged.get("prepared_by", {}))
                if isinstance(
                    prepared_plan.get("prepared_by", merged.get("prepared_by", {})),
                    dict,
                )
                else {}
            ),
            **(
                {"ignoredPlatformCandidateTaskIds": ignored_platform_task_ids}
                if ignored_platform_task_ids
                else {}
            ),
        },
        "preparation_source": prepared_plan.get(
            "preparation_source", merged.get("preparation_source")
        ),
        "agent_note": prepared_plan.get("agent_note", merged.get("agent_note", "")),
        "build_context": build_context,
    }
    return result


def _tasks_by_unit_id(tasks: list[dict]) -> dict[str, list[dict]]:
    """按 Unit ID 分组任务，供复用已准备 Unit 时改写依赖。"""

    grouped: dict[str, list[dict]] = {}
    for task in tasks:
        grouped.setdefault(str(task.get("unit_id") or "application:root"), []).append(task)
    return grouped


def _drop_non_replaceable_unit_tasks(
        generated_tasks: list[dict],
        *,
        replaceable_unit_ids: set[str],
        retained_tasks_by_unit: dict[str, list[dict]],
) -> tuple[list[dict], dict[str, list[str]]]:
    """丢弃模型为已准备 Unit 返回的新任务，并记录依赖应指向的旧任务。"""

    kept_tasks: list[dict] = []
    dependency_map: dict[str, list[str]] = {}
    for task in generated_tasks:
        unit_id = str(task.get("unit_id") or "")
        task_id = str(task.get("id") or "").strip()
        if unit_id in replaceable_unit_ids:
            kept_tasks.append(task)
            continue
        retained_ids = [
            str(retained_task.get("id") or "")
            for retained_task in retained_tasks_by_unit.get(unit_id, [])
            if retained_task.get("id")
        ]
        if task_id:
            dependency_map[task_id] = retained_ids
    return kept_tasks, dependency_map


def _rewrite_generated_task_dependencies(
        generated_tasks: list[dict],
        dependency_map: dict[str, list[str]],
) -> list[dict]:
    """把被丢弃任务的依赖引用改为对应已保留任务。"""

    if not dependency_map:
        return generated_tasks
    return [
        _rewrite_task_dependencies(task, dependency_map)
        for task in generated_tasks
    ]


def _replacement_dependency_map(
        build_task_plan: dict,
        generated_tasks: list[dict],
        replaceable_unit_ids: set[str],
) -> dict[str, list[str]]:
    """按被替换 Unit 建立旧任务到新任务的映射，供全局依赖同步改写。"""

    old_tasks_by_unit = _tasks_by_unit_id(tasks_from_build_task_plan(build_task_plan))
    new_tasks_by_unit = _tasks_by_unit_id(generated_tasks)
    dependency_map: dict[str, list[str]] = {}
    for unit_id in replaceable_unit_ids:
        replacement_ids = [
            str(task.get("id") or "")
            for task in new_tasks_by_unit.get(unit_id, [])
            if task.get("id")
        ]
        for old_task in old_tasks_by_unit.get(unit_id, []):
            old_task_id = str(old_task.get("id") or "").strip()
            if old_task_id:
                dependency_map[old_task_id] = replacement_ids
    return dependency_map


def _rewrite_replaced_unit_dependencies(
        tasks: list[dict],
        dependency_map: dict[str, list[str]],
) -> list[dict]:
    """改写任务中的旧 Unit 任务依赖，并过滤替换映射产生的自依赖。"""

    if not dependency_map:
        return tasks
    rewritten_tasks: list[dict] = []
    for task in tasks:
        rewritten = _rewrite_task_dependencies(task, dependency_map)
        task_id = str(rewritten.get("id") or "")
        dependencies = [
            dependency
            for dependency in _task_dependency_list(rewritten)
            if dependency != task_id
        ]
        rewritten_tasks.append(
            {
                **rewritten,
                "dependencies": dependencies,
            }
        )
    return rewritten_tasks


def _rename_generated_task_id_conflicts(
        generated_tasks: list[dict],
        *,
        reserved_ids: set[str],
) -> list[dict]:
    """为本次模型任务规避已保留任务 ID，并同步改写本批任务依赖。"""

    id_map: dict[str, list[str]] = {}
    used_ids = set(reserved_ids)
    renamed_tasks: list[dict] = []
    for task in generated_tasks:
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            renamed_tasks.append(task)
            continue
        next_id = task_id
        if next_id in used_ids:
            next_id = _unique_scoped_task_id(task, task_id, used_ids)
            id_map[task_id] = [next_id]
        used_ids.add(next_id)
        renamed_tasks.append(
            {
                **task,
                "id": next_id,
            }
        )
    if not id_map:
        return renamed_tasks
    return [_rewrite_task_dependencies(task, id_map) for task in renamed_tasks]


def _unique_scoped_task_id(
        task: dict,
        task_id: str,
        used_ids: set[str],
) -> str:
    """按 Unit ID 生成稳定任务前缀，直到避开已有 ID。"""

    unit_slug = _task_unit_slug(str(task.get("unit_id") or "application:root"))
    base_id = f"{unit_slug}--{task_id}"
    candidate = base_id
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base_id}-{suffix}"
        suffix += 1
    return candidate


def _task_unit_slug(unit_id: str) -> str:
    """把 Unit ID 转成可读、稳定的任务 ID 前缀。"""

    slug = "".join(
        char.lower() if char.isalnum() else "-"
        for char in unit_id.strip()
    ).strip("-")
    return slug or "application-root"


def _rewrite_task_dependencies(task: dict, id_map: dict[str, list[str]]) -> dict:
    """把本次被重命名任务的依赖引用同步改成新 ID。"""

    dependencies: list[str] = []
    for dependency in _task_dependency_list(task):
        replacements = id_map.get(dependency)
        dependencies.extend(replacements if replacements is not None else [dependency])
    return {
        **task,
        "dependencies": list(dict.fromkeys(dependencies)),
    }


def _task_dependency_list(task: dict) -> list[str]:
    """读取任务依赖列表，过滤非字符串形式的空值。"""

    value = task.get("dependencies") or []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _replaceable_unit_ids(
        build_task_plan: dict,
        build_context: dict,
        required_unit_ids: set[str],
) -> set[str]:
    """仅替换目标 Unit 与尚无任务的依赖 Unit，已准备依赖任务始终复用。"""

    target = build_context.get("target") if isinstance(build_context.get("target"), dict) else {}
    required_unit_ids = required_unit_ids - {"frontend:shell"}
    if target.get("type") == "application":
        return required_unit_ids
    target_unit_id = _target_unit_id(target)
    units = build_task_plan.get("build_units") or {}
    replaceable: set[str] = set()
    for unit_id in required_unit_ids:
        unit = units.get(unit_id) if isinstance(units, dict) else {}
        has_tasks = isinstance(unit, dict) and bool(unit.get("task_ids"))
        if unit_id == target_unit_id or not has_tasks:
            replaceable.add(unit_id)
    return replaceable


def _retained_frontend_endpoint_owner_constraints(
    build_task_plan: dict,
    replaceable_unit_ids: set[str],
) -> tuple[list[dict[str, str]], list[str]]:
    """按实际合并边界从保留的普通任务实时提取 Endpoint owner 约束。"""

    retained_tasks = [
        task
        for task in tasks_from_build_task_plan(build_task_plan)
        if str(task.get("unit_id") or "") not in replaceable_unit_ids
    ]
    errors = frontend_endpoint_ownership_errors(retained_tasks)
    constraints = sorted(
        [
            {
                "api_contract_id": str(owner.get("api_contract_id") or ""),
                "endpoint_id": str(owner.get("endpoint_id") or ""),
                "owner_task_id": str(owner.get("owner_task_id") or ""),
                "owner_unit_id": str(owner.get("owner_unit_id") or ""),
                "policy": "reuse_only",
            }
            for owner in frontend_endpoint_implementation_owners(retained_tasks)
        ],
        key=lambda item: (
            item["api_contract_id"].casefold(),
            item["endpoint_id"].casefold(),
            item["owner_task_id"],
        ),
    )
    return constraints, errors


def _target_unit_id(target: dict) -> str:
    """将构建目标转换成 Unit ID，供局部 DAG 判断替换边界。"""

    target_type = str(target.get("type") or "")
    target_id = str(target.get("id") or "")
    if target_type == "page" and target_id:
        return f"page:{target_id}"
    if target_type == "endpoint" and target_id:
        api_contract_id = str(target.get("api_contract_id") or "").strip()
        return f"backend:endpoint:{api_contract_id}:{target_id}" if api_contract_id else ""
    return ""


def _api_contract_inconsistency_payload(
    errors: list[str],
    build_execution_scope: dict[str, str],
) -> dict:
    """构造契约不一致时由用户手动处理的结构化阻断说明。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="契约校验",
                question=(
                    "API 契约与数据源或页面字段引用不一致，已阻止代码生成。"
                    "请返回项目规划阶段修订契约后再继续。"
                ),
                type="text",
                placeholder="例如：请按校验错误修订 API 契约和页面字段引用。",
            )
        ]
    )
    payload.update(
        {
            "mode": "api_contract_consistency_error",
            "code": "api_contract_consistency_error",
            "message": "API 契约一致性校验失败，已阻止任务拆分和代码生成。",
            "target": build_execution_scope,
            "artifact": ".xcodeagent/plans/technical-plan.json",
            "errors": errors,
            "recommended_action": "手动修订并确认 API 契约或页面字段引用后重新发起 DAG 生成。",
            "automatic_routing": False,
        }
    )
    return payload


def _build_context_error_payload(
    error: str,
    build_execution_scope: dict[str, str],
) -> dict:
    """构造目标页面或 endpoint 详情不足时的 AG-UI 阻止说明。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="构建范围",
                question=(
                    "当前构建范围缺少已确认的页面/endpoint 详情或 API 契约依赖，"
                    "暂不能生成可验证代码。请返回页面设计阶段补齐后再继续。"
                ),
                type="text",
                placeholder="例如：返回页面设计阶段，补齐该页面的 API 依赖。",
            )
        ]
    )
    payload.update(
        {
            "mode": "build_context_error",
            "code": "build_context_incomplete",
            "message": "目标构建上下文不完整，已阻止任务拆分和代码生成。",
            "target": build_execution_scope,
            "artifact": "PageImplementationContract / Endpoint Contract / EntitySourceBinding",
            "errors": [error],
            "recommended_action": "手动补齐并确认缺失的范围详情后重新发起 DAG 生成。",
            "automatic_routing": False,
        }
    )
    return payload


def _build_task_plan_generation_failed_result(
    project_plan: dict,
    build_task_plan: dict,
    progress: Any,
    error: str,
    build_execution_scope: dict[str, str],
) -> dict:
    """构造自动重生成耗尽后的平台失败结果，不把平台边界问题交给用户修正。"""

    reason = str(error or "Build DAG 自动重生成失败。").strip()
    failed_plan = _build_task_plan_attempt_view(
        build_task_plan,
        build_execution_scope,
        status="failed",
    )
    persisted_scope = build_task_plan.get("build_execution_scope")
    return {
        "phase": "prepare_build_tasks",
        "status": "failed",
        "project_plan": project_plan,
        "build_task_plan": failed_plan,
        "build_execution_scope": build_execution_scope,
        "last_persisted_build_execution_scope": (
            persisted_scope if isinstance(persisted_scope, dict) else None
        ),
        "build_task_plan_persisted": False,
        "dag_generation_progress": progress.snapshot(),
        "error": reason,
        "message": "Build DAG 自动重生成未得到有效任务计划，已停止代码生成。",
        "timeline": ["prepare_build_tasks"],
    }


def _retained_endpoint_owner_blocked_result(
    project_plan: dict,
    build_task_plan: dict,
    progress: Any,
    errors: list[str],
    build_execution_scope: dict[str, str],
) -> dict:
    """将保留基线 owner 冲突投影为用户手动处理提示，不自动回退上游。"""

    failed_plan = _build_task_plan_attempt_view(
        build_task_plan,
        build_execution_scope,
        status="failed",
    )
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="DAG 基线冲突",
                question=(
                    "当前已保留的 Build 任务对同一前端 Endpoint 声明了多个实现 owner，"
                    "平台无法安全选择其中一个。请手动修正现有任务规划后重新发起 DAG 生成。"
                ),
                type="text",
                placeholder="请保留一个 API 模块 owner，并让页面任务只复用该实现。",
            )
        ]
    )
    payload.update(
        {
            "mode": "retained_endpoint_owner_conflict",
            "code": "retained_frontend_endpoint_owner_conflict",
            "message": "保留 Build DAG 已存在前端 Endpoint 多 owner，未调用模型生成新候选。",
            "target": build_execution_scope,
            "artifact": ".xcodeagent/plans/build-task-plan.json",
            "errors": errors,
            "recommended_action": "手动修正现有 Build Task Plan 的重复 API 实现归属后重新生成 DAG。",
            "automatic_routing": False,
        }
    )
    persisted_scope = build_task_plan.get("build_execution_scope")
    return {
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "build_task_plan": failed_plan,
        "build_execution_scope": build_execution_scope,
        "last_persisted_build_execution_scope": (
            persisted_scope if isinstance(persisted_scope, dict) else None
        ),
        "build_task_plan_persisted": False,
        "dag_generation_progress": progress.snapshot(),
        "clarification": payload,
        "message": payload["message"],
        "timeline": ["prepare_build_tasks"],
    }


def _build_task_plan_attempt_view(
    build_task_plan: dict,
    build_execution_scope: dict[str, str],
    *,
    status: str | None = None,
) -> dict:
    """为本次 DAG 尝试投影当前 scope，失败时不把它写回上一次成功的 JSON。"""

    view = {
        **build_task_plan,
        "build_execution_scope": dict(build_execution_scope),
    }
    if status:
        view["status"] = status
    return view
