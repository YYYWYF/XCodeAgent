"""T7.5 Pending 消费与全新 concurrency=1 PlanningRun 编排适配。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.config import Settings
from app.services.build_task_plan_lifecycle import (
    ConfirmedFrom,
    RegeneratePendingResult,
    abandon_pending_build_task_plan,
)
from app.services.dag_planning_inputs import SequentialPlanningInputs
from app.services.dag_planning_orchestrator import plan_dag_sequential
from app.services.planning_frozen import plain_json
from app.services.planning_run_controller import SnapshotPublisher
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.spec_documents import workspace_root
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_confirmed_build_task_plan,
    load_pending_build_task_plan,
    validate_pending_self_digest,
    write_pending_build_task_plan_atomic,
)


def _new_planning_run_id() -> str:
    """只在后端为 Regenerate 分配不可复用的新 PlanningRun ID。"""

    return f"planning-{uuid4().hex}"


async def regenerate_pending_build_task_plan(
    state: dict[str, Any],
    *,
    planning_run_id: str,
    draft_digest: str,
    workflow_run_id: str,
    thread_id: str,
    current_inputs_factory: Callable[[dict[str, Any] | None], SequentialPlanningInputs],
    policy: UnitGenerationPolicy,
    settings: Settings | None = None,
    generate_once: Callable[..., Awaitable[UnitGenerationAttemptResult]] | None = None,
    publish: SnapshotPublisher | None = None,
    planning_run_id_factory: Callable[[], str] | None = None,
) -> RegeneratePendingResult:
    """消费精确旧 Pending，并从刚重载的 Formal 输入启动全新有限并发 PlanningRun。

    删除旧 Pending 是不可回滚的生命周期提交点。其后 Formal/input reload、Planning、
    模型调用或新 Pending 写入任一步失败，都向调用方传播异常且绝不恢复旧草稿。
    """

    abandoned = abandon_pending_build_task_plan(
        state,
        planning_run_id=planning_run_id,
        draft_digest=draft_digest,
        record_lifecycle=False,
    )
    if abandoned.status != "abandoned":
        errors = abandoned.errors or (
            "当前 PendingPlan 不存在或已更新，不能基于旧版本重新生成。",
        )
        return RegeneratePendingResult(status="stale_draft", errors=errors)

    id_factory = planning_run_id_factory or _new_planning_run_id
    new_planning_run_id = str(id_factory() or "")
    try:
        validated_id = ConfirmedFrom(
            planning_run_id=new_planning_run_id,
            draft_digest=draft_digest,
        ).planning_run_id
    except ValidationError as exc:
        raise ValueError("Regenerate 未能生成有效的 PlanningRun ID。") from exc
    if validated_id == planning_run_id:
        raise ValueError("Regenerate 必须创建不同于旧草稿的 PlanningRun ID。")

    formal_path = build_task_plan_json_path(state)
    fresh_formal = load_confirmed_build_task_plan(workspace_root(state))
    if fresh_formal is None and (formal_path.exists() or formal_path.is_symlink()):
        raise ValueError("当前 Formal build-task-plan.json 不是有效 ConfirmedPlan。")
    inputs = SequentialPlanningInputs.model_validate(
        current_inputs_factory(deepcopy(fresh_formal) if fresh_formal is not None else None)
    )
    if plain_json(inputs.base_confirmed_plan) != fresh_formal:
        raise ValueError("Regenerate 正式输入没有使用刚重载的当前 ConfirmedPlan。")

    planned = await plan_dag_sequential(
        inputs,
        workspace_state=state,
        planning_run_id=validated_id,
        workflow_run_id=workflow_run_id,
        thread_id=thread_id,
        policy=policy,
        settings=settings,
        generate_once=generate_once,
        publish=publish,
    )
    run = planned.planning_run
    write_pending_build_task_plan_atomic(
        state,
        plain_json(planned.assembly.assembled_plan),
        planning_run_id=run.planning_run_id,
        base_confirmed_plan_digest=run.base_confirmed_plan_digest,
        input_fingerprint=run.input_fingerprint,
        build_execution_scope=plain_json(run.build_execution_scope),
        created_at=run.updated_at,
    )
    pending = load_pending_build_task_plan(state)
    if pending is None:
        raise RuntimeError("Regenerate 成功后没有生成 PendingPlan。")
    identity = validate_pending_self_digest(pending)
    return RegeneratePendingResult(
        status="regenerated",
        planning_run=run,
        draft_identity=identity,
    )
