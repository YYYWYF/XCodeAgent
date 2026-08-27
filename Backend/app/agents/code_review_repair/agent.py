"""创建受限的代码审查修复 DeepAgent。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from app.agents.code_analyze.scope import CodeReviewRepairScopedBackend
from app.agents.workspace_scope import create_workspace_backend, create_workspace_permissions
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.tools.code_review_pnpm import (
    PNPM_INSTALL_TOOL_NAME,
    create_code_review_pnpm_install_tool,
)
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


class CodeReviewRepairMiddleware(AgentMiddleware):
    """从默认 DeepAgent 工具中移除通用命令、委派和任务编排能力。"""

    _ALLOWED_TOOLS = frozenset(
        {
            "ls",
            "read_file",
            "glob",
            "grep",
            "write_file",
            "edit_file",
            PNPM_INSTALL_TOOL_NAME,
        }
    )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步模型调用前过滤工具。"""

        return handler(request.override(tools=_allowed_tools(request.tools)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型调用前过滤工具。"""

        return await handler(request.override(tools=_allowed_tools(request.tools)))


def create_code_review_repair_agent(model, workspace_root: str | None = None):
    """创建按 Skill 修复前端项目和后端业务源码的受限 Agent。"""

    system_prompt = (
        "You are the CodeReviewRepairAgent. The user has explicitly chosen one-click repair "
        "for the bounded findings supplied in the current packet. Read both scan Skill entry "
        "documents and the backend rules reference before editing. Apply every supplied finding, "
        "including high-risk rules, but preserve existing APIs and project conventions; never "
        "invent error codes, dependencies, or business semantics. You may read and edit safe files "
        "under /frontend/** and non-test business source under /backend/src/main/java/**. Never read "
        "or modify node_modules, sensitive files, backend configuration/tests, workflow artifacts, "
        "or any other path. Never edit /frontend/pnpm-lock.yaml with file tools. When an issue has "
        "repair_actions=[\"pnpm_install\"], first apply the Skill remediation to package.json and "
        "then call pnpm_install_frontend exactly once; that tool alone regenerates the lockfile. "
        "Do not call it for issues without that repair action. Do not use task/todo tools, delegate "
        "work, or run builds; the workflow performs deterministic build checks after you finish. "
        "Keep method signatures "
        "and behavior stable unless the finding requires the smallest safe change. If a high-risk "
        "finding cannot be fixed safely from existing source evidence, return failed instead of "
        "guessing. Return exactly one JSON object with status, summary, attempted_issue_ids, "
        "changed_files, and failure_reason. status must be completed only after all supplied "
        "issues were attempted and files were actually changed. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    backend = CodeReviewRepairScopedBackend(
        create_workspace_backend(workspace_root, include_builtin_skills=True)
    )
    pnpm_install_tool = create_code_review_pnpm_install_tool(workspace_root)
    return create_deep_agent(
        name="code-review-repair-agent",
        model=model,
        system_prompt=system_prompt,
        middleware=[CodeReviewRepairMiddleware()],
        tools=[pnpm_install_tool],
        skills=[
            f"{BUILTIN_SKILLS_VIRTUAL_ROOT}frontend-code-scan/",
            f"{BUILTIN_SKILLS_VIRTUAL_ROOT}backend-code-scan/",
        ],
        backend=backend,
        permissions=create_workspace_permissions(
            workspace_root,
            mode="code_review_repair",
            include_builtin_skills=True,
        ),
    )


def _allowed_tools(tools: list[Any]) -> list[Any]:
    """按稳定工具名保留源码修复所需的读写工具。"""

    return [
        tool
        for tool in tools
        if str(
            (tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", ""))
            or ""
        ) in CodeReviewRepairMiddleware._ALLOWED_TOOLS
    ]
