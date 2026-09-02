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
    change_id: str
    change_target: dict[str, Any]
    element_context: dict[str, Any]
    lifecycle: dict[str, Any]
    lifecycle_interaction_submission: dict[str, Any]
    selected_skill_names: list[str]
    phase: str
    resume_from: str
    request_complexity: str
    complexity_reason: str
    complexity_decision: dict[str, Any]
    conversation_intent: str
    conversation_response: str
    change_impact_enabled: bool
    change_impact_analysis: dict[str, Any]
    change_impact_context: dict[str, Any]
    change_impact_code_scan_required: bool
    change_impact_code_scan: dict[str, Any]
    revision_impact: dict[str, Any]
    revision_continuation: dict[str, Any]
    revision_draft: dict[str, Any]
    revision_interaction: dict[str, Any]
    design_change_submission: bool
    design_change_request: str
    design_change_target: str
    design_change_reason: str
    design_change_affected_page_ids: list[str]
    design_change_generation_target: str
    design_change_generation_request: str
    design_change_existing_artifacts: dict[str, bool]
    design_interaction_origin: str
    application_planning_interaction: dict[str, Any]
    authorization_config_conflict: dict[str, Any]
    direct_modification_owner: str
    direct_modification_scope: str
    direct_modification_confidence: float
    direct_modification_reason: str
    direct_modification_summary: str
    direct_modification_approved_paths: list[str]
    direct_modification_handoff_decision: str
    direct_modification_target_paths: list[str]
    direct_modification_resume_node: str
    direct_modification_result: dict[str, Any]
    direct_stage_results: dict[str, dict[str, Any]]
    direct_code_change_sets: list[dict[str, Any]]
    unit_test_generation_enabled: bool
    backend_handoff: dict[str, Any]
    integration_repair_enabled: bool
    requirement_spec: dict[str, Any]
    requirements_confirmed: bool
    requirements_clarification_round: int
    edited_requirement_spec: dict[str, Any]
    requirement_spec_feedback: str
    requirement_spec_path: str
    requirement_spec_json_path: str
    product_plan: dict[str, Any]
    product_plan_path: str
    product_plan_json_path: str
    clarification: dict[str, Any]
    technical_plan: dict[str, Any]
    technical_plan_path: str
    technical_plan_json_path: str
    technical_plan_repair_candidate: dict[str, Any]
    technical_plan_repair_errors: list[str]
    project_plan: dict[str, Any]
    pages: list[dict[str, Any]]
    frontend_pages: list[dict[str, Any]]
    pending_project_plan: dict[str, Any]
    project_plan_path: str
    project_plan_json_path: str
    detail_selection: dict[str, Any]
    page_selection: dict[str, Any]
    selectedPageId: str
    selected_api_contract_id: str
    selected_endpoint_id: str
    selected_entity_id: str
    detail_target_type: str
    page_template: dict[str, Any]
    data_source_spec_draft: dict[str, Any]
    detail_plans: list[dict[str, Any]]
    entity_source_binding_submission: dict[str, Any]
    entity_design_action: dict[str, Any]
    development_readiness: dict[str, Any]
    application_planning_confirmation: dict[str, Any]
    ui_designs: dict[str, Any]
    ui_design_action: dict[str, Any]
    workspace_snapshot_summary: dict[str, Any]
    workspace_snapshot_path: str
    workspace_snapshot_hash: str
    workspace_revision: str
    workspace_scan_progress: dict[str, Any]
    code_graph_index: dict[str, Any]
    build_execution_scope: dict[str, str]
    last_persisted_build_execution_scope: dict[str, str] | None
    build_task_plan_persisted: bool
    execution_resource_claims: list[dict[str, Any]]
    build_execution_slice: dict[str, Any]
    build_context: dict[str, Any]
    database_change_plan: dict[str, Any]
    database_approval_requests: list[dict[str, Any]]
    build_task_plan: dict[str, Any]
    build_task_plan_confirmation: dict[str, Any]
    dag_generation_progress: dict[str, Any]
    build_task_plan_path: str
    build_run_id: str
    build_run_plan_path: str
    build_run_plan_sha256: str
    retry_failed_tasks: bool
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
    authorization_bootstrap_result: dict[str, Any]
    authorization_platform_projection_evidence: dict[str, Any]
    build_events: Annotated[list[str], add]
    test_target: dict[str, Any]
    test_phase_confirmation: dict[str, Any]
    review_phase_confirmation: dict[str, Any]
    code_review_result: dict[str, Any]
    code_review_retry: dict[str, Any]
    code_review_report_path: str
    code_review_next_action: str
    code_review_repair_confirmation: dict[str, Any]
    acceptance_phase_confirmation: dict[str, Any]
    code_review_repair_status: str
    code_review_repair_result: dict[str, Any]
    code_review_build_results: list[dict[str, Any]]
    code_review_repair_iteration: int
    code_review_max_repair_iterations: int
    code_review_events: Annotated[list[str], add]
    test_generation_input_code_changes: dict[str, Any]
    test_generation_input_code_change_sets: list[dict[str, Any]]
    unit_test_generation_context: dict[str, Any]
    unit_test_generation: dict[str, Any]
    unit_test_affected_layers: list[str]
    unit_test_mapping_path: str | None
    unit_test_code_change_sets: list[dict[str, Any]]
    unit_test_generation_code_change_sets: list[dict[str, Any]]
    unit_test_decision: str
    unit_test_build_code_changes: dict[str, Any]
    unit_test_build_code_change_sets: list[dict[str, Any]]
    unit_test_build_diff_captured: bool
    unit_test_results: list[dict[str, Any]]
    unit_test_report: dict[str, Any]
    unit_test_report_path: str
    unit_test_quality_gate_passed: bool
    unit_test_next_action: str
    unit_test_gate_passed: bool
    unit_test_repair_enabled: bool
    unit_test_repair_task_plan: dict[str, Any]
    unit_test_repair_task_plan_path: str
    unit_test_repair_iteration: int
    unit_test_max_repair_iterations: int
    frontend_performance_decision: str
    frontend_performance_test_enabled: bool
    integration_build_checks_completed: bool
    integration_build_results: list[dict[str, Any]]
    test_results: list[dict[str, Any]]
    test_events: Annotated[list[str], add]
    test_report: dict[str, Any]
    test_report_path: str
    test_report_json_path: str
    quality_gate_passed: bool
    needs_revision: bool
    revision_requests: list[dict[str, Any]]
    repair_task_plan: dict[str, Any]
    repair_task_plan_path: str
    repair_tasks: list[dict[str, Any]]
    repair_iteration: int
    max_repair_iterations: int
    integration_next_action: str
    small_task_tasks: list[dict[str, Any]]
    small_task_results: list[dict[str, Any]]
    small_task_code_change_sets: list[dict[str, Any]]
    small_task_handoff: dict[str, Any]
    small_task_handoff_submission: dict[str, Any]
    small_task_route: str
    repair_return_node: str
    small_task_max_concurrency: int
    code_changes: dict[str, Any]
    code_change_sets: Annotated[list[dict[str, Any]], add]
    preview_url: str
    launch_result: dict[str, Any]
    acceptance_request: dict[str, Any]
    acceptance_decision: str
    accepted: bool
    status: str
    message: str
    error: str
    timeline: Annotated[list[str], add]
