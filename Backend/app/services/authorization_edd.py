"""读取生成的前端代码，验证步骤 6 的前端权限投影与接入。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.authorization_frontend_projection import verify_authorization_frontend_projection


def verify_authorization_edd(workspace: str | Path, build_task_plan: dict[str, Any]) -> list[str]:
    """在 Build 完成后只读验证前端共享投影和业务源码。"""

    frontend_projection = build_task_plan.get("authorization_frontend_projection")
    if frontend_projection is None:
        return []
    errors: list[str] = []
    try:
        verify_authorization_frontend_projection(workspace, frontend_projection)
    except (ValueError, OSError) as exc:
        return [f"权限共享投影 EDD 失败：{exc}"]
    root = Path(workspace).expanduser().resolve()
    for path, text in _source_items(root / "frontend" / "src", {".ts", ".tsx"}):
        if "/pages/" in path.as_posix() and ("fetch(" in text or "axios." in text or "service." in text):
            errors.append(f"页面源码存在禁止的直接 HTTP 调用：{path.relative_to(root)}。")
        if "/authorization/" not in path.as_posix() and _declares_auth_provider(text):
            errors.append(f"页面或共享业务源码声明了第二套 AuthProvider：{path.relative_to(root)}。")
    return errors


def _declares_auth_provider(source: str) -> bool:
    """识别业务源码中新建的 AuthProvider 声明，不把模板授权目录误判为重复实现。"""

    return any(
        marker in source
        for marker in (
            "function AuthProvider",
            "const AuthProvider",
            "class AuthProvider",
            "createContext<Auth",
        )
    )


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
