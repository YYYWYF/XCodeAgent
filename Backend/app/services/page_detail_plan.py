from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from typing import Any


def detail_design_targets(project_plan: dict[str, Any]) -> list[dict[str, Any]]:
    page_targets = [
        {
            "id": page.get("id"),
            "type": "page",
            "label": f"页面：{page.get('name') or page.get('id') or '未命名页面'}",
            "name": page.get("name") or page.get("id") or "未命名页面",
            "description": (
                f"{page.get('path') or '/'}，"
                f"{page.get('description') or page.get('name') or '待补充页面目标'}"
            ),
        }
        for page in project_plan.get("frontend_pages", [])
        if isinstance(page, dict) and page.get("id")
    ]
    data_source_targets = [
        {
            "id": source.get("id"),
            "type": "data_source",
            "label": f"数据源：{source.get('name') or source.get('id') or '未命名数据源'}",
            "name": source.get("name") or source.get("id") or "未命名数据源",
            "description": f"实体 {source.get('entities', [])}，类型 {source.get('type', '')}",
        }
        for source in project_plan.get("data_sources", [])
        if isinstance(source, dict) and source.get("id")
    ]
    return page_targets + data_source_targets


def resolve_detail_design_target(
    project_plan: dict[str, Any],
    request: str,
    selected_page_id: str | None = None,
    selected_data_source_id: str | None = None,
) -> dict[str, Any] | None:
    targets = detail_design_targets(project_plan)
    for target in targets:
        if target["type"] == "page" and target["id"] == selected_page_id:
            return target
        if target["type"] == "data_source" and target["id"] == selected_data_source_id:
            return target

    request_text = request.strip()
    if not request_text:
        return None

    for target in targets:
        candidates = [
            target["id"],
            target["label"],
            target["name"],
        ]
        if any(candidate and candidate in request_text for candidate in candidates):
            return target

    return None


def _find_by_id(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise ValueError(f"Unknown item id: {item_id}")


def _related_api_contracts(
    project_plan: dict[str, Any],
    data_dependencies: list[str],
) -> list[dict[str, Any]]:
    dependency_set = set(data_dependencies)
    return [
        {
            **contract,
            "data_source_id": data_source_id,
        }
        for contract in project_plan.get("api_contracts", [])
        if isinstance(contract, dict)
        for data_source_id in [_contract_data_source_id(contract, project_plan)]
        if data_source_id in dependency_set
    ]


def _contract_data_source_id(
    contract: dict[str, Any],
    project_plan: dict[str, Any],
) -> str:
    explicit_id = contract.get("data_source_id")
    if explicit_id:
        return str(explicit_id)

    contract_id = str(contract.get("id") or "")
    if contract_id.endswith("_api"):
        inferred_id = contract_id[: -len("_api")]
        if _has_data_source(project_plan, inferred_id):
            return inferred_id

    base_path = str(contract.get("base_path") or "").strip("/")
    route_base = base_path.removeprefix("api/").replace("-", "_")
    for source in project_plan.get("data_sources", []):
        if not isinstance(source, dict) or not source.get("id"):
            continue
        source_id = str(source["id"])
        normalized_source = (
            source_id[: -len("_source")]
            if source_id.endswith("_source")
            else source_id
        )
        if route_base in {source_id, normalized_source}:
            return source_id
    return ""


def _has_data_source(project_plan: dict[str, Any], source_id: str) -> bool:
    return any(
        isinstance(source, dict) and source.get("id") == source_id
        for source in project_plan.get("data_sources", [])
    )


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text_item(item) for item in value if _text_item(item)]


def _text_item(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "label", "title", "description", "id"):
            if value.get(key):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _normalize_basic_layout(value: Any) -> dict[str, Any]:
    layout = value if isinstance(value, dict) else {}
    return {
        **layout,
        "structure": _text_items(layout.get("structure")),
        "states": _text_items(layout.get("states")),
    }


def _page_dependency(
    project_plan: dict[str, Any],
    page_id: str,
) -> dict[str, Any]:
    for dependency in project_plan.get("page_data_dependencies", []):
        if dependency.get("page_id") == page_id:
            return dependency
    return {}


def create_page_spec_from_project_plan(
    project_plan: dict[str, Any],
    page_id: str,
    user_request: str = "",
    existing_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page = _find_by_id(project_plan["frontend_pages"], page_id)
    page_name = str(page.get("name") or page_id)
    page_path = str(page.get("path") or "/")
    dependency = _page_dependency(project_plan, page_id)
    data_source_ids = (
        dependency.get("data_source_ids")
        or page.get("data_dependencies")
        or []
    )
    api_contract_ids = dependency.get("api_contract_ids") or [
        str(contract.get("id"))
        for contract in _related_api_contracts(project_plan, data_source_ids)
        if contract.get("id")
    ]
    spec = {
        "type": "page",
        "page_id": page_id,
        "page_name": page_name,
        "path": page_path,
        "page_goal": page.get("description") or f"完成 {page_name} 的核心业务展示与操作。",
        "layout": {
            "structure": page.get(
                "layout",
                ["页面标题区", "筛选/操作区", "主要内容区", "状态反馈区"],
            ),
            "responsive": "优先桌面端工作台布局，保持内容可扫描、操作可达。",
        },
        "interactions": page.get(
            "interactions",
            ["进入页面后加载数据", "支持查看主要业务内容", "展示 loading、empty、error、ready 状态"],
        ),
        "data_source_ids": data_source_ids,
        "api_contract_ids": api_contract_ids,
        "permissions": page.get("permissions", []),
        "page_dependencies": {
            "data_sources": data_source_ids,
            "api_contracts": api_contract_ids,
            "dependency_detail": dependency,
        },
        "source_project_plan_context": {
            "page": page,
            "api_contracts": [
                contract
                for contract in project_plan.get("api_contracts", [])
                if contract.get("id") in api_contract_ids
            ],
            "data_sources": [
                source
                for source in project_plan.get("data_sources", [])
                if source.get("id") in data_source_ids
            ],
        },
        "user_confirmation_note": user_request.strip(),
    }
    if existing_spec:
        spec.update(existing_spec)
        spec["layout"] = {
            **spec.get("layout", {}),
            **existing_spec.get("layout", {}),
        }
        spec["page_dependencies"] = {
            **spec.get("page_dependencies", {}),
            **existing_spec.get("page_dependencies", {}),
        }
    return spec


def missing_page_spec_aspects(page_spec: dict[str, Any]) -> list[str]:
    missing = []
    if not str(page_spec.get("page_goal") or "").strip():
        missing.append("页面目标")
    if not page_spec.get("layout", {}).get("structure"):
        missing.append("基本布局")
    if not page_spec.get("interactions"):
        missing.append("页面交互")
    if not page_spec.get("data_source_ids") and page_spec.get("path") != "/login":
        missing.append("数据来源")
    if not page_spec.get("permissions"):
        missing.append("页面权限")
    if not page_spec.get("page_dependencies", {}).get("api_contracts") and page_spec.get("data_source_ids"):
        missing.append("页面依赖")
    return missing


def apply_page_spec_answers(
    page_spec: dict[str, Any],
    request: str,
) -> dict[str, Any]:
    updated = deepcopy(page_spec)
    if not request.strip():
        return updated

    updated["user_confirmation_note"] = request.strip()
    lower_request = request.lower()
    if "页面目标" in request or "目标" in request:
        updated["page_goal"] = _answer_after_header(request, ("页面目标", "目标")) or updated["page_goal"]
    if "基本布局" in request or "布局" in request:
        layout_answer = _answer_after_header(request, ("基本布局", "页面布局", "布局"))
        if layout_answer:
            updated["layout"] = {
                **updated.get("layout", {}),
                "structure": _split_answer_items(layout_answer),
            }
    if "页面交互" in request or "交互" in request:
        interaction_answer = _answer_after_header(request, ("页面交互", "交互"))
        if interaction_answer:
            updated["interactions"] = _split_answer_items(interaction_answer)
    if "页面权限" in request or "权限" in request:
        permission_answer = _answer_after_header(request, ("页面权限", "权限"))
        if permission_answer:
            updated["permissions"] = _split_answer_items(permission_answer)
    if "数据来源" in request or "数据源" in lower_request:
        data_answer = _answer_after_header(request, ("数据来源", "数据源"))
        if data_answer:
            updated["data_source_ids"] = _split_answer_items(data_answer)
    return updated


def create_data_source_detail_plan(
    project_plan: dict[str, Any],
    data_source_id: str,
    user_request: str = "",
    agent_detail_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = _find_by_id(project_plan["data_sources"], data_source_id)
    api_contracts = [
        contract
        for contract in project_plan.get("api_contracts", [])
        if contract.get("data_source_id") == data_source_id
    ]
    dependent_pages = [
        dependency
        for dependency in project_plan.get("page_data_dependencies", [])
        if data_source_id in dependency.get("data_source_ids", [])
    ]
    detail_plan = {
        "id": f"data_source_detail:{source['id']}",
        "type": "data_source",
        "data_source_id": source["id"],
        "data_source_name": source["name"],
        "status": "confirmed",
        "confirmed_at": datetime.now(UTC).isoformat(),
        "source_data_source": source,
        "schema": source.get("schema", {}),
        "entities": source.get("entities", []),
        "api_contracts": api_contracts,
        "dependent_pages": dependent_pages,
        "seed_strategy": source.get("seed_strategy"),
        "user_confirmation_note": user_request.strip(),
        "acceptance_criteria": [
            f"数据源 {source['name']} 可以提供已约定实体和字段。",
            "相关 API 契约与 ProjectPlan.api_contracts 保持一致。",
            "依赖该数据源的页面只能通过已声明 API 访问数据。",
        ],
        "approved": True,
    }
    if isinstance(agent_detail_plan, dict):
        detail_plan.update(agent_detail_plan)
    for key in ("entities", "api_contracts", "dependent_pages", "acceptance_criteria"):
        if not isinstance(detail_plan.get(key), list):
            detail_plan[key] = []
    if not isinstance(detail_plan.get("schema"), dict):
        detail_plan["schema"] = {}
    detail_plan.update(
        {
            "id": f"data_source_detail:{source['id']}",
            "type": "data_source",
            "data_source_id": source["id"],
            "data_source_name": source["name"],
            "status": "confirmed",
            "confirmed_at": datetime.now(UTC).isoformat(),
            "source_data_source": source,
            "user_confirmation_note": user_request.strip(),
            "approved": True,
        }
    )
    return detail_plan


def attach_data_source_detail_plan(
    project_plan: dict[str, Any],
    detail_plan: dict[str, Any],
) -> dict[str, Any]:
    updated_plan = deepcopy(project_plan)
    existing_details = {
        item["data_source_id"]: item
        for item in updated_plan.get("data_source_detail_plans", [])
        if isinstance(item, dict) and item.get("data_source_id")
    }
    existing_details[detail_plan["data_source_id"]] = detail_plan
    updated_plan["data_source_detail_plans"] = list(existing_details.values())

    for source in updated_plan["data_sources"]:
        if source.get("id") == detail_plan["data_source_id"]:
            source["detail_status"] = "confirmed"
            source["detail_plan_id"] = detail_plan["id"]

    updated_plan["data_source_detail_confirmation_summary"] = {
        "confirmed_data_sources": len(updated_plan["data_source_detail_plans"]),
        "total_data_sources": len(updated_plan["data_sources"]),
        "latest_data_source_id": detail_plan["data_source_id"],
    }
    return updated_plan


def _answer_after_header(text: str, headers: tuple[str, ...]) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not any(header in line for header in headers):
            continue
        if "回答：" in line:
            return line.split("回答：", 1)[1].strip()
        if index + 1 < len(lines) and "回答：" in lines[index + 1]:
            return lines[index + 1].split("回答：", 1)[1].strip()
    return ""


def _split_answer_items(value: str) -> list[str]:
    normalized = (
        value.replace("，", "、")
        .replace(",", "、")
        .replace("；", "、")
        .replace(";", "、")
        .replace("\n", "、")
    )
    return [item.strip() for item in normalized.split("、") if item.strip()]


def create_page_detail_plan(
    project_plan: dict[str, Any],
    confirmed_page_spec: dict[str, Any],
    agent_note: str = "live main-agent page detail design",
    agent_detail_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_id = confirmed_page_spec["page_id"]
    page = _find_by_id(project_plan["frontend_pages"], page_id)
    page_name = str(page.get("name") or page_id)
    page_path = str(page.get("path") or "/")
    data_source_ids = confirmed_page_spec.get(
        "data_source_ids",
        page.get("data_dependencies", []),
    )
    api_contracts = _related_api_contracts(project_plan, data_source_ids)
    layout = confirmed_page_spec.get("layout", {})

    detail_plan = {
        "id": f"page_detail:{page_id}",
        "type": "page",
        "page_id": page_id,
        "page_name": page_name,
        "path": page_path,
        "status": "confirmed",
        "confirmed_at": datetime.now(UTC).isoformat(),
        "source_page_spec": confirmed_page_spec,
        "page_goal": confirmed_page_spec["page_goal"],
        "basic_layout": {
            "structure": layout.get(
                "structure",
                ["页面标题区", "主要内容区", "操作区", "状态反馈区"],
            ),
            "states": confirmed_page_spec.get("states", page.get("states", [])),
            "responsive": layout.get(
                "responsive",
                "默认支持桌面端布局，后续可扩展移动端适配。",
            ),
        },
        "interactions": confirmed_page_spec["interactions"],
        "data_sources": [
            {
                "id": contract.get("data_source_id", ""),
                "api_contract_id": contract.get("id", ""),
                "base_path": contract.get("base_path", "/api/resource"),
                "endpoints": contract.get("endpoints", []),
            }
            for contract in api_contracts
            if contract.get("data_source_id")
        ],
        "permissions": confirmed_page_spec["permissions"],
        "page_dependencies": confirmed_page_spec.get("page_dependencies", {}),
        "acceptance_criteria": [
            f"用户可以访问 {page_path} 并看到 {page_name} 的主要内容。",
            "页面具备 loading、empty、error、ready 四类基础状态。",
            "页面只访问用户确认的 PageSpec 中声明的数据源和对应 API 契约。",
            "页面权限与用户确认的 PageSpec 保持一致。",
        ],
        "agent_note": agent_note,
        "approved": True,
    }
    if isinstance(agent_detail_plan, dict):
        detail_plan.update(agent_detail_plan)
    if not isinstance(detail_plan.get("basic_layout"), dict):
        detail_plan["basic_layout"] = {}
    detail_plan["basic_layout"] = _normalize_basic_layout(detail_plan["basic_layout"])
    for key in ("interactions", "data_sources", "permissions", "acceptance_criteria"):
        if not isinstance(detail_plan.get(key), list):
            detail_plan[key] = []
    for key in ("interactions", "permissions", "acceptance_criteria"):
        detail_plan[key] = _text_items(detail_plan.get(key))
    if not isinstance(detail_plan.get("page_dependencies"), dict):
        detail_plan["page_dependencies"] = {}
    detail_plan.update(
        {
            "id": f"page_detail:{page_id}",
            "type": "page",
            "page_id": page_id,
            "page_name": page_name,
            "path": page_path,
            "status": "confirmed",
            "confirmed_at": datetime.now(UTC).isoformat(),
            "source_page_spec": confirmed_page_spec,
            "agent_note": agent_note,
            "approved": True,
        }
    )
    return detail_plan


def attach_page_detail_plan(
    project_plan: dict[str, Any],
    detail_plan: dict[str, Any],
) -> dict[str, Any]:
    updated_plan = deepcopy(project_plan)
    existing_details = {
        item["page_id"]: item
        for item in updated_plan.get("page_detail_plans", [])
        if isinstance(item, dict) and item.get("page_id")
    }
    existing_details[detail_plan["page_id"]] = detail_plan
    updated_plan["page_detail_plans"] = list(existing_details.values())

    for page in updated_plan["frontend_pages"]:
        if page.get("id") == detail_plan["page_id"]:
            page["detail_status"] = "confirmed"
            page["detail_plan_id"] = detail_plan["id"]

    updated_plan["detail_confirmation_summary"] = {
        "confirmed_pages": len(updated_plan["page_detail_plans"]),
        "total_pages": len(updated_plan["frontend_pages"]),
        "latest_page_id": detail_plan["page_id"],
    }
    return updated_plan
