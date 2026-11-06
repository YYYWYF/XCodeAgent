"""逐接口检查模块交付物的新鲜度，保留共享任务中未变化的职责。"""

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path

from app.services.api_design_fingerprint import api_design_content
from app.services.backend_module_scope import design_endpoint_keys, task_endpoint_keys
from app.services.planning_frozen import plain_json
from app.services.unit_task_manifest import BACKEND_MODULE_PREFIX


def current_backend_deliverables(
    task: Mapping, plan: Mapping, context: Mapping, *, planning: bool = False,
    workspace_root: str | Path | None = None,
) -> list[dict]:
    """比较冻结设计及接口 HTTP 合同，只返回当前仍有效的交付物。"""
    from app.services.business_acceptance import _endpoint_expectations, _formal_inputs

    source = plain_json(task)
    contract = task["unit_id"].removeprefix(BACKEND_MODULE_PREFIX)
    refs = source.get("source_refs") or {}
    old = {item["endpointId"]: item for item in refs.get("endpoint_designs", [])
           if item.get("apiContractId") == contract}
    current_context = plain_json(context)
    current = {item["endpointId"]: item for item in current_context.get("endpoint_designs", [])
               if item.get("apiContractId") == contract}
    formal = _formal_inputs({**current_context, "project_plan": plain_json(plan)}, source)
    http_now = {item["endpoint_id"]: item for item in _endpoint_expectations(formal)}
    http_old = {item["endpoint_id"]: item for check in source.get("business_acceptance_checks", [])
                for item in check.get("expected", {}).get("endpoints", []) if item.get("endpoint_id")}
    previous_permissions = _endpoint_permissions((refs.get("authorization") or {}).get("endpoints", []), contract)
    current_permissions = _endpoint_permissions(
        (current_context.get("authorization_constraints") or {}).get("endpoints", []), contract,
    )
    finished = task.get("status") in {"completed", "already_satisfied"}
    # 旧的未完成公共任务若跨越当前范围，本次重新规划其范围内职责，保留范围外部分。
    split_pending = planning and not finished and bool(set(old) - set(current))
    missing = _missing_delivery_sources(source, set(current), workspace_root) if finished and workspace_root else set()
    result = []
    for deliverable in source.get("deliverables", []):
        endpoint = deliverable.get("target_id")
        if endpoint not in old or endpoint not in http_now:
            continue
        if endpoint not in current:
            # 当前上下文没有范围外映射。原样保留，等其进入本轮范围再比较。
            result.append(deliverable)
            continue
        if split_pending or endpoint in missing:
            continue
        kind = deliverable.get("kind")
        if kind == "backend.foundation" and finished:
            from app.services.backend_foundation_inputs import foundation_facts

            required = foundation_facts(current[endpoint])
            unchanged = bool(required) and required <= foundation_facts(old[endpoint])
        else:
            unchanged = api_design_content(old[endpoint]) == api_design_content(current[endpoint])
        if kind == "backend.endpoint_controller":
            unchanged = unchanged and previous_permissions.get(endpoint) == current_permissions.get(endpoint)
        if unchanged and (endpoint not in http_old or http_now[endpoint] == http_old[endpoint]):
            result.append(deliverable)
    return result


def _missing_delivery_sources(task: dict, endpoints: set[str], workspace: str | Path) -> set[str]:
    """已完成接口缺少任一交付文件时，业务任务需要重新补齐该接口。"""
    root = Path(workspace).resolve()
    missing = set()
    for item in task.get("deliverables", []):
        endpoint = item.get("target_id")
        if endpoint not in endpoints:
            continue
        try:
            paths = [(root / path).resolve() for path in item.get("paths", [])]
            if not paths or any(not path.is_relative_to(root) or not path.is_file() for path in paths):
                missing.add(endpoint)
        except (OSError, RuntimeError):
            missing.add(endpoint)
    return missing


def _endpoint_permissions(endpoints: list[dict], contract: str) -> dict[str, tuple[str, ...]]:
    """比较逐接口资源集合，不让其他接口的权限变更使整个 Controller 失效。"""
    return {
        item["endpointId"]: tuple(sorted(item.get("operationResourceKeys", [])))
        for item in endpoints if item.get("apiContractId") == contract and item.get("operationResourceKeys")
    }


def project_current_backend_task(
    task: dict, plan: Mapping, context: Mapping, reusable_capabilities: Mapping,
) -> dict | None:
    """投射当前有效职责，不修改原始执行记录；无有效职责时移出当前注册表。"""
    if not task_endpoint_overlaps_context(task, context):
        return task
    valid = current_backend_deliverables(task, plan, context, planning=True)
    current_keys = design_endpoint_keys(context.get("endpoint_designs", ()))
    contract = task["unit_id"].removeprefix(BACKEND_MODULE_PREFIX)
    valid = [
        item for item in valid
        if (contract, item["target_id"]) not in current_keys or any(
            task["id"] in reusable_capabilities.get(capability, ())
            for capability in item.get("provides", [])
        )
    ]
    if not valid:
        return None
    if valid == task.get("deliverables"):
        return task
    from app.services.engineering_acceptance import ensure_engineering_acceptance

    projected = deepcopy(task)
    endpoints = {item["target_id"] for item in valid}
    projected["deliverables"] = valid
    projected["provides_capabilities"] = sorted({value for item in valid for value in item.get("provides", [])})
    paths = {path for item in valid for path in item["paths"]}
    projected["target_files"] = [path for path in task["target_files"] if path in paths]
    projected["allowed_paths"] = [path for path in task["allowed_paths"] if path in paths]
    projected["change_scope"] = [
        {**item, "description": "根据保留接口的已确认契约增量维护本文件。"}
        for item in task["change_scope"] if item["path"] in paths
    ]
    projected["description"] = "\n".join(
        f"{index}. {item['description']}（{item['path']}）"
        for index, item in enumerate(projected["change_scope"], start=1)
    )
    valid_ids = {item["id"] for item in valid}
    projected["business_acceptance_checks"] = [
        check for check in task.get("business_acceptance_checks", []) if check.get("deliverable_id") in valid_ids
    ]
    refs = projected["source_refs"]
    refs["endpoint_ids"] = sorted(endpoints)
    refs["endpoint_designs"] = [item for item in refs.get("endpoint_designs", []) if item.get("endpointId") in endpoints]
    if refs.get("authorization"):
        refs["authorization"]["endpoints"] = [item for item in refs["authorization"].get("endpoints", [])
                                               if item.get("endpointId") in endpoints]
    refs["technical_plan_endpoints"] = [item for item in refs.get("technical_plan_endpoints", [])
                                        if item.get("id") in endpoints]
    refs["business_descriptions"] = [item for item in refs.get("business_descriptions", [])
                                       if item.get("endpoint_id") in endpoints]
    return ensure_engineering_acceptance(projected)


def task_endpoint_overlaps_context(task: Mapping, context: Mapping) -> bool:
    """确认任务的实际接口职责和当前已确认映射存在交集。"""
    return bool(task_endpoint_keys(task) & design_endpoint_keys(context.get("endpoint_designs", ())))
