from app.agents.main.requirements_analyzer import analyze_requirements_with_main_agent
from app.graph.nodes.common import workspace_from_state
from app.graph.state import ProjectState
from app.workspace.spec_documents import (
    requirement_spec_json_path,
    write_requirement_spec_document,
)


def requirements(state: ProjectState) -> dict:
    analysis = analyze_requirements_with_main_agent(
        state["request"],
        workspace=workspace_from_state(state),
    )
    spec = analysis["requirement_spec"]
    clarification = analysis["clarification"]
    spec_path = write_requirement_spec_document(state, spec)

    return {
        "phase": "requirements",
        "status": clarification["status"],
        "requirement_spec": spec,
        "requirement_spec_path": spec_path,
        "requirement_spec_json_path": str(requirement_spec_json_path(state)),
        "clarification": clarification,
        "timeline": ["requirements"],
    }
