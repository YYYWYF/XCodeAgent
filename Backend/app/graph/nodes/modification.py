from app.graph.nodes.common import run_live, workspace_from_state
from app.graph.state import ProjectState


def direct_modification(state: ProjectState) -> dict:
    note = run_live(
        "main",
        f"Apply this simple local modification directly and report changed files: {state['request']}",
        workspace=workspace_from_state(state),
    )
    return {
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
