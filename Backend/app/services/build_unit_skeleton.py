"""Build DAG 全局 Unit 骨架的确定性构造服务。"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from app.services.frontend_page_tree import flatten_frontend_pages
from app.services.database_planning_context import endpoint_detail_uses_database


PUBLIC_UNIT_IDS = (
    "frontend:shell",
    "frontend:route-registry",
    "frontend:api-client",
    "frontend:auth-guard",
    "backend:bootstrap",
    "app:integration",
)


def ensure_build_unit_skeleton(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
    build_task_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """确保计划包含全局 Unit 骨架，并在输入未变化时复用已有骨架。"""

    current_plan = deepcopy(build_task_plan or {})
    fingerprint = _skeleton_fingerprint(project_plan, workspace_snapshot)
    existing_skeleton = current_plan.get("unit_skeleton")
    if (
        isinstance(existing_skeleton, dict)
        and existing_skeleton.get("input_fingerprint") == fingerprint
        and isinstance(current_plan.get("build_units"), dict)
        and isinstance(current_plan.get("unit_graph"), dict)
    ):
        current_plan["unit_skeleton"] = {
            **existing_skeleton,
            "reused": True,
        }
        return current_plan

    build_units = _build_units(project_plan, current_plan.get("build_units"))
    unit_graph = _unit_graph(project_plan, build_units)
    return {
        **current_plan,
        "schema_version": "build-dag.v3",
        "application": {
            "unit_id": "application:root",
            "status": "prepared",
        },
        "build_units": build_units,
        "unit_graph": unit_graph,
        "unit_skeleton": {
            "input_fingerprint": fingerprint,
            "project_plan_version": project_plan.get("version"),
            "workspace_revision": (workspace_snapshot or {}).get("workspace_revision"),
            "reused": False,
        },
    }


def _build_units(
    project_plan: dict[str, Any],
    existing_units: Any,
) -> dict[str, dict[str, Any]]:
    """从确认计划构造公共、数据源、endpoint 和页面 Unit，并尽量保留已有状态。"""

    existing = existing_units if isinstance(existing_units, dict) else {}
    unit_ids = ["application:root", *PUBLIC_UNIT_IDS]
    unit_ids.extend(
        f"database:{source_id}"
        for source_id in _ids(project_plan.get("data_sources"), "id")
    )
    unit_ids.extend(_endpoint_unit_ids(project_plan.get("api_contracts")))
    unit_ids.extend(
        f"page:{page_id}"
        for page_id in _ids(flatten_frontend_pages(project_plan.get("frontend_pages")), "pageId")
    )
    return {
        unit_id: _unit_definition(
            unit_id,
            existing.get(unit_id),
        )
        for unit_id in unit_ids
    }


def _unit_definition(unit_id: str, existing_unit: Any) -> dict[str, Any]:
    """创建 Unit 默认值，同时保留已有任务和执行状态。"""

    existing = existing_unit if isinstance(existing_unit, dict) else {}
    kind, target_id = _unit_identity(unit_id)
    return {
        "id": unit_id,
        "kind": kind,
        **({"page_id": target_id} if kind == "page" else {}),
        **({"data_source_id": target_id} if kind == "database" else {}),
        **(
            {
                "api_contract_id": target_id.split(":", 1)[0],
                "endpoint_id": target_id.split(":", 1)[1],
            }
            if kind == "backend" and ":" in target_id
            else {}
        ),
        "status": existing.get("status", "not_prepared"),
        "task_ids": list(existing.get("task_ids") or []),
        "depends_on_unit_ids": list(existing.get("depends_on_unit_ids") or []),
        "source_refs": dict(existing.get("source_refs") or {}),
    }


def _unit_graph(
    project_plan: dict[str, Any],
    build_units: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """依据 ProjectPlan API 契约建立 Unit 级依赖边与校验结果。"""

    nodes = list(build_units)
    edges: list[dict[str, str]] = []
    errors: list[str] = []
    for public_unit_id in PUBLIC_UNIT_IDS:
        if public_unit_id != "app:integration":
            edges.append(
                {
                    "from": "application:root",
                    "to": public_unit_id,
                    "type": "contains",
                }
            )

    contracts = _dict_items(project_plan.get("api_contracts"))
    page_details_by_id = {
        str(detail.get("pageId") or detail.get("id")): detail
        for detail in _dict_items(project_plan.get("page_detail_plans"))
        if detail.get("pageId") or detail.get("id")
    }
    for source_id in _ids(project_plan.get("data_sources"), "id"):
        source_unit_id = f"database:{source_id}"
        edges.append(
            {"from": "application:root", "to": source_unit_id, "type": "contains"}
        )

    for contract in contracts:
        contract_id = str(contract.get("id") or "")
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if not contract_id or not endpoint_id:
                continue
            endpoint_unit_id = _endpoint_unit_id(contract_id, endpoint_id)
            if endpoint_unit_id not in build_units:
                errors.append(f"API contract {contract_id} endpoint {endpoint_id} has no Unit.")
                continue
            edges.append({"from": "application:root", "to": endpoint_unit_id, "type": "contains"})
            edges.append({"from": "backend:bootstrap", "to": endpoint_unit_id, "type": "depends_on"})
            edges.append({"from": endpoint_unit_id, "to": "app:integration", "type": "depends_on"})

    for page in flatten_frontend_pages(project_plan.get("frontend_pages")):
        page_id = str(page.get("pageId") or "")
        if not page_id:
            continue
        page_unit_id = f"page:{page_id}"
        edges.append({"from": "application:root", "to": page_unit_id, "type": "contains"})
        for public_unit_id in (
            "frontend:shell",
            "frontend:route-registry",
            "frontend:api-client",
        ):
            edges.append({"from": public_unit_id, "to": page_unit_id, "type": "depends_on"})
        if _page_requires_auth(page):
            edges.append(
                {"from": "frontend:auth-guard", "to": page_unit_id, "type": "depends_on"}
            )
        dependency_source = _page_dependency_source(
            page,
            page_details_by_id.get(page_id),
        )
        for endpoint_unit_id in _page_endpoint_unit_ids(dependency_source, contracts):
            if endpoint_unit_id not in build_units:
                errors.append(f"Page {page_id} references unknown endpoint Unit {endpoint_unit_id}.")
                continue
            edges.append({"from": endpoint_unit_id, "to": page_unit_id, "type": "depends_on"})
        edges.append({"from": page_unit_id, "to": "app:integration", "type": "depends_on"})

    return {
        "schema_version": "build-unit-graph.v3",
        "nodes": nodes,
        "edges": _unique_edges(edges),
        "validation": {"is_valid": not errors, "errors": errors},
    }


def apply_target_unit_dependencies(
    build_task_plan: dict[str, Any],
    build_context: dict[str, Any],
) -> dict[str, Any]:
    """按已确认 EndpointDetail 为当前 scope 接入 database→endpoint Unit 依赖。"""

    scoped_plan = deepcopy(build_task_plan)
    unit_graph = scoped_plan.get("unit_graph")
    if not isinstance(unit_graph, dict):
        return scoped_plan
    build_units = scoped_plan.get("build_units")
    build_units = build_units if isinstance(build_units, dict) else {}
    edges = [
        dict(edge)
        for edge in unit_graph.get("edges", [])
        if isinstance(edge, dict)
    ]
    errors = list(
        (unit_graph.get("validation") or {}).get("errors", [])
        if isinstance(unit_graph.get("validation"), dict)
        else []
    )
    for detail in _dict_items(build_context.get("direct_endpoint_details")):
        if not endpoint_detail_uses_database(detail):
            continue
        api_contract_id = str(detail.get("api_contract_id") or "")
        endpoint_id = str(detail.get("endpoint_id") or "")
        data_source_id = str(detail.get("data_source_id") or "")
        database_unit_id = f"database:{data_source_id}"
        endpoint_unit_id = _endpoint_unit_id(api_contract_id, endpoint_id)
        missing_units = [
            unit_id
            for unit_id in (database_unit_id, endpoint_unit_id)
            if unit_id not in build_units
        ]
        if missing_units:
            errors.append(
                f"EndpointDetail {api_contract_id}:{endpoint_id} references missing Units: "
                + ", ".join(missing_units)
            )
            continue
        edges.append(
            {
                "from": database_unit_id,
                "to": endpoint_unit_id,
                "type": "depends_on",
            }
        )
    unique_errors = list(dict.fromkeys(errors))
    scoped_plan["unit_graph"] = {
        **unit_graph,
        "edges": _unique_edges(edges),
        "validation": {
            "is_valid": not unique_errors,
            "errors": unique_errors,
        },
    }
    return scoped_plan


def _page_dependency_source(
    page: dict[str, Any],
    page_detail: dict[str, Any] | None,
) -> dict[str, Any]:
    """优先用已确认 PageDetail 的 endpoint 引用补齐页面 Unit 依赖来源。"""

    if not isinstance(page_detail, dict):
        return page
    detail_references = page_detail.get("references")
    if not isinstance(detail_references, dict):
        return page
    return {**page, "references": detail_references}


def _skeleton_fingerprint(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
) -> str:
    """为 Unit 骨架输入生成稳定指纹，供后续页面请求复用。"""

    payload = {
        "skeleton_policy": "endpoint-detail-scoped-database-v1",
        "project_plan_version": project_plan.get("version"),
        "architecture": project_plan.get("architecture"),
        "permission_model": project_plan.get("permission_model"),
        "frontend_pages": project_plan.get("frontend_pages"),
        "data_sources": project_plan.get("data_sources"),
        "api_contracts": project_plan.get("api_contracts"),
        "workspace_revision": (workspace_snapshot or {}).get("workspace_revision"),
        "tech_stack": (workspace_snapshot or {}).get("tech_stack"),
        "entrypoints": (workspace_snapshot or {}).get("entrypoints"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _ids(value: Any, key: str) -> list[str]:
    """从对象列表中提取去重且非空的稳定业务标识。"""

    return list(
        dict.fromkeys(
            str(item.get(key) or "")
            for item in _dict_items(value)
            if item.get(key)
        )
    )


def _endpoint_unit_ids(value: Any) -> list[str]:
    """从 API 契约清单中生成 backend endpoint Unit ID 列表。"""

    result: list[str] = []
    for contract in _dict_items(value):
        contract_id = str(contract.get("id") or "")
        if not contract_id:
            continue
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if endpoint_id:
                result.append(_endpoint_unit_id(contract_id, endpoint_id))
    return list(dict.fromkeys(result))


def _endpoint_unit_id(api_contract_id: str, endpoint_id: str) -> str:
    """生成 backend endpoint Unit 的稳定复合标识。"""

    return f"backend:endpoint:{api_contract_id}:{endpoint_id}"


def _unit_identity(unit_id: str) -> tuple[str, str]:
    """根据稳定 Unit ID 返回 Unit 类型及其业务目标标识。"""

    if unit_id.startswith("page:"):
        return "page", unit_id.removeprefix("page:")
    if unit_id.startswith("database:"):
        return "database", unit_id.removeprefix("database:")
    if unit_id.startswith("backend:endpoint:"):
        return "backend", unit_id.removeprefix("backend:endpoint:")
    if unit_id.startswith("backend:"):
        return "backend", unit_id.removeprefix("backend:")
    if unit_id.startswith("frontend:"):
        return "frontend", unit_id.removeprefix("frontend:")
    return "application", ""


def _page_endpoint_unit_ids(
    page: dict[str, Any],
    api_contracts: list[dict[str, Any]],
) -> list[str]:
    """根据页面 endpoint 依赖生成精确 endpoint Unit 引用。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    dependencies = references.get("endpoint_dependencies") or page.get("endpoint_dependencies") or []
    endpoint_ids = [
        str(item.get("endpoint_id") or "")
        for item in _dict_items(dependencies)
        if item.get("endpoint_id")
    ]
    if not endpoint_ids:
        return []
    endpoint_to_contract: dict[str, str] = {}
    for contract in api_contracts:
        contract_id = str(contract.get("id") or "")
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if contract_id and endpoint_id:
                endpoint_to_contract.setdefault(endpoint_id, contract_id)
    return [
        _endpoint_unit_id(endpoint_to_contract[endpoint_id], endpoint_id)
        for endpoint_id in dict.fromkeys(endpoint_ids)
        if endpoint_id in endpoint_to_contract
    ]


def _page_requires_auth(page: dict[str, Any]) -> bool:
    """根据页面权限引用判断是否需要应用级鉴权守卫。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    permissions = references.get("permissions") or page.get("permissions") or []
    return bool(permissions) and list(permissions) != ["anonymous"]


def _unique_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    """去除重复 Unit 边，保持首次出现的确定性顺序。"""

    result: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in edges:
        key = (edge["from"], edge["to"], edge["type"])
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典元素，忽略无效输入。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
