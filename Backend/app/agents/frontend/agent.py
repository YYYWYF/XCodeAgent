from app.agents.execution_control import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.code_graph_guidance import CODE_GRAPH_TASK_EXECUTION_GUIDANCE
from app.agents.frontend.execution_scope import FrontendTaskExecutionScope
from app.agents.frontend.execution_middleware import FrontendExecutionMiddleware
from app.agents.frontend.file_backend import FrontendScopedFilesystemBackend
from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.builtin_skills import (
    BUILTIN_SKILLS_VIRTUAL_ROOT,
    FRONTEND_ENGINEERING_SKILL_NAME,
)
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.middleware.direct_modification import DirectModificationMiddleware
from app.tools.delete_file import create_delete_file_tool
from app.tools.code_graph_context import create_code_graph_context_tool
from app.tools.execute import create_execute_tool
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS

FRONTEND_BUILD_BUILTIN_SKILLS = (FRONTEND_ENGINEERING_SKILL_NAME,)


def create_frontend_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
    build_scope: FrontendTaskExecutionScope | None = None,
):
    """创建具备前端工作区权限和必选技能指令的 Deep Agent。"""

    backend = create_workspace_backend(
        workspace_root,
        include_builtin_skills=True,
        builtin_skill_names=FRONTEND_BUILD_BUILTIN_SKILLS if build_scope else None,
        user_skills_backend=user_skills_backend,
        agent_memory_backend=agent_memory_backend,
        workspace_backend=(
            FrontendScopedFilesystemBackend(build_scope) if build_scope else None
        ),
    )
    base_system_prompt = (
        "You are the Frontend Coding Agent. Follow the execution contract in the current "
        "user message. Generate or modify frontend code for "
        "layouts, components, interactions, permissions, API integration, loading, "
        "empty, and error states. Do not silently change API contracts. Return a concise "
        "structured implementation report with changed files, status, verification notes, "
        "and any change request. Follow the current execution prompt for whether project-level "
        "verification is allowed; do not invent validation commands. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS} When deleting a file, "
        'use delete_file(file_path="/path") with a virtual absolute path.\n'
        "IMPORTANT: If write_file returns an error that a file already exists, "
        "use edit_file to modify the existing file instead of retrying write_file "
        "with the same path. If you must create a new file, use a unique filename "
        "(e.g. append a number or timestamp). Never retry write_file with the same "
        "path more than once.\n\n"
        f"{CODE_GRAPH_TASK_EXECUTION_GUIDANCE}"
    )
    design_paths = []
    middleware = []
    if build_scope:
        base_system_prompt = (
            "你是前端 Build Agent。按任务包完成四个步骤，只实施已批准的业务代码。"
            "平台控制当前步骤及允许操作，使用虚拟绝对文件路径。"
            "implementation_contract 是唯一实施事实入口。输出既有 task_results JSON。"
            "页面四步任务：在任何 Skill 或业务文件读取前，第一次工具调用必须单独调用 "
            "begin_frontend_task；等待其成功返回 Step 1 后，才可以调用 read_file 等工具。"
            "不能将 begin_frontend_task 与其他工具并行调用。"
        )
        if build_scope.task_level:
            base_system_prompt = (
                "你是前端 API Build Agent。平台已初始化任务级权限。"
                "按 implementation_contract、适用工程规范和精确虚拟路径实现并自检，"
                "输出既有 task_results JSON，由平台验收。"
            )
        design_paths = [
            path
            for packet in build_scope.packets
            if (path := packet["implementation_contract"].get("confirmed_design"))
        ]
        middleware.append(FrontendExecutionMiddleware(build_scope))

    def block_shell() -> str:
        """在启动任何命令前拒绝前端 Build 的 Shell 执行。"""
        reason = "前端 Build 不允许执行 Shell 命令"
        build_scope.record(
            "frontend_tool_permission",
            tool_name="execute",
            operation="shell",
            permission_decision="denied",
            permission_denial_reason=reason,
        )
        return reason

    def can_delete(path: str) -> bool:
        """删除工具在物理删除前再次校验当前任务或步骤及文件。"""
        build_scope.authorize("delete_file", path)
        return True

    execute_tool = create_execute_tool(
        workspace_root, blocked_by=block_shell if build_scope else None
    )
    runtime_tools = [
        (
            build_scope.code_graph_tool()
            if build_scope
            else create_code_graph_context_tool(workspace_root)
        ),
        create_delete_file_tool(
            workspace_root, path_guard=can_delete if build_scope else None
        ),
        execute_tool,
        *(build_scope.control_tools() if build_scope else []),
    ]
    return create_deep_agent(
        name="frontend-generation-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=runtime_tools,
        middleware=[
            *middleware,
            DirectModificationMiddleware(required_tools=[execute_tool]),
        ],
        backend=backend,
        permissions=create_workspace_permissions(
            workspace_root,
            mode="frontend",
            include_builtin_skills=True,
            include_user_skills=True,
            include_agent_memory=True,
            frontend_design_paths=design_paths,
        ),
    )
