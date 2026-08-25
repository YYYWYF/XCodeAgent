from __future__ import annotations

from typing import Any


def backend_workspace_context(workspace_snapshot: Any) -> dict[str, str]:
    """把 WorkspaceSnapshot 中完整的后端目录树投影给 Java Agent。"""

    snapshot = workspace_snapshot if isinstance(workspace_snapshot, dict) else {}
    backend_section = snapshot.get("backend")
    backend_section = backend_section if isinstance(backend_section, dict) else {}
    directory_structure = backend_section.get("dir_structure")
    directory_structure = (
        directory_structure if isinstance(directory_structure, str) else ""
    )
    candidates = _backend_root_from_dir_structure(directory_structure)
    if candidates:
        selected = "backend" if "backend" in candidates else "Backend"
        return {
            "backend_working_directory": f"/{selected}",
            "backend_directory_structure": directory_structure,
        }
    raise ValueError(
        "WorkspaceSnapshot.backend.dir_structure does not identify a backend directory."
    )


def _backend_root_from_dir_structure(value: Any) -> set[str]:
    """从 backend.dir_structure 的根节点读取后端工程目录。"""

    if not isinstance(value, str):
        return set()
    first_line = next((line.strip() for line in value.splitlines() if line.strip()), "")
    for tree_prefix in ("└── ", "├── "):
        if first_line.startswith(tree_prefix):
            first_line = first_line[len(tree_prefix):]
            break
    normalized = _safe_workspace_relative_path(first_line.rstrip("/"))
    return {normalized} if normalized in {"backend", "Backend"} else set()


def _safe_workspace_relative_path(value: Any) -> str:
    """规整 WorkspaceSnapshot 路径并拒绝绝对路径与目录穿越。"""

    path = str(value or "").strip().replace("\\", "/")
    if not path or path.startswith("/") or ":" in path.split("/", 1)[0]:
        return ""
    parts = [part for part in path.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)
