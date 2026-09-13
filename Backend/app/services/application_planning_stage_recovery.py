"""Application Planning Stage Restart 的正式产物校验与输入重建。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_planning_recovery import (
    ApplicationPlanningRecoveryBoundary,
    ApplicationPlanningOperation,
    application_planning_boundary_payload,
    application_planning_sha256,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryLifecycleOwnershipMode,
    RecoveryPoint,
)
from app.protocols.application_planning_interrupt import (
    application_planning_interrupt_from_snapshot,
)
from app.services.artifact_invalidation import canonical_sha256
from app.workspace.plan_documents import technical_plan_json_path
from app.workspace.product_plan_documents import confirmed_product_plan_json_path
from app.workspace.spec_documents import (
    confirmed_requirement_spec_json_path,
    ui_designs_json_path,
)


@dataclass(frozen=True, slots=True)
class TechnicalPlanningStageRestartAuthority:
    """绑定 Stage Restart 所依据的正式产物、请求和 lifecycle 版本。"""

    stage: str
    operation: ApplicationPlanningOperation
    lifecycle_revision: int
    requirement_spec_sha256: str
    product_plan_sha256: str
    ui_designs_sha256: str
    technical_plan_sha256: str | None
    request_sha256: str
    authority_sha256: str


@dataclass(frozen=True, slots=True)
class TechnicalPlanningStageRestartAssessment:
    """保存 Stage Restart 是否可以安全重建及其新一轮 Graph 输入。"""

    available: bool
    reason_code: str
    reason: str
    operation: ApplicationPlanningOperation | None = None
    request: str = ""
    reconstructed_state: dict[str, Any] | None = None
    authority: TechnicalPlanningStageRestartAuthority | None = None
    lifecycle_ownership_mode: RecoveryLifecycleOwnershipMode = (
        RecoveryLifecycleOwnershipMode.SOURCE_OWNED
    )

    @property
    def state(self) -> dict[str, Any] | None:
        """兼容旧调用点读取一次执行期 convenience state。"""

        return self.reconstructed_state


class ApplicationPlanningStageRecoveryContract:
    """定义跨 checkpoint 的 Application Planning 阶段重启安全合同。"""

    def assess(
        self,
        *,
        workspace: str,
        source: DurableExecutionRecord,
        point: RecoveryPoint | None,
        snapshot: Any,
        lifecycle: ApplicationLifecycle | None,
        expected_owner: str | None = None,
        authority_lifecycle_revision: int | None = None,
    ) -> TechnicalPlanningStageRestartAssessment:
        """验证正式产物、lifecycle authority 和 revision 后构造干净输入。"""

        if source.execution_kind != "application_planning" or source.status not in {
            DurableExecutionStatus.FAILED,
            DurableExecutionStatus.INTERRUPTED,
        }:
            return _unavailable("STAGE_RESTART_SOURCE_NOT_FAILED", "当前 source 不是可重启的失败规划执行。")
        if point is not None and (
            point.run_id != source.run_id or point.thread_id != source.thread_id
        ):
            return _unavailable("STAGE_RESTART_POINT_MISMATCH", "source RecoveryPoint 不属于当前 canonical execution。")
        if lifecycle is None:
            return _unavailable("STAGE_RESTART_LIFECYCLE_MISSING", "ApplicationLifecycle 不存在，不能重启技术规划阶段。")
        if lifecycle.initialization.thread_id != source.thread_id:
            return _unavailable("STAGE_RESTART_THREAD_MISMATCH", "ApplicationLifecycle 与 source execution 的 threadId 不一致。")
        owner = expected_owner or source.run_id
        if lifecycle.active_run_id != owner:
            return _unavailable("STAGE_RESTART_OWNER_DRIFT", "ApplicationLifecycle 已不再由当前 recovery execution 持有。")
        if lifecycle.initialization.stage is not ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN:
            return _unavailable("STAGE_RESTART_STAGE_MISMATCH", "当前生命周期不在 Technical Planning 生成阶段。")
        if lifecycle.initialization.status not in {
            ApplicationLifecycleStatus.RUNNING,
            ApplicationLifecycleStatus.FAILED,
        }:
            return _unavailable("STAGE_RESTART_LIFECYCLE_NOT_RESTARTABLE", "当前生命周期状态不允许重启 Technical Planning。")
        if application_planning_interrupt_from_snapshot(snapshot) is not None:
            return _unavailable("STAGE_RESTART_USER_INTERRUPT", "当前现场仍有待用户处理的交互，不能绕过确认门。")

        artifacts = _load_formal_artifacts(workspace)
        if artifacts is None:
            return _unavailable("STAGE_RESTART_FORMAL_ARTIFACT_MISSING", "RequirementSpec、ProductPlan 或 UiDesign 正式产物缺失或无效。")
        requirement_spec, product_plan, ui_designs, technical_plan = artifacts
        operation, request, change_id, change_target, gate_id = _operation_context(
            lifecycle=lifecycle,
            requirement_spec=requirement_spec,
            product_plan=product_plan,
            technical_plan=technical_plan,
        )
        if operation is None:
            return _unavailable("STAGE_RESTART_REVISION_AMBIGUOUS", "无法唯一确定 Technical Planning 是首次生成还是 Formal Revision。")
        if operation is not ApplicationPlanningOperation.INITIAL and not technical_plan:
            return _unavailable("STAGE_RESTART_BASELINE_MISSING", "TechnicalPlan 修订缺少当前正式 baseline。")
        if operation is not ApplicationPlanningOperation.INITIAL and lifecycle.active_formal_revision is not None:
            expected = lifecycle.active_formal_revision.technical_plan_sha256
            technical_path = technical_plan_json_path({"workspace": workspace})
            if expected and (
                not technical_path.is_file()
                or canonical_sha256(technical_path) != expected
            ):
                return _unavailable("STAGE_RESTART_FORMAL_REVISION_DRIFT", "TechnicalPlan 正式 baseline 已发生 revision/hash 漂移。")

        authority = _build_authority(
            workspace=workspace,
            operation=operation,
            lifecycle_revision=(
                authority_lifecycle_revision
                if authority_lifecycle_revision is not None
                else lifecycle.revision
            ),
            request=request,
            technical_plan=technical_plan
            if operation is not ApplicationPlanningOperation.INITIAL
            else None,
        )
        if authority_lifecycle_revision is not None and lifecycle.revision not in {
            authority_lifecycle_revision,
            authority_lifecycle_revision + 1,
        }:
            return _unavailable(
                "STAGE_RESTART_LIFECYCLE_DRIFT",
                "ApplicationLifecycle revision 已偏离 Stage Restart authority。",
            )

        boundary = application_planning_boundary_payload(
            operation_id=f"stage-restart-{source.run_id}",
            operation=operation,
            boundary=ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED,
            request=request,
            gate_id=gate_id,
            baseline=technical_plan if operation is not ApplicationPlanningOperation.INITIAL else None,
        )
        restart_state: dict[str, Any] = {
            "phase": "technical_planning_begin",
            "status": "running",
            "request": request,
            "workflow_scope": "application_planning",
            "application_name": lifecycle.application.name,
            "requirement_spec": requirement_spec,
            "product_plan": product_plan,
            "ui_designs": ui_designs,
            "technical_plan": technical_plan if operation is not ApplicationPlanningOperation.INITIAL else {},
            "application_planning_recovery_boundary": boundary,
            "application_planning_interaction": {},
            "technical_plan_candidate": {},
            "technical_plan_candidate_sha256": "",
            "technical_plan_repair_candidate": {},
            "technical_plan_repair_errors": [],
            "resume_from": "technical_planning_begin",
            "selected_skill_names": [],
            "timeline": [],
        }
        if change_id:
            restart_state["change_id"] = change_id
        if change_target is not None:
            restart_state["change_target"] = change_target
        return TechnicalPlanningStageRestartAssessment(
            available=True,
            reason_code="TECHNICAL_PLANNING_STAGE_RESTART_AVAILABLE",
            reason="已重新校验正式产物，可以从 Technical Planning 阶段入口重新执行。",
            operation=operation,
            request=request,
            reconstructed_state=restart_state,
            authority=authority,
            lifecycle_ownership_mode=RecoveryLifecycleOwnershipMode.SOURCE_OWNED,
        )

    def assess_before_handoff(
        self,
        *,
        workspace: str,
        source: DurableExecutionRecord,
        snapshot: Any,
        lifecycle: ApplicationLifecycle | None,
    ) -> TechnicalPlanningStageRestartAssessment:
        """只验证 source ownership，生成供 durable claim 固化的 authority。"""

        return self.assess(
            workspace=workspace,
            source=source,
            point=None,
            snapshot=snapshot,
            lifecycle=lifecycle,
            expected_owner=source.run_id,
        )

    def revalidate_after_handoff(
        self,
        *,
        workspace: str,
        source: DurableExecutionRecord,
        snapshot: Any,
        lifecycle: ApplicationLifecycle | None,
        expected_owner: str,
        expected_authority_sha256: str,
        expected_lifecycle_revision: int,
    ) -> TechnicalPlanningStageRestartAssessment:
        """以 child ownership 重读正式产物，并拒绝 authority 或 lifecycle 漂移。"""

        assessment = self.assess(
            workspace=workspace,
            source=source,
            point=None,
            snapshot=snapshot,
            lifecycle=lifecycle,
            expected_owner=expected_owner,
            authority_lifecycle_revision=expected_lifecycle_revision,
        )
        if not assessment.available:
            return assessment
        if (
            assessment.authority is None
            or assessment.authority.authority_sha256 != expected_authority_sha256
        ):
            return _unavailable(
                "RECOVERY_STAGE_AUTHORITY_DRIFT",
                "正式产物或 Stage Restart authority 已发生变化，请刷新恢复动作。",
            )
        return assessment


def _build_authority(
    *,
    workspace: str,
    operation: ApplicationPlanningOperation,
    lifecycle_revision: int,
    request: str,
    technical_plan: dict[str, Any] | None,
) -> TechnicalPlanningStageRestartAuthority:
    """从当前正式文件和稳定请求构造唯一的 Stage Restart fingerprint。"""

    state = {"workspace": workspace}
    paths = {
        "requirement_spec": confirmed_requirement_spec_json_path(state),
        "product_plan": confirmed_product_plan_json_path(state),
        "ui_designs": ui_designs_json_path(state),
        "technical_plan": technical_plan_json_path(state),
    }
    requirement_spec_sha256 = canonical_sha256(paths["requirement_spec"])
    product_plan_sha256 = canonical_sha256(paths["product_plan"])
    ui_designs_sha256 = canonical_sha256(paths["ui_designs"])
    technical_plan_sha256 = (
        canonical_sha256(paths["technical_plan"])
        if technical_plan is not None and paths["technical_plan"].is_file()
        else None
    )
    request_sha256 = application_planning_sha256(request)
    fingerprint = {
        "stage": "technical_planning",
        "operation": operation.value,
        "lifecycleRevision": lifecycle_revision,
        "requirementSpecSha256": requirement_spec_sha256,
        "productPlanSha256": product_plan_sha256,
        "uiDesignsSha256": ui_designs_sha256,
        "technicalPlanSha256": technical_plan_sha256,
        "requestSha256": request_sha256,
    }
    return TechnicalPlanningStageRestartAuthority(
        stage="technical_planning",
        operation=operation,
        lifecycle_revision=lifecycle_revision,
        requirement_spec_sha256=requirement_spec_sha256,
        product_plan_sha256=product_plan_sha256,
        ui_designs_sha256=ui_designs_sha256,
        technical_plan_sha256=technical_plan_sha256,
        request_sha256=request_sha256,
        authority_sha256=application_planning_sha256(fingerprint),
    )


def _load_formal_artifacts(
    workspace: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """只读取当前正式 JSON 产物，不读取失败 execution 的旧 Graph state。"""

    state = {"workspace": workspace}
    paths = (
        confirmed_requirement_spec_json_path(state),
        confirmed_product_plan_json_path(state),
        ui_designs_json_path(state),
        technical_plan_json_path(state),
    )
    try:
        requirement_spec = _load_json(paths[0])
        product_plan = _load_json(paths[1])
        ui_designs = _load_json(paths[2])
        technical_plan = _load_json(paths[3], allow_missing=True)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not requirement_spec
        or requirement_spec.get("confirmation_status") != "confirmed"
        or not product_plan
        or product_plan.get("confirmation_status") != "confirmed"
        or not ui_designs
        or ui_designs.get("confirmation_status") not in {"confirmed", "skipped"}
    ):
        return None
    return requirement_spec, product_plan, ui_designs, technical_plan


def _load_json(path: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    """读取单个当前 JSON 产物并拒绝非对象内容。"""

    if allow_missing and not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"正式产物不是对象：{path}")
    return value


def _operation_context(
    *,
    lifecycle: ApplicationLifecycle,
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    technical_plan: dict[str, Any],
) -> tuple[
    ApplicationPlanningOperation | None,
    str,
    str | None,
    dict[str, Any] | None,
    str | None,
]:
    """从唯一 Formal Revision authority 或当前正式事实确定重启输入。"""

    active = lifecycle.active_formal_revision
    pending = lifecycle.pending_revision_impact
    if active is not None and pending is not None:
        return None, "", None, None, None
    if active is not None:
        return (
            ApplicationPlanningOperation.REVISE,
            active.request,
            active.change_id,
            active.target.model_dump(mode="json", by_alias=True),
            active.impact_interaction_id,
        )
    if pending is not None:
        return (
            ApplicationPlanningOperation.REVISE,
            pending.request,
            pending.change_id,
            pending.target.model_dump(mode="json", by_alias=True),
            pending.interaction_id,
        )
    request = _request_from_formal_artifacts(requirement_spec, product_plan)
    return ApplicationPlanningOperation.INITIAL, request, None, None, None


def _request_from_formal_artifacts(
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
) -> str:
    """只从正式 RequirementSpec/ProductPlan 事实提取首次生成请求。"""

    for artifact in (requirement_spec, product_plan):
        for key in ("request", "original_request", "user_request", "description"):
            value = str(artifact.get(key) or "").strip()
            if value:
                return value
    return ""


def _unavailable(code: str, reason: str) -> TechnicalPlanningStageRestartAssessment:
    """构造没有安全重启入口的 fail-closed assessment。"""

    return TechnicalPlanningStageRestartAssessment(
        available=False,
        reason_code=code,
        reason=reason,
    )


__all__ = [
    "ApplicationPlanningStageRecoveryContract",
    "TechnicalPlanningStageRestartAuthority",
    "TechnicalPlanningStageRestartAssessment",
]
