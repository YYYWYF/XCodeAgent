from app.agents.main.requirements_analyzer import analyze_requirements_with_main_agent
from app.graph.nodes.common import capture_agent_file_changes, workspace_from_state
from app.graph.state import ProjectState
from app.workspace.code_changes import code_change_state_update
from app.workspace.spec_documents import (
    requirement_spec_json_path,
    write_requirement_spec_document,
)


def requirements(state: ProjectState) -> dict:
    workspace = workspace_from_state(state)
    captured = capture_agent_file_changes(
        workspace=workspace,
        source_tool="main.requirements",
        action=lambda: analyze_requirements_with_main_agent(
            state["request"],
            workspace=workspace,
        ),
    )
    analysis = captured.value
    spec = analysis["requirement_spec"]
    clarification = analysis["clarification"]
    spec_path = write_requirement_spec_document(state, spec)

    return {
        **code_change_state_update(captured.code_change_set),
        "phase": "requirements",
        "status": clarification["status"],
        "requirement_spec": spec,
        "requirement_spec_path": spec_path,
        "requirement_spec_json_path": str(requirement_spec_json_path(state)),
        "clarification": clarification,
        "timeline": ["requirements"],
    }
