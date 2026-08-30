"""将确认的操作资源常量投影写入 auth 模板声明的 AuthConstants 托管区。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.services.application_template_generation import load_template_generation_manifest


# auth 模板以固定常量类和标记区声明平台唯一可写的业务资源常量位置。
AUTH_CONSTANTS_RELATIVE_PATH = Path(
    "backend/src/main/java/com/cmbchina/backend/auth/domain/constant/AuthConstants.java"
)
AUTH_CONSTANTS_START = "// XCODEAGENT_AUTH_CONSTANTS_START"
AUTH_CONSTANTS_END = "// XCODEAGENT_AUTH_CONSTANTS_END"


class AuthorizationConstantsProjectionError(ValueError):
    """表示 AuthConstants 模板托管区缺失或投影不安全。"""


def apply_authorization_constants_projection(
    workspace: str | Path,
    projection: Any,
) -> dict[str, Any]:
    """在 Endpoint 叶子任务分发前幂等写入业务操作资源常量。"""

    items = _projection_items(projection)
    if not items:
        return {"applied": False, "reason": "authorization_disabled_or_no_operation_resources"}
    workspace_path = Path(workspace).expanduser().resolve()
    manifest = load_template_generation_manifest(workspace_path)
    if _backend_branch(manifest) != "auth":
        raise AuthorizationConstantsProjectionError("权限常量投影存在，但后端模板不是 auth 分支。")
    target = _auth_constants_path(workspace_path)
    content = target.read_text(encoding="utf-8")
    start, end = _managed_bounds(content)
    body_start = start + len(AUTH_CONSTANTS_START)
    updated = content[:body_start] + "\n" + _render_projection(items) + content[end:]
    if updated != content:
        _write_text_atomically(target, updated)
    return {"applied": True, "path": str(target.relative_to(workspace_path)), "count": len(items)}


def verify_authorization_constants_projection(
    workspace: str | Path,
    projection: Any,
) -> dict[str, Any]:
    """只读验证 AuthConstants 托管区与确认投影完全一致，不写入任何文件。"""

    items = _projection_items(projection)
    if not items:
        return {"verified": False, "reason": "authorization_disabled_or_no_operation_resources"}
    workspace_path = Path(workspace).expanduser().resolve()
    manifest = load_template_generation_manifest(workspace_path)
    if _backend_branch(manifest) != "auth":
        raise AuthorizationConstantsProjectionError("权限常量投影存在，但后端模板不是 auth 分支。")
    target = _auth_constants_path(workspace_path)
    content = target.read_text(encoding="utf-8")
    start, end = _managed_bounds(content)
    body_start = start + len(AUTH_CONSTANTS_START)
    expected = content[:body_start] + "\n" + _render_projection(items) + content[end:]
    if content != expected:
        raise AuthorizationConstantsProjectionError("AuthConstants 托管区与确认投影不一致。")
    return {"verified": True, "path": str(target.relative_to(workspace_path)), "count": len(items)}


def _projection_items(value: Any) -> list[dict[str, str]]:
    """严格校验平台持久化的常量名和值，拒绝系统或页面资源。"""

    if value is None:
        return []
    if not isinstance(value, list):
        raise AuthorizationConstantsProjectionError("Build DAG 的 authorization_constants_projection 必须是数组。")
    result: list[dict[str, str]] = []
    names: set[str] = set()
    keys: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise AuthorizationConstantsProjectionError("AuthConstants 投影包含非法项。")
        name = _required_text(item, "name")
        resource_key = _required_text(item, "resourceKey")
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9_]*_RESOURCE", name)
            or name != f"{resource_key.upper()}_RESOURCE"
            or resource_key == "system_authorization_management"
            or name in names
            or resource_key in keys
        ):
            raise AuthorizationConstantsProjectionError("AuthConstants 投影存在非法、重复或漂移常量。")
        names.add(name)
        keys.add(resource_key)
        result.append({"name": name, "resourceKey": resource_key})
    return sorted(result, key=lambda item: item["name"])


def _auth_constants_path(workspace: Path) -> Path:
    """定位 auth 模板唯一且受平台管理的 AuthConstants 常量文件。"""

    target = (workspace / AUTH_CONSTANTS_RELATIVE_PATH).resolve()
    if not target.is_file():
        raise AuthorizationConstantsProjectionError(
            f"auth 模板缺少 AuthConstants 托管文件：{AUTH_CONSTANTS_RELATIVE_PATH}。"
        )
    return target


def _managed_bounds(content: str) -> tuple[int, int]:
    """校验并定位固定 AuthConstants 业务常量插槽，拒绝模板漂移。"""

    start = content.find(AUTH_CONSTANTS_START)
    end = content.find(AUTH_CONSTANTS_END)
    if start < 0 or end < 0 or end <= start:
        raise AuthorizationConstantsProjectionError("AuthConstants 托管文件缺少有效边界标记。")
    return start, end


def _backend_branch(manifest: dict[str, Any]) -> str:
    """读取模板 manifest 中后端实际下载的分支。"""

    steps = manifest.get("steps") if isinstance(manifest.get("steps"), dict) else {}
    download = steps.get("download") if isinstance(steps.get("download"), dict) else {}
    targets = download.get("targets") if isinstance(download.get("targets"), dict) else {}
    backend = targets.get("backend") if isinstance(targets.get("backend"), dict) else {}
    return str(backend.get("branch") or "").strip()


def _required_text(value: dict[str, Any], key: str) -> str:
    """读取必填文本字段。"""

    text = str(value.get(key) or "").strip()
    if not text:
        raise AuthorizationConstantsProjectionError(f"AuthConstants 投影缺少 {key}。")
    return text


def _render_projection(items: list[dict[str, str]]) -> str:
    """渲染 Java 8 可用的业务资源常量声明。"""

    return "\n".join(
        f'    public static final String {item["name"]} = "{item["resourceKey"]}";'
        for item in items
    ) + "\n"


def _write_text_atomically(path: Path, content: str) -> None:
    """原子更新 AuthConstants 标记区，保留模板其他逻辑。"""

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
