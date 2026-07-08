from operator import add
from typing import Annotated, Any, TypedDict


class ProjectState(TypedDict, total=False):
    request: str
    project_id: str
    workspace: str
    workspace_path: str
    phase: str
    request_complexity: str
    complexity_reason: str
    complexity_decision: dict[str, Any]
    requirement_spec: dict[str, Any]
    requirement_spec_path: str
    requirement_spec_json_path: str
    clarification: dict[str, Any]
    project_plan: dict[str, Any]
    project_plan_path: str
    project_plan_json_path: str
    page_selection: dict[str, Any]
    page_spec_confirmation: dict[str, Any]
    selected_page_id: str
    confirmed_page_spec: dict[str, Any]
    detail_plans: list[dict[str, Any]]
    build_task_plan: dict[str, Any]
    build_task_plan_path: str
    tasks: list[dict[str, Any]]
    ready_tasks: list[dict[str, Any]]
    pending_build_results: list[dict[str, Any]]
    build_results: list[dict[str, Any]]
    build_summary: dict[str, Any]
    build_events: Annotated[list[str], add]
    test_results: list[dict[str, Any]]
    test_events: list[str]
    test_agent_review: dict[str, Any]
    test_report: dict[str, Any]
    test_report_path: str
    quality_gate_passed: bool
    needs_revision: bool
    revision_requests: list[dict[str, Any]]
    repair_task_plan: dict[str, Any]
    repair_task_plan_path: str
    repair_tasks: list[dict[str, Any]]
    preview_url: str
    accepted: bool
    status: str
    timeline: Annotated[list[str], add]
