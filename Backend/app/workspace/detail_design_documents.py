"""页面与数据源详细设计的独立文件持久化。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workflow_artifact_root


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的字典项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _safe_file_stem(value: Any, *, prefix: str) -> str:
    """把稳定业务 id 转换为可安全写入文件系统的文件名。"""

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "")).strip("-_")
    return f"{prefix}{normalized or 'unknown'}"


def _artifact_directory(state: dict[str, Any], *, artifact_type: str) -> Path:
    """按详细设计类型返回页面或数据源的独立目录。"""

    directory_name = "data-source" if artifact_type == "data_source" else "pages"
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


def hydrate_external_detail_designs(
    project_plan_path: str | Path,
    project_plan: dict[str, Any],
) -> dict[str, Any]:
    """把外置页面/数据源详情按原文件内容读回内存，供 Workflow 展示和确认。"""

    plan_path = Path(project_plan_path).expanduser()
    hydrated = deepcopy(project_plan)
    page_details: list[dict[str, Any]] = []
    data_source_details: list[dict[str, Any]] = []

    for page in _dict_items(hydrated.get("frontend_pages")):
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
            page_details.append(detail)

    for source in _dict_items(hydrated.get("data_sources")):
        source_id = str(source.get("id") or "")
        if not source_id:
            continue
        detail_path = _resolve_detail_json_path(
            plan_path,
            source.get("detail_design") if isinstance(source.get("detail_design"), dict) else {},
            fallback_directory="data-source",
            fallback_stem=_safe_file_stem(source_id, prefix="data-source--"),
        )
        detail = _read_json_object(detail_path) if detail_path else None
        if isinstance(detail, dict):
            data_source_details.append(detail)

    if page_details:
        hydrated["page_detail_plans"] = page_details
    if data_source_details:
        hydrated["data_source_detail_plans"] = data_source_details
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
    ):
        persisted.pop(key, None)
    return persisted


def _data_source_dependencies(detail: dict[str, Any]) -> dict[str, list[str]]:
    """从数据源详情中提取它关联的契约和页面，供后续任务装配使用。"""

    dependentPageIds = []
    for item in detail.get("dependent_pages", []):
        if isinstance(item, dict) and item.get("pageId"):
            dependentPageIds.append(str(item.get("pageId")))
        elif isinstance(item, str) and item.strip():
            dependentPageIds.append(item.strip())
    return {
        "api_contract_ids": sorted(
            str(item.get("id"))
            for item in _dict_items(detail.get("api_contracts"))
            if item.get("id")
        ),
        "pageIds": sorted(dict.fromkeys(dependentPageIds)),
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


def externalize_detail_designs(state: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """写出详情文件，并返回不再内嵌详情正文的轻量 ProjectPlan。"""

    from app.workspace.plan_documents import (
        render_data_source_detail_markdown,
        render_page_detail_markdown,
    )

    compact_plan = deepcopy(plan)
    page_directory = _artifact_directory(state, artifact_type="page")
    data_source_directory = _artifact_directory(state, artifact_type="data_source")
    page_directory.mkdir(parents=True, exist_ok=True)
    data_source_directory.mkdir(parents=True, exist_ok=True)

    for detail in _dict_items(plan.get("page_detail_plans")):
        pageId = str(detail.get("pageId") or "")
        if not pageId:
            continue
        stem = _safe_file_stem(pageId, prefix="page--")
        json_path = page_directory / f"{stem}.json"
        markdown_path = page_directory / f"{stem}.md"
        persisted_detail = _persisted_page_detail(detail)
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
        for page in _dict_items(compact_plan.get("frontend_pages")):
            if str(page.get("pageId") or "") == pageId:
                page["detail_design"] = reference
                page["detail_status"] = reference["status"]
                break

    for detail in _dict_items(plan.get("data_source_detail_plans")):
        source_id = str(detail.get("data_source_id") or "")
        if not source_id:
            continue
        stem = _safe_file_stem(source_id, prefix="data-source--")
        json_path = data_source_directory / f"{stem}.json"
        markdown_path = data_source_directory / f"{stem}.md"
        sha256 = _write_json_atomically(json_path, detail)
        _write_markdown_atomically(markdown_path, render_data_source_detail_markdown(detail))
        reference = _detail_reference(
            state,
            json_path=json_path,
            markdown_path=markdown_path,
            detail=detail,
            sha256=sha256,
            dependencies=_data_source_dependencies(detail),
        )
        for source in _dict_items(compact_plan.get("data_sources")):
            if str(source.get("id") or "") == source_id:
                source["detail_design"] = reference
                source["detail_status"] = reference["status"]
                break

    compact_plan.pop("page_detail_plans", None)
    compact_plan.pop("data_source_detail_plans", None)
    return compact_plan


def write_compact_project_plan(state: dict[str, Any], path: Path, plan: dict[str, Any]) -> None:
    """写出独立详情后原子写入仅含索引的 ProjectPlan。"""

    _write_project_plan_atomically(path, externalize_detail_designs(state, plan))
