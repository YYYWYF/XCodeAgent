"""创建只读前后端代码审查 DeepAgent。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from app.agents.code_analyze.scope import CodeAnalyzeScopedBackend
from app.agents.workspace_scope import create_workspace_backend, create_workspace_permissions
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


class CodeAnalyzeMiddleware(AgentMiddleware):
    """从默认工具集硬性移除代码审查不需要的写入和委派能力。"""

    # DeepAgent 默认还会注册执行、委派、待办及写入工具；审查 Agent 只能看到这四个只读工具。
    _ALLOWED_TOOLS = frozenset({"ls", "read_file", "glob", "grep"})

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步模型调用前过滤越权工具。"""

        return handler(request.override(tools=_without_disabled_tools(request.tools)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步模型调用前过滤越权工具。"""

        return await handler(request.override(tools=_without_disabled_tools(request.tools)))


def create_code_analyze_agent(
    model, workspace_root: str | None = None, *, allowed_files: frozenset[str] | None = None
):
    """创建只读审查 Agent；Diff 模式由精确文件清单控制后端配置读取。"""

    diff_mode = allowed_files is not None
    scope_policy = (
        "Scan ONLY the exact changed project files listed in the user request, including "
        "backend configuration and resource files when listed. Never read, search, list deeply, "
        "write, edit, delete, upload, execute, or delegate work outside those files and the "
        "three required Skill files. Missing scan roots are skipped and reported. "
        if diff_mode else
        "Scan ONLY /frontend/** (excluding every node_modules subtree and sensitive file) "
        "and /backend/src/main/java/** in the user workspace. Never read, search, list deeply, "
        "write, edit, delete, upload, execute, or delegate work outside those paths. "
        "Missing scan roots are skipped and reported. "
    )
    frontend_policy = (
        "If the frontend Skill contains no concrete scan rules, still read every selected "
        "frontend file, report its warning, and do not invent frontend issues. "
        if diff_mode else
        "If the frontend Skill contains no concrete scan rules, do NOT read frontend project "
        "files; report its warning, a completed target with scanned_file_count 0, and NEVER "
        "create frontend issues. "
    )
    issue_root = "backend/" if diff_mode else "backend/src/main/java/"
    target_rule = (
        "use root exactly (frontend and backend), never scan_root. "
        if diff_mode else "use root exactly, never scan_root. "
    )
    system_prompt = "".join([
        "You are the CodeAnalyze Agent. You perform a read-only security and quality review. "
        "Before inspecting any source, you MUST read all required Skill documents from the "
        "virtual builtin-skills path: frontend-code-scan/SKILL.md, backend-code-scan/SKILL.md, "
        "and backend-code-scan/references/rules-reference.md. Apply both scan Skills in the "
        "same invocation. ",
        scope_policy,
        frontend_policy,
        "Do not fix findings. Return exactly one JSON object without Markdown fences using keys "
        "status, summary, loaded_skills, targets, issues, and truncated. "
        "Set status to completed whenever the scan finishes, including when one or more issues "
        "are found; findings belong only in issues and never make status failed. "
        f"Each issue must use a relative workspace path prefixed with frontend/ or {issue_root}, "
        "side frontend/backend, optional rule_id, severity critical/high/medium/low, title, "
        "summary, optional line, and repair_actions. "
        "Set repair_actions to [\"pnpm_install\"] only when the loaded Skill remediation explicitly "
        "requires pnpm i or pnpm install; otherwise use an empty array. Never invent other actions. "
        "Each targets item must use side, root, status, scanned_file_count, and optional warning; ",
        target_rule,
        "Do not include source excerpts, absolute host paths, secrets, or model reasoning. "
        "Limit issues to 100 and set truncated when more exist."
        f" {VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}",
    ])
    backend = CodeAnalyzeScopedBackend(
        create_workspace_backend(workspace_root, include_builtin_skills=True),
        allowed_files=allowed_files,
    )
    return create_deep_agent(
        name="code-analyze-agent",
        model=model,
        system_prompt=system_prompt,
        middleware=[CodeAnalyzeMiddleware()],
        # 只注册两个扫描 Skill 目录，避免把其它生成类内置 Skill 暴露给审查 Agent。
        skills=[
            f"{BUILTIN_SKILLS_VIRTUAL_ROOT}frontend-code-scan/",
            f"{BUILTIN_SKILLS_VIRTUAL_ROOT}backend-code-scan/",
        ],
        backend=backend,
        permissions=create_workspace_permissions(
            workspace_root,
            mode="code_analyze_diff" if allowed_files is not None else "code_analyze",
            include_builtin_skills=True,
        ),
    )


def _without_disabled_tools(tools: list[Any]) -> list[Any]:
    """按稳定工具名只保留目录列表、文件读取和代码搜索工具。"""

    return [
        tool
        for tool in tools
        if str(
            (tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", ""))
            or ""
        ) in CodeAnalyzeMiddleware._ALLOWED_TOOLS
    ]
