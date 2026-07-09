from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.agents.data_source import create_data_source_agent
from app.agents.frontend import create_frontend_agent
from app.agents.main import create_main_agent
from app.agents.model_factory import create_chat_model
from app.agents.test import create_test_agent
from app.agents.workspace_scope import resolve_workspace_root
from app.config import Settings


@dataclass(frozen=True)
class AgentBundle:
    main: Any
    frontend: Any
    data_source: Any
    test: Any


def create_agent_bundle(workspace_root: str | None = None) -> AgentBundle:
    root = resolve_workspace_root(workspace_root)
    workspace_key = str(root) if root else ""
    return _create_agent_bundle_for_workspace(workspace_key)


@lru_cache(maxsize=16)
def _create_agent_bundle_for_workspace(workspace_key: str) -> AgentBundle:
    workspace_root = workspace_key or None
    settings = Settings.from_env()
    chat_model = create_chat_model(settings)
    frontend = create_frontend_agent(chat_model, workspace_root=workspace_root)
    data_source = create_data_source_agent(chat_model, workspace_root=workspace_root)
    test = create_test_agent(chat_model, workspace_root=workspace_root)
    main = create_main_agent(
        chat_model,
        frontend,
        data_source,
        test,
        workspace_root=workspace_root,
    )
    return AgentBundle(
        main=main,
        frontend=frontend,
        data_source=data_source,
        test=test,
    )


def clear_agent_bundle_cache() -> None:
    _create_agent_bundle_for_workspace.cache_clear()
