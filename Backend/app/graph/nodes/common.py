from typing import Any

from app.agents import create_agent_bundle


def workspace_from_state(state: dict[str, Any]) -> str | None:
    return state.get("workspace") or state.get("workspace_path")


def last_agent_text(result: dict[str, Any]) -> str:
    messages = result.get("messages", [])
    if not messages:
        return "Agent completed without a text message."
    content = getattr(messages[-1], "content", "")
    return content if isinstance(content, str) else str(content)


def run_live(agent_name: str, prompt: str, workspace: str | None = None) -> str:
    agent = getattr(create_agent_bundle(workspace), agent_name)
    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    return last_agent_text(result)
