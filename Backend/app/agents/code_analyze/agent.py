"""创建工作区受限的前端代码审查 DeepAgent。"""

from __future__ import annotations

from pathlib import PurePosixPath

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware.permissions import FilesystemPermission

from app.agents.workspace_scope import create_workspace_backend, resolve_workspace_root
from app.middleware.code_analyze import CodeAnalyzeToolPolicyMiddleware
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS
from app.workspace.workspace import SENSITIVE_FILE_NAMES


def create_code_analyze_agent(
    model,
    workspace_root: str,
    *,
    source_roots: tuple[str, ...],
    agent_memory_backend: BackendProtocol,
    tools: list[object],
):
    """创建只能读取指定前端源码并通过受控工具保存报告的 DeepAgent。"""

    root = resolve_workspace_root(workspace_root)
    if root is None:
        raise ValueError("代码审查 Agent 必须绑定显式 workspaceRoot。")
    system_prompt = (
        "You are XCodeAgent's dedicated frontend codeAnalyzeAgent. Your only task is a "
        "read-only security audit of the explicitly supplied frontend source roots. Your FIRST "
        "tool call must be load_mayun_frontend_code_review_skill. Follow the returned SKILL.md, "
        "security_checks.md, and report_template.md exactly. Never inspect Backend/backend, "
        "dependencies, build output, caches, secrets, or any path outside the supplied frontend "
        "roots. Use glob and grep to cover the candidate source set, then read only the evidence "
        "needed to validate findings. Do not modify source code. Finish by calling "
        "save_code_audit_report exactly once with the complete Chinese Markdown report. Do not "
        "paste the full report in your final response; return a concise Chinese completion summary. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    return create_deep_agent(
        name="frontend-code-analyze-agent",
        model=model,
        system_prompt=system_prompt,
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=tools,
        middleware=[CodeAnalyzeToolPolicyMiddleware()],
        backend=create_workspace_backend(
            workspace_root,
            include_builtin_skills=True,
            agent_memory_backend=agent_memory_backend,
        ),
        permissions=_code_analyze_permissions(source_roots),
    )


def _code_analyze_permissions(source_roots: tuple[str, ...]) -> list[FilesystemPermission]:
    """按前端根目录生成先允许后拒绝的只读文件权限。"""

    sensitive_paths: list[str] = []
    for name in sorted(SENSITIVE_FILE_NAMES):
        sensitive_paths.extend([f"/{name}", f"/**/{name}"])
    permissions = [
        FilesystemPermission(
            operations=["read", "write"],
            paths=sensitive_paths,
            mode="deny",
        ),
        FilesystemPermission(
            operations=["write"],
            paths=[
                BUILTIN_SKILLS_VIRTUAL_ROOT.rstrip("/"),
                f"{BUILTIN_SKILLS_VIRTUAL_ROOT.rstrip('/')}/*",
                f"{BUILTIN_SKILLS_VIRTUAL_ROOT.rstrip('/')}/*/**",
            ],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=[
                f"{BUILTIN_SKILLS_VIRTUAL_ROOT}mayun-frontend-code-review",
                f"{BUILTIN_SKILLS_VIRTUAL_ROOT}mayun-frontend-code-review/**",
            ],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["write"],
            paths=[AGENT_MEMORY_VIRTUAL_PATH],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=[AGENT_MEMORY_VIRTUAL_PATH],
            mode="allow",
        ),
    ]
    for relative_root in source_roots:
        normalized = PurePosixPath(relative_root).as_posix().strip("/")
        permissions.append(
            FilesystemPermission(
                operations=["read"],
                paths=[f"/{normalized}", f"/{normalized}/**"],
                mode="allow",
            )
        )
    permissions.extend(
        [
            FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"),
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        ]
    )
    return permissions
