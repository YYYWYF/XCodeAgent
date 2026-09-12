from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Literal

from app.agents.auto_dedup_backend import AutoDedupFilesystemBackend
from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.permissions import FilesystemPermission

from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_ROOT
from app.services.builtin_skills import (
    BUILTIN_SKILLS_VIRTUAL_ROOT,
    validate_required_builtin_skills,
)
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.workspace.virtual_paths import host_workspace_virtual_deny_patterns
from app.workspace.workspace import SENSITIVE_FILE_NAMES


AgentWorkspaceMode = Literal[
    "frontend",
    "data_source",
    "database",
    "code_analyze",
    "code_review_repair",
    "repair_planner",
    "small_task",
    "test_generation",
    "workspace_assistant",
]

_DATA_SOURCE_IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".gradle",
        ".hg",
        ".idea",
        ".settings",
        ".svn",
        ".vscode",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "out",
    }
)


def resolve_workspace_root(workspace_root: str | None) -> Path | None:
    if not workspace_root:
        return None

    root = Path(workspace_root).expanduser().resolve()
    if not root.exists():
        raise ValueError(f"Workspace root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Workspace root is not a directory: {root}")
    return root


def create_workspace_backend(
    workspace_root: str | None,
    *,
    include_builtin_skills: bool = False,
    user_skills_backend: BackendProtocol | None = None,
    agent_memory_backend: BackendProtocol | None = None,
) -> BackendProtocol:
    root = resolve_workspace_root(workspace_root)
    default_backend = (
        StateBackend()
        if root is None
        else AutoDedupFilesystemBackend(root_dir=root, virtual_mode=True)
    )
    routes: dict[str, BackendProtocol] = {}
    if include_builtin_skills:
        skills_root = validate_required_builtin_skills()
        routes[BUILTIN_SKILLS_VIRTUAL_ROOT] = AutoDedupFilesystemBackend(
            root_dir=skills_root,
            virtual_mode=True,
        )
    if user_skills_backend is not None:
        routes[USER_SKILLS_VIRTUAL_ROOT] = user_skills_backend
    if agent_memory_backend is not None:
        routes[AGENT_MEMORY_VIRTUAL_ROOT] = agent_memory_backend
    if not routes:
        return default_backend

    return CompositeBackend(
        default=default_backend,
        routes=routes,
    )


def create_workspace_permissions(
    workspace_root: str | None,
    *,
    mode: AgentWorkspaceMode,
    include_builtin_skills: bool = False,
    include_user_skills: bool = False,
    include_agent_memory: bool = False,
) -> list[FilesystemPermission]:
    root = resolve_workspace_root(workspace_root)
    skill_permissions: list[FilesystemPermission] = []
    if include_builtin_skills:
        skill_permissions.extend(_read_only_virtual_permissions(BUILTIN_SKILLS_VIRTUAL_ROOT))
    if include_user_skills:
        skill_permissions.extend(_read_only_virtual_permissions(USER_SKILLS_VIRTUAL_ROOT))
    if include_agent_memory:
        skill_permissions.extend(_read_only_virtual_permissions(AGENT_MEMORY_VIRTUAL_ROOT))
    if root is None:
        return [
            *skill_permissions,
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            )
        ]

    permissions = [
        FilesystemPermission(
            operations=["read", "write"],
            paths=_sensitive_virtual_paths(),
            mode="deny",
        )
    ]
    host_path_patterns = host_workspace_virtual_deny_patterns(root)
    if host_path_patterns:
        permissions.append(
            FilesystemPermission(
                operations=["read", "write"],
                paths=host_path_patterns,
                mode="deny",
            )
        )
    permissions.extend(skill_permissions)
    if mode == "data_source":
        # DataSource Agent 只实现后端任务；平台技能与记忆已由前面的精确规则放行。
        permissions.extend(
            [
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=_data_source_ignored_virtual_paths(),
                    mode="deny",
                ),
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=[
                        "/backend",
                        "/backend/**",
                        "/backend/**/.*",
                        "/backend/**/.*/**",
                        "/Backend",
                        "/Backend/**",
                        "/Backend/**/.*",
                        "/Backend/**/.*/**",
                    ],
                    mode="allow",
                ),
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=["/**", "/.*", "/.*/**", "/**/.*", "/**/.*/**"],
                    mode="deny",
                ),
            ]
        )
        return permissions

    if mode in {"database", "repair_planner", "workspace_assistant"}:
        permissions.extend(
            [
                FilesystemPermission(operations=["read"], paths=["/**"], mode="allow"),
                FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
            ]
        )
        return permissions

    if mode == "test_generation":
        permissions.extend(
            [
                FilesystemPermission(operations=["read"], paths=["/**"], mode="allow"),
                FilesystemPermission(
                    operations=["write"],
                    paths=[
                        "/frontend/tests",
                        "/frontend/tests/**",
                        "/Frontend/tests",
                        "/Frontend/tests/**",
                        "/backend/src/test/java",
                        "/backend/src/test/java/**",
                        "/Backend/src/test/java",
                        "/Backend/src/test/java/**",
                    ],
                    mode="allow",
                ),
                FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
            ]
        )
        return permissions

    if mode == "code_analyze":
        # 代码审查允许读取前端项目文件和后端业务源码，但永不暴露依赖目录。
        permissions.extend(
            [
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=[
                        "/frontend/node_modules",
                        "/frontend/node_modules/**",
                    ],
                    mode="deny",
                ),
                FilesystemPermission(
                    operations=["read"],
                    paths=[
                        "/frontend",
                        "/frontend/**",
                        "/backend/src/main/java",
                        "/backend/src/main/java/**",
                    ],
                    mode="allow",
                ),
                FilesystemPermission(
                    operations=["write"],
                    paths=["/**"],
                    mode="deny",
                ),
            ]
        )
        return permissions

    if mode == "code_review_repair":
        # 审查修复可处理前端项目文件；依赖目录和 lockfile 仍由专用 pnpm 工具独占。
        permissions.extend(
            [
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=[
                        "/frontend/node_modules",
                        "/frontend/node_modules/**",
                    ],
                    mode="deny",
                ),
                FilesystemPermission(
                    operations=["write"],
                    paths=["/frontend/pnpm-lock.yaml"],
                    mode="deny",
                ),
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=[
                        "/frontend",
                        "/frontend/**",
                        "/backend/src/main/java",
                        "/backend/src/main/java/**",
                    ],
                    mode="allow",
                ),
                FilesystemPermission(
                    operations=["read", "write"],
                    paths=["/**"],
                    mode="deny",
                ),
            ]
        )
        return permissions

    permissions.append(
        FilesystemPermission(operations=["read", "write"], paths=["/**"], mode="allow")
    )
    return permissions


def is_data_source_backend_path(virtual_path: str) -> bool:
    """判断虚拟绝对路径是否位于 DataSource Agent 可操作的后端源码范围。"""

    parts = PurePosixPath(str(virtual_path or "").replace("\\", "/")).parts
    return (
        len(parts) >= 2
        and parts[0] == "/"
        and parts[1] in {"backend", "Backend"}
        and ".." not in parts
        and not any(
            part.casefold() in _DATA_SOURCE_IGNORED_DIRECTORY_NAMES
            for part in parts[2:]
        )
    )


def _data_source_ignored_virtual_paths() -> list[str]:
    """生成 DataSource Agent 项目路径中 IDE 元数据和构建产物的拒绝规则。"""

    paths: list[str] = []
    for root in ("backend", "Backend"):
        for name in sorted(_DATA_SOURCE_IGNORED_DIRECTORY_NAMES):
            paths.extend(
                [
                    f"/{root}/**/{name}",
                    f"/{root}/**/{name}/**",
                ]
            )
    return paths


def _read_only_virtual_permissions(virtual_root: str) -> list[FilesystemPermission]:
    route_root = virtual_root.rstrip("/")
    route_paths = [route_root, f"{route_root}/**"]
    return [
        FilesystemPermission(
            operations=["write"],
            paths=route_paths,
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=route_paths,
            mode="allow",
        ),
    ]


def _sensitive_virtual_paths() -> list[str]:
    paths: list[str] = []
    for name in sorted(SENSITIVE_FILE_NAMES):
        paths.extend([f"/{name}", f"/**/{name}"])
    return paths
