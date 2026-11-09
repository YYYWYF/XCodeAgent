"""当前正式修订与 Pending DAG 的只读审阅装配。"""

from app.services.document_review_assets import DocumentReview, _require_confirmed


def find_workbench_document_review(files, state: dict, lifecycle) -> DocumentReview | None:
    """从明确的当前草稿读取审阅内容，不运行规划器、不接管其他用户的活任务。"""
    active = lifecycle.active_formal_revision
    if active and active.formal_branch.value == "workbench_plan_revision" and active.current_artifact == "technical-plan":
        from app.workspace.revision_draft_documents import load_revision_draft, revision_draft_directory
        from app.graph.nodes.application_revision import _draft_projection, _validate_technical_plan
        from app.services.application_config import read_application_config
        metadata, artifact = load_revision_draft(files.workspace, change_id=active.change_id, artifact_key="technical-plan")
        directory = revision_draft_directory(files.workspace, change_id=active.change_id, artifact_key="technical-plan")
        files.track(directory / "artifact.json")
        files.track(directory / "metadata.json")
        baseline, _ = files.read("plans/technical-plan.json", required=True)
        from app.services.artifact_invalidation import canonical_sha256
        upstream_paths = {"product-plan": files.root / "plans/product-plan.json", "ui-design": files.root / "specs/ui-designs.json"}
        if canonical_sha256(files.root / "plans/technical-plan.json") != metadata.base_canonical_sha256:
            raise ValueError("当前正式技术规划已变化，请先核对现有修订草稿。")
        if {item.artifact_key: item.sha256 for item in metadata.based_on_canonical} != {
            key: canonical_sha256(path) for key, path in upstream_paths.items()}:
            raise ValueError("修订草稿的直接上游已变化，请先核对现有文档。")
        _require_confirmed(state["requirement_spec"], "需求文档")
        _require_confirmed(state["product_plan"], "产品规划")
        _validate_technical_plan(artifact, state["requirement_spec"], state["product_plan"], state["ui_designs"],
                                 read_application_config(files.workspace))
        review = _draft_projection(metadata.artifact_key, artifact, state["product_plan"], metadata)
        review = {key: value for key, value in review.items() if key not in {"technicalPlan", "productPlan", "baseline"}}
        state.update(phase="technical_planning", status="requires_user_input", change_id=active.change_id,
            technical_plan=artifact, technical_plan_baseline=baseline, revision_draft=review,
            clarification={"mode": "revision_draft_confirmation", "status": "requires_user_input",
                "message": "请审阅当前已有技术规划草稿；确认后才更新正式版本。", "revisionDraft": review,
                "questions": [{"id": "revision_draft_confirmation", "header": "正式产物草稿", "type": "single",
                    "question": "请确认当前已有版本，或放弃本次修改。",
                    "options": [{"label": "确认当前版本", "value": "confirm"}, {"label": "放弃本次修改", "value": "discard"}]}]})
        return DocumentReview("revision_draft", "technical_planning", "ready_for_workbench", state, files.sources, False)
    from app.workspace.task_documents import load_pending_build_task_plan, validate_pending_self_digest, load_confirmed_build_task_plan
    from app.services.planning_refresh_recovery import _identity_matches, _pending_confirmation
    pending = load_pending_build_task_plan({"workspace": files.workspace})
    if pending is None:
        return None
    identity = validate_pending_self_digest(pending)
    formal = load_confirmed_build_task_plan(files.workspace)
    marker = lifecycle.extensions.get("planningResultLifecycle")
    if (identity.workflow_run_id in lifecycle.ended_executions
        or (formal and _identity_matches(formal.get("confirmed_from"), identity))
        or (isinstance(marker, dict) and marker.get("status") == "abandoned" and _identity_matches(marker, identity, camel_case=True))):
        return None
    _require_confirmed(state["requirement_spec"], "需求文档")
    _require_confirmed(state["product_plan"], "产品规划")
    plan, _ = files.read("plans/technical-plan.json", required=True)
    _require_confirmed(plan, "技术规划")
    from app.workspace.task_documents import build_task_plan_pending_json_path
    files.track(build_task_plan_pending_json_path({"workspace": files.workspace}))
    state.update(phase="prepare_build_tasks", status="requires_user_input", build_task_plan=pending,
        technical_plan=plan, project_plan=plan, build_execution_scope=dict(identity.build_execution_scope),
        clarification=_pending_confirmation(files.workspace, pending, identity))
    return DocumentReview("build_task_plan", "prepare_build_tasks", "ready_for_workbench", state, files.sources, False)
