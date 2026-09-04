"""面向 production Workflow adapter 的 Mainline DAG Planning 业务边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal
from uuid import uuid4

from pydantic import model_validator

from app.config import Settings
from app.services.build_task_plan_lifecycle import DraftIdentity
from app.services.dag_planning_inputs import MainlinePlanningInputs
from app.services.dag_planning_orchestrator import (
    ValidatedAssembledPlan,
    plan_dag_sequential,
)
from app.services.planning_frozen import (
    FrozenJsonObject,
    FrozenPlanningModel,
    plain_json,
)
from app.services.planning_run_controller import SnapshotPublisher
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.task_documents import (
    build_task_plan_lifecycle_lock,
    load_pending_build_task_plan,
    validate_pending_self_digest,
    write_pending_build_task_plan_atomic,
)


class PendingPlanPersistenceResult(FrozenPlanningModel):
    """返回现有 Pending writer 落盘后自校验通过的内容与身份。"""

    pending_plan_path: str
    pending_plan: FrozenJsonObject
    draft_identity: DraftIdentity


class MainlinePlanningResult(FrozenPlanningModel):
    """返回已验证 PlanningRun、PendingPlan 及其唯一草稿身份。"""

    planning_run_id: str
    planning_status: Literal["validated"] = "validated"
    terminal_status: Literal["pending_confirmation"] = "pending_confirmation"
    pending_plan_path: str
    pending_plan: FrozenJsonObject
    draft_identity: DraftIdentity
    validated_assembled_plan: ValidatedAssembledPlan

    @model_validator(mode="after")
    def validate_identity(self) -> "MainlinePlanningResult":
        """拒绝 Run、Pending 与结果 DTO 之间的身份错配。"""

        run = self.validated_assembled_plan.planning_run
        if (
            self.planning_run_id != run.planning_run_id
            or self.draft_identity.planning_run_id != run.planning_run_id
            or self.pending_plan.get("draft_identity")
            != self.draft_identity.model_dump(mode="json")
        ):
            raise ValueError("Mainline Planning 结果中的 Run 与 DraftIdentity 不一致。")
        return self


def _new_planning_run_id() -> str:
    """为一次 mainline service 调用分配后端拥有的 PlanningRun ID。"""

    return f"planning-{uuid4().hex}"


def _persist_validated_pending_plan(
    state: dict[str, Any],
    planned: ValidatedAssembledPlan,
    *,
    owner_session_id: str,
) -> PendingPlanPersistenceResult:
    """通过唯一 Pending storage authority 写入，并在同一锁内回读身份。"""

    run = planned.planning_run
    with build_task_plan_lifecycle_lock:
        pending_path = write_pending_build_task_plan_atomic(
            state,
            plain_json(planned.assembly.assembled_plan),
            owner_session_id=owner_session_id,
            planning_run_id=run.planning_run_id,
            base_confirmed_plan_digest=run.base_confirmed_plan_digest,
            input_fingerprint=run.input_fingerprint,
            build_execution_scope=plain_json(run.build_execution_scope),
            created_at=run.updated_at,
        )
        pending = load_pending_build_task_plan(state)
        if pending is None:
            raise RuntimeError("Mainline Planning 成功后没有生成 PendingPlan。")
        identity = validate_pending_self_digest(pending)
    if identity.planning_run_id != run.planning_run_id:
        raise RuntimeError("PendingPlan 的 PlanningRun identity 与本次规划不一致。")
    return PendingPlanPersistenceResult(
        pending_plan_path=pending_path,
        pending_plan=pending,
        draft_identity=identity,
    )


async def run_mainline_planning(
    inputs: MainlinePlanningInputs,
    *,
    workspace_state: Mapping[str, Any],
    policy: UnitGenerationPolicy,
    settings: Settings | None = None,
    generate_once: Callable[
        ..., Awaitable[UnitGenerationAttemptResult]
    ] | None = None,
    publish: SnapshotPublisher | None = None,
) -> MainlinePlanningResult:
    """执行完整 PlanningRun，并仅在全局验证成功后原子写入 PendingPlan。

    Workflow adapter 只提供已确认的正式上下文与运行身份；本服务拥有 PlanningRun
    身份分配、Scheduler orchestration 和 Pending persistence 顺序。异常与取消均原样
    向上传播，且不会执行 Pending writer。
    """

    frozen = MainlinePlanningInputs.model_validate(inputs)
    planning_run_id = _new_planning_run_id()
    planned = await plan_dag_sequential(
        frozen.sequential_inputs(),
        workspace_state=workspace_state,
        planning_run_id=planning_run_id,
        workflow_run_id=frozen.workflow_run_id,
        thread_id=frozen.thread_id,
        policy=policy,
        settings=settings,
        generate_once=generate_once,
        publish=publish,
    )
    persisted = _persist_validated_pending_plan(
        dict(workspace_state),
        planned,
        owner_session_id=frozen.owner_session_id,
    )
    return MainlinePlanningResult(
        planning_run_id=planning_run_id,
        pending_plan_path=persisted.pending_plan_path,
        pending_plan=persisted.pending_plan,
        draft_identity=persisted.draft_identity,
        validated_assembled_plan=planned,
    )
