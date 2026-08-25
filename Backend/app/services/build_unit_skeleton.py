"""Build DAG 全局 Unit 骨架的确定性构造服务。"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from app.services.entity_definitions import plan_data_sources
from app.services.frontend_page_tree import project_plan_page_records


def _public_unit_ids(project_plan: dict[str, Any]) -> tuple[str, ...]:
    """按数据源类型选择公共 Unit，bootstrap 仅服务数据库后端能力。"""

    source_types = {
        str(source.get("type") or "")
        for source in plan_data_sources(project_plan)
    }
    units = ["frontend:shell"]
    if not (source_types and source_types <= {"static"}):
        units.append("frontend:api-client")
    units.append("frontend:auth-guard")
    if "database" in source_types:
        units.append("backend:bootstrap")
    units.append("app:integration")
    return tuple(units)


def _source_type_map(project_plan: dict[str, Any]) -> dict[str, str]:
    """建立数据源 id 到类型的映射，供按源构建 Unit。"""

    return {
        str(source.get("id") or ""): str(source.get("type") or "")
        for source in plan_data_sources(project_plan)
        if source.get("id")
    }


def _string_items(value: Any) -> list[str]:
    """把未知值收窄为去重后的非空字符串列表。"""

    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _entity_to_source_map(project_plan: dict[str, Any]) -> dict[str, str]:
    """建立实体 id 到数据源 id 的映射，契约通过 entity_ids 反查数据源。"""

    result: dict[str, str] = {}
    for source in plan_data_sources(project_plan):
        source_id = str(source.get("id") or "")
        for entity in _dict_items(source.get("entities")):
            entity_id = str(entity.get("id") or "")
            if entity_id and entity_id not in result:
                result[entity_id] = source_id
    return result


def _contract_source_ids(
    project_plan: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> dict[str, str]:
    """按契约 entity_ids 反查所属数据源类型；混合实体源时优先数据库/外部 API。"""

    entity_to_source = _entity_to_source_map(project_plan)
    result: dict[str, str] = {}
    for contract in contracts:
        contract_id = str(contract.get("id") or "")
        source_ids = [
            entity_to_source[entity_id]
            for entity_id in _string_items(contract.get("entity_ids"))
            if entity_id in entity_to_source
        ]
        source_id = _preferred_source_id(source_ids)
        if contract_id:
            result[contract_id] = source_id
    return result


def _preferred_source_id(source_ids: list[str]) -> str:
    """按 数据库 > 外部 API > 静态 的顺序选取契约级数据源标识。"""

    for preferred in ("database", "external_api", "static"):
        if preferred in source_ids:
            return preferred
    return source_ids[0] if source_ids else ""


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
    """从确认计划构造公共、静态数据、endpoint 和页面 Unit，并保留已有状态。"""

    existing = existing_units if isinstance(existing_units, dict) else {}
    source_type_map = _source_type_map(project_plan)
    unit_ids = ["application:root", *_public_unit_ids(project_plan)]
    for source_id, source_type in source_type_map.items():
        if source_type == "static":
            unit_ids.append(f"frontend:data:{source_id}")
        # database 已在实体确认阶段落库，external_api 由 endpoint Unit 承载。
    unit_ids.extend(
        _endpoint_unit_ids(
            project_plan.get("api_contracts"),
            source_type_map,
            _contract_source_ids(
                project_plan,
                _dict_items(project_plan.get("api_contracts")),
            ),
        )
    )
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
    public_unit_ids = _public_unit_ids(project_plan)
    source_type_map = _source_type_map(project_plan)
    source_types = set(source_type_map.values())
    all_static = bool(source_types) and source_types <= {"static"}
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
    contract_source_ids = _contract_source_ids(project_plan, contracts)
    contract_source_types = {
        str(contract.get("id") or ""): source_type_map.get(
            contract_source_ids.get(str(contract.get("id") or ""), ""),
            "",
        )
        for contract in contracts
    }
    page_contracts_by_id = {
        str(contract.get("pageId") or contract.get("id")): contract
        for contract in _dict_items(project_plan.get("page_implementation_contracts"))
        if contract.get("pageId") or contract.get("id")
    }
    for source_id, source_type in source_type_map.items():
        if source_type == "static":
            source_unit_id = f"frontend:data:{source_id}"
        else:
            continue
        edges.append(
            {"from": "application:root", "to": source_unit_id, "type": "contains"}
        )

    for contract in contracts:
        contract_id = str(contract.get("id") or "")
        contract_source_type = contract_source_types.get(contract_id)
        if contract_source_type not in {"database", "external_api"}:
            continue
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "")
            if not contract_id or not endpoint_id:
                continue
            endpoint_unit_id = _endpoint_unit_id(contract_id, endpoint_id)
            if endpoint_unit_id not in build_units:
                errors.append(f"API contract {contract_id} endpoint {endpoint_id} has no Unit.")
                continue
            edges.append({"from": "application:root", "to": endpoint_unit_id, "type": "contains"})
            if contract_source_type == "database":
                edges.append({"from": "backend:bootstrap", "to": endpoint_unit_id, "type": "depends_on"})
            edges.append({"from": endpoint_unit_id, "to": "app:integration", "type": "depends_on"})

    for page in project_plan_page_records(project_plan):
        page_id = str(page.get("pageId") or "")
        if not page_id:
            continue
        page_unit_id = f"page:{page_id}"
        edges.append({"from": "application:root", "to": page_unit_id, "type": "contains"})
        for public_unit_id in (
            "frontend:shell",
            *([] if all_static else ["frontend:api-client"]),
        ):
            edges.append({"from": public_unit_id, "to": page_unit_id, "type": "depends_on"})
        if _page_requires_auth(page):
            edges.append(
                {"from": "frontend:auth-guard", "to": page_unit_id, "type": "depends_on"}
            )
        dependency_source = _page_dependency_source(
            page,
            page_contracts_by_id.get(page_id),
        )
        endpoint_unit_ids = _page_endpoint_unit_ids(dependency_source, contracts)
        static_endpoint_unit_ids = [
            unit_id
            for unit_id in endpoint_unit_ids
            if contract_source_types.get(unit_id.removeprefix("backend:endpoint:").split(":", 1)[0])
            == "static"
        ]
        backend_endpoint_unit_ids = [
            unit_id
            for unit_id in endpoint_unit_ids
            if contract_source_types.get(unit_id.removeprefix("backend:endpoint:").split(":", 1)[0])
            in {"database", "external_api"}
        ]
        for source_unit_id in _page_static_source_unit_ids(
            static_endpoint_unit_ids,
            contract_source_ids,
        ):
            edges.append({"from": source_unit_id, "to": page_unit_id, "type": "depends_on"})
        for endpoint_unit_id in backend_endpoint_unit_ids:
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
        "skeleton_policy": "page-implementation-contract-v3",
        "project_plan_version": project_plan.get("version"),
        "architecture": project_plan.get("architecture"),
        "permission_model": project_plan.get("permission_model"),
        "pages": project_plan_page_records(project_plan),
        "data_sources": plan_data_sources(project_plan),
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


def _endpoint_unit_ids(
    value: Any,
    source_type_map: dict[str, str],
    contract_source_ids: dict[str, str],
) -> list[str]:
    """从 API 契约清单中生成后端 endpoint Unit ID（数据库与外部 API 源）。"""

    result: list[str] = []
    for contract in _dict_items(value):
        contract_id = str(contract.get("id") or "")
        if not contract_id:
            continue
        if source_type_map.get(contract_source_ids.get(contract_id, "")) not in {
            "database",
            "external_api",
        }:
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


def _page_static_source_unit_ids(
    endpoint_unit_ids: list[str],
    contract_source_ids: dict[str, str],
) -> list[str]:
    """把页面契约依赖映射为 Static 前端数据模块 Unit。"""

    source_ids = [
        contract_source_ids.get(
            unit_id.removeprefix("backend:endpoint:").split(":", 1)[0],
            "",
        )
        for unit_id in endpoint_unit_ids
    ]
    return [f"frontend:data:{source_id}" for source_id in dict.fromkeys(source_ids) if source_id]


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
