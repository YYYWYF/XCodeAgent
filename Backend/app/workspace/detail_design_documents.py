"""页面与 endpoint 详细设计的独立文件持久化。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.services.api_contracts import normalize_page_api_dependencies
from app.services.frontend_page_tree import (
    find_frontend_page,
    project_plan_page_records,
    update_frontend_page_leaves,
)
from app.workspace.spec_documents import workflow_artifact_root


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _endpoint_identity(endpoint: dict[str, Any], index: int) -> str:
    """返回 endpoint 的稳定文件标识；没有显式 id 时使用与选择器一致的 1-based 序号。"""

    return str(endpoint.get("id") or index + 1)


def _safe_file_stem(value: Any, *, prefix: str) -> str:
    """把稳定业务 id 转换为可安全写入文件系统的文件名。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "")).strip("-_")
    return f"{prefix}{normalized or 'unknown'}"


def _artifact_directory(state: dict[str, Any], *, artifact_type: str) -> Path:
    """按详细设计类型返回页面或 endpoint 的独立目录。"""

    directory_name = "endpoints" if artifact_type == "endpoint" else "pages"
    return workflow_artifact_root(state) / "plans" / directory_name


def _workspace_relative_path(state: dict[str, Any], path: Path) -> str:
    """生成从工作区根目录开始的稳定相对路径，供 ProjectPlan 索引引用。"""

    workspace = Path(str(state.get("workspace") or "")).expanduser()
    if workspace.is_dir():
        try:
            return str(path.relative_to(workspace.resolve()))
        except ValueError:
            pass
    return str(path)


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> str:
    """通过同目录临时文件原子替换 JSON 详细设计文件。"""

    content = f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_project_plan_atomically(path: Path, payload: dict[str, Any]) -> None:
    """通过同目录临时文件原子替换轻量 ProjectPlan JSON。"""

    content = f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _read_json_object(path: Path) -> dict[str, Any] | None:
    """读取已存在的详情 JSON；格式不匹配时忽略该详情。"""

    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _resolve_detail_json_path(
    project_plan_path: Path,
    reference: dict[str, Any],
    *,
    fallback_directory: str,
    fallback_stem: str,
) -> Path | None:
    """按索引路径或约定文件名定位外置详情 JSON。"""

    raw_path = str(reference.get("json_path") or "").strip()
    candidates: list[Path] = []
    if raw_path:
        indexed_path = Path(raw_path).expanduser()
        if indexed_path.is_absolute():
            candidates.append(indexed_path)
        else:
            workspace_root = project_plan_path.parent.parent.parent
            candidates.extend(
                [
                    workspace_root / indexed_path,
                    project_plan_path.parent / indexed_path,
                    project_plan_path.parent.parent / indexed_path,
                ]
            )
    candidates.append(project_plan_path.parent / fallback_directory / f"{fallback_stem}.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _hydrate_page_detail_runtime_fields(
    project_plan: dict[str, Any],
    page: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    """从主计划的页面引用恢复外置详情中省略的运行期字段。"""

    hydrated_detail = deepcopy(detail)
    page_references = page.get("references")
    detail_references = detail.get("references")
    references = (
        page_references
        if isinstance(page_references, dict)
        else detail_references if isinstance(detail_references, dict) else {}
    )
    endpoint_dependencies = [
        dict(item) for item in _dict_items(references.get("endpoint_dependencies"))
    ]
    hydrated_detail["permissions"] = list(references.get("permissions") or [])
    hydrated_detail["endpoint_dependencies"] = endpoint_dependencies
    hydrated_detail["navigation_targets"] = [
        dict(item) for item in _dict_items(references.get("navigation_targets"))
    ]
    hydrated_detail["endpoint_detail_refs"] = [
        dict(item) for item in _dict_items(references.get("endpoint_detail_refs"))
    ]
    hydrated_detail["api_dependencies"] = normalize_page_api_dependencies(
        _dict_items(project_plan.get("api_contracts")),
        [],
        endpoint_dependencies,
        page_path=str(page.get("path") or detail.get("path") or ""),
        page_name=str(page.get("name") or detail.get("page_name") or ""),
    )
    return hydrated_detail


def hydrate_external_detail_designs(
    project_plan_path: str | Path,
    project_plan: dict[str, Any],
) -> dict[str, Any]:
    """把外置页面/endpoint 详情按原文件内容读回内存，供 Workflow 展示和确认。"""

    plan_path = Path(project_plan_path).expanduser()
    hydrated = deepcopy(project_plan)
    page_details: list[dict[str, Any]] = []
    endpoint_details: list[dict[str, Any]] = []

    for page in project_plan_page_records(hydrated):
        pageId = str(page.get("pageId") or "")
        if not pageId:
            continue
        detail_path = _resolve_detail_json_path(
            plan_path,
            page.get("detail_design") if isinstance(page.get("detail_design"), dict) else {},
            fallback_directory="pages",
            fallback_stem=_safe_file_stem(pageId, prefix="page--"),
        )
        detail = _read_json_object(detail_path) if detail_path else None
        if isinstance(detail, dict):
            page_details.append(
                _hydrate_page_detail_runtime_fields(hydrated, page, detail)
            )

    for contract in _dict_items(hydrated.get("api_contracts")):
        contract_id = str(contract.get("id") or "")
        if not contract_id:
            continue
        for endpoint_index, endpoint in enumerate(_dict_items(contract.get("endpoints"))):
            endpoint_id = _endpoint_identity(endpoint, endpoint_index)
            detail_path = _resolve_detail_json_path(
                plan_path,
                endpoint.get("detail_design") if isinstance(endpoint.get("detail_design"), dict) else {},
                fallback_directory="endpoints",
                fallback_stem=_safe_file_stem(
                    f"{contract_id}--{endpoint_id}",
                    prefix="endpoint--",
                ),
            )
            detail = _read_json_object(detail_path) if detail_path else None
            if isinstance(detail, dict):
                endpoint_details.append(detail)

    if page_details:
        hydrated["page_detail_plans"] = page_details
    if endpoint_details:
        hydrated["endpoint_detail_plans"] = endpoint_details
    return hydrated


def _write_markdown_atomically(path: Path, content: str) -> None:
    """通过同目录临时文件原子替换用户可读的 Markdown 详细设计文件。"""

    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _page_dependencies(plan: dict[str, Any], detail: dict[str, Any]) -> dict[str, list[str]]:
    """从 ProjectPlan 原样投射的页面引用生成轻量索引，不重复存契约和数据源。"""

    del plan
    endpoint_ids = {
        str(item.get("endpoint_id"))
        for item in _dict_items(detail.get("endpoint_dependencies"))
        if item.get("endpoint_id")
    }
    pageIds = {
        str(item.get("targetPageId"))
        for item in _dict_items(detail.get("navigation_targets"))
        if item.get("targetPageId")
    }
    return {
        "endpoint_ids": sorted(endpoint_ids),
        "navigationTargetPageIds": sorted(pageIds),
    }


def _persisted_page_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """移除运行期上下文与可推导 API 数据，仅保留页面独有设计和固定引用。"""

    persisted = deepcopy(detail)
    persisted["references"] = {
        "permissions": list(detail.get("permissions") or []),
        "endpoint_dependencies": [
            dict(item) for item in _dict_items(detail.get("endpoint_dependencies"))
        ],
        "navigation_targets": [
            dict(item) for item in _dict_items(detail.get("navigation_targets"))
        ],
        "endpoint_detail_refs": [
            dict(item) for item in _dict_items(detail.get("endpoint_detail_refs"))
        ],
    }
    for key in (
        "source_page_context",
        "agent_note",
        "api_dependencies",
        "data_sources",
        "page_navigation",
        "permissions",
        "endpoint_dependencies",
        "navigation_targets",
        "endpoint_detail_refs",
    ):
        persisted.pop(key, None)
    return persisted


def _endpoint_dependencies(detail: dict[str, Any]) -> dict[str, list[str]]:
    """从 endpoint 详情中提取契约、接口、数据源和页面依赖索引。"""

    dependentPageIds = []
    data_usage = detail.get("data_usage") if isinstance(detail.get("data_usage"), dict) else {}
    for item in data_usage.get("served_pages", []) or detail.get("dependent_pages", []):
        if isinstance(item, dict) and item.get("pageId"):
            dependentPageIds.append(str(item.get("pageId")))
        elif isinstance(item, str) and item.strip():
            dependentPageIds.append(item.strip())
    api_contract_id = str(detail.get("api_contract_id") or "")
    endpoint_id = str(detail.get("endpoint_id") or "")
    data_source_id = str(detail.get("data_source_id") or "")
    return {
        "api_contract_ids": [api_contract_id] if api_contract_id else [],
        "endpoint_ids": [endpoint_id] if endpoint_id else [],
        "dataSourceIds": [data_source_id] if data_source_id else [],
        "pageIds": sorted(dict.fromkeys(item for item in dependentPageIds if item)),
    }


def _detail_reference(
    state: dict[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
    detail: dict[str, Any],
    sha256: str,
    dependencies: dict[str, list[str]],
) -> dict[str, Any]:
    """构造写回 ProjectPlan 的轻量详情索引。"""

    return {
        "json_path": _workspace_relative_path(state, json_path),
        "markdown_path": _workspace_relative_path(state, markdown_path),
        "status": str(detail.get("status") or "draft"),
        "sha256": sha256,
        "generation_dependencies": dependencies,
    }


def _page_endpoint_detail_refs(
    compact_plan: dict[str, Any],
    detail: dict[str, Any],
) -> list[dict[str, Any]]:
    """把页面依赖解析为 EndpointDetail 独立产物引用，不复制详情正文。"""

    references: list[dict[str, Any]] = []
    for dependency in _dict_items(detail.get("endpoint_dependencies")):
        endpoint_id = str(dependency.get("endpoint_id") or "")
        requested_contract_id = str(dependency.get("api_contract_id") or "")
        matches: list[tuple[str, dict[str, Any]]] = []
        for contract in _dict_items(compact_plan.get("api_contracts")):
            contract_id = str(contract.get("id") or "")
            if requested_contract_id and contract_id != requested_contract_id:
                continue
            for endpoint_index, endpoint in enumerate(_dict_items(contract.get("endpoints"))):
                if _endpoint_identity(endpoint, endpoint_index) == endpoint_id:
                    matches.append((contract_id, endpoint))
        if len(matches) != 1:
            raise ValueError(
                f"PageDetail dependency cannot uniquely resolve EndpointDetail: "
                f"{requested_contract_id}:{endpoint_id}"
            )
        api_contract_id, endpoint = matches[0]
        endpoint_reference = endpoint.get("detail_design")
        if not isinstance(endpoint_reference, dict) or not endpoint_reference.get("json_path"):
            raise ValueError(
                f"PageDetail dependency is missing EndpointDetail artifact: "
                f"{api_contract_id}:{endpoint_id}"
            )
        references.append(
            {
                "api_contract_id": api_contract_id,
                "endpoint_id": endpoint_id,
                "json_path": endpoint_reference.get("json_path"),
                "markdown_path": endpoint_reference.get("markdown_path"),
                "status": endpoint_reference.get("status"),
                "sha256": endpoint_reference.get("sha256"),
            }
        )
    return references


def externalize_detail_designs(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """写出详情文件，并返回不再内嵌详情正文的轻量 ProjectPlan。"""

    from app.workspace.plan_documents import (
        render_endpoint_detail_markdown,
        render_page_detail_markdown,
    )

    compact_plan = deepcopy(plan)
    page_directory = _artifact_directory(state, artifact_type="page")
    endpoint_directory = _artifact_directory(state, artifact_type="endpoint")
    if _dict_items(plan.get("page_detail_plans")):
        page_directory.mkdir(parents=True, exist_ok=True)
    if _dict_items(plan.get("endpoint_detail_plans")):
        endpoint_directory.mkdir(parents=True, exist_ok=True)

    # 先写 EndpointDetail 并建立索引，随后 PageDetail 才能记录稳定的独立产物路径。
    for detail in _dict_items(plan.get("endpoint_detail_plans")):
        api_contract_id = str(detail.get("api_contract_id") or "")
        endpoint_id = str(detail.get("endpoint_id") or "")
        if not api_contract_id or not endpoint_id:
            continue
        stem = _safe_file_stem(f"{api_contract_id}--{endpoint_id}", prefix="endpoint--")
        json_path = endpoint_directory / f"{stem}.json"
        markdown_path = endpoint_directory / f"{stem}.md"
        sha256 = _write_json_atomically(json_path, detail)
        _write_markdown_atomically(markdown_path, render_endpoint_detail_markdown(detail))
        reference = _detail_reference(
            state,
            json_path=json_path,
            markdown_path=markdown_path,
            detail=detail,
            sha256=sha256,
            dependencies=_endpoint_dependencies(detail),
        )
        for contract in _dict_items(compact_plan.get("api_contracts")):
            if str(contract.get("id") or "") != api_contract_id:
                continue
            for endpoint_index, endpoint in enumerate(_dict_items(contract.get("endpoints"))):
                if _endpoint_identity(endpoint, endpoint_index) == endpoint_id:
                    endpoint["detail_design"] = reference
                    endpoint["detail_status"] = reference["status"]
                    break

    for detail in _dict_items(plan.get("page_detail_plans")):
        pageId = str(detail.get("pageId") or "")
        if not pageId:
            continue
        stem = _safe_file_stem(pageId, prefix="page--")
        json_path = page_directory / f"{stem}.json"
        markdown_path = page_directory / f"{stem}.md"
        detail_with_refs = {
            **detail,
            "endpoint_detail_refs": _page_endpoint_detail_refs(compact_plan, detail),
        }
        persisted_detail = _persisted_page_detail(detail_with_refs)
        sha256 = _write_json_atomically(json_path, persisted_detail)
        _write_markdown_atomically(markdown_path, render_page_detail_markdown(persisted_detail))
        reference = _detail_reference(
            state,
            json_path=json_path,
            markdown_path=markdown_path,
            detail=detail,
            sha256=sha256,
            dependencies=_page_dependencies(plan, detail),
        )
        page_field = "pages" if compact_plan.get("artifact_type") == "technical-plan" else "frontend_pages"
        compact_plan[page_field] = update_frontend_page_leaves(
            compact_plan.get(page_field),
            {
                pageId: {
                    "detail_design": reference,
                    "detail_status": reference["status"],
                }
            },
        )

    compact_plan.pop("page_detail_plans", None)
    compact_plan.pop("data_source_detail_plans", None)
    compact_plan.pop("endpoint_detail_plans", None)
    return compact_plan


def write_compact_project_plan(state: dict[str, Any], path: Path, plan: dict[str, Any]) -> None:
    """写出独立详情后原子写入仅含索引的 ProjectPlan。"""

    _write_project_plan_atomically(path, externalize_detail_designs(state, plan))
