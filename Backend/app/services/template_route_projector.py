"""调用模板随工作区交付的 Route Projector 契约。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.workspace_process_registry import workspace_process_registry


ROUTE_PROJECTOR_PROTOCOL = "route-projector.v2"
ROUTE_PROJECTOR_CONTRACT = Path(".xcodeagent/template-contracts/route-projector.json")
ROUTE_PROJECTOR_CONTRACTS_DIRECTORY = ROUTE_PROJECTOR_CONTRACT.parent
_COMMAND_TIMEOUT_SECONDS = 30


class TemplateRouteProjectorError(ValueError):
    """表示模板 Route Projector 契约、输入或执行结果无效。"""


def load_route_projector_contract(workspace: str | Path) -> dict[str, Any]:
    """读取模板声明的 Projector 描述符，不理解任何模板源码结构。"""

    root = Path(workspace).expanduser().resolve()
    path = root / ROUTE_PROJECTOR_CONTRACT
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TemplateRouteProjectorError("模板缺少 Route Projector Descriptor。") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateRouteProjectorError("Route Projector Descriptor 无法读取或不是有效 JSON。") from exc
    if not isinstance(value, dict):
        raise TemplateRouteProjectorError("Route Projector Descriptor 必须是对象。")
    if value.get("schemaVersion") != "route-projector-contract.v2":
        raise TemplateRouteProjectorError("Route Projector Descriptor schemaVersion 不受支持。")
    if value.get("protocol") != ROUTE_PROJECTOR_PROTOCOL:
        raise TemplateRouteProjectorError("Route Projector Descriptor protocol 不受支持。")
    command = value.get("command")
    if not isinstance(command, list) or not command or any(
        not isinstance(item, str) or not item.strip() for item in command
    ):
        raise TemplateRouteProjectorError("Route Projector Descriptor command 必须是非空字符串数组。")
    input_schema = _load_contract_schema(root, value.get("inputSchema"), "Input")
    output_schema = _load_contract_schema(root, value.get("outputSchema"), "Output")
    return {
        "schemaVersion": value["schemaVersion"],
        "protocol": value["protocol"],
        "command": list(command),
        "inputSchema": input_schema,
        "outputSchema": output_schema,
    }


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


def apply_template_route_projection(
    workspace: str | Path, route_projector_input: Mapping[str, Any], *, run_id: str = "",
) -> dict[str, Any]:
    """在固定 workspace 内以 JSON stdin 调用模板 Projector，绝不使用 shell。"""

    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise TemplateRouteProjectorError("Route Projector workspace 不存在。")
    contract = load_route_projector_contract(root)
    validate_route_projector_input(route_projector_input, contract["inputSchema"])
    # Process registry 使用二进制 stdin；显式 UTF-8 编码避免 subprocess 在运行时拒绝 str。
    payload = json.dumps(dict(route_projector_input), ensure_ascii=False).encode("utf-8")
    try:
        completed = workspace_process_registry.run(
            contract["command"], workspace=root, input=payload, capture_output=True,
            timeout=_COMMAND_TIMEOUT_SECONDS, check=False, cwd=root, run_id=run_id,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise TemplateRouteProjectorError(f"Route Projector 执行失败：{exc}") from exc
    if completed.returncode != 0:
        detail = _process_text(completed.stderr or completed.stdout or "未知错误").strip()
        raise TemplateRouteProjectorError(f"Route Projector 返回失败：{detail}")
    try:
        result = json.loads(_process_text(completed.stdout))
    except json.JSONDecodeError as exc:
        raise TemplateRouteProjectorError("Route Projector stdout 必须是 JSON 结果。") from exc
    validate_route_projector_result(result, route_projector_input, contract["outputSchema"])
    return result


def _process_text(value: Any) -> str:
    """将受控子进程的 stdout 或 stderr 统一解码为 UTF-8 文本。"""

    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")


def validate_route_projector_input(value: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    """按模板公开 Schema 和跨仓库通用规则校验投影输入。"""

    _validate_json_schema(value, schema, "Route Projector Input")
    pages = value.get("pages") if isinstance(value, Mapping) else None
    if value.get("protocol") != ROUTE_PROJECTOR_PROTOCOL or not isinstance(pages, list):
        raise TemplateRouteProjectorError("Route Projector Input 不满足当前协议。")
    _validate_pages(pages)


def validate_route_projector_result(
    value: Any, route_projector_input: Mapping[str, Any], schema: Mapping[str, Any]
) -> None:
    """校验模板 stdout 与输入页面集合的精确对应关系，异常一律阻断收尾。"""

    _validate_json_schema(value, schema, "Route Projector Output")
    if not isinstance(value, Mapping) or value.get("status") != "applied":
        raise TemplateRouteProjectorError("Route Projector 未返回 applied 成功结果。")
    requested = value.get("requestedPageIds")
    applied = value.get("appliedPageIds")
    skipped = value.get("skippedPageIds")
    expected = [page["pageId"] for page in route_projector_input.get("pages", [])]
    if not all(isinstance(ids, list) and all(isinstance(item, str) and item for item in ids) and len(ids) == len(set(ids)) for ids in (requested, applied, skipped)):
        raise TemplateRouteProjectorError("Route Projector Output 页面 ID 数组无效。")
    if requested != expected or set(applied) & set(skipped) or set(applied) | set(skipped) != set(requested):
        raise TemplateRouteProjectorError("Route Projector Output 页面集合与请求不一致。")
    positions = {page_id: index for index, page_id in enumerate(requested)}
    if any(positions[left] >= positions[right] for left, right in zip(applied, applied[1:])) or any(positions[left] >= positions[right] for left, right in zip(skipped, skipped[1:])):
        raise TemplateRouteProjectorError("Route Projector Output 页面顺序与请求不一致。")


def _load_contract_schema(root: Path, raw_path: Any, label: str) -> dict[str, Any]:
    """读取且限制 Schema 路径在模板公开契约目录内，拒绝路径逃逸。"""

    if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute():
        raise TemplateRouteProjectorError(f"Route Projector Descriptor {label} Schema 路径无效。")
    contracts_root = (root / ROUTE_PROJECTOR_CONTRACTS_DIRECTORY).resolve()
    path = (contracts_root / raw_path).resolve()
    if path.parent != contracts_root or path.suffix != ".json":
        raise TemplateRouteProjectorError(f"Route Projector Descriptor {label} Schema 路径越界。")
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateRouteProjectorError(f"Route Projector {label} Schema 无法读取或不是有效 JSON。") from exc
    if not isinstance(schema, dict):
        raise TemplateRouteProjectorError(f"Route Projector {label} Schema 必须是对象。")
    return schema


def _validate_pages(pages: list[Any]) -> None:
    """执行双方固定的页面 DTO 校验，避免 Schema 被误配时放宽公共契约。"""

    seen: set[str] = set()
    for page in pages:
        if not isinstance(page, Mapping):
            raise TemplateRouteProjectorError("Route Projector Input pages 包含非法对象。")
        page_id, name, resource_key = page.get("pageId"), page.get("name"), page.get("resourceKey")
        if not isinstance(page_id, str) or not page_id or page_id in seen or not _is_page_id(page_id):
            raise TemplateRouteProjectorError("Route Projector Input pageId 无效。")
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise TemplateRouteProjectorError("Route Projector Input name 无效。")
        if resource_key is not None and (not isinstance(resource_key, str) or not resource_key.strip() or resource_key != resource_key.strip()):
            raise TemplateRouteProjectorError("Route Projector Input resourceKey 无效。")
        seen.add(page_id)


def _is_page_id(value: str) -> bool:
    """判断 pageId 是否满足当前跨仓库约定的小写下划线格式。"""

    parts = value.split("_")
    return bool(parts) and all(part and part.isascii() and part.isalnum() and part == part.lower() for part in parts)


def _validate_json_schema(value: Any, schema: Mapping[str, Any], label: str, path: str = "$") -> None:
    """校验本契约使用的 JSON Schema 子集，避免引入未声明的新运行时依赖。"""

    if "const" in schema and value != schema["const"]:
        raise TemplateRouteProjectorError(f"{label} {path} 不满足 const。")
    if "enum" in schema and value not in schema["enum"]:
        raise TemplateRouteProjectorError(f"{label} {path} 不在 enum 中。")
    expected_type = schema.get("type")
    valid_type = {
        "object": isinstance(value, Mapping), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
    }
    if isinstance(expected_type, str) and not valid_type.get(expected_type, False):
        raise TemplateRouteProjectorError(f"{label} {path} 类型必须是 {expected_type}。")
    if isinstance(value, Mapping):
        required = schema.get("required", [])
        if not isinstance(required, list) or any(key not in value for key in required):
            raise TemplateRouteProjectorError(f"{label} {path} 缺少必填字段。")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise TemplateRouteProjectorError(f"{label} {path} properties 无效。")
        if schema.get("additionalProperties") is False and any(key not in properties for key in value):
            raise TemplateRouteProjectorError(f"{label} {path} 包含未声明字段。")
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, Mapping):
                _validate_json_schema(value[key], child_schema, label, f"{path}.{key}")
    elif isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            raise TemplateRouteProjectorError(f"{label} {path} 项目数量不足。")
        if schema.get("uniqueItems") is True and len({json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value}) != len(value):
            raise TemplateRouteProjectorError(f"{label} {path} 包含重复项目。")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_json_schema(item, item_schema, label, f"{path}[{index}]")
    elif isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            raise TemplateRouteProjectorError(f"{label} {path} 字符串过短。")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            import re
            if re.search(pattern, value) is None:
                raise TemplateRouteProjectorError(f"{label} {path} 不匹配 pattern。")


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
