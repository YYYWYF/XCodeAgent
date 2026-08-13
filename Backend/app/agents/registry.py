import logging
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.agents.database import create_database_agent
from app.agents.data_source import create_data_source_agent
from app.agents.frontend import create_frontend_agent
from app.agents.model_factory import create_chat_model
from app.agents.repair_planner import create_repair_planner_agent
from app.agents.small_task import create_small_task_agent
from app.agents.test import create_test_agent
from app.agents.workspace_assistant import create_workspace_assistant_agent
from app.agents.code_analyze import create_code_analyze_agent
from app.agents.workspace_scope import resolve_workspace_root
from app.config import Settings
from app.services.agent_memory_runtime import (
    AgentMemorySnapshotChangedError,
    create_agent_memory_runtime_snapshot,
    get_agent_memory_runtime_revision,
)
from app.services.user_skill_runtime import (
    UserSkillSnapshotChangedError,
    build_required_user_skills_prompt,
    create_user_skill_runtime_snapshot,
    get_user_skill_runtime_revision,
)


logger = logging.getLogger(__name__)
_MAX_SNAPSHOT_ATTEMPTS = 3


@dataclass(frozen=True)
class AgentBundle:
    frontend: Any
    data_source: Any
    database: Any
    test: Any
    repair_planner: Any
    small_task: Any
    workspace_assistant: Any
    selected_skill_names: tuple[str, ...] = ()
    user_skills_revision: str = ""


@dataclass(frozen=True)
class _CodeAnalyzeRuntime:
    """缓存代码审查 Agent 可安全复用的模型和只读记忆快照。"""

    model: Any
    agent_memory_backend: Any


def create_agent_bundle(
    workspace_root: str | None = None,
    selected_skill_names: Sequence[str] | None = None,
) -> AgentBundle:
    """按工作区和用户技能白名单创建或复用一组 Deep Agent。"""

    root = resolve_workspace_root(workspace_root)
    workspace_key = str(root) if root else ""
    selected_skill_key = _normalize_selected_skill_key(selected_skill_names)
    for _attempt in range(_MAX_SNAPSHOT_ATTEMPTS):
        user_skills_revision = get_user_skill_runtime_revision()
        agent_memory_revision = get_agent_memory_runtime_revision()
        try:
            bundle = _create_agent_bundle_for_workspace(
                workspace_key,
                selected_skill_key,
                user_skills_revision,
                agent_memory_revision,
            )
        except (UserSkillSnapshotChangedError, AgentMemorySnapshotChangedError):
            continue
        if (
            get_user_skill_runtime_revision() == user_skills_revision
            and get_agent_memory_runtime_revision() == agent_memory_revision
        ):
            return bundle
    raise RuntimeError("用户技能或 AGENTS.md 持续变化，无法创建稳定的 Agent 运行时快照。")


@lru_cache(maxsize=16)
def _create_agent_bundle_for_workspace(
    workspace_key: str,
    selected_skill_names: tuple[str, ...],
    user_skills_revision: str,
    agent_memory_revision: str,
) -> AgentBundle:
    """使用不可变技能和记忆快照构建缓存中的 Agent bundle。"""

    workspace_root = workspace_key or None
    user_skills = create_user_skill_runtime_snapshot(
        user_skills_revision,
        selected_skill_names=selected_skill_names or None,
    )
    agent_memory = create_agent_memory_runtime_snapshot(agent_memory_revision)
    required_user_skills_prompt = build_required_user_skills_prompt(
        getattr(user_skills, "prompt_documents", ())
    )
    if user_skills.issues:
        logger.warning(
            "User skill runtime snapshot skipped %d entry or entries: %s",
            len(user_skills.issues),
            [
                {
                    "relative_path": issue.relative_path,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in user_skills.issues
            ],
        )
    settings = Settings.from_env()
    chat_model = create_chat_model(settings)
    frontend = create_frontend_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
        agent_memory_backend=agent_memory.backend,
        required_user_skills_prompt=required_user_skills_prompt,
    )
    data_source = create_data_source_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
        agent_memory_backend=agent_memory.backend,
        required_user_skills_prompt=required_user_skills_prompt,
    )
    database = create_database_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
        agent_memory_backend=agent_memory.backend,
        required_user_skills_prompt=required_user_skills_prompt,
    )
    test = create_test_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
        agent_memory_backend=agent_memory.backend,
        required_user_skills_prompt=required_user_skills_prompt,
    )
    repair_planner = create_repair_planner_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
        agent_memory_backend=agent_memory.backend,
        required_user_skills_prompt=required_user_skills_prompt,
    )
    small_task = create_small_task_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
        agent_memory_backend=agent_memory.backend,
        required_user_skills_prompt=required_user_skills_prompt,
    )
    workspace_assistant = create_workspace_assistant_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
        agent_memory_backend=agent_memory.backend,
        required_user_skills_prompt=required_user_skills_prompt,
    )
    return AgentBundle(
        frontend=frontend,
        data_source=data_source,
        database=database,
        test=test,
        repair_planner=repair_planner,
        small_task=small_task,
        workspace_assistant=workspace_assistant,
        selected_skill_names=selected_skill_names,
        user_skills_revision=user_skills_revision,
    )


def clear_agent_bundle_cache() -> None:
    """清理所有按工作区和技能集合缓存的 Agent bundle。"""

    _create_agent_bundle_for_workspace.cache_clear()
    _create_code_analyze_runtime.cache_clear()


def create_code_analyze_agent_for_workspace(
    *,
    workspace_root: str,
    source_roots: tuple[str, ...],
    tools: list[object],
) -> Any:
    """使用稳定 AGENTS.md 快照创建一次请求作用域的代码审查 Agent。"""

    for _attempt in range(_MAX_SNAPSHOT_ATTEMPTS):
        agent_memory_revision = get_agent_memory_runtime_revision()
        try:
            runtime = _create_code_analyze_runtime(
                workspace_root,
                agent_memory_revision,
            )
        except AgentMemorySnapshotChangedError:
            continue
        if get_agent_memory_runtime_revision() != agent_memory_revision:
            continue
        return create_code_analyze_agent(
            runtime.model,
            workspace_root,
            source_roots=source_roots,
            agent_memory_backend=runtime.agent_memory_backend,
            tools=tools,
        )
    raise RuntimeError("AGENTS.md 持续变化，无法创建稳定的代码审查 Agent 快照。")


@lru_cache(maxsize=16)
def _create_code_analyze_runtime(
    workspace_key: str,
    agent_memory_revision: str,
) -> _CodeAnalyzeRuntime:
    """按工作区和记忆版本缓存 codeAnalyzeAgent 的无状态运行基础。"""

    del workspace_key
    settings = Settings.from_env()
    agent_memory = create_agent_memory_runtime_snapshot(agent_memory_revision)
    return _CodeAnalyzeRuntime(
        model=create_chat_model(settings),
        agent_memory_backend=agent_memory.backend,
    )


def _normalize_selected_skill_key(
    selected_skill_names: Sequence[str] | None,
) -> tuple[str, ...]:
    """把调用方提供的技能名称规范化为稳定缓存键。"""

    if not selected_skill_names:
        return ()
    normalized = {str(name).strip() for name in selected_skill_names if str(name).strip()}
    return tuple(sorted(normalized, key=lambda name: (name.casefold(), name)))
