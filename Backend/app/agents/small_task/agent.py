"""创建工作台和自由对话共同使用的局部代码修改 Agent。"""

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.small_task.scope import ScopedSmallTaskBackend, is_small_task_path_allowed
from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.middleware.direct_modification import DirectModificationMiddleware
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.builtin_skills import BUILTIN_SKILLS_VIRTUAL_ROOT
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.delete_file import create_delete_file_tool
from app.tools.execute import create_execute_tool
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def create_small_task_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只处理局部工作区改动、禁止任务编排和正式工件变更的 Deep Agent。"""

    base_system_prompt = (
        "You are the SmallTask Coding Agent shared by the workbench repair flow and free chat. "
        "The current user message contains one bounded task packet. Treat that packet as the "
        "complete execution contract: inspect only the relevant workspace context, make the "
        "smallest authorized change, and verify the supplied acceptance criteria. You may edit files "
        "only inside packet.allowedPaths. Start inspection from packet.candidateFiles and application "
        "source directories. Never inspect installed dependencies, caches, or generated build output "
        "such as node_modules, dist, build, target, .next, or .turbo; package manifests and lockfiles "
        "are sufficient dependency evidence. Do not create or modify RequirementSpec, ProjectPlan, "
        "BuildTaskPlan, API contract documents, workflow artifacts, environment files, or database "
        "schema/DDL. Ordinary documentation, tests, scripts, and repository configuration may be "
        "edited only when the packet explicitly identifies them as allowed paths. Do not add a page, "
        "endpoint, data source, migration, or product behavior that "
        "is not already described by the packet. If the task needs any of those things, stop before "
        "editing and return requires_workflow with a reasonCode and workflowIntent. If an existing "
        "implementation is correct, verify it and return already_satisfied. If the implementation "
        "needs an additional code path with the same semantics, return requires_user_confirmation "
        "with the exact requestedPaths before writing outside the current scope. Never broaden a "
        "path glob on your own. Do not use task, write_todos, or subagents. Return exactly one JSON "
        "object and no Markdown. The JSON status must be completed, already_satisfied, "
        "requires_user_confirmation, requires_workflow, or failed. Include summary, changedFiles, "
        "verification, failureReason, and escalation when applicable. A single transient tool error "
        "does not decide the task result: when the authorized change is actually written and the "
        "requested outcome is verified, return completed and keep the tool error only as a warning. "
        "Return failed only for a missing or incorrect outcome, an unauthorized change, or a genuine "
        "verification blocker. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    return create_deep_agent(
        name="small-task-coding-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[BUILTIN_SKILLS_VIRTUAL_ROOT, USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=[
            create_delete_file_tool(workspace_root, path_guard=is_small_task_path_allowed),
            create_execute_tool(workspace_root),
        ],
        middleware=[DirectModificationMiddleware()],
        backend=ScopedSmallTaskBackend(
            create_workspace_backend(
                workspace_root,
                include_builtin_skills=True,
                user_skills_backend=user_skills_backend,
                agent_memory_backend=agent_memory_backend,
            )
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="small_task",
            include_builtin_skills=True,
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
