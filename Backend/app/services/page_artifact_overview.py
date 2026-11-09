"""从当前工作区事实生成页面开发概览，不写盘、不推断或生成任务。"""

import json
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.application_lifecycle import load_application_lifecycle
from app.services.page_artifact_tasks import page_tasks
from app.workspace.content_artifacts import expand_materials


def _document(workspace: Path, relative: str) -> dict[str, Any]:
    """读取唯一当前文档并展开当前内容引用，缺失与损坏均交由调用方明确处理。"""
    path = workspace / WORKSPACE_ARTIFACT_DIR / relative
    if not path.resolve().is_relative_to(workspace):
        raise ValueError("文档不在当前工作区内。")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("页面概览来源文档过大，无法完整读取。")
    data = expand_materials(workspace, json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(data, dict):
        raise ValueError(f"{relative} 根节点必须是对象。")
    return data


def _records(value: Any, field: str) -> list[dict[str, Any]]:
    """要求来源集合保持当前对象数组合同，损坏时不能假装成空目录。"""
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{field} 必须是对象数组。")
    return value


def _endpoints(workspace: Path, page_id: str, progress: dict[str, Any]) -> list[dict[str, Any]]:
    """严格通过 TechnicalPlan 当前页面的 endpoint_dependencies 关联接口。"""
    plan = _document(workspace, "plans/technical-plan.json")
    pages = _records(plan.get("pages"), "TechnicalPlan.pages")
    page = next((item for item in pages if item.get("pageId") == page_id), None)
    if page is None:
        raise ValueError("技术规划中尚无此页面，无法读取关联接口。")
    references = page.get("references")
    if not isinstance(references, dict):
        raise ValueError("页面技术引用缺失。")
    dependencies = _records(references.get("endpoint_dependencies"), "endpoint_dependencies")
    catalog: dict[str, list[dict[str, Any]]] = {}
    for contract in _records(plan.get("api_contracts"), "api_contracts"):
        for endpoint in _records(contract.get("endpoints"), "endpoints"):
            key = str(endpoint.get("id") or "")
            catalog.setdefault(key, []).append({
                "apiContractId": str(contract.get("id") or ""),
                "endpointId": key, "name": str(endpoint.get("name") or endpoint.get("summary") or key),
                "method": str(endpoint.get("method") or ""), "path": str(endpoint.get("path") or ""),
                "summary": str(endpoint.get("summary") or ""),
                "progress": progress.get(str(contract.get("id") or ""), {}).get(key),
            })
    result = []
    seen: set[tuple[str, str]] = set()
    for dependency in dependencies:
        key = str(dependency.get("endpoint_id") or "")
        matches = catalog.get(key, [])
        if len(matches) != 1:
            raise ValueError(f"页面关联接口 {key} 不存在或身份不唯一。")
        endpoint = matches[0]
        identity = (endpoint["apiContractId"], key)
        if identity not in seen:
            seen.add(identity)
            result.append(endpoint)
    return result


def page_artifact_overview(workspace_root: str, page_id: str) -> dict[str, Any]:
    """独立读取各来源；某个来源失败时保留其余事实，并给该区域显式错误。"""
    workspace = Path(workspace_root).expanduser().resolve()
    if not workspace.is_dir() or not (workspace / WORKSPACE_ARTIFACT_DIR).is_dir():
        raise ValueError("当前应用工作区不存在。")
    product = _document(workspace, "plans/product-plan.json")
    pages = _records(product.get("pages"), "ProductPlan.pages")
    matches = [page for page in pages if page.get("pageId") == page_id]
    if len(matches) != 1:
        raise ValueError("产品规划中没有唯一对应的页面，请刷新应用产物目录。")
    page = matches[0]
    overview: dict[str, Any] = {
        "page": {"id": page_id, "name": str(page.get("name") or page_id),
                 "path": str(page.get("path") or ""), "description": str(page.get("description") or ""),
                 "confirmationStatus": str(product.get("confirmation_status") or "")},
        "progress": None, "tasks": [], "files": [], "endpoints": [],
        "taskPlanAvailable": False, "issues": {},
    }
    endpoint_progress: dict[str, Any] = {}
    try:
        lifecycle = load_application_lifecycle(workspace)
        if lifecycle is not None:
            artifacts = lifecycle.development_artifacts.model_dump(by_alias=True, mode="json")
            overview["progress"] = artifacts["pages"].get(page_id)
            endpoint_progress = artifacts["endpoints"]
            if artifacts.get("catalogError"):
                overview["issues"]["progress"] = artifacts["catalogError"]
        else:
            overview["issues"]["progress"] = "尚无应用开发状态记录。"
    except (OSError, ValueError) as exc:
        overview["issues"]["progress"] = str(exc)
    try:
        overview.update(page_tasks(workspace, page_id))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        overview["issues"]["tasks"] = str(exc)
    try:
        overview["endpoints"] = _endpoints(workspace, page_id, endpoint_progress)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        overview["issues"]["endpoints"] = str(exc)
    return overview
