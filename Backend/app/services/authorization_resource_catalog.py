"""从确认 manifest 的完整资源源数据编译目录身份，不读取路由、Task 或生成文件。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any


class AuthorizationFrontendProjectionError(ValueError):
    """表示前端资源常量或业务路由无法按确认权限事实安全生成。"""


@dataclass(frozen=True)
class ResourceIdentity:
    """保留资源的精确源身份，避免常量名归一化吞掉目标变化。"""

    resource_key: str
    resource_type: str
    target_resource_ref: str

    def constant_reference(self) -> dict[str, str]:
        """按既有规则将源身份投射为 RESOURCES 常量引用。"""

        target = self.target_resource_ref
        page_id = target.removeprefix("page:") if target.startswith("page:") else ""
        action_parts = target.removeprefix("action:").split(":", 1) if target.startswith("action:") else []
        return resource_constant_reference(
            self.resource_key,
            self.resource_type,
            page_id=page_id or (action_parts[0] if len(action_parts) == 2 else ""),
            action_id=action_parts[1] if len(action_parts) == 2 else "",
        )


@dataclass(frozen=True)
class ResourceCatalog:
    """包含全部 system/page/operation 资源的不可变目录，不携带路由或角色授权。"""

    resources: tuple[ResourceIdentity, ...]

    def frontend_resources(self) -> list[dict[str, str]]:
        """输出现有前端投影结构，保持常量分组、命名和排序不变。"""

        return sorted(
            [item.constant_reference() for item in self.resources],
            key=lambda item: (item["group"], item["name"]),
        )


def compile_frontend_resource_catalog(manifest: dict[str, Any]) -> ResourceCatalog | None:
    """编译上游已确认 manifest 的全部资源；确认门禁仍由正式产物调用方负责。

    当前 manifest.resources 已包含 compiled page、operation 和 system resources，
    不从当前 Scope 或 bindings 补造资源。关闭权限返回 None；重复输入显式报错。
    """

    if not isinstance(manifest, dict) or not isinstance(manifest.get("enabled"), bool):
        raise AuthorizationFrontendProjectionError("authorization_manifest.enabled 必须为布尔值。")
    if not manifest["enabled"]:
        return None
    items = manifest.get("resources")
    if not isinstance(items, list) or not items:
        raise AuthorizationFrontendProjectionError("已启用权限的正式资源目录必须为非空数组。")
    resources: list[ResourceIdentity] = []
    keys: set[str] = set()
    symbols: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            raise AuthorizationFrontendProjectionError("资源目录项必须为对象。")
        resource = ResourceIdentity(
            resource_key=_identity_text(item, "resourceKey"),
            resource_type=_identity_text(item, "type"),
            target_resource_ref=_identity_text(item, "targetResourceRef"),
        )
        reference = resource.constant_reference()
        symbol = (reference["group"], reference["name"])
        # 保持旧投影的常量冲突规则，包括完全重复记录；另拒绝跨符号的同键资源。
        if symbol in symbols:
            raise AuthorizationFrontendProjectionError(f"RESOURCES 常量名冲突：{symbol[0]}.{symbol[1]}。")
        if resource.resource_key in keys:
            raise AuthorizationFrontendProjectionError(f"资源目录 resourceKey 重复：{resource.resource_key}。")
        symbols.add(symbol)
        keys.add(resource.resource_key)
        resources.append(resource)
    return ResourceCatalog(tuple(sorted(resources, key=lambda item: item.resource_key)))


def resource_catalog_fingerprint(catalog: ResourceCatalog) -> str:
    """对 canonical 源身份及其常量引用计算 64 位 SHA-256，不依赖生成文件格式。"""

    # 保留目标原文而非仅散列常量名，目标变化即使被大写/下划线归一化也会改变身份。
    canonical = [
        {
            **item.constant_reference(),
            "type": item.resource_type,
            "targetResourceRef": item.target_resource_ref,
        }
        for item in sorted(catalog.resources, key=lambda item: item.resource_key)
    ]
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _identity_text(item: dict[str, Any], field: str) -> str:
    """拒绝缺失、空白或非字符串身份，不通过隐式转换合并不同源数据。"""

    value = item.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        raise AuthorizationFrontendProjectionError(f"资源目录 {field} 必须为精确非空字符串。")
    return value


def resource_constant_reference(resource_key: str, resource_type: str, *, page_id: str = "", action_id: str = "") -> dict[str, str]:
    """把确认资源键转换为前端 RESOURCES 的稳定分组与属性名。"""

    group = {"system": "SYSTEM", "page": "PAGE", "operation": "OPERATION"}.get(resource_type)
    if not group:
        raise AuthorizationFrontendProjectionError(f"不支持的前端资源类型：{resource_type}。")
    if resource_type == "system":
        source = resource_key.removeprefix("system_")
    elif resource_type == "page":
        source = (page_id or resource_key).removeprefix("page_")
    else:
        source = "_".join(part for part in ((page_id or "").removeprefix("page_"), action_id) if part)
        source = source or resource_key.removeprefix("page_")
    name = re.sub(r"[^A-Za-z0-9]+", "_", source).strip("_").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
        raise AuthorizationFrontendProjectionError(f"资源 {resource_key} 无法生成合法 RESOURCES 常量名。")
    return {"group": group, "name": name, "resourceKey": resource_key}
