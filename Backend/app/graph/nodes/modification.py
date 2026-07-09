from app.graph.nodes.common import capture_agent_file_changes, run_live, workspace_from_state
from app.graph.state import ProjectState
from app.workspace.code_changes import code_change_state_update


def direct_modification(state: ProjectState) -> dict:
    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="main.direct_modification",
        action=lambda: run_live(
            "main",
            f"Apply this simple local modification directly and report changed files: {state['request']}",
            workspace=workspace,
        ),
    )
    note = captured.value
    return {
        **code_change_state_update(captured.code_change_set),
        "phase": "direct_modification",
        "tasks": [
            {
                "id": "direct-modification",
                "owner": "main",
                "description": state["request"],
                "dependencies": [],
                "status": "completed",
            }
        ],
        "build_results": [
            {
                "task_id": "direct-modification",
                "status": "completed",
                "agent_note": note,
            }
        ],
        "timeline": ["direct_modification"],
    }
