"""T7.5 Pending 消费与全新 concurrency=1 PlanningRun 编排适配。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.config import Settings
from app.services.build_task_plan_lifecycle import (
    ConfirmedFrom,
    DraftIdentity,
    RegeneratePendingResult,
    abandon_pending_build_task_plan,
)
from app.services.dag_planning_inputs import SequentialPlanningInputs
from app.services.dag_planning_orchestrator import DagPlanningError, plan_dag_sequential
from app.services.planning_frozen import plain_json
from app.services.planning_run_contracts import PlanningRunProjection
from app.services.build_task_planning_service import (
    persist_planning_recovery_if_applicable,
    _load_recovery_for_retry,
)
from app.services.planning_run_controller import SnapshotPublisher
from app.services.unit_generation_contracts import (
    UnitGenerationAttemptResult,
    UnitGenerationPolicy,
)
from app.workspace.spec_documents import workspace_root
from app.workspace.spec_documents import workflow_artifact_root
from app.workspace.json_documents import write_json_atomic
from app.workspace.planning_run_documents import load_planning_run
from app.workspace.task_documents import (
    build_planning_provenance,
    build_task_plan_json_path,
    load_confirmed_build_task_plan,
    load_pending_build_task_plan,
    build_task_plan_lifecycle_lock,
    validate_pending_self_digest,
    write_pending_build_task_plan_atomic,
)


def _new_planning_run_id() -> str:
    """只在后端为 Regenerate 分配不可复用的新 PlanningRun ID。"""

    return f"planning-{uuid4().hex}"


def _regeneration_fact_path(state: dict[str, Any], digest: str) -> Path:
    """以经过 DraftIdentity 校验的旧摘要定位本次 Regenerate 操作事实。"""

    ConfirmedFrom(planning_run_id="path-check", draft_digest=digest)
    return workflow_artifact_root(state) / "runtime" / "dag-regeneration" / f"{digest}.json"


def _load_regeneration_fact(state: dict[str, Any], digest: str) -> dict[str, Any] | None:
    """读取服务端提交的操作事实；无记录与损坏记录严格区分。"""

    try:
        value = json.loads(_regeneration_fact_path(state, digest).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "dag-regeneration.v1"
        or value.get("draft_digest") != digest
        or value.get("phase") not in {"prepared", "consumed", "committed"}
    ):
        raise ValueError("Regenerate 操作事实无效。")
    return value


def _write_regeneration_fact(state: dict[str, Any], fact: dict[str, Any]) -> None:
    """在不可逆的 Pending 删除前后原子记录操作阶段和执行身份。"""

    write_json_atomic(_regeneration_fact_path(state, fact["draft_digest"]), fact)


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
    recovery_source_workflow_run_id: str | None = None,
) -> RegeneratePendingResult:
    """消费精确旧 Pending，并从刚重载的 Formal 输入启动全新有限并发 PlanningRun。

    删除旧 Pending 是不可回滚的生命周期提交点。其后 Formal/input reload、Planning、
    模型调用或新 Pending 写入任一步失败，都向调用方传播异常且绝不恢复旧草稿。
    """

    try:
        request = ConfirmedFrom(planning_run_id=planning_run_id, draft_digest=draft_digest)
    except ValidationError:
        return RegeneratePendingResult(status="stale_draft")
    with build_task_plan_lifecycle_lock(workspace_root(state)):
        fact = _load_regeneration_fact(state, request.draft_digest)
        if fact is None:
            pending = load_pending_build_task_plan(state)
            try:
                identity = validate_pending_self_digest(pending) if pending else None
            except (ValueError, TypeError):
                identity = None
            if identity is None or identity.planning_run_id != planning_run_id or identity.draft_digest != draft_digest:
                return RegeneratePendingResult(status="stale_draft")
            if state.get("build_execution_scope") and dict(identity.build_execution_scope) != state["build_execution_scope"]:
                return RegeneratePendingResult(status="stale_draft")
            if state.get("owner_session_id") and identity.owner_session_id != state["owner_session_id"]:
                return RegeneratePendingResult(status="stale_draft")
            fact = {
                "schema_version": "dag-regeneration.v1",
                "planning_run_id": planning_run_id,
                "draft_digest": draft_digest,
                "draft_identity": identity.model_dump(mode="json"),
                "current_workflow_run_id": workflow_run_id,
                "thread_id": thread_id,
                "phase": "prepared",
                "generated_planning_run_id": "",
            }
            _write_regeneration_fact(state, fact)
        else:
            if (
                fact.get("planning_run_id") != planning_run_id
                or fact.get("draft_digest") != draft_digest
                or fact.get("thread_id") != thread_id
                or not recovery_source_workflow_run_id
                or fact.get("current_workflow_run_id") != recovery_source_workflow_run_id
            ):
                return RegeneratePendingResult(status="stale_draft")
            identity = DraftIdentity.model_validate(fact.get("draft_identity"))
            if identity.planning_run_id != planning_run_id or identity.draft_digest != draft_digest:
                raise ValueError("Regenerate 操作事实与旧草稿身份不一致。")
            if dict(identity.build_execution_scope) != state.get("build_execution_scope"):
                return RegeneratePendingResult(status="stale_draft")
            fact["current_workflow_run_id"] = workflow_run_id
            _write_regeneration_fact(state, fact)

        current_pending = load_pending_build_task_plan(state)
        if fact["phase"] == "prepared":
            if current_pending is not None:
                abandoned = abandon_pending_build_task_plan(
                    state, planning_run_id=planning_run_id,
                    draft_digest=draft_digest, record_lifecycle=False,
                )
                if abandoned.status != "abandoned":
                    return RegeneratePendingResult(status="stale_draft", errors=abandoned.errors)
            fact["phase"] = "consumed"
            _write_regeneration_fact(state, fact)
        elif current_pending is not None:
            pending_identity = validate_pending_self_digest(current_pending)
            if pending_identity.planning_run_id != fact.get("generated_planning_run_id"):
                return RegeneratePendingResult(status="stale_draft")
            run_payload = load_planning_run(state)
            if not isinstance(run_payload, dict) or run_payload.get("planning_run_id") != pending_identity.planning_run_id:
                raise ValueError("Regenerate 已提交 Pending 缺少对应 PlanningRun。")
            fact["phase"] = "committed"
            _write_regeneration_fact(state, fact)
            return RegeneratePendingResult(
                status="regenerated", planning_run=PlanningRunProjection.model_validate(run_payload),
                draft_identity=pending_identity,
            )
        if fact["phase"] == "committed":
            return RegeneratePendingResult(status="stale_draft")
        owner_session_id = identity.owner_session_id

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
    fact["generated_planning_run_id"] = validated_id
    _write_regeneration_fact(state, fact)

    formal_path = build_task_plan_json_path(state)
    fresh_formal = load_confirmed_build_task_plan(workspace_root(state))
    if fresh_formal is None and (formal_path.exists() or formal_path.is_symlink()):
        raise ValueError("当前 Formal build-task-plan.json 不是有效 ConfirmedPlan。")
    inputs = SequentialPlanningInputs.model_validate(
        current_inputs_factory(deepcopy(fresh_formal) if fresh_formal is not None else None)
    )
    if plain_json(inputs.base_confirmed_plan) != fresh_formal:
        raise ValueError("Regenerate 正式输入没有使用刚重载的当前 ConfirmedPlan。")

    try:
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
            recovery_snapshot=_load_recovery_for_retry(state, recovery_source_workflow_run_id),
        )
    except DagPlanningError as exc:
        persist_planning_recovery_if_applicable(
            state,
            exc,
            owner_session_id=owner_session_id,
        )
        raise
    run = planned.planning_run
    assembled_plan = plain_json(planned.assembly.assembled_plan)
    planning_provenance = build_planning_provenance(
        assembled_plan,
        planned.assembly.retained_task_ids,
        planned.assembly.review_task_ids,
        planned.assembly.reused_task_ids,
        planned.assembly.platform_task_ids,
    )
    write_pending_build_task_plan_atomic(
        state,
        assembled_plan,
        owner_session_id=owner_session_id,
        planning_run_id=run.planning_run_id,
        workflow_run_id=run.workflow_run_id,
        base_confirmed_plan_digest=run.base_confirmed_plan_digest,
        input_fingerprint=run.input_fingerprint,
        build_execution_scope=plain_json(run.build_execution_scope),
        created_at=run.updated_at,
        planning_provenance=planning_provenance,
    )
    pending = load_pending_build_task_plan(state)
    if pending is None:
        raise RuntimeError("Regenerate 成功后没有生成 PendingPlan。")
    identity = validate_pending_self_digest(pending)
    fact["phase"] = "committed"
    _write_regeneration_fact(state, fact)
    return RegeneratePendingResult(
        status="regenerated",
        planning_run=run,
        draft_identity=identity,
    )
