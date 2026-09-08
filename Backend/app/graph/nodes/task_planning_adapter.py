"""T11.6.3 显式 opt-in 的异步 Workflow Planning Graph adapter。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.config import Settings
from app.graph.nodes.common import workspace_from_state
from app.graph.nodes.task_planning_inputs import mainline_formal_contract_inputs
from app.graph.nodes.tasks import (
    _api_contract_inconsistency_payload,
    _build_context_error_payload,
    _build_execution_scope_from_state,
    _build_prerequisite_blocked_result,
    _build_prerequisite_errors,
    _build_task_plan_confirmation_payload,
    _confirmed_baseline_blocked_result,
    _existing_build_task_plan,
    _formal_artifact_state_update,
    _latest_project_plan,
    _load_formal_artifacts,
    _resolve_build_context,
    _scoped_contract_errors,
    _workspace_snapshot_from_state,
    clear_planning_projection,
)
from app.graph.state import ProjectState
from app.services.application_template_generation import (
    inspect_template_generation_readiness,
)
from app.services.authorization_overlay import compile_authorization_overlay
from app.services.build_task_planner import tasks_from_build_task_plan
from app.services.build_task_planning_service import (
    MainlinePlanningResult,
    run_mainline_planning,
)
from app.services.build_task_progress import create_planning_run_progress_publisher
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.dag_planning_inputs import assemble_mainline_planning_inputs
from app.services.planning_frozen import plain_json
from app.services.planning_run_progress import project_planning_run_progress
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.task_documents import build_task_plan_json_path


MainlinePlanningService = Callable[..., Awaitable[MainlinePlanningResult]]
UnitGenerationCallable = Callable[..., Awaitable[UnitGenerationAttemptResult]]


def create_async_workflow_planning_adapter(
    *,
    policy: UnitGenerationPolicy,
    settings: Settings | None = None,
    generate_once: UnitGenerationCallable | None = None,
    planning_service: MainlinePlanningService = run_mainline_planning,
) -> Callable[[ProjectState], Awaitable[dict[str, Any]]]:
    """创建显式 opt-in 的异步 Graph 节点，默认 production 图不会自动采用它。"""

    frozen_policy = UnitGenerationPolicy.model_validate(policy)

    async def async_workflow_planning_adapter(state: ProjectState) -> dict[str, Any]:
        """解析 Workflow state，等待 mainline service，并投射现有确认读模型。"""

        return await _run_async_workflow_planning_adapter(
            state,
            policy=frozen_policy,
            settings=settings,
            generate_once=generate_once,
            planning_service=planning_service,
        )

    return async_workflow_planning_adapter


async def _run_async_workflow_planning_adapter(
    state: ProjectState,
    *,
    policy: UnitGenerationPolicy,
    settings: Settings | None,
    generate_once: UnitGenerationCallable | None,
    planning_service: MainlinePlanningService,
) -> dict[str, Any]:
    """执行 generation 分支；不接管 Confirm 或默认 production authority。"""

    action_payload = state.get("build_task_plan_confirmation")
    if isinstance(action_payload, dict) and action_payload.get("action"):
        raise ValueError("T11.6.3 async planning adapter 尚未接管 Confirm authority。")

    workspace = workspace_from_state(state)
    formal_artifacts = _load_formal_artifacts(workspace)
    formal_state = _formal_artifact_state_update(formal_artifacts)
    project_plan = _latest_project_plan(state, formal_artifacts=formal_artifacts)
    scope = _build_execution_scope_from_state(state)
    prerequisite_errors = _build_prerequisite_errors(
        state,
        project_plan,
        workspace=workspace,
        build_execution_scope=scope,
        formal_artifacts=formal_artifacts,
    )
    if prerequisite_errors:
        return {
            **_build_prerequisite_blocked_result(
                project_plan, scope, prerequisite_errors
            ),
            **formal_state,
        }
    try:
        confirmed_plan = _existing_build_task_plan(state) or None
    except (OSError, ValueError) as exc:
        return {
            **_confirmed_baseline_blocked_result(
                project_plan,
                scope,
                [f"正式 build-task-plan.json 无法作为 ConfirmedPlan：{exc}"],
            ),
            **formal_state,
        }

    workspace_snapshot = _workspace_snapshot_from_state(state)
    skeleton = ensure_build_unit_skeleton(
        project_plan, workspace_snapshot, confirmed_plan
    )
    try:
        build_context = compile_authorization_overlay(
            project_plan,
            _resolve_build_context(state, project_plan, scope, skeleton),
        )
    except ValueError as exc:
        return _context_blocked_result(
            project_plan=project_plan,
            skeleton=skeleton,
            scope=scope,
            formal_state=formal_state,
            clarification=_build_context_error_payload(str(exc), scope),
        )
    contract_errors = _scoped_contract_errors(project_plan, scope, build_context)
    if contract_errors:
        return _context_blocked_result(
            project_plan=project_plan,
            skeleton=skeleton,
            scope=scope,
            formal_state=formal_state,
            clarification=_api_contract_inconsistency_payload(
                contract_errors, scope
            ),
        )

    template_readiness = inspect_template_generation_readiness(workspace)
    build_context = {
        **build_context,
        "scope": scope,
        "template_variant": template_readiness.get("templateVariant"),
    }
    reuse_facts = resolve_reuse_facts(
        confirmed_plan=confirmed_plan,
        unit_skeleton=skeleton,
        build_context=build_context,
        workspace_snapshot=workspace_snapshot,
        formal_plan=project_plan,
        template_readiness=template_readiness,
    )
    workflow_run_id, thread_id = _workflow_identity(state)
    inputs = assemble_mainline_planning_inputs(
        project_plan=project_plan,
        base_confirmed_plan=confirmed_plan,
        skeleton_plan=skeleton,
        build_context=build_context,
        build_execution_scope=scope,
        workspace_snapshot=workspace_snapshot,
        reuse_facts=reuse_facts,
        formal_contract_inputs=mainline_formal_contract_inputs(
            formal_artifacts, project_plan
        ),
        workflow_run_id=workflow_run_id,
        thread_id=thread_id,
    )
    result = await planning_service(
        inputs,
        workspace_state=state,
        policy=policy,
        settings=settings,
        generate_once=generate_once,
        publish=create_planning_run_progress_publisher(),
    )
    return _project_planning_result(
        result,
        project_plan=project_plan,
        build_context=build_context,
        scope=scope,
        confirmed_plan=confirmed_plan,
        formal_state=formal_state,
        formal_plan_path=str(build_task_plan_json_path(state)),
    )


def _context_blocked_result(
    *,
    project_plan: dict[str, Any],
    skeleton: dict[str, Any],
    scope: dict[str, str],
    formal_state: dict[str, dict[str, Any]],
    clarification: dict[str, Any],
) -> dict[str, Any]:
    """保持旧节点的可恢复上下文字段，同时明确本轮没有写 Pending。"""

    return {
        **clear_planning_projection(),
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "build_task_plan": skeleton,
        "build_execution_scope": scope,
        "clarification": clarification,
        "timeline": ["prepare_build_tasks"],
        **formal_state,
    }


def _workflow_identity(state: ProjectState) -> tuple[str, str]:
    """从 runtime 已写入的 Graph state 读取 Workflow Run 与 Thread 身份。"""

    workflow_run_id = str(state.get("active_run_id") or "").strip()
    thread_id = str(state.get("active_thread_id") or "").strip()
    if not workflow_run_id or not thread_id:
        raise ValueError("Async planning adapter 需要 active_run_id 和 active_thread_id。")
    return workflow_run_id, thread_id


def _project_planning_result(
    result: MainlinePlanningResult,
    *,
    project_plan: dict[str, Any],
    build_context: dict[str, Any],
    scope: dict[str, str],
    confirmed_plan: dict[str, Any] | None,
    formal_state: dict[str, dict[str, Any]],
    formal_plan_path: str,
) -> dict[str, Any]:
    """把 Pending 只读投影到既有 Graph contract，不触碰 Formal authority。

    ``build_task_plan_path`` 始终指向 Formal authority（build-task-plan.json），
    ``pending_build_task_plan_path`` 指向本轮 Pending authority；
    ``build_task_plan_persisted`` 只表示当前 scope 的 Formal 是否已存在，
    Pending 落盘由 ``pending_build_task_plan_persisted`` 单独表达。
    """

    pending_plan = plain_json(result.pending_plan)
    confirmation = _build_task_plan_confirmation_payload(
        pending_plan,
        scope,
        project_plan=project_plan,
        build_context=build_context,
    )
    previous_scope = (
        confirmed_plan.get("build_execution_scope")
        if isinstance(confirmed_plan, dict)
        and isinstance(confirmed_plan.get("build_execution_scope"), dict)
        else None
    )
    return {
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": project_plan,
        "build_task_plan": pending_plan,
        "build_task_plan_path": formal_plan_path,
        # Formal persisted 只按当前 scope 判断；本轮 Pending 另由
        # pending_build_task_plan_persisted 表达，两者不能互相代替。
        "build_task_plan_persisted": (
            isinstance(confirmed_plan, dict)
            and confirmed_plan.get("build_execution_scope") == scope
        ),
        "pending_build_task_plan_path": result.pending_plan_path,
        "pending_build_task_plan_persisted": True,
        "planning_run_id": result.planning_run_id,
        "draft_digest": result.draft_identity.draft_digest,
        "dag_generation_progress": project_planning_run_progress(
            result.validated_assembled_plan.planning_run
        ),
        "build_execution_scope": scope,
        "last_persisted_build_execution_scope": previous_scope,
        "build_context": build_context,
        "build_units": pending_plan.get("build_units", {}),
        "unit_graph": pending_plan.get("unit_graph", {}),
        "task_registry": pending_plan.get("task_registry", {}),
        "task_graph": pending_plan.get("task_graph", {}),
        "tasks": tasks_from_build_task_plan(pending_plan),
        "build_task_plan_confirmation": confirmation,
        "clarification": confirmation,
        "timeline": ["prepare_build_tasks"],
        **formal_state,
    }
