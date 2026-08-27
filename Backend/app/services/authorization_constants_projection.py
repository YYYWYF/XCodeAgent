"""将确认的操作资源常量投影写入 auth 模板声明的 AuthConstants 托管区。"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.services.application_template_generation import load_template_generation_manifest


AUTH_CONSTANTS_DESCRIPTOR_RELATIVE_PATH = Path(
    "backend/.xcodeagent/auth-constants-projection.json"
)
AUTH_CONSTANTS_DESCRIPTOR_SCHEMA = "xcodeagent.auth-constants-projection.v1"


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
    descriptor = _load_descriptor(workspace_path)
    target = _target_path(workspace_path, descriptor)
    start_marker = _required_text(descriptor, "startMarker")
    end_marker = _required_text(descriptor, "endMarker")
    content = target.read_text(encoding="utf-8")
    start = content.find(start_marker)
    end = content.find(end_marker)
    if start < 0 or end < 0 or end <= start:
        raise AuthorizationConstantsProjectionError("AuthConstants 托管文件缺少有效边界标记。")
    body_start = start + len(start_marker)
    updated = content[:body_start] + "\n" + _render_projection(items) + content[end:]
    if updated != content:
        _write_text_atomically(target, updated)
    return {"applied": True, "path": str(target.relative_to(workspace_path)), "count": len(items)}


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


def _load_descriptor(workspace: Path) -> dict[str, Any]:
    """读取 auth 后端模板提供的 AuthConstants 托管区声明。"""

    path = workspace / AUTH_CONSTANTS_DESCRIPTOR_RELATIVE_PATH
    if not path.is_file():
        raise AuthorizationConstantsProjectionError(
            f"auth 模板缺少 AuthConstants 托管区声明：{path.relative_to(workspace)}。"
        )
    try:
        descriptor = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorizationConstantsProjectionError("AuthConstants 托管区声明无法读取。") from exc
    if not isinstance(descriptor, dict) or descriptor.get("schemaVersion") != AUTH_CONSTANTS_DESCRIPTOR_SCHEMA:
        raise AuthorizationConstantsProjectionError("AuthConstants 托管区声明 schema 无效。")
    return descriptor


def _target_path(workspace: Path, descriptor: dict[str, Any]) -> Path:
    """将模板目标限定在 backend 目录内的现有 Java 常量文件。"""

    target = (workspace / "backend" / _required_text(descriptor, "targetPath")).resolve()
    backend_root = (workspace / "backend").resolve()
    if backend_root not in target.parents or target.suffix != ".java" or not target.is_file():
        raise AuthorizationConstantsProjectionError("AuthConstants 托管目标必须是 backend 内已有 Java 文件。")
    return target


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
