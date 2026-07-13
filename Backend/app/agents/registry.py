import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.agents.data_source import create_data_source_agent
from app.agents.frontend import create_frontend_agent
from app.agents.model_factory import create_chat_model
from app.agents.repair_planner import create_repair_planner_agent
from app.agents.test import create_test_agent
from app.agents.workspace_scope import resolve_workspace_root
from app.config import Settings
from app.services.user_skill_runtime import (
    UserSkillSnapshotChangedError,
    create_user_skill_runtime_snapshot,
    get_user_skill_runtime_revision,
)


logger = logging.getLogger(__name__)
_MAX_SNAPSHOT_ATTEMPTS = 3


@dataclass(frozen=True)
class AgentBundle:
    frontend: Any
    data_source: Any
    test: Any
    repair_planner: Any


def create_agent_bundle(workspace_root: str | None = None) -> AgentBundle:
    root = resolve_workspace_root(workspace_root)
    workspace_key = str(root) if root else ""
    for _attempt in range(_MAX_SNAPSHOT_ATTEMPTS):
        revision = get_user_skill_runtime_revision()
        try:
            return _create_agent_bundle_for_workspace(workspace_key, revision)
        except UserSkillSnapshotChangedError:
            continue
    raise RuntimeError("用户技能持续变化，无法创建稳定的 Agent 运行时快照。")


@lru_cache(maxsize=16)
def _create_agent_bundle_for_workspace(
    workspace_key: str,
    user_skills_revision: str,
) -> AgentBundle:
    workspace_root = workspace_key or None
    user_skills = create_user_skill_runtime_snapshot(user_skills_revision)
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
    )
    data_source = create_data_source_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
    )
    test = create_test_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
    )
    repair_planner = create_repair_planner_agent(
        chat_model,
        workspace_root=workspace_root,
        user_skills_backend=user_skills.backend,
    )
    return AgentBundle(
        frontend=frontend,
        data_source=data_source,
        test=test,
        repair_planner=repair_planner,
    )


def clear_agent_bundle_cache() -> None:
    _create_agent_bundle_for_workspace.cache_clear()
