"""按当前 Engine managedFiles 契约检查 Workspace 模板健康状态。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from app.services.workspace_bootstrap.models import TemplateStateError


def assert_managed_workspace_healthy(workspace: str | Path, state: dict[str, Any]) -> None:
    """确认每个 Engine 受管文件存在、为普通文件且内容等于当前 State。"""

    root = Path(workspace).expanduser().resolve()
    managed_files = state.get("managedFiles")
    if not isinstance(managed_files, dict):
        raise TemplateStateError("TemplateState.managedFiles 无效，无法执行健康检查。")
    for raw_path, expected_content in managed_files.items():
        relative = _relative_path(raw_path)
        target = root / relative
        if not target.is_file() or target.is_symlink():
            raise TemplateStateError(f"MANAGED_WORKSPACE_UNHEALTHY：缺少受管文件 {raw_path}。")
        try:
            actual_content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise TemplateStateError(f"MANAGED_WORKSPACE_UNHEALTHY：无法读取 {raw_path}。") from exc
        if actual_content != expected_content:
            raise TemplateStateError(f"MANAGED_WORKSPACE_UNHEALTHY：受管文件漂移 {raw_path}。")


def _relative_path(raw_path: Any) -> Path:
    """将 Engine POSIX 路径限制为 Workspace 内的普通相对路径。"""

    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise TemplateStateError("TemplateState.managedFiles 包含无效路径。")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or path.parts[0] == ".git":
        raise TemplateStateError("TemplateState.managedFiles 包含越界路径。")
    return Path(*path.parts)
