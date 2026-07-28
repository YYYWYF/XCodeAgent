"""按页面或后端数据单元定向加载 Build DAG 编译上下文。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def resolve_target_build_context(
    project_plan: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
    api_contract_id: str | None = None,
    project_plan_path: str | Path | None = None,
) -> dict[str, Any]:
    """解析目标详情、直接 endpoint/API 依赖与编译所需的 Unit 标识。"""

    if target_type == "page":
        return _page_context(project_plan, target_id, project_plan_path)
    if target_type == "data_source":
        return _data_source_context(project_plan, target_id, project_plan_path)
    if target_type == "endpoint":
        return _endpoint_context(project_plan, target_id, api_contract_id, project_plan_path)
    raise ValueError(f"Unsupported build target type: {target_type}.")


def _page_context(
    project_plan: dict[str, Any],
    page_id: str,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """以 ProjectPlan 契约为主解析指定页面，并按需补充已有 endpoint 详情。"""

    page = _required_item(project_plan.get("frontend_pages"), "pageId", page_id, "page")
    page_detail = _load_external_detail(
        page.get("detail_design"),
        "PageDetail",
        page_id,
        project_plan_path,
    )
    endpoint_index = _endpoint_index(project_plan.get("api_contracts"))
    endpoint_ids = _endpoint_ids(page_detail)
    source_ids: list[str] = []
    contract_ids: list[str] = []
    endpoint_unit_ids: list[str] = []
    for endpoint_id in endpoint_ids:
        endpoint = endpoint_index.get(endpoint_id)
        if endpoint is None:
            raise ValueError(f"Page {page_id} references unknown endpoint {endpoint_id}.")
        source_id = str(endpoint.get("data_source_id") or "")
        if not source_id:
            raise ValueError(f"Endpoint {endpoint_id} does not declare a data source.")
        if source_id not in source_ids:
            source_ids.append(source_id)
        contract_id = str(endpoint.get("api_contract_id") or "")
        if contract_id:
            if contract_id not in contract_ids:
                contract_ids.append(contract_id)
            endpoint_unit_ids.append(_endpoint_unit_id(contract_id, endpoint_id))

    endpoint_details = []
    endpoint_refs = []
    for source_id in source_ids:
        _required_item(project_plan.get("data_sources"), "id", source_id, "data source")
    for endpoint_id in endpoint_ids:
        endpoint = endpoint_index[endpoint_id]
        detail = _load_optional_external_detail(
            endpoint.get("detail_design"),
            project_plan_path,
        )
        if detail is not None:
            endpoint_details.append(detail)
            endpoint_refs.append(_artifact_ref(endpoint.get("detail_design"), endpoint_id))

    return {
        "target": {"type": "page", "id": page_id},
        "page_detail": page_detail,
        "endpoint_detail": None,
        "direct_endpoint_details": endpoint_details,
        "endpoint_ids": endpoint_ids,
        "api_contract_ids": contract_ids,
        "data_source_ids": source_ids,
        "required_unit_ids": [
            "app:frontend-shell",
            "app:route-registry",
            "app:api-client",
            *( ["app:auth-guard"] if _page_requires_auth(page) else [] ),
            *( ["app:backend-bootstrap"] if source_ids else [] ),
            *(f"data-source:{source_id}" for source_id in source_ids),
            *list(dict.fromkeys(endpoint_unit_ids)),
            f"page:{page_id}",
        ],
        "source_refs": {
            "page_detail": _artifact_ref(page.get("detail_design"), page_id),
            "endpoint_details": endpoint_refs,
        },
    }


def _data_source_context(
    project_plan: dict[str, Any],
    source_id: str,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """以 ProjectPlan 契约解析数据单元，并按需补充已有 endpoint 详情。"""

    source = _required_item(project_plan.get("data_sources"), "id", source_id, "data source")
    del source
    endpoint_index = _endpoint_index(project_plan.get("api_contracts"))
    endpoint_ids = [
        endpoint_id for endpoint_id, endpoint in endpoint_index.items()
        if endpoint.get("data_source_id") == source_id
        and "\0" not in endpoint_id
    ]
    endpoint_details = []
    endpoint_refs = []
    for endpoint_id in endpoint_ids:
        reference = endpoint_index[endpoint_id].get("detail_design")
        detail = _load_optional_external_detail(reference, project_plan_path)
        if detail is not None:
            endpoint_details.append(detail)
            endpoint_refs.append(_artifact_ref(reference, endpoint_id))
    return {
        "target": {"type": "data_source", "id": source_id},
        "page_detail": None,
        "endpoint_detail": None,
        "direct_endpoint_details": endpoint_details,
        "endpoint_ids": endpoint_ids,
        "data_source_ids": [source_id],
        "required_unit_ids": ["app:backend-bootstrap", f"data-source:{source_id}"],
        "source_refs": {
            "endpoint_details": endpoint_refs,
        },
    }


def _endpoint_context(
    project_plan: dict[str, Any],
    endpoint_id: str,
    api_contract_id: str | None,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """解析单个 endpoint 的已确认详情，并只暴露该接口的后端构建范围。"""

    endpoint_index = _endpoint_index(project_plan.get("api_contracts"))
    contract_id = str(api_contract_id or "").strip()
    endpoint = (
        endpoint_index.get(f"{contract_id}\0{endpoint_id}")
        if contract_id
        else endpoint_index.get(endpoint_id)
    )
    if endpoint is None:
        target_label = f"{contract_id}/{endpoint_id}" if contract_id else endpoint_id
        raise ValueError(f"ProjectPlan does not contain endpoint {target_label}.")
    source_id = str(endpoint.get("data_source_id") or "")
    contract_id = str(endpoint.get("api_contract_id") or "")
    if not source_id:
        raise ValueError(f"Endpoint {endpoint_id} does not declare a data source.")
    if not contract_id:
        raise ValueError(f"Endpoint {endpoint_id} does not declare an API contract.")
    _required_item(project_plan.get("data_sources"), "id", source_id, "data source")
    detail = _load_external_detail(
        endpoint.get("detail_design"),
        "EndpointDetail",
        endpoint_id,
        project_plan_path,
    )
    detail_endpoint_id = str(detail.get("endpoint_id") or "")
    detail_contract_id = str(detail.get("api_contract_id") or "")
    if detail_endpoint_id and detail_endpoint_id != endpoint_id:
        raise ValueError(
            f"EndpointDetail {endpoint_id} file contains endpoint {detail_endpoint_id}."
        )
    if detail_contract_id and detail_contract_id != contract_id:
        raise ValueError(
            f"EndpointDetail {endpoint_id} file contains API contract {detail_contract_id}."
        )
    return {
        "target": {
            "type": "endpoint",
            "id": endpoint_id,
            "api_contract_id": contract_id,
        },
        "page_detail": None,
        "endpoint_detail": detail,
        "direct_endpoint_details": [detail],
        "endpoint_ids": [endpoint_id],
        "api_contract_ids": [contract_id],
        "data_source_ids": [source_id],
        "required_unit_ids": [
            "app:backend-bootstrap",
            f"data-source:{source_id}",
            _endpoint_unit_id(contract_id, endpoint_id),
        ],
        "source_refs": {
            "endpoint_detail": _artifact_ref(endpoint.get("detail_design"), endpoint_id),
            "endpoint_details": [_artifact_ref(endpoint.get("detail_design"), endpoint_id)],
        },
    }


def _required_item(value: Any, key: str, target_id: str, label: str) -> dict[str, Any]:
    """读取目标业务对象，缺失时返回可定位的构建前置错误。"""

    item = next(
        (
            candidate
            for candidate in _dict_items(value)
            if str(candidate.get(key) or "") == target_id
        ),
        None,
    )
    if item is None:
        raise ValueError(f"ProjectPlan does not contain {label} {target_id}.")
    return item


def _load_external_detail(
    reference: Any,
    label: str,
    target_id: str,
    project_plan_path: str | Path | None,
) -> dict[str, Any]:
    """按 ProjectPlan 中的 detail_design 引用读取外置详情文件。"""

    detail_ref = reference if isinstance(reference, dict) else {}
    json_path = str(detail_ref.get("json_path") or "").strip()
    if not json_path:
        raise ValueError(f"{label} {target_id} is missing detail_design.json_path.")
    if detail_ref.get("status") != "confirmed":
        raise ValueError(f"{label} {target_id} detail_design is not confirmed.")
    detail_path = _resolve_detail_path(json_path, project_plan_path)
    if detail_path is None or not detail_path.is_file():
        raise ValueError(f"{label} {target_id} detail file does not exist: {json_path}.")
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    if not isinstance(detail, dict):
        raise ValueError(f"{label} {target_id} detail file must contain a JSON object.")
    if detail.get("status") != "confirmed":
        raise ValueError(f"{label} {target_id} external detail is not confirmed.")
    return detail


def _load_optional_external_detail(
    reference: Any,
    project_plan_path: str | Path | None,
) -> dict[str, Any] | None:
    """仅把可用的已确认 endpoint 详情作为补充上下文，缺失或失效时回退到 ProjectPlan 契约。"""

    detail_ref = reference if isinstance(reference, dict) else {}
    json_path = str(detail_ref.get("json_path") or "").strip()
    if not json_path or detail_ref.get("status") != "confirmed":
        return None
    try:
        detail_path = _resolve_detail_path(json_path, project_plan_path)
        if detail_path is None or not detail_path.is_file():
            return None
        detail = json.loads(detail_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(detail, dict) or detail.get("status") != "confirmed":
        return None
    return detail


def _resolve_detail_path(
    json_path: str,
    project_plan_path: str | Path | None,
) -> Path | None:
    """解析 detail_design.json_path，兼容 workspace 根相对和 plans 目录相对引用。"""

    path = Path(json_path).expanduser()
    if path.is_absolute():
        return path
    if project_plan_path is None:
        return path
    plan_path = Path(project_plan_path).expanduser()
    plan_dir = plan_path.parent
    workspace_root = _workspace_root_from_project_plan_path(plan_path)
    candidates = []
    if workspace_root is not None:
        candidates.append(workspace_root / path)
    candidates.append(plan_dir / path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if candidates else path


def _workspace_root_from_project_plan_path(project_plan_path: Path) -> Path | None:
    """由 project-plan.json 路径推导 workspace 根目录，用于解析 .xcodeagent 相对引用。"""

    plan_dir = project_plan_path.parent
    if plan_dir.name == "plans" and plan_dir.parent.name == ".xcodeagent":
        return plan_dir.parent.parent
    if plan_dir.name == "plans":
        return plan_dir.parent
    return plan_dir


def _endpoint_ids(page_detail: dict[str, Any]) -> list[str]:
    """从页面详情持久化引用中提取稳定且去重的 endpoint 标识。"""

    references = page_detail.get("references") if isinstance(page_detail.get("references"), dict) else {}
    dependencies = page_detail.get("endpoint_dependencies") or references.get("endpoint_dependencies") or []
    result: list[str] = []
    for dependency in _dict_items(dependencies):
        endpoint_id = str(dependency.get("endpoint_id") or "")
        if endpoint_id and endpoint_id not in result:
            result.append(endpoint_id)
    return result


def _endpoint_index(value: Any) -> dict[str, dict[str, Any]]:
    """建立 endpoint 到数据源、契约和详情引用的只读反向索引。"""

    index: dict[str, dict[str, Any]] = {}
    for contract in _dict_items(value):
        contract_id = str(contract.get("id") or "")
        for endpoint_index, endpoint in enumerate(_dict_items(contract.get("endpoints"))):
            endpoint_id = str(endpoint.get("id") or endpoint_index + 1)
            indexed_endpoint = {
                "data_source_id": str(contract.get("data_source_id") or ""),
                "api_contract_id": contract_id,
                "detail_design": endpoint.get("detail_design"),
            }
            index.setdefault(endpoint_id, indexed_endpoint)
            if contract_id:
                index[f"{contract_id}\0{endpoint_id}"] = indexed_endpoint
    return index


def _endpoint_unit_id(api_contract_id: str, endpoint_id: str) -> str:
    """生成 endpoint Unit 的稳定复合标识，避免不同契约下接口 ID 冲突。"""

    return f"endpoint:{api_contract_id}:{endpoint_id}"


def _artifact_ref(reference: Any, target_id: str) -> dict[str, Any]:
    """投射详情 artifact 的稳定路径、哈希和业务标识。"""

    detail_ref = reference if isinstance(reference, dict) else {}
    return {
        "id": target_id,
        "json_path": detail_ref.get("json_path"),
        "sha256": detail_ref.get("sha256"),
    }


def _page_requires_auth(page: dict[str, Any]) -> bool:
    """根据页面权限引用判断当前页面构建是否需要鉴权公共能力。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    permissions = references.get("permissions") or page.get("permissions") or []
    return bool(permissions) and list(permissions) != ["anonymous"]


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项，统一处理不可信外部结构。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
