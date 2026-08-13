from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def create_repair_planner_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只读修复规划 Deep Agent，并注入用户必选技能。"""

    base_system_prompt = (
        "You are the RepairPlanner Agent for the app-generation workflow. "
        "You are a planning-only DeepAgent node. Analyze failed task attempts, "
        "test reports, failure logs, workspace snapshots, allowed change scope, "
        "and acceptance criteria. Return structured repair plans for scheduler "
        "consumption. Do not edit files, do not run commands, do not mutate "
        "ProjectPlan, RequirementSpec, BuildTaskPlan, test reports, DAG state, "
        "or scheduler state. If a repair requires expanding change scope, changing "
        "confirmed requirements, expanding API contracts, or making a user-visible "
        "product decision, return requires_user_confirmation. "
        "If the failure is not actionable with the provided evidence, return "
        "terminal_failure. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    return create_deep_agent(
        name="repair-planner-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        tools=[],
        backend=create_workspace_backend(
            workspace_root,
            user_skills_backend=user_skills_backend,
            agent_memory_backend=agent_memory_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="repair_planner",
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
