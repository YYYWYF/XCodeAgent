from operator import add
from typing import Annotated, Any, TypedDict


class ProjectState(TypedDict, total=False):
    request: str
    project_id: str
    workspace: str
    workspace_path: str
    editor_mode: str
    workflow_scope: str
    application_name: str
    active_thread_id: str
    active_run_id: str
    user_interaction_submission: bool
    lifecycle: dict[str, Any]
    lifecycle_interaction_submission: dict[str, Any]
    selected_skill_names: list[str]
    phase: str
    resume_from: str
    request_complexity: str
    complexity_reason: str
    complexity_decision: dict[str, Any]
    direct_modification_owner: str
    direct_modification_scope: str
    direct_modification_confidence: float
    direct_modification_reason: str
    direct_modification_summary: str
    direct_modification_result: dict[str, Any]
    direct_stage_results: dict[str, dict[str, Any]]
    direct_code_change_sets: list[dict[str, Any]]
    backend_handoff: dict[str, Any]
    integration_contract_check_enabled: bool
    integration_repair_enabled: bool
    requirement_spec: dict[str, Any]
    edited_requirement_spec: dict[str, Any]
    requirement_spec_feedback: str
    requirement_spec_path: str
    requirement_spec_json_path: str
    clarification: dict[str, Any]
    project_plan: dict[str, Any]
    frontend_pages: list[dict[str, Any]]
    pending_project_plan: dict[str, Any]
    project_plan_path: str
    project_plan_json_path: str
    detail_selection: dict[str, Any]
    page_selection: dict[str, Any]
    selectedPageId: str
    selected_api_contract_id: str
    selected_endpoint_id: str
    detail_target_type: str
    page_template: dict[str, Any]
    data_source_spec_draft: dict[str, Any]
    detail_plans: list[dict[str, Any]]
    detail_review_submission: dict[str, Any]
    application_planning_confirmation: dict[str, Any]
    ui_designs: dict[str, Any]
    workspace_snapshot_summary: dict[str, Any]
    workspace_snapshot_path: str
    workspace_snapshot_hash: str
    workspace_revision: str
    build_execution_scope: dict[str, str]
    execution_resource_claims: list[dict[str, Any]]
    build_execution_slice: dict[str, Any]
    build_context: dict[str, Any]
    database_planning_context: dict[str, Any]
    database_change_plan: dict[str, Any]
    database_approval_requests: list[dict[str, Any]]
    build_task_plan: dict[str, Any]
    dag_generation_progress: dict[str, Any]
    build_task_plan_path: str
    build_task_dag_path: str
    build_units: dict[str, dict[str, Any]]
    unit_graph: dict[str, Any]
    task_registry: dict[str, dict[str, Any]]
    task_graph: dict[str, Any]
    execution_history: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    ready_tasks: list[dict[str, Any]]
    pending_build_results: list[dict[str, Any]]
    build_results: list[dict[str, Any]]
    build_summary: dict[str, Any]
    build_events: Annotated[list[str], add]
    test_results: list[dict[str, Any]]
    test_events: Annotated[list[str], add]
    test_agent_review: dict[str, Any]
    test_report: dict[str, Any]
    test_report_path: str
    quality_gate_passed: bool
    needs_revision: bool
    revision_requests: list[dict[str, Any]]
    repair_task_plan: dict[str, Any]
    repair_task_plan_path: str
    repair_tasks: list[dict[str, Any]]
    repair_iteration: int
    max_repair_iterations: int
    integration_next_action: str
    code_changes: dict[str, Any]
    code_change_sets: Annotated[list[dict[str, Any]], add]
    preview_url: str
    launch_result: dict[str, Any]
    acceptance_request: dict[str, Any]
    acceptance_decision: str
    accepted: bool
    status: str
    timeline: Annotated[list[str], add]
