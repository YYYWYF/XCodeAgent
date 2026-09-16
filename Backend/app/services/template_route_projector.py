"""调用模板随工作区交付的 Route Projector 契约。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.workspace_process_registry import workspace_process_registry


ROUTE_PROJECTOR_PROTOCOL = "route-projector.v1"
ROUTE_PROJECTOR_CONTRACT = Path(".xcodeagent/template-contracts/route-projector.json")
_COMMAND_TIMEOUT_SECONDS = 30


class TemplateRouteProjectorError(ValueError):
    """表示模板 Route Projector 契约、输入或执行结果无效。"""


def load_route_projector_contract(workspace: str | Path) -> dict[str, Any]:
    """读取模板声明的 Projector 描述符，不理解任何模板源码结构。"""

    path = Path(workspace).expanduser().resolve() / ROUTE_PROJECTOR_CONTRACT
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TemplateRouteProjectorError("模板缺少 Route Projector Descriptor。") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateRouteProjectorError("Route Projector Descriptor 无法读取或不是有效 JSON。") from exc
    if not isinstance(value, dict):
        raise TemplateRouteProjectorError("Route Projector Descriptor 必须是对象。")
    if value.get("schemaVersion") != "route-projector-contract.v1":
        raise TemplateRouteProjectorError("Route Projector Descriptor schemaVersion 不受支持。")
    if value.get("protocol") != ROUTE_PROJECTOR_PROTOCOL:
        raise TemplateRouteProjectorError("Route Projector Descriptor protocol 不受支持。")
    command = value.get("command")
    if not isinstance(command, list) or not command or any(
        not isinstance(item, str) or not item.strip() for item in command
    ):
        raise TemplateRouteProjectorError("Route Projector Descriptor command 必须是非空字符串数组。")
    return {"schemaVersion": value["schemaVersion"], "protocol": value["protocol"], "command": list(command)}


def build_route_projector_input(
    product_plan: Mapping[str, Any], authorization_manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """从两份权威业务产物裁剪模板执行时唯一需要的页面 DTO。"""

    pages = product_plan.get("pages") if isinstance(product_plan, Mapping) else None
    if not isinstance(pages, list):
        raise TemplateRouteProjectorError("Confirmed ProductPlan 缺少 pages 数组。")
    resource_map = _page_resource_map(authorization_manifest)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for page in pages:
        if not isinstance(page, Mapping):
            raise TemplateRouteProjectorError("ProductPlan.pages 包含非法页面对象。")
        page_id = page.get("pageId")
        name = page.get("name")
        if not isinstance(page_id, str) or not page_id.strip() or page_id != page_id.strip() or page_id in seen:
            raise TemplateRouteProjectorError("ProductPlan.pages 包含缺失或重复 pageId。")
        if not isinstance(name, str) or not name.strip():
            raise TemplateRouteProjectorError(f"ProductPlan 页面 {page_id} 缺少名称。")
        seen.add(page_id)
        item = {"pageId": page_id, "name": name}
        if page_id in resource_map:
            item["resourceKey"] = resource_map[page_id]
        result.append(item)
    return {"protocol": ROUTE_PROJECTOR_PROTOCOL, "pages": result}


def extract_route_facts(route_projector_input: Mapping[str, Any]) -> dict[str, dict[str, str | None]]:
    """将最小 Projector 输入转换为与页面顺序无关的比较事实。"""

    pages = route_projector_input.get("pages") if isinstance(route_projector_input, Mapping) else None
    if route_projector_input.get("protocol") != ROUTE_PROJECTOR_PROTOCOL or not isinstance(pages, list):
        raise TemplateRouteProjectorError("Route Projector Input 不满足当前协议。")
    facts: dict[str, dict[str, str | None]] = {}
    for page in pages:
        if not isinstance(page, Mapping):
            raise TemplateRouteProjectorError("Route Projector Input pages 包含非法对象。")
        page_id, name = page.get("pageId"), page.get("name")
        resource_key = page.get("resourceKey")
        if not isinstance(page_id, str) or not page_id.strip() or not isinstance(name, str) or not name.strip() or page_id in facts:
            raise TemplateRouteProjectorError("Route Projector Input 页面身份无效。")
        if resource_key is not None and (not isinstance(resource_key, str) or not resource_key.strip()):
            raise TemplateRouteProjectorError("Route Projector Input resourceKey 无效。")
        facts[page_id] = {"name": name, "resourceKey": resource_key}
    return facts


def requires_route_projection(previous_route_facts: Mapping[str, Any] | None, current_input: Mapping[str, Any]) -> bool:
    """比较最近成功 Build 证据与当前最小事实，缺少历史证据时强制 reconcile。"""

    return previous_route_facts is None or dict(previous_route_facts) != extract_route_facts(current_input)


def apply_template_route_projection(
    workspace: str | Path, route_projector_input: Mapping[str, Any], *, run_id: str = "",
) -> dict[str, Any]:
    """在固定 workspace 内以 JSON stdin 调用模板 Projector，绝不使用 shell。"""

    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise TemplateRouteProjectorError("Route Projector workspace 不存在。")
    contract = load_route_projector_contract(root)
    payload = json.dumps(dict(route_projector_input), ensure_ascii=False)
    try:
        completed = workspace_process_registry.run(
            contract["command"], workspace=root, input=payload, capture_output=True,
            timeout=_COMMAND_TIMEOUT_SECONDS, check=False, cwd=root, run_id=run_id,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise TemplateRouteProjectorError(f"Route Projector 执行失败：{exc}") from exc
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "未知错误").strip()
        raise TemplateRouteProjectorError(f"Route Projector 返回失败：{detail}")
    try:
        result = json.loads(str(completed.stdout or ""))
    except json.JSONDecodeError as exc:
        raise TemplateRouteProjectorError("Route Projector stdout 必须是 JSON 结果。") from exc
    if not isinstance(result, dict) or result.get("status") != "applied":
        raise TemplateRouteProjectorError("Route Projector 未返回 applied 成功结果。")
    return result


def _page_resource_map(authorization_manifest: Mapping[str, Any] | None) -> dict[str, str]:
    """从权限 manifest 提取受控页面资源，不把它投影为独立规划模型。"""

    if not isinstance(authorization_manifest, Mapping):
        return {}
    bindings = authorization_manifest.get("bindings")
    pages = bindings.get("pages") if isinstance(bindings, Mapping) else None
    if pages is None:
        return {}
    if not isinstance(pages, list):
        raise TemplateRouteProjectorError("authorization_manifest.bindings.pages 必须是数组。")
    result: dict[str, str] = {}
    for item in pages:
        if not isinstance(item, Mapping):
            raise TemplateRouteProjectorError("authorization_manifest 页面绑定无效。")
        page_id, resource_key = item.get("pageId"), item.get("resourceKey")
        if not isinstance(page_id, str) or not page_id.strip() or not isinstance(resource_key, str) or not resource_key.strip() or page_id in result:
            raise TemplateRouteProjectorError("authorization_manifest 页面资源绑定无效。")
        result[page_id] = resource_key
    return result
