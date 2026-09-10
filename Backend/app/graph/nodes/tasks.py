"""Build DAG 当前 async Unit Planner 共用的正式产物与范围投影辅助函数。

生产图的任务规划节点由 ``task_planning_adapter`` 提供；本模块不再提供旧的
Scope 级 TaskPreparer 或模型任务生成入口。
"""

import json
from pathlib import Path
from typing import Any

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
from app.services.template_scaffold_injection import prebuilt_files_for_plan
from app.services.api_design import api_design_readiness
from app.services.application_template_generation import inspect_template_generation_readiness
from app.services.build_task_planner import tasks_from_build_task_plan
from app.services.frontend_page_tree import project_plan_page_records
from app.services.page_dependencies import validate_project_plan_dependencies
from app.services.planning_issues import ValidationIssue
from app.services.page_implementation_contract import materialize_technical_plan_runtime
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.endpoint_design_documents import technical_plan_path
from app.workspace.plan_documents import (
    load_project_plan_json,
    project_plan_json_path,
)
from app.workspace.spec_documents import workspace_root
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_confirmed_build_task_plan,
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
            readiness = api_design_readiness(
                workspace or "",
                project_plan,
                target_type=target_type,
                target_id=target_id,
                api_contract_id=str(
                    scope.get("apiContractId") or scope.get("api_contract_id") or ""
                ).strip() or None,
            )
            if not readiness.get("ready"):
                missing = "、".join(
                    f"{item.get('method')} {item.get('path')}"
                    for item in readiness.get("missing_api_designs", [])
                    if isinstance(item, dict)
                )
                errors.append(f"API 设计未完成或已失效：{missing}。")
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


def clear_planning_projection() -> dict[str, Any]:
    """清除上一轮 PlanningRun/Pending 的只读投影，避免 checkpoint 延续旧身份。

    Graph state 按 key 合并，本轮没有产生 PlanningRun 的阻断或失败结果必须显式
    覆盖这些字段，否则会出现 status=requires_user_input 却挂着上一轮
    planning_run_id/draft_digest/dag_generation_progress 的错配。
    调用方用 ``{**clear_planning_projection(), **本轮结果}`` 合并，本轮真实写入的
    dag_generation_progress 或 persisted 事实仍以本轮结果为准。
    """

    return {
        "planning_run_id": "",
        "draft_digest": "",
        "dag_generation_progress": {},
        "build_task_plan_confirmation": {},
        "pending_build_task_plan_path": "",
        "pending_build_task_plan_persisted": False,
        "build_task_plan_persisted": False,
    }


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
                    "请返回对应的规划、模板初始化或 Endpoint API 设计流程处理。"
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
                "template-generation-manifest.json / Endpoint API Design"
            ),
            "recommended_action": "手动完成并确认错误所指向的前置产物后重新发起 DAG 生成。",
            "automatic_routing": False,
            "upstreamStages": [
                "requirements",
                "product_planning",
                "ui_confirmation",
                "technical_planning",
                "application_lifecycle",
                "api_design",
            ],
            "buildExecutionScope": build_execution_scope,
        }
    )
    return {
        **clear_planning_projection(),
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "build_execution_scope": build_execution_scope,
        "clarification": payload,
        "timeline": ["prepare_build_tasks"],
    }


def _confirmed_baseline_blocked_result(
    project_plan: dict[str, Any],
    build_execution_scope: dict[str, str],
    errors: list[str],
) -> dict[str, Any]:
    """将非法正式 DAG 单独投影为平台基线问题，等待人工修复后重新校验。"""

    artifact = ".xcodeagent/plans/build-task-plan.json"
    recovery = (
        "请由平台维护者检查正式 Build Task Plan 的文件内容、读取权限、确认状态和 DAG 校验结果；"
        "修复并验证为合法 ConfirmedPlan 后，重新发起任务规划。"
    )
    issue = ValidationIssue(
        code="CONFIRMED_BASELINE_INVALID", level="pre_generation", category="platform",
        retryable=False, message="正式 Confirmed baseline 非法或无法读取。",
        details={"artifact": artifact, "errors": errors},
    )
    payload = build_ask_user_payload([
        AskUserQuestion(
            header="DAG 基线非法",
            question=f"正式任务基线 {artifact} 非法或无法读取，本轮规划已阻断。{recovery}",
            type="text", placeholder="请先完成正式基线修复；回复确认不能代替基线校验。",
        )
    ])
    payload.update({
        "mode": "confirmed_baseline_error",
        "code": "confirmed_baseline_invalid",
        "message": f"正式任务基线 {artifact} 非法或无法读取，等待平台维护者处理。",
        "artifact": artifact, "target": build_execution_scope,
        "errors": errors, "issues": [issue.model_dump(mode="json")],
        "recommended_action": recovery, "automatic_routing": False,
        "retryable": False,
    })
    return {
        **clear_planning_projection(),
        "phase": "prepare_build_tasks", "status": "requires_user_input",
        "project_plan": project_plan, "build_execution_scope": build_execution_scope,
        "clarification": payload, "message": payload["message"],
        "timeline": ["prepare_build_tasks"],
    }


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
        "actionValues": ["confirm", "abandon", "regenerate"],
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
    draft_identity = build_task_plan.get("draft_identity")
    if isinstance(draft_identity, dict):
        planning_run_id = draft_identity.get("planning_run_id")
        draft_digest = draft_identity.get("draft_digest")
        owner_session_id = draft_identity.get("owner_session_id")
        # 只公开 Abandon/Confirm 所需的最小身份，不泄露冻结输入和内部 Draft 元数据。
        if (
            isinstance(owner_session_id, str)
            and isinstance(planning_run_id, str)
            and isinstance(draft_digest, str)
        ):
            payload["draftIdentity"] = {
                "ownerSessionId": owner_session_id,
                "planningRunId": planning_run_id,
                "draftDigest": draft_digest,
            }
    if errors:
        payload["errors"] = errors
        payload["message"] = "Build DAG 需要处理后才能继续。"
    return payload


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
            project_plan_path=technical_plan_path(workspace_from_state(state)),
        )
        return context
    return {
        "target": {"type": "application", "id": "application"},
        "page_implementation_contract": None,
        "endpoint_contract": None,
        "direct_endpoint_contracts": [],
        "endpoint_ids": [],
        "entity_ids": [],
        "endpoint_designs": [],
        "source_types": [],
        "required_unit_ids": list((build_task_plan.get("build_units") or {}).keys()),
        "source_refs": {},
        "prebuilt_files": prebuilt_files_for_plan(project_plan),
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
    target = build_context.get("target") if isinstance(build_context.get("target"), dict) else {}
    target_page_id = str(target.get("id") or "") if target.get("type") == "page" else ""
    pages = _scoped_pages(project_plan, target_page_id)
    contracts = _scoped_contracts(project_plan, build_context)
    # TechnicalPlan 的全局 entity_ids 约束在本阶段保持原语义；物理来源映射
    # 只作为 Build 上下文的 Endpoint 局部事实，不应污染 Contract 一致性校验投影。
    entity_ids = [
        str(entity_id).strip()
        for contract in contracts
        for entity_id in contract.get("entity_ids") or []
        if str(entity_id).strip()
    ]
    if not entity_ids:
        entity_ids = [
            str(item).strip()
            for item in build_context.get("entity_ids") or []
            if str(item).strip()
        ]
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
                    "请返回项目计划阶段修订契约后再继续。"
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
            "artifact": "PageImplementationContract / Endpoint Contract / Endpoint API Design",
            "errors": [error],
            "recommended_action": "手动补齐并确认缺失的范围详情后重新发起 DAG 生成。",
            "automatic_routing": False,
        }
    )
    return payload
