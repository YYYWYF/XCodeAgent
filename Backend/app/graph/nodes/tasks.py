from app.agents.main.document_sync import sync_project_plan_from_markdown
from app.agents.main.planner import revise_project_plan_with_chat_model
from app.agents.main.task_preparer import prepare_build_tasks_with_main_agent
from app.graph.nodes.confirmation import (
    user_confirmed_text,
    user_requested_changes_text,
)
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.services.api_contract_validation import validate_api_contract_consistency
from app.services.build_context_resolver import resolve_target_build_context
from app.services.build_task_progress import (
    build_task_artifacts,
    create_build_task_progress_tracker,
)
from app.services.build_task_planner import (
    compile_build_task_plan_scope,
    tasks_from_build_task_plan,
)
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.page_dependencies import validate_project_plan_dependencies
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.plan_documents import (
    edited_project_plan_markdown,
    load_project_plan_json,
    project_plan_json_path,
    project_plan_markdown_path,
    write_project_plan_document,
    write_project_plan_json,
)
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_build_task_plan_json,
    write_build_task_dag_markdown,
    write_build_task_plan_json,
)
from app.workspace.workspace_snapshot_documents import load_workspace_snapshot_json


def _latest_compact_project_plan(state: ProjectState) -> dict:
    """优先从 ProjectPlan JSON 读取最新轻量计划，避免 Build 使用 checkpoint 中的内嵌详情旧对象。"""

    project_plan = state["project_plan"]
    if project_plan.get("confirmation_status") != "confirmed":
        return project_plan
    if not state.get("project_plan_json_path"):
        return project_plan
    path = project_plan_json_path(state)
    if path.is_file():
        return load_project_plan_json(path)
    return project_plan


def prepare_build_tasks(state: ProjectState) -> dict:
    """按应用、页面或数据源范围编译任务子图并持久化 Build DAG。"""
    project_plan = _latest_compact_project_plan(state)
    if project_plan.get("confirmation_status") != "confirmed":
        if _user_confirmed_project_plan(state.get("request", "")):
            edited_markdown = edited_project_plan_markdown(state, project_plan)
            synchronized_plan = (
                sync_project_plan_from_markdown(
                    project_plan,
                    state["requirement_spec"],
                    edited_markdown,
                )
                if edited_markdown is not None
                else project_plan
            )
            project_plan = {
                **synchronized_plan,
                "confirmation_status": "confirmed",
            }
            if project_plan_markdown_path(state).is_file():
                write_project_plan_json(state, project_plan)
            else:
                write_project_plan_document(state, project_plan)
        elif user_requested_changes_text(state.get("request", "")):
            project_plan = revise_project_plan_with_chat_model(
                project_plan,
                state.get("request", ""),
            )
            project_plan_path = write_project_plan_document(state, project_plan)
            clarification = _project_plan_confirmation_payload(project_plan)
            return {
                "phase": "prepare_build_tasks",
                "status": "requires_user_input",
                "project_plan": project_plan,
                "project_plan_path": project_plan_path,
                "clarification": clarification,
                "timeline": ["prepare_build_tasks"],
            }
        else:
            clarification = _project_plan_confirmation_payload(project_plan)
            return {
                "phase": "prepare_build_tasks",
                "status": "requires_user_input",
                "project_plan": project_plan,
                "clarification": clarification,
                "timeline": ["prepare_build_tasks"],
            }

    workspace = workspace_from_state(state)
    workspace_snapshot = _workspace_snapshot_from_state(state)
    build_execution_scope = _build_execution_scope_from_state(state)
    existing_build_task_plan = _existing_build_task_plan(state)
    progress = create_build_task_progress_tracker()

    progress.start("unit_skeleton", "正在根据已确认项目计划生成 Unit DAG 骨架。")
    try:
        build_task_plan = ensure_build_unit_skeleton(
            project_plan,
            workspace_snapshot,
            existing_build_task_plan,
        )
    except Exception as exc:
        progress.fail("unit_skeleton", f"Unit DAG 骨架生成失败：{exc}")
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
        progress.fail("build_context", f"构建上下文解析失败：{exc}")
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": build_task_plan,
            "dag_generation_progress": progress.snapshot(),
            "clarification": _build_context_error_payload(str(exc)),
            "timeline": ["prepare_build_tasks"],
        }
    except Exception as exc:
        progress.fail("build_context", f"构建上下文解析异常：{exc}")
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
    )

    progress.start("contract_validation", "正在校验页面依赖和 API 契约一致性。")
    try:
        contract_errors = _scoped_contract_errors(
            project_plan,
            build_execution_scope,
            build_context,
        )
    except Exception as exc:
        progress.fail("contract_validation", f"契约校验异常：{exc}")
        raise
    if contract_errors:
        progress.fail(
            "contract_validation",
            f"契约校验发现 {len(contract_errors)} 个问题：{contract_errors[0]}",
            build_task_plan=build_task_plan,
        )
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": build_task_plan,
            "dag_generation_progress": progress.snapshot(),
            "clarification": _api_contract_inconsistency_payload(contract_errors),
            "timeline": ["prepare_build_tasks"],
        }
    progress.complete(
        "contract_validation",
        "页面依赖与 API 契约校验通过。",
        build_task_plan=build_task_plan,
    )

    progress.start("model_planning", "正在调用任务规划模型生成候选构建任务。")
    try:
        prepared_plan = prepare_build_tasks_with_main_agent(
            _task_preparation_project_plan(project_plan, build_context),
            workspace=workspace,
            workspace_snapshot=workspace_snapshot,
            build_context=build_context,
            build_task_plan=build_task_plan,
        )
    except ValueError as exc:
        progress.fail("model_planning", f"候选任务生成失败：{exc}")
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": build_task_plan,
            "dag_generation_progress": progress.snapshot(),
            "clarification": _build_task_plan_generation_error_payload(str(exc)),
            "timeline": ["prepare_build_tasks"],
        }
    except Exception as exc:
        progress.fail("model_planning", f"候选任务生成异常：{exc}")
        raise
    prepared_tasks = tasks_from_build_task_plan(prepared_plan)
    progress.complete(
        "model_planning",
        f"任务规划模型已生成 {len(prepared_tasks)} 个有效候选任务。",
        build_task_plan=prepared_plan,
    )

    progress.start("task_compilation", "正在归一化任务并编译 Unit 与任务依赖。")
    try:
        build_task_plan = _merge_prepared_scope_tasks(
            build_task_plan,
            prepared_plan,
            build_context,
        )
    except ValueError as exc:
        progress.fail(
            "task_compilation",
            f"任务依赖编译失败：{exc}",
            build_task_plan=build_task_plan,
        )
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": build_task_plan,
            "dag_generation_progress": progress.snapshot(),
            "clarification": _build_task_plan_generation_error_payload(str(exc)),
            "timeline": ["prepare_build_tasks"],
        }
    except Exception as exc:
        progress.fail(
            "task_compilation",
            f"任务依赖编译异常：{exc}",
            build_task_plan=build_task_plan,
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
    )

    progress.start("dag_validation", "正在校验任务拓扑、循环依赖和执行批次。")
    dag_errors = (
        build_task_plan.get("task_graph", {})
        .get("validation", {})
        .get("errors", [])
    )
    if dag_errors:
        progress.fail(
            "dag_validation",
            f"任务 DAG 校验发现 {len(dag_errors)} 个问题：{dag_errors[0]}",
            build_task_plan=build_task_plan,
        )
        return {
            "phase": "prepare_build_tasks",
            "status": "requires_user_input",
            "project_plan": project_plan,
            "build_task_plan": build_task_plan,
            "dag_generation_progress": progress.snapshot(),
            "clarification": _build_task_plan_validation_error_payload(dag_errors),
            "timeline": ["prepare_build_tasks"],
        }
    execution = build_task_plan.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    progress.complete(
        "dag_validation",
        f"任务 DAG 校验通过，共 {len(execution.get('batches') or [])} 个执行批次。",
        build_task_plan=build_task_plan,
    )

    progress.start("artifact_persistence", "正在保存内部任务计划和 Markdown DAG。")
    try:
        build_task_plan_path = write_build_task_plan_json(state, build_task_plan)
        build_task_dag_path = write_build_task_dag_markdown(state, build_task_plan)
    except Exception as exc:
        progress.fail(
            "artifact_persistence",
            f"DAG 产物保存失败：{exc}",
            build_task_plan=build_task_plan,
        )
        raise
    artifacts = build_task_artifacts(build_task_dag_path)
    progress.complete(
        "artifact_persistence",
        "内部 Build Task Plan 与 BUILD_TASK_DAG.md 已保存。",
        build_task_plan=build_task_plan,
        artifacts=artifacts,
    )
    return {
        "phase": "prepare_build_tasks",
        "status": "completed",
        "project_plan": project_plan,
        "build_task_plan": build_task_plan,
        "dag_generation_progress": progress.snapshot(),
        "build_task_plan_path": build_task_plan_path,
        "build_task_dag_path": build_task_dag_path,
        "build_execution_scope": build_execution_scope,
        "build_context": build_context,
        "build_units": build_task_plan.get("build_units", {}),
        "unit_graph": build_task_plan.get("unit_graph", {}),
        "task_registry": build_task_plan.get("task_registry", {}),
        "task_graph": build_task_plan.get("task_graph", {}),
        "tasks": tasks_from_build_task_plan(build_task_plan),
        "timeline": ["prepare_build_tasks"],
    }


def _project_plan_confirmation_payload(project_plan: dict) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="计划确认",
                question=(
                    "代码生成即将开始，但当前项目计划尚未由用户确认。"
                    "请确认项目规划书是否正确。正确请回复“正确，继续”；"
                    "如需调整，请说明要修改的架构、API、页面、数据源、权限或验收标准。"
                ),
                type="text",
                placeholder="例如：正确，继续 / 需要增加审批流 API。",
            )
        ]
    )
    payload["mode"] = "project_plan_confirmation"
    payload["message"] = "项目计划未确认，已阻止任务拆分和代码生成。"
    payload["plan_summary"] = project_plan.get("app", {}).get("name", "未命名应用")
    return payload


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
        if target_type in {"application", "page", "data_source"}:
            return {
                "type": target_type,
                "targetId": target_id or "application",
            }
    selected_page_id = str(state.get("selectedPageId") or "").strip()
    return (
        {"type": "page", "targetId": selected_page_id}
        if selected_page_id
        else {"type": "application", "targetId": "application"}
    )


def _existing_build_task_plan(state: ProjectState) -> dict:
    """优先读取 checkpoint 计划，必要时从工作区持久化 DAG 恢复全局 Unit 骨架。"""

    in_state = state.get("build_task_plan")
    if isinstance(in_state, dict) and in_state.get("schema_version") == "build-dag.v2":
        return in_state
    plan_path = build_task_plan_json_path(state)
    if not plan_path.is_file():
        return {}
    persisted = load_build_task_plan_json(plan_path)
    return persisted if persisted.get("schema_version") == "build-dag.v2" else {}


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
            project_plan_path=state.get("project_plan_json_path")
            or project_plan_json_path(state),
        )
        return _add_reusable_task_context(context, build_task_plan)
    return _add_reusable_task_context({
        "target": {"type": "application", "id": "application"},
        "page_detail": None,
        "data_source_detail": None,
        "direct_data_source_details": [],
        "endpoint_ids": [],
        "data_source_ids": [],
        "required_unit_ids": list((build_task_plan.get("build_units") or {}).keys()),
        "source_refs": {},
    }, build_task_plan)


def _add_reusable_task_context(build_context: dict, build_task_plan: dict) -> dict:
    """向模型公开已完成准备的公共任务，避免后续页面重复生成公共模块。"""

    reusable_tasks = {
        unit_id: list(unit.get("task_ids") or [])
        for unit_id, unit in (build_task_plan.get("build_units") or {}).items()
        if isinstance(unit, dict)
        and unit_id.startswith("app:")
        and unit.get("task_ids")
    }
    return {**build_context, "reusable_tasks_by_unit": reusable_tasks}


def _task_preparation_project_plan(project_plan: dict, build_context: dict) -> dict:
    """构造任务拆分视图：ProjectPlan 只作 Unit 骨架，Detail 才作可执行任务输入。"""

    return {
        "version": project_plan.get("version"),
        "confirmation_status": project_plan.get("confirmation_status"),
        "app": project_plan.get("app"),
        "requirements_overview": project_plan.get("requirements_overview"),
        "architecture": project_plan.get("architecture"),
        "project_acceptance_criteria": project_plan.get("project_acceptance_criteria"),
        "application_skeleton": {
            "pages": _skeleton_pages(project_plan),
            "data_sources": _skeleton_data_sources(project_plan),
            "api_contracts": _skeleton_api_contracts(project_plan),
            "permission_model": project_plan.get("permission_model"),
        },
        "execution_target": build_context.get("target"),
        "allowed_unit_ids": list(build_context.get("required_unit_ids") or []),
        "executable_details": _executable_details(project_plan, build_context),
    }


def _skeleton_pages(project_plan: dict) -> list[dict]:
    """提取页面 Unit 骨架摘要，不携带可执行页面详情。"""

    return [
        {
            "pageId": page.get("pageId"),
            "name": page.get("name"),
            "path": page.get("path"),
            "module_id": page.get("module_id"),
            "description": page.get("description"),
            "detail_status": (
                page.get("detail_design", {}).get("status")
                if isinstance(page.get("detail_design"), dict)
                else None
            ),
        }
        for page in project_plan.get("frontend_pages", [])
        if isinstance(page, dict)
    ]


def _skeleton_data_sources(project_plan: dict) -> list[dict]:
    """提取数据源 Unit 骨架摘要，不携带数据源详情正文。"""

    return [
        {
            "id": source.get("id"),
            "name": source.get("name"),
            "type": source.get("type"),
            "entities": source.get("entities"),
            "schema_refs": source.get("schema_refs"),
            "detail_status": (
                source.get("detail_design", {}).get("status")
                if isinstance(source.get("detail_design"), dict)
                else None
            ),
        }
        for source in project_plan.get("data_sources", [])
        if isinstance(source, dict)
    ]


def _skeleton_api_contracts(project_plan: dict) -> list[dict]:
    """提取 API 契约骨架摘要，完整字段契约只在 executable_details 中按范围暴露。"""

    return [
        {
            "id": contract.get("id"),
            "data_source_id": contract.get("data_source_id"),
            "resource": contract.get("resource"),
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
    """按当前构建目标投射可执行任务所需的页面、数据源和 API 详情。"""

    endpoint_ids = {str(item) for item in build_context.get("endpoint_ids") or []}
    source_ids = {str(item) for item in build_context.get("data_source_ids") or []}
    return {
        "page_detail_plans": (
            [build_context["page_detail"]] if build_context.get("page_detail") else []
        ),
        "data_source_detail_plans": list(
            build_context.get("direct_data_source_details") or []
        ),
        "data_sources": [
            source
            for source in project_plan.get("data_sources", [])
            if isinstance(source, dict) and str(source.get("id") or "") in source_ids
        ],
        "api_contracts": [
            _scoped_api_contract(contract, endpoint_ids)
            for contract in project_plan.get("api_contracts", [])
            if isinstance(contract, dict)
            and str(contract.get("data_source_id") or "") in source_ids
        ],
    }


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
    validation_plan = _scoped_contract_validation_plan(project_plan, build_context)
    return [
        *validate_project_plan_dependencies(validation_plan),
        *validate_api_contract_consistency(validation_plan),
    ]


def _scoped_contract_validation_plan(project_plan: dict, build_context: dict) -> dict:
    """投射目标详情、直接数据源和其 API 契约，供局部构建执行独立校验。"""

    source_ids = {str(item) for item in build_context.get("data_source_ids") or []}
    target = build_context.get("target") if isinstance(build_context.get("target"), dict) else {}
    target_page_id = str(target.get("id") or "") if target.get("type") == "page" else ""
    pages = []
    for page in project_plan.get("frontend_pages", []):
        if not isinstance(page, dict):
            continue
        if str(page.get("pageId") or "") == target_page_id:
            pages.append(page)
        else:
            pages.append(
                {
                    "pageId": page.get("pageId"),
                    "path": page.get("path"),
                    "references": {
                        "endpoint_dependencies": [],
                        "navigation_targets": [],
                    },
                }
            )
    return {
        **project_plan,
        "frontend_pages": pages,
        "data_sources": [
            source
            for source in project_plan.get("data_sources", [])
            if isinstance(source, dict) and str(source.get("id") or "") in source_ids
        ],
        "api_contracts": [
            contract
            for contract in project_plan.get("api_contracts", [])
            if isinstance(contract, dict)
            and str(contract.get("data_source_id") or "") in source_ids
        ],
        "page_detail_plans": (
            [build_context["page_detail"]] if build_context.get("page_detail") else []
        ),
        "data_source_detail_plans": list(
            build_context.get("direct_data_source_details") or []
        ),
    }


def _merge_prepared_scope_tasks(
    skeleton_plan: dict,
    prepared_plan: dict,
    build_context: dict,
) -> dict:
    """用本次范围任务替换同 Unit 旧任务，并保留其他已准备 Unit 的任务。"""

    required_unit_ids = set(build_context.get("required_unit_ids") or [])
    generated_tasks = tasks_from_build_task_plan(prepared_plan)
    if not generated_tasks and isinstance(prepared_plan.get("tasks"), list):
        generated_tasks = [task for task in prepared_plan["tasks"] if isinstance(task, dict)]
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
    merged = compile_build_task_plan_scope(
        skeleton_plan,
        [*retained_tasks, *generated_tasks],
        build_context,
    )
    for unit_id, unit in (merged.get("build_units") or {}).items():
        if not isinstance(unit, dict) or unit_id not in replaceable_unit_ids:
            continue
        if unit.get("task_ids"):
            unit["status"] = "prepared"
            continue
        reuse_evidence = _existing_application_unit_evidence(unit_id, prepared_plan)
        if reuse_evidence:
            unit["status"] = "reused"
            unit["reuse_evidence"] = reuse_evidence
        else:
            unit["status"] = "not_prepared"
    result = {
        **merged,
        "prepared_by": prepared_plan.get("prepared_by", merged.get("prepared_by", {})),
        "preparation_source": prepared_plan.get(
            "preparation_source", merged.get("preparation_source")
        ),
        "agent_note": prepared_plan.get("agent_note", merged.get("agent_note", "")),
        "build_context": build_context,
    }
    return result


def _existing_application_unit_evidence(
    unit_id: str,
    prepared_plan: dict,
) -> dict[str, object] | None:
    """根据任务规划前的工作区检查证据识别可复用的前端壳与路由 Unit。"""

    if unit_id not in {"app:frontend-shell", "app:route-registry"}:
        return None
    analysis = prepared_plan.get("workspace_analysis")
    if not isinstance(analysis, dict) or analysis.get("inspection_status") != "completed":
        return None
    paths = [
        str(path)
        for key in ("entry_files", "inspected_directories")
        for path in analysis.get(key, [])
        if str(path).strip()
    ]
    lowered = [path.lower() for path in paths]
    if unit_id == "app:frontend-shell":
        matched = [
            path
            for path, normalized in zip(paths, lowered)
            if any(token in normalized for token in ("package.json", "/main.", "/app."))
        ]
    else:
        matched = [
            path
            for path, normalized in zip(paths, lowered)
            if any(token in normalized for token in ("route", "router"))
        ]
    if not matched:
        return None
    return {
        "source": "workspace_snapshot",
        "paths": matched,
        "reason": "Existing capability is reused; integration_test owns verification.",
    }


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
        task_id = str(task.get("id") or task.get("task_id") or "").strip()
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
        task_id = str(task.get("id") or task.get("task_id") or "").strip()
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
                "task_id": next_id,
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
        "dependsOn": list(dict.fromkeys(dependencies)),
    }


def _task_dependency_list(task: dict) -> list[str]:
    """读取任务依赖列表，过滤非字符串形式的空值。"""

    value = task.get("dependencies") or task.get("dependsOn") or []
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


def _target_unit_id(target: dict) -> str:
    """将构建目标转换成 Unit ID，供局部 DAG 判断替换边界。"""

    target_type = str(target.get("type") or "")
    target_id = str(target.get("id") or "")
    if target_type == "page" and target_id:
        return f"page:{target_id}"
    if target_type == "data_source" and target_id:
        return f"data-source:{target_id}"
    return ""


def _api_contract_inconsistency_payload(errors: list[str]) -> dict:
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
    payload["mode"] = "api_contract_consistency_error"
    payload["message"] = "API 契约一致性校验失败，已阻止任务拆分和代码生成。"
    payload["errors"] = errors
    return payload


def _build_context_error_payload(error: str) -> dict:
    """构造目标页面或数据源详情不足时的 AG-UI 阻止说明。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="构建范围",
                question=(
                    "当前构建范围缺少已确认的页面/数据源详情或 API 契约依赖，"
                    "暂不能生成可验证代码。请返回页面设计阶段补齐后再继续。"
                ),
                type="text",
                placeholder="例如：返回页面设计阶段，补齐该页面的 API 依赖。",
            )
        ]
    )
    payload["mode"] = "build_context_error"
    payload["message"] = "目标构建上下文不完整，已阻止任务拆分和代码生成。"
    payload["error"] = error
    return payload


def _build_task_plan_generation_error_payload(error: str) -> dict:
    """构造任务拆分失败提示，并将底层错误直接呈现在前端问题文本中。"""

    error_message = str(error or "").strip() or "未提供底层错误信息。"
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="任务拆分",
                question=(
                    "Build DAG 生成失败，模型没有返回可执行的任务列表。"
                    f"失败原因：{error_message}\n"
                    "请确认是否重试任务拆分，或返回项目规划阶段调整计划。"
                ),
                type="text",
                placeholder="例如：请重试任务拆分 / 返回项目规划阶段补充任务边界。",
            )
        ]
    )
    payload["mode"] = "build_task_plan_generation_error"
    payload["message"] = "Build DAG 生成失败，已阻止代码生成。"
    payload["error"] = error
    return payload


def _build_task_plan_validation_error_payload(errors: list[str]) -> dict:
    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="DAG 校验",
                question=(
                    "Build DAG 校验失败，存在缺失依赖或循环依赖。"
                    "请确认是否重试任务拆分，或返回项目规划阶段调整任务边界。"
                ),
                type="text",
                placeholder="例如：请重试任务拆分 / 返回项目规划阶段补充依赖关系。",
            )
        ]
    )
    payload["mode"] = "build_task_plan_validation_error"
    payload["message"] = "Build DAG 校验失败，已阻止代码生成。"
    payload["errors"] = errors
    return payload


def _user_confirmed_project_plan(request: str) -> bool:
    return user_confirmed_text(
        request,
        positive_signals=("正确", "没问题", "继续", "可以继续", "无误", "确认"),
        negative_signals=(
            "不正确",
            "需要修改",
            "要修改",
            "请修改",
            "想修改",
            "修改一下",
            "去修改",
            "重新修改",
            "需要调整",
            "要调整",
            "请调整",
            "调整一下",
            "需要补充",
            "要补充",
            "请补充",
            "补充一下",
            "不对",
        ),
    )
