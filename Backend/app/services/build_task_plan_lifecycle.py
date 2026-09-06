"""BuildTaskPlan 草稿身份、Confirm 原子提升与 Abandon；不处理重新生成。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import StringConstraints, ValidationError

from app.services.dag_planning_inputs import SequentialPlanningInputs, _input_digest
from app.services.build_task_planner import replace_build_task_plan_tasks
from app.services.planning_frozen import FrozenJsonObject, FrozenPlanningModel, plain_json
from app.services.planning_run_contracts import PlanningRun


_Identifier = Annotated[str, StringConstraints(min_length=1, pattern=r"^\S(?:.*\S)?$")]
_Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DraftIdentity(FrozenPlanningModel):
    """绑定用户眼前 PendingPlan 的服务端身份与稳定内容摘要。"""

    planning_run_id: _Identifier
    draft_digest: _Sha256
    base_confirmed_plan_digest: _Sha256 | None
    input_fingerprint: _Sha256
    build_execution_scope: FrozenJsonObject
    created_at: _Identifier


class ConfirmedFrom(FrozenPlanningModel):
    """保存精确确认请求，供成功重试识别已提交的 Formal。"""

    planning_run_id: _Identifier
    draft_digest: _Sha256


class ConfirmPromotionResult(FrozenPlanningModel):
    """区分业务拒绝、提交成功及提交后的 Pending 清理故障。"""

    status: Literal["confirmed", "already_confirmed", "stale_draft", "stale_base", "stale_inputs", "invalid_dag"]
    confirmed_plan: FrozenJsonObject | None = None
    pending_cleanup_error: str | None = None
    errors: tuple[str, ...] = ()


class AbandonPendingResult(FrozenPlanningModel):
    """区分成功删除、无 Pending 与身份已过期三类 Abandon 结果。"""

    status: Literal["abandoned", "no_pending", "stale_draft"]
    errors: tuple[str, ...] = ()


class RegeneratePendingResult(FrozenPlanningModel):
    """返回 Regenerate 的新 Run 与新 Pending identity，或明确拒绝旧草稿请求。"""

    status: Literal["regenerated", "stale_draft"]
    planning_run: PlanningRun | None = None
    draft_identity: DraftIdentity | None = None
    errors: tuple[str, ...] = ()


def abandon_pending_build_task_plan(
    state: dict[str, Any],
    *,
    planning_run_id: str,
    draft_digest: str,
) -> AbandonPendingResult:
    """仅删除自摘要有效且精确匹配请求身份的当前 PendingPlan。"""

    from app.workspace.task_documents import (
        build_task_plan_lifecycle_lock,
        build_task_plan_pending_json_path,
        load_pending_build_task_plan,
        validate_pending_self_digest,
    )

    try:
        request = ConfirmedFrom(
            planning_run_id=planning_run_id,
            draft_digest=draft_digest,
        )
    except ValidationError:
        return AbandonPendingResult(
            status="stale_draft",
            errors=("放弃请求缺少有效 Draft identity。",),
        )

    with build_task_plan_lifecycle_lock:
        try:
            pending = load_pending_build_task_plan(state)
            if pending is None:
                return AbandonPendingResult(status="no_pending")
            if not _matches_request(pending.get("draft_identity"), request):
                return AbandonPendingResult(status="stale_draft")
            validate_pending_self_digest(pending)
        except (ValueError, TypeError) as exc:
            return AbandonPendingResult(status="stale_draft", errors=(str(exc),))

        build_task_plan_pending_json_path(state).unlink()
        return AbandonPendingResult(status="abandoned")


def _dag_gate_errors(plan: dict, inputs: SequentialPlanningInputs) -> list[str]:
    """只读复核原稿的图与合同；编译检查结果绝不覆盖用户确认的任务正文。"""

    graph = plan.get("task_graph")
    validation = graph.get("validation") if isinstance(graph, dict) else None
    execution = plan.get("execution")
    if (plan.get("schema_version") != "build-dag.v3" or plan.get("status") != "ready"
            or plan.get("confirmation_status") != "pending"
            or not isinstance(validation, dict) or validation.get("is_valid") is not True
            or validation.get("errors") or not isinstance(execution, dict)
            or execution.get("blocked_batches")
            or not isinstance(execution.get("batches"), list)
            or any(not isinstance(batch, dict) or batch.get("mode") == "blocked"
                   for batch in execution["batches"])):
        return ["Pending DAG 未通过 ready/validation/execution 门禁。"]
    registry = plan.get("task_registry")
    units = plan.get("build_units")
    if (not isinstance(registry, dict) or not isinstance(units, dict)
            or any(not isinstance(task, dict) or task.get("id") != key
                   or not isinstance(task.get("unit_id"), str)
                   or task["unit_id"] not in units for key, task in registry.items())):
        return ["Pending DAG 的 Task registry 或 Unit 归属无效。"]
    baseline = plain_json(inputs.base_confirmed_plan) or {}
    retained_ids = set(baseline.get("task_registry", {}))
    if not retained_ids <= set(registry):
        return ["Pending DAG 缺少正式基线中的 retained Task。"]
    context = {
        **plain_json(inputs.build_context),
        "project_plan": plain_json(inputs.project_plan),
        "executable_details": plain_json(inputs.project_plan.get("executable_details")
                                         or inputs.build_context.get("executable_details") or {}),
        "_validate_task_scope": False,
        "_allow_missing_business_deliverable_task_ids": sorted(retained_ids),
        "_compile_auth_capability_dependencies": True,
        "external_capabilities": [item.model_dump(mode="json") for item in inputs.reuse_facts.external_capabilities],
    }
    # 保留全部 Task 合同，不调用候选修复/补齐；只消费重新计算的语义和拓扑结论。
    try:
        checked = replace_build_task_plan_tasks(
            deepcopy(plan), deepcopy(list(registry.values())), context,
            preserve_task_contract_ids=set(registry),
        )
    except (ValueError, TypeError, KeyError) as exc:
        return [f"Pending DAG 无法完成校验：{exc}"]
    errors = list(checked["task_graph"]["validation"]["errors"])
    if checked["execution"]["blocked_batches"]:
        errors.append("Pending DAG 重算后存在 blocked batches。")
    # canonical digest 不依赖 JSON 对象键顺序；图校验也不能依赖 registry 插入顺序。
    for field in ("nodes", "topological_order"):
        values = graph.get(field)
        if (not isinstance(values, list) or any(not isinstance(value, str) for value in values)
                or sorted(values) != sorted(registry)):
            errors.append(f"Pending DAG 的 {field} 与 Task registry 不一致。")
    edges = graph.get("edges")
    if (not isinstance(edges, list) or any(not isinstance(edge, dict) for edge in edges)
            or sorted(edges, key=_input_digest) != sorted(checked["task_graph"]["edges"], key=_input_digest)):
        errors.append("Pending DAG 的 edges 与 Task registry 不一致。")
    if not errors:
        positions = {key: index for index, key in enumerate(graph["topological_order"])}
        if any(positions[edge["from"]] >= positions[edge["to"]] for edge in edges):
            errors.append("Pending DAG 的 topological_order 违反依赖顺序。")
    return errors


def _cleanup_matching_pending(state: dict, request: ConfirmedFrom) -> str | None:
    """只清理摘要自洽且匹配本次请求的 Pending，绝不删除较新的草稿。"""

    from app.workspace.task_documents import (
        build_task_plan_pending_json_path, load_pending_build_task_plan, validate_pending_self_digest,
    )

    try:
        pending = load_pending_build_task_plan(state)
        if pending is None or not _matches_request(pending.get("draft_identity"), request):
            return None
        validate_pending_self_digest(pending)
        build_task_plan_pending_json_path(state).unlink(missing_ok=True)
    except (OSError, ValueError, TypeError) as exc:
        return str(exc)
    return None


def _matches_request(value: Any, request: ConfirmedFrom) -> bool:
    """仅精确比较 Run 与 digest，不修剪、转换或采用旧版本别名。"""

    return (isinstance(value, dict) and value.get("planning_run_id") == request.planning_run_id
            and value.get("draft_digest") == request.draft_digest)


def confirm_pending_build_task_plan(
    state: dict[str, Any], *, planning_run_id: str, draft_digest: str,
    current_inputs: SequentialPlanningInputs,
) -> ConfirmPromotionResult:
    """核验当前文件并原子提升 Pending；Formal 写入失败抛 OSError，成功后不回滚。

    current_inputs 必须由服务端从当前正式输入重新构造，使用 Planning 相同的冻结 DTO
    和摘要算法，不接受前端指纹或 checkpoint。只在同一后端进程内串行化 Pending writer
    和 Confirm；调用方负责正式输入稳定性，不提供跨进程或外部编辑器事务。
    当前服务尚未接入旧 tasks.py/AG-UI adapter，不提供新的 HTTP 产品接口。
    """

    # 延迟导入避免 task_documents 的 DraftIdentity 类型依赖形成循环。
    from app.workspace.json_documents import write_json_atomic
    from app.workspace.task_documents import (
        build_task_plan_json_path, build_task_plan_lifecycle_lock, build_task_plan_sha256,
        load_confirmed_build_task_plan, load_pending_build_task_plan, validate_pending_self_digest,
    )
    from app.workspace.spec_documents import workspace_root

    try:
        request = ConfirmedFrom(planning_run_id=planning_run_id, draft_digest=draft_digest)
    except ValidationError:
        return ConfirmPromotionResult(status="stale_draft", errors=("确认请求缺少有效 Draft identity。",))

    with build_task_plan_lifecycle_lock:
        path = build_task_plan_json_path(state)
        try:
            formal = load_confirmed_build_task_plan(workspace_root(state))
            invalid_formal = formal is None and (path.exists() or path.is_symlink())
        except (ValueError, TypeError):
            formal, invalid_formal = None, True

        # Formal replace 是提交点：重试不能因旧 baseline 或新 inputs 再次提升。
        if formal is not None and _matches_request(formal.get("confirmed_from"), request):
            return ConfirmPromotionResult(
                status="already_confirmed", confirmed_plan=formal,
                pending_cleanup_error=_cleanup_matching_pending(state, request),
            )
        try:
            pending = load_pending_build_task_plan(state)
            if pending is None or not _matches_request(pending.get("draft_identity"), request):
                return ConfirmPromotionResult(status="stale_draft")
            identity = validate_pending_self_digest(pending)
        except (ValueError, TypeError) as exc:
            return ConfirmPromotionResult(status="stale_draft", errors=(str(exc),))

        base_digest = build_task_plan_sha256(formal) if formal is not None else None
        if invalid_formal or identity.base_confirmed_plan_digest != base_digest:
            return ConfirmPromotionResult(status="stale_base")
        try:
            inputs = SequentialPlanningInputs.model_validate(current_inputs)
            supplied_base = _input_digest(inputs.base_confirmed_plan) if inputs.base_confirmed_plan is not None else None
            if (supplied_base != base_digest or _input_digest(inputs.model_dump(mode="json")) != identity.input_fingerprint
                    or inputs.build_execution_scope != identity.build_execution_scope
                    or ("build_execution_scope" in pending
                        and pending["build_execution_scope"] != plain_json(identity.build_execution_scope))):
                return ConfirmPromotionResult(status="stale_inputs")
        except (ValueError, TypeError) as exc:
            return ConfirmPromotionResult(status="stale_inputs", errors=(str(exc),))

        errors = _dag_gate_errors(pending, inputs)
        if errors:
            return ConfirmPromotionResult(status="invalid_dag", errors=tuple(errors))
        confirmed = deepcopy(pending)
        confirmed.pop("draft_identity")
        confirmed.update(
            confirmation_status="confirmed", confirmed_at=datetime.now(UTC).isoformat(),
            confirmed_from=request.model_dump(mode="json"),
            build_execution_scope=plain_json(identity.build_execution_scope),
        )
        write_json_atomic(path, confirmed)
        return ConfirmPromotionResult(
            status="confirmed", confirmed_plan=confirmed,
            pending_cleanup_error=_cleanup_matching_pending(state, request),
        )
