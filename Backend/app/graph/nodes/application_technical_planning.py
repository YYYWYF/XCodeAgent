"""Application Planning TechnicalPlan 的事务化 Graph 节点。"""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_revision import RevisionTarget
from app.domain.application_planning_recovery import (
    ApplicationPlanningRecoveryBoundary,
    ApplicationPlanningOperation,
    application_planning_boundary_payload,
    application_planning_sha256,
    parse_application_planning_boundary,
    validate_application_planning_baseline,
)
from app.graph.nodes.common import workspace_from_state

logger = logging.getLogger("uvicorn.error")
from app.graph.state import ProjectState
from app.services.technical_plan_fragments import TechnicalPlanFragmentStore
from app.services.requirement_spec_carryover import read_requirement_spec_carryover
from app.services.technical_plan_carryover import (
    drop_generated_fields,
    read_technical_plan_carryover,
)
from app.services.technical_plan_content_changes import (
    direct_endpoint_revision_targets, direct_revision_schema_targets,
)
from app.services.technical_plan_merger import (
    merge_direct_endpoint_revision,
    merge_technical_plan,
    relevant_object_ids,
    strip_change_status,
    technical_plan_revision_diff,
)
from app.services.application_lifecycle import (
    application_lifecycle_payload,
    load_application_lifecycle,
    persist_application_lifecycle_transition,
)
from app.services.application_planning_persistence import (
    confirm_application_planning_artifacts,
)
from app.services.development_change_snapshot import write_change_snapshot
from app.services.application_revision_lifecycle import (
    begin_technical_plan_revision_generation,
    ensure_technical_plan_generation_lifecycle,
    update_active_revision_progress,
)
from app.workspace.plan_documents import (
    commit_technical_plan_document,
    current_technical_plan_json_path,
    load_project_plan_json,
)


def _deleted_page_ids(state: ProjectState) -> set[str]:
    """取本轮需求标记为删除的页面 ID。

    优先读 `requirement_spec_diff`（带标记的展示版）；没有时回退读 `requirement_spec`
    上的标记（部分路径直接沿用带标记的规格）。两者都是同一份需求事实。
    """

    for value in (state.get("requirement_spec_diff"), state.get("requirement_spec")):
        pages = value.get("pages") if isinstance(value, dict) else None
        if not isinstance(pages, list):
            continue
        deleted = {
            str(page.get("pageId") or "").strip()
            for page in pages
            if isinstance(page, dict)
            and str(page.get("_change_status") or "") == "deleted"
            and str(page.get("pageId") or "").strip()
        }
        if deleted:
            return deleted
    return set()


def _technical_plan_transaction_baseline(state: ProjectState, workspace: str) -> dict[str, Any]:
    """取 TechnicalPlan **事务恢复基线**：优先 state 中的上一版，缺失时回退读磁盘。

    这份基线用于 boundary 摘要、续跑和冷进入判定，回答“本次生成从哪一版继续修改”。
    它不等于审阅差异用的迭代初始基线：二次修订时上一版已经包含本迭代新增内容，
    如果也拿它做变更对比，会把较早新增的对象错误显示为未变更。

    **不能直接返回 `state["technical_plan"]`**：换分支派生新迭代时，checkpoint 里可能
    残留更早一轮的旧技术规划，它描述的是别的分支的现状，拿来当基线会把整份计划标错。
    只有基线变更块（`technical_plan_baseline_origin`）标记为 `resumed` 时才沿用 state；
    其余情况一律以磁盘 / 交接记录为准。没有磁盘依据时返回空 dict，按首轮创建处理。

    首轮要显示"全部新增"是**展示层**的事：前端在基线为空时把每个对象都视为新增，
    见 `TechnicalPlanReviewPanel`。合并逻辑里则由 `merge_technical_plan(None, ...)`
    标记 added，同样不依赖这个字段。
    """

    if state.get("technical_plan_baseline_origin") == "resumed":
        current = state.get("technical_plan")
        if isinstance(current, dict) and current:
            return drop_generated_fields(deepcopy(current))
    # 校验通过的 state 规划就是本事务的基线：begin 冷进入时读到它，说明它还没被
    # 新一轮生成覆盖（derive 分支残留的旧规划已在 derive 阶段清空）。校验没过时
    # 上面的异常已经抛出，不会走到这里，因此这里取 state 不会绕过基线一致性校验。
    current = state.get("technical_plan")
    if isinstance(current, dict) and current:
        return drop_generated_fields(deepcopy(current))
    path = current_technical_plan_json_path(state)
    if path.is_file():
        plan = load_project_plan_json(path, hydrate_detail_designs=True)
        if isinstance(plan, dict) and plan:
            return drop_generated_fields(plan)
    carried = read_technical_plan_carryover(workspace)
    return drop_generated_fields(carried) if carried else {}


def _technical_plan_review_baseline(
    state: ProjectState,
    workspace: str,
    *,
    transaction_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """取本轮变更审阅的**迭代初始基线**，与 RequirementSpec 保持同口径。

    `technical-plan-carryover.json` 在新迭代开始时冻结，之后本轮多次设计修订、
    技术修订和开发续接都不会推进它。因此第一次新增的页面与第二次新增的页面，
    相对同一个上迭代基线都会标 added，而不会出现第二次修订把第一次新增洗成
    unchanged 的不一致。没有交接记录时回退到事务基线，保持首轮创建行为。
    """

    carried = read_technical_plan_carryover(workspace)
    if carried:
        return drop_generated_fields(carried)
    if transaction_baseline is not None:
        return transaction_baseline
    return _technical_plan_transaction_baseline(state, workspace)


def _technical_boundary(state: ProjectState):
    """读取当前 Graph State 中的 TechnicalPlan boundary。"""
    boundary = parse_application_planning_boundary(
        state.get("application_planning_recovery_boundary")
    )
    if boundary is None or boundary.artifact.value != "technical_plan":
        raise ValueError(
            "TechnicalPlan 缺少可验证的 application-planning-boundary，"
            "拒绝猜测恢复位置。"
        )
    return boundary


def _technical_request(state: ProjectState) -> str:
    """从当前交互或服务端设计变更上下文读取 TechnicalPlan 请求。"""

    interaction = state.get("application_planning_interaction")
    if isinstance(interaction, dict) and interaction.get("action") in {"answer", "revise", "design_change"}:
        return str(interaction.get("request") or "").strip()
    return str(
        state.get("design_change_generation_request")
        or state.get("design_change_request")
        or state.get("request")
        or ""
    ).strip()


def _ensure_technical_lifecycle(state: ProjectState):
    """读取 TechnicalPlan 所属的权威 ApplicationLifecycle。"""

    workspace = workspace_from_state(state)
    if not workspace:
        raise ValueError("TechnicalPlan 必须提供 workspaceRoot。")
    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None:
        raise ValueError("TechnicalPlan 缺少权威 ApplicationLifecycle。")
    return workspace, lifecycle


def _boundary_payload_for_state(
    state: ProjectState,
    *,
    boundary: ApplicationPlanningRecoveryBoundary,
    candidate: Any | None = None,
    drop_baseline: bool = False,
) -> dict[str, Any]:
    """沿用既有 operation identity 生成下一事务边界，避免重复分配操作。

    `drop_baseline` 用于冷进入清空 `technical_plan` 的场景：此时 state 里的旧规划
    已不再作为基线，boundary 若继续沿用旧摘要，续修时会拿着"有摘要但没有基线"
    的状态去校验，必然报 baseline 与 boundary 不匹配。
    """

    current = _technical_boundary(state)
    # 与校验侧（`validate_application_planning_baseline`）同口径：都按业务内容算摘要。
    # 确认界面的 state 计划可能是带 `_change_status` / `confirmation_status` 的展示版，
    # 直接算会让"同一份计划"在两侧得到不同摘要。
    baseline = (
        None
        if drop_baseline
        else drop_generated_fields(state.get("technical_plan"))
        if isinstance(state.get("technical_plan"), dict) and state.get("technical_plan")
        else None
    )
    payload = application_planning_boundary_payload(
        operation_id=current.operation_id,
        operation=ApplicationPlanningOperation(current.operation.value),
        boundary=boundary,
        request=_technical_request(state),
        gate_id=current.gate_id,
        artifact_revision=current.artifact_revision,
        baseline=baseline if isinstance(baseline, dict) and baseline else None,
        candidate=candidate,
    )
    # 后续 boundary 必须沿用同一个事务的 request/baseline identity，不能因 State
    # 在 begin 或 commit 期间改变展示字段而重新计算成另一笔操作。
    payload["requestSha256"] = current.request_sha256
    if current.baseline_sha256 is None or drop_baseline:
        payload.pop("baselineSha256", None)
    else:
        payload["baselineSha256"] = current.baseline_sha256
    return payload


async def technical_planning_begin(state: ProjectState) -> dict[str, Any]:
    """校验 TechnicalPlan 输入事务并幂等推进到模型生成阶段。"""

    boundary = _technical_boundary(state)
    if boundary.boundary is not ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED:
        raise ValueError("TechnicalPlan begin 只能从 INPUT_COMMITTED boundary 开始。")
    workspace, _lifecycle = _ensure_technical_lifecycle(state)
    operation = boundary.operation
    request = _technical_request(state)
    if operation is ApplicationPlanningOperation.REVISE and not request:
        raise ValueError("TechnicalPlan revise 必须提供明确的修改要求。")
    validate_application_planning_baseline(boundary, state.get("technical_plan"))

    # 事务基线与审阅基线分开：前者跟随上一版计划用于恢复，后者在本迭代开始时冻结，
    # 保证多次修订的变更标记始终相对同一个上迭代版本计算。
    transaction_baseline = _technical_plan_transaction_baseline(state, workspace)
    technical_plan_baseline = _technical_plan_review_baseline(
        state, workspace, transaction_baseline=transaction_baseline,
    )
    # 沿用 state 里的规划当基线，说明这是同一轮已提交计划的续跑 / 重试；
    # 否则是冷进入（新迭代、换分支派生），state 里的旧规划不可信。
    # 比较前先剥掉展示 / 派生字段：`_technical_plan_transaction_baseline` 返回的已是干净版，
    # state 里可能是带 `_change_status` 的展示版，直接比较会因多出的标记误判成
    # "不是同一份"，把本应续跑的场景错当成冷进入而清空计划。
    state_plan_business = (
        drop_generated_fields(deepcopy(state.get("technical_plan")))
        if isinstance(state.get("technical_plan"), dict) and state.get("technical_plan")
        else {}
    )
    technical_plan_baseline_origin = (
        "resumed"
        if state.get("technical_plan_baseline_origin") == "resumed"
        or (
            bool(state_plan_business)
            and transaction_baseline == state_plan_business
        )
        else ""
    )

    change_id = str(state.get("change_id") or "").strip()
    repair_source = str(state.get("technical_plan_repair_source_run_id") or "")
    if repair_source and _lifecycle.initialization.stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN:
        # 阶段重试由原 checkpoint 证明来源，再用既有 CAS 把生成归属交给新 Run。
        ensure_technical_plan_generation_lifecycle(
            workspace, active_run_id=state.get("active_run_id"),
            thread_id=state.get("active_thread_id"), change_id=change_id or None,
            repair_source_run_id=repair_source,
        )
    # 需求/设计正式修订已经拥有自己的 revision，不得再次按技术专项修订入场。
    active_revision = _lifecycle.active_formal_revision
    design_revision = (
        active_revision is not None
        and active_revision.change_id == change_id
        and active_revision.formal_branch.value == "design_stage_revision"
        and active_revision.status == "design_planning"
    )
    if operation is ApplicationPlanningOperation.REVISE and change_id and not design_revision:
        target = state.get("change_target")
        if not isinstance(target, dict) or not target:
            raise ValueError("TechnicalPlan formal revision 缺少 change target。")
        target_model = RevisionTarget.model_validate(target)
        lifecycle = begin_technical_plan_revision_generation(
            workspace,
            change_id=change_id,
            interaction_id=boundary.gate_id or "",
            request=request,
            target=target_model,
            thread_id=str(state.get("active_thread_id") or ""),
            active_run_id=str(state.get("active_run_id") or ""),
        )
    else:
        lifecycle = ensure_technical_plan_generation_lifecycle(
            workspace,
            active_run_id=state.get("active_run_id"),
            thread_id=state.get("active_thread_id"),
            change_id=change_id or None,
            repair_source_run_id=(
                state.get("technical_plan_repair_source_run_id")
                if operation is ApplicationPlanningOperation.REPAIR
                else None
            ),
        )
    # 冷进入 begin 且 state 残留了别的分支的旧规划：不能带着它进 generate，
    # 否则整份计划被当成"上一版现状"，合并后既丢了本轮产出、又全部标错。
    # 判定依据是基线取值来源，而不是"baseline 是否非空"——旧规划可能在下一轮
    # 已作废，derived 分支不会把它登记进交接记录，此时 state 有值但基线为空。
    cold_stale_plan = (
        technical_plan_baseline_origin != "resumed"
        and isinstance(state.get("technical_plan"), dict)
        and bool(state.get("technical_plan"))
    )
    return {
        "phase": "technical_planning_begin",
        "status": "running",
        # 每轮从已校验的输入捕获基线；后续候选提交不得覆盖它，冷进入沿 checkpoint 读取。
        "technical_plan_baseline": technical_plan_baseline,
        # 基线来源：本轮已写入 state 的规划视为沿用旧值，冷进入 / 新迭代按磁盘重算。
        "technical_plan_baseline_origin": technical_plan_baseline_origin,
        **((
            {"technical_plan": {}, "project_plan": {}}
        ) if cold_stale_plan else {}),
        "resume_from": "",
        "application_planning_recovery_boundary": _boundary_payload_for_state(
            state,
            boundary=ApplicationPlanningRecoveryBoundary.GENERATION_READY,
            # 旧规划被清空时必须同步去掉 boundary 里的基线摘要，否则后续续修
            # 会以"有摘要、无基线"的状态校验失败。
            drop_baseline=cold_stale_plan,
        ),
        "application_planning_interaction": {},
        "technical_plan_candidate": {},
        "technical_plan_candidate_sha256": "",
        "technical_plan_repair_source_run_id": "",
        "lifecycle": application_lifecycle_payload(lifecycle),
        "timeline": ["technical_planning_begin"],
    }


def technical_planning_generate(state: ProjectState) -> dict[str, Any]:
    """生成和校验候选并保存内部片段，不写正式文档或消费确认。"""

    boundary = _technical_boundary(state)
    if boundary.boundary is not ApplicationPlanningRecoveryBoundary.GENERATION_READY:
        raise ValueError("TechnicalPlan generate 只能从 GENERATION_READY boundary 开始。")
    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        raise ValueError("TechnicalPlan 必须读取已确认的 RequirementSpec。")

    # 仅在模型节点读取规划助手；这些 import 不能移动到 begin/commit，确保副作用边界清晰。
    from app.graph.nodes.planning import (
        _attach_technical_plan_contracts,
        _generate_valid_technical_plan,
        _project_plan_validation_errors,
        _technical_planning_requirement_spec,
    )

    technical_requirement = _technical_planning_requirement_spec(
        state,
        requirement_spec,
    )
    request = _technical_request(state)
    if request:
        technical_requirement = {
            **technical_requirement,
            "planning_adjustment_request": request,
        }
    existing_plan = (
        deepcopy(state.get("technical_plan"))
        if isinstance(state.get("technical_plan"), dict)
        and state.get("technical_plan")
        else None
    )
    # 审阅与合并统一使用迭代初始基线，不能直接吃 `state["technical_plan"]`。
    # existing_plan 可以跟随上一版继续修改，但 baseline 必须保持在进入本迭代时的
    # 交接版本；否则二次修订会把本迭代较早新增的页面和接口错误标成 unchanged。
    # 它与 existing_plan 用途不同：
    # - existing_plan 交给模型当"继续修改的起点"，模型仍会整份重写；
    # - baseline 用于生成后做**保守合并**和本轮变更标记。
    workspace = workspace_from_state(state)
    baseline = _technical_plan_review_baseline(state, workspace) or None
    generation_fragments = {}
    # 直接修订及其失败重试使用当前计划匹配目标，不把设计派生或首轮需求当成接口授权。
    direct_revision = existing_plan is not None and boundary.operation in {
        ApplicationPlanningOperation.REVISE, ApplicationPlanningOperation.REPAIR,
    }
    targets, target_errors = (
        direct_endpoint_revision_targets(request, existing_plan)
        if direct_revision else ({}, [])
    )
    schema_targets, schema_errors = (
        direct_revision_schema_targets(request, existing_plan)
        if direct_revision else ({}, [])
    )
    target_errors.extend(schema_errors)
    if target_errors:
        # 生成前即挂既有 AG-UI 错误审阅门；不调用模型、不覆盖旧草稿、不进入确认成功。
        return _technical_generation_failure(state, target_errors, generation_fragments)
    if targets:
        technical_requirement = {
            **technical_requirement,
            "technical_plan_revision_scope": {
                "endpoints": list(targets.values()),
                "schemas": {key: sorted(names) for key, names in schema_targets.items()},
            },
        }
    plan, errors, failed_candidate = _generate_valid_technical_plan(
        state,
        technical_requirement,
        existing_plan,
        generation_fragments=generation_fragments,
    )
    if plan is None:
        # 生成失败不写磁盘、不改生命周期；审阅节点会把错误作为当前业务交互展示。
        return {
            "phase": "technical_planning_generate",
            "status": "requires_user_input",
            "technical_plan_repair_candidate": failed_candidate or {},
            "technical_plan_repair_source_run_id": "",
            "technical_plan_repair_errors": errors[:12],
            "technical_plan_generation_input_sha256": generation_fragments.get("input_sha256", ""),
            "application_planning_recovery_boundary": _boundary_payload_for_state(
                state,
                boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
            ),
            "technical_plan_candidate": {},
            "technical_plan_candidate_sha256": "",
            "clarification": _technical_generation_error_payload(errors),
            "timeline": ["technical_planning_generate"],
        }
    # 跨迭代增量合并：模型整份重新生成 technical-plan.json 时，会把继承自基线的对象
    # 也顺手重写一遍（措辞润色、删掉 error_codes、改 required_for_initial_load 等）。
    # 用户这轮没要求改的对象必须**原样继承基线**，否则下游会基于漂移的契约推进。
    # 与需求文档侧 requirement_spec_merger 同一口径：基线不动，只做按 ID 的增量合并。
    #
    # 合并产出两份：
    # - candidate：剥离 _change_status 的干净版，走原有校验 / 写盘 / 入库全流程；
    # - technical_plan_diff：带标记的展示版，仅存 state 供前端分区显示，不落盘。
    if targets or schema_targets:
        try:
            # 当前事务基线决定可修改内容；迭代初始基线仅重算展示，不能回退已采纳修订。
            controlled = merge_direct_endpoint_revision(
                existing_plan, plan, targets, schema_targets=schema_targets,
            )
            merged_with_status = technical_plan_revision_diff(baseline, controlled)
        except (TypeError, ValueError) as exc:
            return _technical_generation_failure(state, [str(exc)], generation_fragments)
    else:
        merged_with_status = merge_technical_plan(
            baseline,
            plan,
            relevant=relevant_object_ids(
                requirement_spec,
                state.get("product_plan"),
                baseline,
                # 上一轮的需求文档用于证明本轮对象变化，不以模型候选反证用户意图。
                read_requirement_spec_carryover(workspace) if workspace else None,
            ),
            deleted_page_ids=_deleted_page_ids(state),
        )
    candidate = deepcopy(strip_change_status(merged_with_status))
    # 模型候选通过校验不代表增量合并后仍有效：必须复用完整确认校验链，
    # 不合格的合并结果只进入错误审阅，不能写草稿后再让用户反复点击确认。
    try:
        validation_plan = _attach_technical_plan_contracts(state, candidate)
        errors = _project_plan_validation_errors(validation_plan, state)
    except (TypeError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        return {
            "phase": "technical_planning_generate",
            "status": "requires_user_input",
            "technical_plan_repair_candidate": candidate,
            "technical_plan_repair_errors": errors[:12],
            "technical_plan_generation_input_sha256": generation_fragments.get("input_sha256", ""),
            "application_planning_recovery_boundary": _boundary_payload_for_state(
                state, boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
            ),
            "technical_plan_candidate": {},
            "technical_plan_candidate_sha256": "",
            "technical_plan_candidate_diff": {},
            "clarification": _technical_generation_error_payload(errors),
            "timeline": ["technical_planning_generate"],
        }
    candidate_sha256 = application_planning_sha256(candidate)
    # 展示版必须随候选一起流转：它不参与校验，但 commit 落盘后前端读的是 `technical_plan`，
    # 只有独立字段才能带着 `_change_status` 抵达确认界面。它经 checkpoint 传递给 commit，
    # 绝不能放进 `technical_plan`（校验会拒绝该字段，下一轮基线也会被污染）。
    return {
        "phase": "technical_planning_generate",
        "status": "running",
        # 清理可能残留的展示态；本轮的展示版走 `technical_plan_candidate_diff`。
        "technical_plan": {},
        "project_plan": {},
        "technical_plan_candidate": candidate,
        "technical_plan_candidate_sha256": candidate_sha256,
        "technical_plan_candidate_diff": merged_with_status,
        "technical_plan_diff": merged_with_status,
        "application_planning_recovery_boundary": _boundary_payload_for_state(
            state,
            boundary=ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED,
            candidate=candidate,
        ),
        "technical_plan_repair_candidate": {},
        "technical_plan_repair_errors": [],
        "technical_plan_generation_input_sha256": generation_fragments.get("input_sha256", ""),
        "technical_plan_repair_source_run_id": "",
        "timeline": ["technical_planning_generate"],
    }


def _technical_generation_failure(
    state: ProjectState, errors: list[str], fragments: dict[str, Any],
) -> dict[str, Any]:
    """将目标匹配或受控合并失败接回原错误审阅门，保留现有计划和磁盘草稿。"""

    clarification = _technical_generation_error_payload(errors)
    clarification["message"] = "技术修订未完成，请核对点名接口及修改范围。"
    return {
        "phase": "technical_planning_generate",
        "status": "requires_user_input",
        "technical_plan_repair_candidate": {},
        "technical_plan_repair_errors": errors[:12],
        "technical_plan_generation_input_sha256": fragments.get("input_sha256", ""),
        "technical_plan_repair_source_run_id": "",
        "application_planning_recovery_boundary": _boundary_payload_for_state(
            state, boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
        ),
        "technical_plan_candidate": {},
        "technical_plan_candidate_sha256": "",
        "technical_plan_candidate_diff": {},
        "clarification": clarification,
        "timeline": ["technical_planning_generate"],
    }


def _technical_generation_error_payload(errors: list[str]) -> dict[str, Any]:
    """把模型生成失败转换为仅供当前 TechnicalPlan 审阅门消费的错误载荷。"""

    return {
        "mode": "technical_plan_generation_error",
        "status": "requires_user_input",
        "message": "技术规划自动修复后仍未通过校验。",
        "errors": [str(error).strip() for error in errors if str(error).strip()][:8],
        "questions": [],
    }


def technical_planning_commit(state: ProjectState) -> dict[str, Any]:
    """校验候选摘要并幂等原子保存 TechnicalPlan 草稿，等待用户确认。"""

    boundary = _technical_boundary(state)
    if boundary.boundary is not ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED:
        raise ValueError("TechnicalPlan commit 只能从 CANDIDATE_COMMITTED boundary 开始。")
    candidate = state.get("technical_plan_candidate")
    candidate_sha256 = str(state.get("technical_plan_candidate_sha256") or "")
    if not isinstance(candidate, dict) or not candidate:
        raise ValueError("TechnicalPlan candidate 缺失，拒绝执行 commit。")
    actual_sha256 = application_planning_sha256(candidate)
    if actual_sha256 != candidate_sha256 or actual_sha256 != boundary.candidate_sha256:
        raise ValueError("TechnicalPlan candidate SHA-256 不匹配，拒绝执行 commit。")
    # 展示版只用于确认界面的分区与徽标；剥离标记后必须与候选完全一致，
    # 否则说明展示版被篡改，宁可退回不带标记的视图也不能给用户看错的分区。
    display_plan = state.get("technical_plan_candidate_diff")
    display_plan = (
        deepcopy(display_plan)
        if isinstance(display_plan, dict)
        and display_plan
        and application_planning_sha256(strip_change_status(display_plan)) == candidate_sha256
        else {}
    )
    workspace, lifecycle = _ensure_technical_lifecycle(state)
    if lifecycle.initialization.stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN:
        if lifecycle.initialization.status is not ApplicationLifecycleStatus.RUNNING:
            raise ValueError("TechnicalPlan commit 的生成 lifecycle 不在运行状态。")
    elif not (
        lifecycle.initialization.stage
        is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
        and lifecycle.initialization.status is ApplicationLifecycleStatus.AWAITING_USER
    ):
        raise ValueError("TechnicalPlan commit 的 lifecycle 不在可确认状态。")
    json_path = commit_technical_plan_document(
        state, candidate,
        expected_sha256=candidate_sha256,
    )
    TechnicalPlanFragmentStore(workspace).clear(str(state.get("technical_plan_generation_input_sha256") or ""))
    if lifecycle.initialization.stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
            status=ApplicationLifecycleStatus.AWAITING_USER,
            active_run_id=state.get("active_run_id"),
        )
    from app.graph.nodes.planning import _planning_confirmation_payload

    # 确认界面的 state 规划是展示版；boundary 必须按**展示版剥离后**的口径写基线摘要，
    # 与入口侧 `_technical_input_boundary` 完全同源。这样用户在确认卡上点"修改"或
    # 触发 repair 时，begin 拿 state 校验才不会因为"摘要按候选算、state 是展示版"而对不上。
    committed_plan = display_plan or candidate
    return {
        "phase": "technical_planning",
        "status": "requires_user_input",
        # 确认界面读取的正式计划带本轮变更标记；校验 / 入库始终用剥离标记的候选。
        "technical_plan": committed_plan,
        "technical_plan_diff": display_plan,
        "technical_plan_candidate_diff": {},
        "technical_plan_generation_input_sha256": "",
        "project_plan": candidate,
        "technical_plan_json_path": json_path,
        "project_plan_path": json_path,
        "project_plan_json_path": json_path,
        "clarification": _planning_confirmation_payload(state, candidate),
        "application_planning_recovery_boundary": _boundary_payload_for_state(
            {**state, "technical_plan": committed_plan},
            boundary=ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED,
            candidate=candidate,
        ),
        "technical_plan_candidate": {},
        "technical_plan_candidate_sha256": "",
        "timeline": ["technical_planning_commit"],
        "lifecycle": application_lifecycle_payload(lifecycle),
    }


def technical_planning_confirm(state: ProjectState) -> dict[str, Any]:
    """确认当前 TechnicalPlan 并执行既有 continuation/template 收口逻辑。"""

    boundary = _technical_boundary(state)
    if boundary.boundary is not ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED:
        raise ValueError("TechnicalPlan confirm 只能从 ARTIFACT_COMMITTED boundary 开始。")
    plan = state.get("technical_plan")
    if not isinstance(plan, dict) or not plan:
        raise ValueError("TechnicalPlan 正式候选缺失，无法确认。")
    # 确认界面拿到的计划带本轮 `_change_status` 展示标记；确认、校验、落盘一律用剥离后的干净版，
    # 开发变更稍后按正式规划与迭代基线独立计算。
    # 否则标记会被校验当成非法字段，也会跟着正式产物写进 git。
    plan = strip_change_status(plan)
    from app.graph.nodes.planning import (
        _attach_technical_plan_contracts,
        _project_plan_dependency_error_payload,
        _project_plan_validation_errors,
    )
    from app.graph.application_planning_revision import cleared_design_change_context
    from app.services.project_plan import apply_project_plan_feedback

    request = _technical_request(state)
    confirmed_plan = {
        **apply_project_plan_feedback(plan, request),
        "confirmation_status": "confirmed",
    }
    confirmed_plan = _attach_technical_plan_contracts(state, confirmed_plan)
    validation_errors = _project_plan_validation_errors(confirmed_plan, state)
    # 确认动作只校验并提交当前版本，不是重新生成授权。任何失败都停在原生错误审阅门，
    # 只有用户提交修改意见才回到 begin/generate，避免内容每次稍变就绕过摘要去重。
    if validation_errors:
        failed_confirm_sha256 = list(state.get("technical_plan_failed_confirm_sha256") or [])
        confirmed_business_sha256 = application_planning_sha256(drop_generated_fields(confirmed_plan))
        return {
            "phase": "technical_planning",
            "status": "requires_user_input",
            "clarification": _project_plan_dependency_error_payload(validation_errors),
            "technical_plan_repair_errors": validation_errors[:12],
            "technical_plan_failed_confirm_sha256": list(
                dict.fromkeys([*failed_confirm_sha256, confirmed_business_sha256])
            )[-8:],
            "application_planning_review_route": "",
            "application_planning_interaction": {},
            "timeline": ["technical_planning_confirm"],
        }

    workspace, lifecycle = _ensure_technical_lifecycle(state)
    technical_plan_json = commit_technical_plan_document(
        state,
        confirmed_plan,
    )
    confirmation = confirm_application_planning_artifacts(
        {
            **state,
            "technical_plan": confirmed_plan,
            "technical_plan_json_path": technical_plan_json,
        }
    )
    # 开发资格独立基于最终正式规划与迭代起始基线计算，不能复制计划审阅标记。
    write_change_snapshot(
        workspace, technical_plan=confirmed_plan, product_plan=state.get("product_plan") or {},
    )
    revision_continuation: dict[str, Any] = {}
    active_revision = lifecycle.active_formal_revision
    if active_revision is not None and active_revision.formal_branch.value == "design_stage_revision":
        # 确认文件与全部正式产物校验完成后，先提交修订进度再进入模板节点；
        # 新事务节点必须保留原确认节点的生命周期同步，不能只更新磁盘文件。
        update_active_revision_progress(
            workspace,
            change_id=active_revision.change_id,
            status="design_planning",
            current_artifact="technical-plan",
            remaining_artifacts=[],
        )
        _, lifecycle = _ensure_technical_lifecycle(state)
    template_reconcile_pending = bool(
        active_revision is not None
        and active_revision.formal_branch.value
        in {"design_stage_revision", "workbench_plan_revision"}
    )
    if not template_reconcile_pending:
        lifecycle = persist_application_lifecycle_transition(
            workspace,
            stage=ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            status=ApplicationLifecycleStatus.RUNNING,
            active_run_id=state.get("active_run_id"),
        )
    return {
        "phase": "technical_planning_confirm",
        "status": "completed",
        "technical_plan": confirmed_plan,
        "project_plan": confirmed_plan,
        "technical_plan_json_path": technical_plan_json,
        "project_plan_path": technical_plan_json,
        "project_plan_json_path": technical_plan_json,
        "application_planning_confirmation": confirmation,
        "revision_continuation": revision_continuation,
        "template_reconcile_pending": template_reconcile_pending,
        # 确认成功，失败记录不再有约束意义，清空以免影响下一轮修订。
        "technical_plan_failed_confirm_sha256": [],
        "technical_plan_repair_errors": [],
        "application_planning_review_route": "",
        "application_planning_interaction": {},
        "lifecycle": application_lifecycle_payload(lifecycle),
        "timeline": ["technical_planning_confirm"],
        **cleared_design_change_context(),
    }


__all__ = [
    "technical_planning_begin",
    "technical_planning_commit",
    "technical_planning_confirm",
    "technical_planning_generate",
]
