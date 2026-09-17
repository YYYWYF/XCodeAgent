"""构造 Build DAG 确认页使用的只读目标与任务投影。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from app.services.build_task_planner import tasks_from_build_task_plan
from app.services.frontend_page_tree import (
    find_frontend_page,
    project_plan_page_records,
)
from app.workspace.task_documents import validate_pending_planning_provenance


_COMPLETED_STATUSES = {"completed", "already_satisfied"}


def project_build_task(
    task: dict[str, Any],
    *,
    review_role: str | None = None,
) -> dict[str, Any]:
    """把内部叶子任务裁剪成 DAG 确认允许公开的 JSON 安全字段。"""

    projected = {
        "id": task.get("id"),
        "title": task.get("title") or "",
        "description": task.get("description") or "",
        "owner": task.get("owner"),
        "unit_id": task.get("unit_id"),
        "dependencies": task.get("dependencies") or [],
        "target_files": task.get("target_files") or [],
        "allowed_paths": task.get("allowed_paths") or [],
        "change_scope": task.get("change_scope") or [],
        "deliverables": task.get("deliverables") or [],
        "business_acceptance_checks": task.get("business_acceptance_checks") or [],
        "status": task.get("status") or "pending",
    }
    if review_role in {"new", "reused"}:
        projected["reviewRole"] = review_role
    return projected


def build_task_confirmation_read_model(
    build_task_plan: dict[str, Any],
    build_execution_scope: dict[str, Any] | None,
    *,
    project_plan: dict[str, Any] | None = None,
    build_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成不写回累计 DAG 的目标、统一任务列表及历史任务摘要投影。"""

    tasks = tasks_from_build_task_plan(build_task_plan)
    context = _effective_build_context(build_task_plan, build_context)
    scope = build_execution_scope or build_task_plan.get("build_execution_scope") or {}
    review_tasks, retained_tasks, classification_errors, classification_blocked = _partition_confirmation_tasks(
        tasks,
        build_task_plan,
        scope,
        context,
    )
    read_model: dict[str, Any] = {
        "targetReview": _target_review(project_plan or {}, scope, context),
        "reviewTasks": [
            project_build_task(task, review_role=review_role)
            for task, review_role in review_tasks
        ],
        "retainedTaskSummary": _retained_task_summary(retained_tasks),
    }
    if classification_errors:
        read_model["classificationErrors"] = list(classification_errors)
    if classification_blocked:
        read_model["classificationBlocked"] = True
    return read_model


def _effective_build_context(
    build_task_plan: dict[str, Any],
    build_context: dict[str, Any] | None,
) -> dict[str, Any]:
    """优先使用本轮 Graph 上下文，否则读取已随当前 DAG 持久化的上下文。"""

    if isinstance(build_context, dict) and build_context:
        return build_context
    persisted = build_task_plan.get("build_context")
    return persisted if isinstance(persisted, dict) else {}


def _partition_confirmation_tasks(
    tasks: list[dict[str, Any]],
    build_task_plan: dict[str, Any],
    scope: dict[str, Any],
    build_context: dict[str, Any],
) -> tuple[list[tuple[dict[str, Any], str]], list[dict[str, Any]], tuple[str, ...], bool]:
    """优先按 Pending provenance 分类，旧草稿才临时使用可恢复的 BuildContext。"""

    provenance = build_task_plan.get("planning_provenance")
    if isinstance(provenance, Mapping):
        try:
            validated = validate_pending_planning_provenance(build_task_plan, provenance)
        except ValueError as exc:
            return (
                [],
                tasks,
                (f"当前 PendingPlan 的 planning_provenance 无效：{exc}",),
                True,
            )
        new_task_ids = set(validated["new_task_ids"])
        task_ids = {str(task.get("id") or "") for task in tasks}
        missing = sorted(new_task_ids - task_ids)
        if missing:
            return (
                [],
                tasks,
                ("当前 PendingPlan 的 provenance 缺少可展示的 Task：" + "、".join(missing) + "。",),
                True,
            )
        new_tasks = [task for task in tasks if str(task.get("id") or "") in new_task_ids]
        tasks_by_id = {str(task.get("id") or ""): task for task in tasks}
        reused_ids = _dependency_ancestor_ids(new_tasks, tasks_by_id) - new_task_ids
        review_ids = new_task_ids | reused_ids
        review_tasks = [
            (
                task,
                "new" if str(task.get("id") or "") in new_task_ids else "reused",
            )
            for task in tasks
            if str(task.get("id") or "") in review_ids
        ]
        retained_tasks = [
            task
            for task in tasks
            if str(task.get("id") or "") not in review_ids
        ]
        return review_tasks, retained_tasks, (), False

    if "planning_provenance" in build_task_plan:
        return (
            [],
            tasks,
            ("当前 PendingPlan 的 planning_provenance 缺少有效对象，请重新生成。",),
            True,
        )
    required_unit_ids = _legacy_required_unit_ids(build_context)
    if required_unit_ids is None:
        return (
            [],
            tasks,
            ("旧 PendingPlan 缺少 planning_provenance，且无法从 BuildContext 恢复本轮任务来源，请重新生成。",),
            True,
        )
    target_type = str(scope.get("type") or "")
    # 只有能恢复完整旧 BuildContext 时才保留 application 旧草稿的全量兼容语义。
    if target_type == "application":
        return [(task, "new") for task in tasks], [], (), False

    scope_tasks = [
        task for task in tasks if str(task.get("unit_id") or "") in required_unit_ids
    ]
    scope_task_ids = {str(task.get("id") or "") for task in scope_tasks}
    tasks_by_id = {str(task.get("id") or ""): task for task in tasks}
    prerequisite_ids = _dependency_ancestor_ids(scope_tasks, tasks_by_id) - scope_task_ids
    reused_prerequisites = [
        task for task in tasks if str(task.get("id") or "") in prerequisite_ids
    ]
    retained_tasks = [
        task
        for task in tasks
        if str(task.get("id") or "") not in scope_task_ids | prerequisite_ids
    ]
    review_ids = scope_task_ids | prerequisite_ids
    review_tasks = [
        (
            task,
            "new" if str(task.get("id") or "") in scope_task_ids else "reused",
        )
        for task in tasks
        if str(task.get("id") or "") in review_ids
    ]
    return review_tasks, retained_tasks, (), False


def _legacy_required_unit_ids(build_context: Mapping[str, Any]) -> set[str] | None:
    """读取旧 Pending 兼容分类所需的完整 required_unit_ids 字段。"""

    if "required_unit_ids" not in build_context:
        return None
    value = build_context.get("required_unit_ids")
    if not isinstance(value, (list, tuple, set)):
        return None
    return {
        str(unit_id).strip()
        for unit_id in value
        if str(unit_id).strip()
    }


def _dependency_ancestor_ids(
    scope_tasks: list[dict[str, Any]],
    tasks_by_id: dict[str, dict[str, Any]],
) -> set[str]:
    """递归收集当前任务依赖的既有祖先，保留跨 Unit 的完整阻塞链。"""

    ancestors: set[str] = set()
    pending = [
        str(dependency)
        for task in scope_tasks
        for dependency in task.get("dependencies") or []
        if str(dependency)
    ]
    while pending:
        task_id = pending.pop()
        if task_id in ancestors:
            continue
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        ancestors.add(task_id)
        pending.extend(
            str(dependency)
            for dependency in task.get("dependencies") or []
            if str(dependency) and str(dependency) not in ancestors
        )
    return ancestors


def _retained_task_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总与本次目标无直接关系的累计任务，不返回其任务正文。"""

    status_counts = Counter(str(task.get("status") or "pending") for task in tasks)
    completed = sum(status_counts[status] for status in _COMPLETED_STATUSES)
    active = status_counts["pending"] + status_counts["running"]
    accounted = completed + active + status_counts["failed"]
    return {
        "total": len(tasks),
        "completed": completed,
        "active": active,
        "failed": status_counts["failed"],
        "other": len(tasks) - accounted,
        "statusCounts": dict(sorted(status_counts.items())),
    }


def _target_review(
    project_plan: dict[str, Any],
    scope: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """按页面或 Endpoint 目标生成验收来源明确的首屏只读信息。"""

    context_target = build_context.get("target")
    context_target = context_target if isinstance(context_target, dict) else {}
    target_type = str(scope.get("type") or context_target.get("type") or "")
    target_id = str(scope.get("targetId") or context_target.get("id") or "")
    if target_type == "page":
        return _page_target_review(project_plan, target_id, build_context)
    if target_type == "endpoint":
        endpoint = build_context.get("endpoint_contract")
        endpoint = endpoint if isinstance(endpoint, dict) else {}
        return {"target": {"type": "endpoint", **_endpoint_review(endpoint, target_id)}}
    return {
        "target": {
            "type": target_type or "application",
            "id": target_id or "application",
            "label": target_id or "application",
        }
    }


def _page_target_review(
    project_plan: dict[str, Any],
    page_id: str,
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """投影页面身份、ProductPlan 页面验收标准及其直接关联 Endpoint。"""

    page = find_frontend_page(project_plan_page_records(project_plan), page_id) or {}
    contract = build_context.get("page_implementation_contract")
    contract = contract if isinstance(contract, dict) else {}
    target = {
        "type": "page",
        "id": page_id,
        "label": str(page.get("name") or page_id),
        "acceptanceCriteria": [
            str(item)
            for item in contract.get("productAcceptance") or []
            if str(item).strip()
        ],
        "acceptanceSource": {
            "artifact": "product-plan",
            "field": "pages[].acceptance_criteria",
            "runtimeField": "PageImplementationContract.productAcceptance",
        },
    }
    for output_key, source_keys in {
        "path": ("path", "unique_path"),
        "description": ("description",),
    }.items():
        value = next(
            (
                str(page.get(key) or "").strip()
                for key in source_keys
                if str(page.get(key) or "").strip()
            ),
            "",
        )
        if value:
            target[output_key] = value
    result: dict[str, Any] = {"target": target}
    endpoints = [
        _endpoint_review(endpoint)
        for endpoint in build_context.get("direct_endpoint_contracts") or []
        if isinstance(endpoint, dict)
    ]
    # 页面没有关联接口时不输出 relatedEndpoints，避免前端渲染空的“接口模块”。
    if endpoints:
        result["relatedEndpoints"] = endpoints
    return result


def _endpoint_review(endpoint: dict[str, Any], fallback_id: str = "") -> dict[str, Any]:
    """把 TechnicalPlan Endpoint 裁剪成确认页所需的接口契约摘要。"""

    endpoint_id = str(endpoint.get("id") or fallback_id)
    return {
        "id": endpoint_id,
        "label": str(endpoint.get("summary") or endpoint_id),
        "apiContractId": str(endpoint.get("api_contract_id") or ""),
        "method": str(endpoint.get("method") or ""),
        "path": str(endpoint.get("path") or ""),
        "summary": str(endpoint.get("summary") or ""),
        "parameters": [
            item
            for item in endpoint.get("parameters") or []
            if isinstance(item, dict)
        ],
        "requestSchemaRef": endpoint.get("request_schema_ref"),
        "responseSchemaRef": endpoint.get("response_schema_ref"),
        "errorCodes": [str(item) for item in endpoint.get("error_codes") or []],
        "authentication": endpoint.get("authentication")
        if isinstance(endpoint.get("authentication"), dict)
        else {},
        "source": {
            "artifact": "technical-plan",
            "field": "api_contracts[].endpoints[]",
        },
    }
