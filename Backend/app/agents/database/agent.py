from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.tools.mysql_info import get_mysql_table_info


def create_database_agent(
        model,
        workspace_root: str | None = None,
        *,
        user_skills_backend: BackendProtocol,
        agent_memory_backend: BackendProtocol,
        required_user_skills_prompt: str = "",
):
    """创建具备数据库 DDL 执行能力的 Deep Agent。"""

    base_system_prompt = (
        "You are the Database Change Agent. Your job is to inspect the assigned "
        "database tasks and the latest real database summary, then produce an exact "
        "database change plan.\n"
        "\n"
        "Before planning, call the get_mysql_table_info tool to verify the live "
        "database: invoke it without a table_name to list the current tables and "
        "columns, or with a table_name to fetch the detailed schema of a single "
        "table. The supplied database summary may be truncated or stale; the tool "
        "result is the source of truth for what already exists.\n"
        "\n"
        "Your DDL is executed only inside the configured target database by the "
        "harness. Do not emit USE <database>, CREATE DATABASE, DROP DATABASE, or "
        "any fully-qualified name that references or switches to another database; "
        "every statement must target the configured database.\n"
        "\n"
        "You must not edit workspace files. You must not invent tables or columns "
        "not supported by the supplied schema and API contract. Return only one "
        "JSON object with `database_change_plan`: "
        "`task_results`, `statements`, `summary`, `rollback`, and `assumptions`. "
        "`statements` must be ordered SQL statements without trailing semicolons. "
        "If no SQL is needed, return an empty `statements` list and mark the task "
        "as `already_satisfied`. "
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
        tools=[get_mysql_table_info],
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
