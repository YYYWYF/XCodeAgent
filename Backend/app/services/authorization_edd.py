"""读取真实生成代码，验证步骤 6 的权限投影与接入证据。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.authorization_constants_projection import apply_authorization_constants_projection
from app.services.authorization_route_projection import apply_authorization_route_projection


def verify_authorization_edd(workspace: str | Path, build_task_plan: dict[str, Any]) -> list[str]:
    """在 Build 完成后验证共享投影、页面 Permission 与 Controller ANY-OF 证据。"""

    route_projection = build_task_plan.get("authorization_route_projection")
    constants_projection = build_task_plan.get("authorization_constants_projection")
    if route_projection is None and constants_projection is None:
        return []
    errors: list[str] = []
    # 两个投影函数均为幂等写入；此处再次调用同时验证模板声明、标记与精确投影未漂移。
    try:
        apply_authorization_route_projection(workspace, route_projection)
        apply_authorization_constants_projection(workspace, constants_projection)
    except (ValueError, OSError) as exc:
        return [f"权限共享投影 EDD 失败：{exc}"]
    root = Path(workspace).expanduser().resolve()
    backend_sources = _source_texts(root / "backend" / "src", {".java"})
    for item in constants_projection if isinstance(constants_projection, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if name and not any("@RequireAnyResource" in text and name in text for text in backend_sources):
            errors.append(f"操作资源常量 {name} 缺少真实 Controller RequireAnyResource 证据。")
    for path, text in _source_items(root / "frontend" / "src", {".ts", ".tsx"}):
        if "/pages/" in path.as_posix() and ("fetch(" in text or "axios." in text or "service." in text):
            errors.append(f"页面源码存在禁止的直接 HTTP 调用：{path.relative_to(root)}。")
    return errors


def _source_items(root: Path, suffixes: set[str]) -> list[tuple[Path, str]]:
    """收集有限源码文本，读取失败时保留为空以便 EDD 给出缺失证据。"""

    if not root.is_dir():
        return []
    result: list[tuple[Path, str]] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in suffixes:
            try:
                result.append((path, path.read_text(encoding="utf-8")))
            except OSError:
                continue
    return result


def _source_texts(root: Path, suffixes: set[str]) -> list[str]:
    """返回 EDD 所需的源码文本列表。"""

    return [text for _path, text in _source_items(root, suffixes)]
