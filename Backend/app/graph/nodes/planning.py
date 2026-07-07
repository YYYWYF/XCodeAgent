from app.agents.main.planner import plan_project_with_main_agent
from app.agents.main.page_designer import design_page_with_main_agent
from app.graph.state import ProjectState
from app.services.page_detail_plan import (
    attach_page_detail_plan,
)
from app.tools.page_selection import present_page_selection
from app.tools.page_spec_confirmation import confirm_page_spec
from app.workspace.plan_documents import (
    project_plan_json_path,
    write_project_plan_document,
)


def project_planning(state: ProjectState) -> dict:
    requirement_spec = state["requirement_spec"]
    project_plan = plan_project_with_main_agent(requirement_spec)
    project_plan_path = write_project_plan_document(state, project_plan)

    return {
        "phase": "project_planning",
        "project_plan": project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": str(project_plan_json_path(state)),
        "timeline": ["project_planning"],
    }


def detail_confirmation(state: ProjectState) -> dict:
    project_plan = state["project_plan"]
    page_selection = present_page_selection(
        project_plan["frontend_pages"],
        selected_page_id=state.get("selected_page_id"),
    )
    selected_page_id = page_selection["selected_page_id"]
    selected_page = next(
        page
        for page in project_plan["frontend_pages"]
        if page["id"] == selected_page_id
    )
    page_spec_confirmation = confirm_page_spec(
        selected_page,
        confirmed_page_spec=state.get("confirmed_page_spec"),
    )
    confirmed_page_spec = page_spec_confirmation["confirmed_page_spec"]
    page_detail_plan = design_page_with_main_agent(
        project_plan,
        confirmed_page_spec,
    )
    updated_project_plan = attach_page_detail_plan(project_plan, page_detail_plan)
    project_plan_path = write_project_plan_document(state, updated_project_plan)

    return {
        "phase": "detail_confirmation",
        "page_selection": page_selection,
        "page_spec_confirmation": page_spec_confirmation,
        "selected_page_id": selected_page_id,
        "confirmed_page_spec": confirmed_page_spec,
        "detail_plans": [page_detail_plan],
        "project_plan": updated_project_plan,
        "project_plan_path": project_plan_path,
        "project_plan_json_path": str(project_plan_json_path(state)),
        "timeline": ["detail_confirmation"],
    }
