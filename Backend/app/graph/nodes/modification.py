from typing import Literal, cast

from app.agents.messages import last_agent_text
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.workspace.code_changes import code_change_state_update

DirectModificationOwner = Literal["frontend", "data_source"]
_DIRECT_MODIFICATION_OWNERS = {"frontend", "data_source"}


def _direct_modification_owner(state: ProjectState) -> DirectModificationOwner:
    editor_mode = state.get("editor_mode")
    owner = "data_source" if editor_mode == "backend" else editor_mode
    if owner not in _DIRECT_MODIFICATION_OWNERS:
        raise ValueError(
            "Direct modification requires a validated frontend or data_source owner."
        )
    return cast(DirectModificationOwner, owner)


def _run_direct_modification_agent(
    *,
    owner: DirectModificationOwner,
    prompt: str,
    workspace: str | None,
    selected_skill_names: list[str] | None,
) -> str:
    """使用当前工作流的技能白名单执行直接修改。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle

    bundle = create_agent_bundle(workspace, selected_skill_names)
    agent = bundle.frontend if owner == "frontend" else bundle.data_source
    result = agent.invoke({"messages": [{"role": "user", "content": prompt}]})
    return last_agent_text(result)


def _direct_modification_prompt(request: str, owner: DirectModificationOwner) -> str:
    return (
        f"Execute this approved {owner} direct-modification task in the existing workspace. "
        "Keep the change local to the requested behavior, inspect the smallest relevant file set, "
        "do not change product requirements or API contracts, and run focused verification. "
        "If the request is cross-layer or cannot be completed safely by your role, do not make "
        "speculative changes; report a change request instead. Report changed files and commands.\n\n"
        f"User request:\n{request}"
    )


def direct_modification(state: ProjectState) -> dict[str, object]:
    workspace = workspace_from_state(state)
    owner = _direct_modification_owner(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool=f"{owner}.direct_modification",
        action=lambda: _run_direct_modification_agent(
            owner=owner,
            prompt=_direct_modification_prompt(state["request"], owner),
            workspace=workspace,
            selected_skill_names=state.get("selected_skill_names"),
        ),
    )
    note = captured.value
    return {
        **code_change_state_update(captured.code_change_set),
        "phase": "direct_modification",
        "tasks": [
            {
                "id": "direct-modification",
                "owner": owner,
                "description": state["request"],
                "dependencies": [],
                "status": "completed",
            }
        ],
        "build_results": [
            {
                "task_id": "direct-modification",
                "owner": owner,
                "status": "completed",
                "agent_note": note,
                "requiredSkillsLoaded": list(state.get("selected_skill_names") or []),
            }
        ],
        "timeline": ["direct_modification"],
    }
