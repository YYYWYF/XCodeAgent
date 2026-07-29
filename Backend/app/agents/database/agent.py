from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT


def create_database_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只读工作区的数据库规划 Deep Agent，由外层服务执行审批和 SQL。"""

    base_system_prompt = (
        "You are the Database Change Agent. Your job is to inspect the assigned "
        "database tasks and the latest real database summary, then produce an exact "
        "database change plan. You must not edit workspace files. You must not invent "
        "tables or columns not supported by the supplied schema and API contract. "
        "Return only one JSON object with `database_change_plan`: "
        "`task_results`, `statements`, `summary`, `rollback`, and `assumptions`. "
        "`statements` must be ordered SQL statements. If no SQL is needed, return "
        "an empty `statements` list and mark the task as `already_satisfied`. "
        "Never execute SQL yourself; execution and approval are handled by the harness."
    )
    return create_deep_agent(
        name="database-change-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        backend=create_workspace_backend(
            workspace_root,
            user_skills_backend=user_skills_backend,
            agent_memory_backend=agent_memory_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="database",
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )
