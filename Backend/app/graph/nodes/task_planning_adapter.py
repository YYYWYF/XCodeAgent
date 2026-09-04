"""T11.6.4 production Workflow Planning/Confirm authority adapter。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
    _confirmed_build_task_plan_result,
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
from app.services.build_task_plan_lifecycle import (
    ConfirmPromotionResult,
    RegeneratePendingResult,
    confirm_pending_build_task_plan,
)
from app.services.build_task_planner import tasks_from_build_task_plan
from app.services.build_task_planning_service import (
    MainlinePlanningResult,
    run_mainline_planning,
)
from app.services.build_task_progress import create_planning_run_progress_publisher
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.dag_planning_inputs import assemble_mainline_planning_inputs
from app.services.dag_planning_regeneration import regenerate_pending_build_task_plan
from app.services.planning_frozen import plain_json
from app.services.planning_run_contracts import PlanningRun
from app.services.planning_run_progress import project_planning_run_progress
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.task_documents import (
    build_task_plan_json_path,
    build_task_plan_pending_json_path,
    load_pending_build_task_plan,
)


MainlinePlanningService = Callable[..., Awaitable[MainlinePlanningResult]]
UnitGenerationCallable = Callable[..., Awaitable[UnitGenerationAttemptResult]]
ConfirmService = Callable[..., ConfirmPromotionResult]
RegenerateService = Callable[..., Awaitable[RegeneratePendingResult]]


def production_unit_generation_policy() -> UnitGenerationPolicy:
    """返回 production Workflow 使用的 Unit 生成策略。

    Local=3、SDK retry=0 与 token budget 由 DTO 固定；这里的超时、turn 与
    Frozen Contract 读取预算是 cutover 的显式生产取值，不再依赖测试常量。
    """

    return UnitGenerationPolicy(
        request_timeout=120.0,
        unit_session_timeout=600.0,
        model_turn_limit=8,
        frozen_contract_read_limits={
            "max_reads": 24,
            "max_total_bytes": 2_000_000,
            "max_bytes_per_read": 200_000,
        },
    )


def create_async_workflow_planning_adapter(
    *,
    policy: UnitGenerationPolicy | None = None,
    settings: Settings | None = None,
    generate_once: UnitGenerationCallable | None = None,
    planning_service: MainlinePlanningService = run_mainline_planning,
    confirm_service: ConfirmService = confirm_pending_build_task_plan,
    regenerate_service: RegenerateService = regenerate_pending_build_task_plan,
) -> Callable[[ProjectState], Awaitable[dict[str, Any]]]:
    """创建 production Graph 节点：生成、确认和重新生成各走唯一业务 authority。"""

    frozen_policy = UnitGenerationPolicy.model_validate(
        policy or production_unit_generation_policy()
    )

    async def async_workflow_planning_adapter(state: ProjectState) -> dict[str, Any]:
        """解析 Workflow state，按本轮动作分派 generation、Confirm 或 Regenerate。"""

        return await _run_async_workflow_planning_adapter(
            state,
            policy=frozen_policy,
            settings=settings,
            generate_once=generate_once,
            planning_service=planning_service,
            confirm_service=confirm_service,
            regenerate_service=regenerate_service,
        )

    return async_workflow_planning_adapter


@dataclass(frozen=True)
class _PlanningContext:
    """一次 generation 或 Confirm 共享的服务端权威规划上下文。"""

    workspace: str
    formal_state: dict[str, dict[str, Any]]
    project_plan: dict[str, Any]
    scope: dict[str, str]
    confirmed_plan: dict[str, Any] | None
    skeleton: dict[str, Any]
    build_context: dict[str, Any]
    workspace_snapshot: dict[str, Any]
    inputs: Any
    formal_plan_path: str


async def _run_async_workflow_planning_adapter(
    state: ProjectState,
    *,
    policy: UnitGenerationPolicy,
    settings: Settings | None,
    generate_once: UnitGenerationCallable | None,
    planning_service: MainlinePlanningService,
    confirm_service: ConfirmService,
    regenerate_service: RegenerateService,
) -> dict[str, Any]:
    """执行 generation、Confirm 或 Regenerate，并只信任服务端正式输入。"""

    action_payload = state.get("build_task_plan_confirmation")
    if isinstance(action_payload, dict) and action_payload.get("action"):
        action = action_payload.get("action")
        if action == "confirm":
            return await _run_confirm_branch(
                state,
                action_payload,
                confirm_service=confirm_service,
            )
        if action == "regenerate":
            return await _run_regenerate_branch(
                state,
                action_payload,
                policy=policy,
                settings=settings,
                generate_once=generate_once,
                regenerate_service=regenerate_service,
            )
        # Abandon 仍由 plan-control 收口；其它异常值一律 fail closed。
        return _reject_unsupported_planning_action(state, action_payload)

    context = _assemble_planning_context(state)
    if isinstance(context, dict):
        return context
    result = await planning_service(
        context.inputs,
        workspace_state=state,
        policy=policy,
        settings=settings,
        generate_once=generate_once,
        publish=create_planning_run_progress_publisher(),
    )
    return _project_planning_result(result, context=context)


async def _run_confirm_branch(
    state: ProjectState,
    action_payload: dict[str, Any],
    *,
    confirm_service: ConfirmService,
) -> dict[str, Any]:
    """只接受精确 DraftIdentity，并交给 lifecycle Confirm authority 提升 Formal。"""

    context = _assemble_planning_context(state)
    if isinstance(context, dict):
        return context
    result = confirm_service(
        state,
        planning_run_id=str(action_payload.get("planning_run_id") or ""),
        draft_digest=str(action_payload.get("draft_digest") or ""),
        current_inputs=context.inputs.sequential_inputs(),
    )
    return _project_confirm_result(result, state=state, context=context)


async def _run_regenerate_branch(
    state: ProjectState,
    action_payload: dict[str, Any],
    *,
    policy: UnitGenerationPolicy,
    settings: Settings | None,
    generate_once: UnitGenerationCallable | None,
    regenerate_service: RegenerateService,
) -> dict[str, Any]:
    """消费旧 Pending 后重建正式输入，并把新 PlanningRun 投影回确认门。"""

    workflow_run_id, thread_id = _workflow_identity(state)
    refreshed_context: list[_PlanningContext] = []

    def current_inputs_factory(
        fresh_formal: dict[str, Any] | None,
    ) -> Any:
        """在旧 Pending 删除后重新读取全部正式输入，拒绝复用旧 checkpoint 上下文。"""

        context = _assemble_planning_context(state)
        if isinstance(context, dict):
            clarification = context.get("clarification")
            message = (
                str(clarification.get("message") or "")
                if isinstance(clarification, dict)
                else ""
            )
            raise ValueError(message or "Regenerate 无法从当前正式产物重建 Planning 输入。")
        if plain_json(context.confirmed_plan) != fresh_formal:
            raise ValueError("Regenerate 重建输入期间 ConfirmedPlan 已变化。")
        refreshed_context.append(context)
        return context.inputs.sequential_inputs()

    result = await regenerate_service(
        state,
        planning_run_id=str(action_payload.get("planning_run_id") or ""),
        draft_digest=str(action_payload.get("draft_digest") or ""),
        workflow_run_id=workflow_run_id,
        thread_id=thread_id,
        current_inputs_factory=current_inputs_factory,
        policy=policy,
        settings=settings,
        generate_once=generate_once,
        publish=create_planning_run_progress_publisher(),
    )
    if result.status != "regenerated":
        context = _assemble_planning_context(state)
        if isinstance(context, dict):
            return context
        return _stale_confirm_result(
            ConfirmPromotionResult(
                status="stale_draft",
                errors=result.errors,
            ),
            state=state,
            context=context,
        )
    if not refreshed_context or result.planning_run is None or result.draft_identity is None:
        raise RuntimeError("Regenerate 成功结果缺少新 PlanningRun 或 DraftIdentity。")
    pending = load_pending_build_task_plan(state)
    if pending is None:
        raise RuntimeError("Regenerate 成功后没有可投影的 PendingPlan。")
    return _project_pending_result(
        pending_plan=pending,
        pending_plan_path=str(build_task_plan_pending_json_path(state)),
        planning_run_id=result.planning_run.planning_run_id,
        draft_digest=result.draft_identity.draft_digest,
        planning_run=result.planning_run,
        context=refreshed_context[-1],
    )


def _reject_unsupported_planning_action(
    state: ProjectState, action_payload: dict[str, Any]
) -> dict[str, Any]:
    """Abandon 或未知 DAG 动作必须 fail closed：不调 Confirm、不写 Formal。

    复用 stale 投影语义保留当前 Pending 与 DraftIdentity，让用户能重新确认；
    任何异常动作值都不会被当作 Confirm 处理。
    """

    context = _assemble_planning_context(state)
    if isinstance(context, dict):
        return context
    action = str(action_payload.get("action") or "")
    return _stale_confirm_result(
        ConfirmPromotionResult(
            status="stale_draft",
            errors=(
                f"build_task_plan_confirmation.action 只支持 confirm/regenerate，收到 {action!r}。",
            ),
        ),
        state=state,
        context=context,
    )


def _assemble_planning_context(
    state: ProjectState,
) -> _PlanningContext | dict[str, Any]:
    """按正式产物重建 planning 上下文；返回 dict 表示本轮被门禁阻断。

    生成与 Confirm 都必须使用服务端重新构造的正式输入，不接受前端指纹或
    checkpoint；两分支复用同一构造，保证 input_fingerprint 可精确复验。
    """

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
    owner_session_id = str(state.get("owner_session_id") or "").strip()
    if not owner_session_id:
        raise ValueError("Async planning adapter 需要页面对话 owner_session_id。")
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
        owner_session_id=owner_session_id,
        workflow_run_id=workflow_run_id,
        thread_id=thread_id,
    )
    return _PlanningContext(
        workspace=workspace,
        formal_state=formal_state,
        project_plan=project_plan,
        scope=scope,
        confirmed_plan=confirmed_plan,
        skeleton=skeleton,
        build_context=build_context,
        workspace_snapshot=workspace_snapshot,
        inputs=inputs,
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
    context: _PlanningContext,
) -> dict[str, Any]:
    """把首次 Planning 结果交给统一 Pending 投影，不触碰 Formal authority。"""

    return _project_pending_result(
        pending_plan=plain_json(result.pending_plan),
        pending_plan_path=result.pending_plan_path,
        planning_run_id=result.planning_run_id,
        draft_digest=result.draft_identity.draft_digest,
        planning_run=result.validated_assembled_plan.planning_run,
        context=context,
    )


def _project_pending_result(
    *,
    pending_plan: dict[str, Any],
    pending_plan_path: str,
    planning_run_id: str,
    draft_digest: str,
    planning_run: PlanningRun,
    context: _PlanningContext,
) -> dict[str, Any]:
    """统一投影首次生成或 Regenerate 产生的新 PendingPlan。

    ``build_task_plan_path`` 始终指向 Formal authority（build-task-plan.json），
    ``pending_build_task_plan_path`` 指向本轮 Pending authority；
    ``build_task_plan_persisted`` 只表示当前 scope 的 Formal 是否已存在，
    Pending 落盘由 ``pending_build_task_plan_persisted`` 单独表达。
    """

    confirmation = _build_task_plan_confirmation_payload(
        pending_plan,
        context.scope,
        project_plan=context.project_plan,
        build_context=context.build_context,
    )
    previous_scope = (
        context.confirmed_plan.get("build_execution_scope")
        if isinstance(context.confirmed_plan, dict)
        and isinstance(context.confirmed_plan.get("build_execution_scope"), dict)
        else None
    )
    return {
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": context.project_plan,
        "build_task_plan": pending_plan,
        "build_task_plan_path": context.formal_plan_path,
        # Formal persisted 只按当前 scope 判断；本轮 Pending 另由
        # pending_build_task_plan_persisted 表达，两者不能互相代替。
        "build_task_plan_persisted": (
            isinstance(context.confirmed_plan, dict)
            and context.confirmed_plan.get("build_execution_scope") == context.scope
        ),
        "pending_build_task_plan_path": pending_plan_path,
        "pending_build_task_plan_persisted": True,
        "planning_run_id": planning_run_id,
        "draft_digest": draft_digest,
        "dag_generation_progress": project_planning_run_progress(planning_run),
        "build_execution_scope": context.scope,
        "last_persisted_build_execution_scope": previous_scope,
        "build_context": context.build_context,
        "build_units": pending_plan.get("build_units", {}),
        "unit_graph": pending_plan.get("unit_graph", {}),
        "task_registry": pending_plan.get("task_registry", {}),
        "task_graph": pending_plan.get("task_graph", {}),
        "tasks": tasks_from_build_task_plan(pending_plan),
        "build_task_plan_confirmation": confirmation,
        "clarification": confirmation,
        "timeline": ["prepare_build_tasks"],
        **context.formal_state,
    }


def _project_confirm_result(
    result: ConfirmPromotionResult,
    *,
    state: ProjectState,
    context: _PlanningContext,
) -> dict[str, Any]:
    """把 Confirm 结果投影到 Graph：成功放行 Build，失败保留可重试 Pending。"""

    if result.status in {"confirmed", "already_confirmed"} and result.confirmed_plan is not None:
        confirmed_plan = plain_json(result.confirmed_plan)
        return {
            **clear_planning_projection(),
            **_confirmed_build_task_plan_result(
                state,
                context.project_plan,
                confirmed_plan,
                context.scope,
                path=context.formal_plan_path,
            ),
            "build_context": context.build_context,
            "build_units": confirmed_plan.get("build_units", {}),
            "unit_graph": confirmed_plan.get("unit_graph", {}),
            "last_persisted_build_execution_scope": context.scope,
            **context.formal_state,
        }
    return _stale_confirm_result(result, state=state, context=context)


def _stale_confirm_result(
    result: ConfirmPromotionResult,
    *,
    state: ProjectState,
    context: _PlanningContext,
) -> dict[str, Any]:
    """拒绝过期、已放弃或校验失败的 Confirm，并保留当前 Pending 供重新确认。"""

    pending = load_pending_build_task_plan(state) or {}
    errors = list(result.errors) or [
        "待确认任务规划已过期或被替换，请重新生成后再确认。"
    ]
    confirmation = _build_task_plan_confirmation_payload(
        pending,
        context.scope,
        project_plan=context.project_plan,
        build_context=context.build_context,
        errors=errors,
    )
    identity = (
        pending.get("draft_identity") if isinstance(pending, dict) else None
    )
    identity = identity if isinstance(identity, dict) else {}
    pending_path = build_task_plan_pending_json_path(state) if pending else None
    return {
        "phase": "prepare_build_tasks",
        "status": "requires_user_input",
        "project_plan": context.project_plan,
        "build_task_plan": pending,
        "build_task_plan_path": context.formal_plan_path,
        "build_task_plan_persisted": (
            isinstance(context.confirmed_plan, dict)
            and context.confirmed_plan.get("build_execution_scope") == context.scope
        ),
        "pending_build_task_plan_path": str(pending_path) if pending_path else "",
        "pending_build_task_plan_persisted": bool(pending),
        "planning_run_id": str(identity.get("planning_run_id") or ""),
        "draft_digest": str(identity.get("draft_digest") or ""),
        "dag_generation_progress": {},
        "build_execution_scope": context.scope,
        "build_context": context.build_context,
        "build_units": pending.get("build_units", {}),
        "unit_graph": pending.get("unit_graph", {}),
        "task_registry": pending.get("task_registry", {}),
        "task_graph": pending.get("task_graph", {}),
        "tasks": tasks_from_build_task_plan(pending),
        "build_task_plan_confirmation": confirmation,
        "clarification": confirmation,
        "timeline": ["prepare_build_tasks"],
        **context.formal_state,
    }
