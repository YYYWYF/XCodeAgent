"""Build DAG 全局 Unit 骨架的确定性构造服务。"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from app.services.page_dependencies import page_data_source_ids


PUBLIC_UNIT_IDS = (
    "app:frontend-shell",
    "app:route-registry",
    "app:api-client",
    "app:auth-guard",
    "app:backend-bootstrap",
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
        "schema_version": "build-dag.v2",
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
    """从确认计划构造公共、数据源和页面 Unit，并尽量保留已有状态。"""

    existing = existing_units if isinstance(existing_units, dict) else {}
    unit_ids = ["application:root", *PUBLIC_UNIT_IDS]
    unit_ids.extend(
        f"data-source:{source_id}"
        for source_id in _ids(project_plan.get("data_sources"), "id")
    )
    unit_ids.extend(
        f"page:{page_id}"
        for page_id in _ids(project_plan.get("frontend_pages"), "pageId")
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
        **({"data_source_id": target_id} if kind == "data_source" else {}),
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
    for source_id in _ids(project_plan.get("data_sources"), "id"):
        source_unit_id = f"data-source:{source_id}"
        edges.extend(
            [
                {"from": "application:root", "to": source_unit_id, "type": "contains"},
                {
                    "from": "app:backend-bootstrap",
                    "to": source_unit_id,
                    "type": "depends_on",
                },
            ]
        )

    for page in _dict_items(project_plan.get("frontend_pages")):
        page_id = str(page.get("pageId") or "")
        if not page_id:
            continue
        page_unit_id = f"page:{page_id}"
        edges.append({"from": "application:root", "to": page_unit_id, "type": "contains"})
        for public_unit_id in (
            "app:frontend-shell",
            "app:route-registry",
            "app:api-client",
        ):
            edges.append({"from": public_unit_id, "to": page_unit_id, "type": "depends_on"})
        if _page_requires_auth(page):
            edges.append(
                {"from": "app:auth-guard", "to": page_unit_id, "type": "depends_on"}
            )
        for source_id in page_data_source_ids(page, contracts):
            source_unit_id = f"data-source:{source_id}"
            if source_unit_id not in build_units:
                errors.append(f"Page {page_id} references unknown data source {source_id}.")
                continue
            edges.append({"from": source_unit_id, "to": page_unit_id, "type": "depends_on"})
        edges.append({"from": page_unit_id, "to": "app:integration", "type": "depends_on"})

    return {
        "schema_version": "build-unit-graph.v2",
        "nodes": nodes,
        "edges": _unique_edges(edges),
        "validation": {"is_valid": not errors, "errors": errors},
    }


def _skeleton_fingerprint(
    project_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any] | None,
) -> str:
    """为 Unit 骨架输入生成稳定指纹，供后续页面请求复用。"""

    payload = {
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


def _unit_identity(unit_id: str) -> tuple[str, str]:
    """根据稳定 Unit ID 返回 Unit 类型及其业务目标标识。"""

    if unit_id.startswith("page:"):
        return "page", unit_id.removeprefix("page:")
    if unit_id.startswith("data-source:"):
        return "data_source", unit_id.removeprefix("data-source:")
    return "application", ""


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
