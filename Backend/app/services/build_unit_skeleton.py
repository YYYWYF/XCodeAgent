"""Build DAG 全局 Unit 骨架的确定性构造服务。"""

from __future__ import annotations

from app.domain.api_design import API_DESIGN_SCHEMA_VERSION

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from app.services.frontend_page_tree import project_plan_page_records
from app.services.authorization_resource_catalog import authorization_resource_consumer_page_ids
from app.services.unit_task_manifest import BACKEND_MODULE_PREFIX, backend_module_unit_id


def _public_unit_ids(project_plan: dict[str, Any]) -> tuple[str, ...]:
    """返回 Endpoint API 设计旅程固定需要的公共 Unit。"""

    return (
        "frontend:shell",
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
        "schema_version": "build-dag.v4",
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


def build_unit_skeleton_input_fingerprint(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
) -> str:
    """返回当前项目计划与工作区输入的 Unit 骨架指纹，供增量 DAG 判断新鲜度。"""

    return _skeleton_fingerprint(project_plan, workspace_snapshot)


def _build_units(
    project_plan: dict[str, Any],
    existing_units: Any,
) -> dict[str, dict[str, Any]]:
    """从确认计划构造公共、后端模块和页面 Unit，并保留同一 Unit 的状态。"""

    existing = existing_units if isinstance(existing_units, dict) else {}
    unit_ids = ["application:root", *_public_unit_ids(project_plan)]
    unit_ids.extend(_backend_module_unit_ids(project_plan.get("api_contracts")))
    unit_ids.extend(
        f"page:{page_id}"
        for page_id in _ids(project_plan_page_records(project_plan), "pageId")
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
        **(
            {"data_source_id": target_id.removeprefix("data:")}
            if target_id.startswith("data:")
            else {}
        ),
        **({"api_contract_id": target_id} if unit_id.startswith(BACKEND_MODULE_PREFIX) else {}),
        "status": existing.get("status", "not_prepared"),
        "task_ids": list(existing.get("task_ids") or []),
        "depends_on_unit_ids": list(existing.get("depends_on_unit_ids") or []),
        "source_refs": dict(existing.get("source_refs") or {}),
        **({
            "generation_strategy": "prerequisite_only",
            "participation": "prerequisite_only",
            "generation_status": "not_required",
        } if unit_id == "frontend:shell" else {}),
    }


def _unit_graph(
    project_plan: dict[str, Any],
    build_units: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """依据 ProjectPlan API 契约建立 Unit 级依赖边与校验结果。"""

    nodes = list(build_units)
    edges: list[dict[str, str]] = []
    errors: list[str] = []
    public_unit_ids = _public_unit_ids(project_plan)
    for public_unit_id in public_unit_ids:
        if public_unit_id != "app:integration":
            edges.append(
                {
                    "from": "application:root",
                    "to": public_unit_id,
                    "type": "contains",
                }
            )

    contracts = _dict_items(project_plan.get("api_contracts"))
    page_contracts_by_id = {
        str(contract.get("pageId") or contract.get("id")): contract
        for contract in _dict_items(project_plan.get("page_implementation_contracts"))
        if contract.get("pageId") or contract.get("id")
    }
    for module_unit_id in _backend_module_unit_ids(contracts):
        if module_unit_id not in build_units:
            errors.append(f"API contract backend module {module_unit_id} has no Unit.")
            continue
        edges.append({"from": "application:root", "to": module_unit_id, "type": "contains"})
        edges.append({"from": "backend:bootstrap", "to": module_unit_id, "type": "depends_on"})
        edges.append({"from": module_unit_id, "to": "app:integration", "type": "depends_on"})

    auth_resource_pages = authorization_resource_consumer_page_ids(project_plan)
    for page in project_plan_page_records(project_plan):
        page_id = str(page.get("pageId") or "")
        if not page_id:
            continue
        page_unit_id = f"page:{page_id}"
        edges.append({"from": "application:root", "to": page_unit_id, "type": "contains"})
        for public_unit_id in (
            "frontend:shell",
            "frontend:api-client",
        ):
            edges.append({"from": public_unit_id, "to": page_unit_id, "type": "depends_on"})
        if page_id in auth_resource_pages:
            edges.append(
                {"from": "frontend:auth-guard", "to": page_unit_id, "type": "depends_on"}
            )
        dependency_source = _page_dependency_source(
            page,
            page_contracts_by_id.get(page_id),
        )
        for module_unit_id in _page_backend_module_unit_ids(dependency_source, contracts):
            if module_unit_id not in build_units:
                errors.append(f"Page {page_id} references unknown backend module Unit {module_unit_id}.")
                continue
            edges.append({"from": module_unit_id, "to": page_unit_id, "type": "depends_on"})
        edges.append({"from": page_unit_id, "to": "app:integration", "type": "depends_on"})

    return {
        "schema_version": "build-unit-graph.v3",
        "nodes": nodes,
        "edges": _unique_edges(edges),
        "validation": {"is_valid": not errors, "errors": errors},
    }


def _page_dependency_source(
    page: dict[str, Any],
    page_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    """用 PageImplementationContract 的 endpoint 引用补齐页面 Unit 依赖。"""

    if not isinstance(page_contract, dict):
        return page
    required_endpoint_ids = [
        str(endpoint_id or "").strip()
        for endpoint_id in page_contract.get("requiredEndpointIds") or []
        if str(endpoint_id or "").strip()
    ]
    if required_endpoint_ids:
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        return {
            **page,
            "references": {
                **references,
                "endpoint_dependencies": [
                    {"endpoint_id": endpoint_id} for endpoint_id in required_endpoint_ids
                ],
            },
        }
    return page


def _skeleton_fingerprint(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
) -> str:
    """为 Unit 骨架输入生成稳定指纹，供后续页面请求复用。"""

    payload = {
        "skeleton_policy": "backend-module-units-and-shell-prerequisite",
        "project_plan_version": project_plan.get("version"),
        "architecture": project_plan.get("architecture"),
        "permission_model": project_plan.get("permission_model"),
        "pages": project_plan_page_records(project_plan),
        # 绑定增删会改变 Page 前置依赖，必须参与骨架复用指纹。
        "authorization_resource_pages": sorted(authorization_resource_consumer_page_ids(project_plan)),
        "endpoint_api_design_policy": API_DESIGN_SCHEMA_VERSION,
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


def _backend_module_unit_ids(value: Any) -> list[str]:
    """每个包含正式 Endpoint 的 API Contract 只注册一个后端模块 Unit。"""

    return list(dict.fromkeys(
        backend_module_unit_id(str(contract["id"]))
        for contract in _dict_items(value)
        if contract.get("id") and any(
            endpoint.get("id") for endpoint in _dict_items(contract.get("endpoints"))
        )
    ))


def _unit_identity(unit_id: str) -> tuple[str, str]:
    """根据稳定 Unit ID 返回 Unit 类型及其业务目标标识。"""

    if unit_id.startswith("page:"):
        return "page", unit_id.removeprefix("page:")
    if unit_id.startswith(BACKEND_MODULE_PREFIX):
        return "backend", unit_id.removeprefix(BACKEND_MODULE_PREFIX)
    if unit_id.startswith("backend:"):
        return "backend", unit_id.removeprefix("backend:")
    if unit_id.startswith("frontend:"):
        return "frontend", unit_id.removeprefix("frontend:")
    return "application", ""


def _page_backend_module_unit_ids(
    page: dict[str, Any],
    api_contracts: list[dict[str, Any]],
) -> list[str]:
    """将页面依赖的 Endpoint 映射到去重的后端模块 Unit。"""

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
    return list(dict.fromkeys(
        backend_module_unit_id(endpoint_to_contract[endpoint_id])
        for endpoint_id in endpoint_ids
        if endpoint_id in endpoint_to_contract
    ))


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
