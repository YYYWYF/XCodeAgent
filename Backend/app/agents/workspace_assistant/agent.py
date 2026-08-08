"""创建只读工作区问答 Agent。"""

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol

from app.agents.workspace_scope import (
    create_workspace_backend,
    create_workspace_permissions,
)
from app.middleware.direct_modification import (
    DIRECT_MODIFICATION_MODE_MARKER,
    DirectModificationMiddleware,
)
from app.services.agent_memory_runtime import AGENT_MEMORY_VIRTUAL_PATH
from app.services.user_skill_runtime import USER_SKILLS_VIRTUAL_ROOT
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def create_workspace_assistant_agent(
    model,
    workspace_root: str | None = None,
    *,
    user_skills_backend: BackendProtocol,
    agent_memory_backend: BackendProtocol,
    required_user_skills_prompt: str = "",
):
    """创建只允许读取当前工程并以自然语言回答问题的 Deep Agent。"""

    base_system_prompt = (
        "You are XCodeAgent's read-only Workspace Assistant. Answer questions about the current "
        "workspace by progressively reading only the files needed for the question. Never edit, "
        "write, delete, or execute files and commands. Do not use task, write_todos, or subagents. "
        "Separate facts observed in files from inferences, and say when the available evidence is "
        "insufficient. Reply in the user's language with concise natural language, not JSON. "
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}"
    )
    return create_deep_agent(
        name="workspace-assistant-agent",
        model=model,
        system_prompt="\n\n".join(
            part for part in (base_system_prompt, required_user_skills_prompt) if part
        ),
        skills=[USER_SKILLS_VIRTUAL_ROOT],
        memory=[AGENT_MEMORY_VIRTUAL_PATH],
        middleware=[DirectModificationMiddleware()],
        backend=create_workspace_backend(
            workspace_root,
            user_skills_backend=user_skills_backend,
            agent_memory_backend=agent_memory_backend,
        ),
        permissions=create_workspace_permissions(
            workspace_root,
            mode="workspace_assistant",
            include_user_skills=True,
            include_agent_memory=True,
        ),
    )


def workspace_assistant_prompt(*, request: str, conversation_summary: str) -> str:
    """构造带稳定受限标记和有界对话摘要的工作区问答输入。"""

    return (
        f"{DIRECT_MODIFICATION_MODE_MARKER}\n"
        "Answer the current question using read-only workspace evidence. Do not make any changes.\n\n"
        f"Bounded conversation summary:\n{conversation_summary[-4_000:] or '(empty)'}\n\n"
        f"Current user question:\n{request.strip()}"
    )
