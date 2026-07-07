from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.agents.data_source import create_data_source_agent
from app.agents.frontend import create_frontend_agent
from app.agents.main import create_main_agent
from app.agents.model_factory import create_chat_model
from app.agents.test import create_test_agent
from app.config import Settings


@dataclass(frozen=True)
class AgentBundle:
    main: Any
    frontend: Any
    data_source: Any
    test: Any


@lru_cache(maxsize=1)
def create_agent_bundle() -> AgentBundle:
    settings = Settings.from_env()
    chat_model = create_chat_model(settings)
    frontend = create_frontend_agent(chat_model)
    data_source = create_data_source_agent(chat_model)
    test = create_test_agent(chat_model)
    main = create_main_agent(chat_model, frontend, data_source, test)
    return AgentBundle(
        main=main,
        frontend=frontend,
        data_source=data_source,
        test=test,
    )
