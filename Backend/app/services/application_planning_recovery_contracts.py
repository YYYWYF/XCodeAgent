"""Application Planning 各业务事务的统一 replay safety Contract 注册表。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from app.domain.application_lifecycle import (
    ApplicationLifecycle,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_planning_recovery import (
    ApplicationPlanningRecoveryBoundary,
    ApplicationPlanningOperation,
    application_planning_sha256,
    parse_application_planning_boundary,
)
from app.domain.application_revision import FormalRevisionBranch, RevisionTarget
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
    RecoveryLifecycleOwnershipMode,
    RecoveryPoint,
    RecoveryStrategy,
)
from app.protocols.application_planning_interrupt import (
    application_planning_interrupt_from_snapshot,
)
from app.services.application_planning_recovery_policy import (
    application_planning_committed_input_lifecycle_compatible,
    application_planning_committed_input_recovery_candidate,
)
from app.services.execution_recovery_strategy import RecoveryStrategyAssessment


@dataclass(frozen=True)
class ApplicationPlanningLifecycleRecoveryAssessment:
    """保存业务 Contract 对 lifecycle 兼容性和 ownership 的联合判断。"""

    compatible: bool
    ownership_mode: RecoveryLifecycleOwnershipMode = (
        RecoveryLifecycleOwnershipMode.SOURCE_OWNED
    )


class ApplicationPlanningRecoveryContract(Protocol):
    """定义一个业务事务如何识别、评估并验证自身恢复现场。"""

    def match(self, *, source: DurableExecutionRecord, point: RecoveryPoint, snapshot: Any) -> bool:
        """判断当前 checkpoint 是否由本 Contract 负责解释。"""

    def assess_replay(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment:
        """返回当前业务现场是否可以 Native Replay 的结论。"""

    def assess_lifecycle(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> ApplicationPlanningLifecycleRecoveryAssessment:
        """验证业务事务允许的 lifecycle revision、阶段窗口和 ownership 模式。"""

    def lifecycle_compatible(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> bool:
        """保留旧的布尔适配入口，供现有调用方平滑迁移。"""

    def activity_label(self) -> str:
        """返回恢复卡片使用的业务活动名称。"""

    def recovery_message(self) -> str:
        """返回恢复卡片使用的用户可见文案。"""


class RequirementCommittedInputRecoveryContract:
    """把 P0.3A Requirement answer 规则纳入统一 Contract abstraction。"""

    def match(self, *, source: DurableExecutionRecord, point: RecoveryPoint, snapshot: Any) -> bool:
        """识别已提交回答并且尚未进入 requirements interrupt 的 checkpoint。"""

        return application_planning_committed_input_recovery_candidate(
            source=source,
            point=point,
            snapshot=snapshot,
        )

    def assess_replay(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment:
        """将既有 Requirement replay 规则转换成统一评估结果。"""

        if not self.match(source=source, point=point, snapshot=snapshot):
            raise ValueError("当前 checkpoint 不是已提交 Requirement answer。")
        return RecoveryStrategyAssessment(
            decision=RecoveryDecision.READY_NATIVE,
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            reason_code="APPLICATION_PLANNING_INPUT_COMMITTED_REPLAY_SAFE",
            reason="已提交的需求澄清回答和 requirements checkpoint 已通过 Contract 校验。",
        )

    def assess_lifecycle(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> ApplicationPlanningLifecycleRecoveryAssessment:
        """复用 P0.3A 已验证的 Requirement lifecycle 两窗口规则。"""

        return ApplicationPlanningLifecycleRecoveryAssessment(
            compatible=application_planning_committed_input_lifecycle_compatible(
                source=source,
                point=point,
                snapshot=snapshot,
                lifecycle=lifecycle,
            )
        )

    def lifecycle_compatible(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> bool:
        """兼容旧调用方并固定 Requirement 为 source-owned。"""

        return self.assess_lifecycle(
            source=source,
            point=point,
            snapshot=snapshot,
            lifecycle=lifecycle,
        ).compatible

    def activity_label(self) -> str:
        """返回需求分析业务名称。"""

        return "需求分析"

    def recovery_message(self) -> str:
        """返回需求回答中断恢复文案。"""

        return "上一次需求分析被中断，已保存你提交的回答，可以继续执行。"


class TechnicalPlanningRecoveryContract:
    """定义 TechnicalPlan 五个 durable boundary 的 Native Replay 规则。"""

    _NEXT_BY_BOUNDARY = {
        ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED.value: "technical_planning_begin",
        ApplicationPlanningRecoveryBoundary.GENERATION_READY.value: "technical_planning_generate",
        ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED.value: "technical_planning_commit",
        ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED.value: "technical_planning_review",
        ApplicationPlanningRecoveryBoundary.REVIEW_READY.value: "technical_planning_review",
    }

    def match(self, *, source: DurableExecutionRecord, point: RecoveryPoint, snapshot: Any) -> bool:
        """仅识别带完整技术规划 boundary、同线程 owner 和合法 successor 的现场。"""

        if (
            source.execution_kind != "application_planning"
            or source.status is not DurableExecutionStatus.INTERRUPTED
            or point.run_id != source.run_id
            or point.thread_id != source.thread_id
        ):
            return False
        values = getattr(snapshot, "values", {})
        values = values if isinstance(values, dict) else {}
        boundary = parse_application_planning_boundary(
            values.get("application_planning_recovery_boundary")
        )
        if boundary is None or boundary.artifact.value != "technical_plan":
            return False
        if boundary.request_sha256 != application_planning_sha256(
            _technical_request_from_values(values)
        ):
            return False
        if boundary.operation in {
            ApplicationPlanningOperation.REVISE,
            ApplicationPlanningOperation.REPAIR,
        } and boundary.baseline_sha256 is None:
            return False
        if (
            boundary.boundary is not ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED
            and boundary.baseline_sha256 is not None
            and application_planning_sha256(values.get("technical_plan"))
            != boundary.baseline_sha256
        ):
            return False
        if (
            boundary.boundary
            in {
                ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED,
                ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED,
            }
            and boundary.candidate_sha256 is None
        ):
            return False
        expected_next = self._NEXT_BY_BOUNDARY.get(boundary.boundary.value)
        if expected_next is None or point.next_nodes != [expected_next]:
            return False
        active_run_id = str(values.get("active_run_id") or "").strip()
        if (
            boundary.boundary is not ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED
            and active_run_id != source.run_id
        ):
            return False
        if application_planning_interrupt_from_snapshot(snapshot) is not None:
            return False
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED:
            candidate = values.get("technical_plan_candidate")
            candidate_sha256 = str(values.get("technical_plan_candidate_sha256") or "")
            if (
                not isinstance(candidate, dict)
                or not candidate
                or application_planning_sha256(candidate) != candidate_sha256
                or candidate_sha256 != boundary.candidate_sha256
            ):
                return False
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.REVIEW_READY:
            clarification = values.get("clarification")
            clarification = clarification if isinstance(clarification, dict) else {}
            if (
                clarification.get("status") != "requires_user_input"
                or clarification.get("mode")
                not in {
                    "technical_plan_generation_error",
                    "project_plan_dependency_validation_error",
                }
            ):
                return False
            errors = clarification.get("errors")
            if errors is not None and not isinstance(errors, list):
                return False
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED:
            technical_plan = values.get("technical_plan")
            if (
                not isinstance(technical_plan, dict)
                or not technical_plan
                or application_planning_sha256(technical_plan)
                != boundary.candidate_sha256
            ):
                return False
        return True

    def assess_replay(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
    ) -> RecoveryStrategyAssessment:
        """将完整 boundary 的五个 successor 统一标记为 Native Recoverable。"""

        if not self.match(source=source, point=point, snapshot=snapshot):
            raise ValueError("当前 checkpoint 不是可验证的 TechnicalPlan boundary。")
        return RecoveryStrategyAssessment(
            decision=RecoveryDecision.READY_NATIVE,
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            reason_code="APPLICATION_PLANNING_TECHNICAL_BOUNDARY_REPLAY_SAFE",
            reason="TechnicalPlan 事务边界已通过 deterministic、replay-safe Contract 校验。",
        )

    def assess_lifecycle(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> ApplicationPlanningLifecycleRecoveryAssessment:
        """由业务 predecessor 判断 source-owned 或 pre-ownership 恢复窗口。"""

        if lifecycle.initialization.thread_id != source.thread_id:
            return ApplicationPlanningLifecycleRecoveryAssessment(False)
        values = getattr(snapshot, "values", {})
        values = values if isinstance(values, dict) else {}
        boundary = parse_application_planning_boundary(
            values.get("application_planning_recovery_boundary")
        )
        if boundary is None:
            return ApplicationPlanningLifecycleRecoveryAssessment(False)
        current_stage = lifecycle.initialization.stage
        current_status = lifecycle.initialization.status
        expected_revision = point.lifecycle_revision
        if expected_revision is not None and lifecycle.revision not in {
            expected_revision,
            expected_revision + 1,
        }:
            return ApplicationPlanningLifecycleRecoveryAssessment(False)
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED:
            change_id = str(values.get("change_id") or "").strip()
            if boundary.operation is ApplicationPlanningOperation.REVISE and change_id:
                if not self._formal_revision_identity_matches(
                    values=values,
                    boundary=boundary,
                    lifecycle=lifecycle,
                ):
                    return ApplicationPlanningLifecycleRecoveryAssessment(False)
            if current_stage is ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY:
                return ApplicationPlanningLifecycleRecoveryAssessment(
                    compatible=(
                        boundary.operation is ApplicationPlanningOperation.INITIAL
                        and current_status is ApplicationLifecycleStatus.AWAITING_USER
                    ),
                    ownership_mode=RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
                )
            if current_stage is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION:
                return ApplicationPlanningLifecycleRecoveryAssessment(
                    compatible=(
                        boundary.operation
                        in {
                            ApplicationPlanningOperation.REVISE,
                            ApplicationPlanningOperation.REPAIR,
                        }
                        and current_status is ApplicationLifecycleStatus.AWAITING_USER
                    ),
                    ownership_mode=RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
                )
            if (
                boundary.operation is ApplicationPlanningOperation.REVISE
                and change_id
                and current_stage is ApplicationLifecycleStage.READY_FOR_WORKBENCH
                and current_status is ApplicationLifecycleStatus.COMPLETED
                and (
                    lifecycle.pending_revision_impact is not None
                    or lifecycle.active_formal_revision is not None
                )
            ):
                return ApplicationPlanningLifecycleRecoveryAssessment(
                    compatible=True,
                    ownership_mode=RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP,
                )
            return ApplicationPlanningLifecycleRecoveryAssessment(
                compatible=(
                    current_stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
                    and current_status is ApplicationLifecycleStatus.RUNNING
                    and lifecycle.active_run_id == source.run_id
                ),
                ownership_mode=RecoveryLifecycleOwnershipMode.SOURCE_OWNED,
            )
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.GENERATION_READY:
            return ApplicationPlanningLifecycleRecoveryAssessment(
                compatible=(
                    lifecycle.active_run_id == source.run_id
                    and current_stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
                    and current_status is ApplicationLifecycleStatus.RUNNING
                    and (
                        expected_revision is None
                        or lifecycle.revision == expected_revision
                    )
                )
            )
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED:
            if current_stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN:
                compatible = (
                    lifecycle.active_run_id == source.run_id
                    and current_status is ApplicationLifecycleStatus.RUNNING
                )
            else:
                compatible = (
                    lifecycle.active_run_id == source.run_id
                    and current_stage
                    is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
                    and current_status is ApplicationLifecycleStatus.AWAITING_USER
                )
            return ApplicationPlanningLifecycleRecoveryAssessment(compatible)
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED:
            return ApplicationPlanningLifecycleRecoveryAssessment(
                compatible=(
                    lifecycle.active_run_id == source.run_id
                    and current_stage
                    is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
                    and current_status is ApplicationLifecycleStatus.AWAITING_USER
                )
            )
        return ApplicationPlanningLifecycleRecoveryAssessment(
            compatible=(
                boundary.boundary is ApplicationPlanningRecoveryBoundary.REVIEW_READY
                and lifecycle.active_run_id == source.run_id
                and current_stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
                and current_status is ApplicationLifecycleStatus.RUNNING
                and (
                    expected_revision is None
                    or lifecycle.revision == expected_revision
                )
            )
        )

    def lifecycle_compatible(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> bool:
        """兼容旧调用方并只返回新的 Contract assessment 结论。"""

        return self.assess_lifecycle(
            source=source,
            point=point,
            snapshot=snapshot,
            lifecycle=lifecycle,
        ).compatible

    def _formal_revision_identity_matches(
        self,
        *,
        values: dict[str, Any],
        boundary: Any,
        lifecycle: ApplicationLifecycle,
    ) -> bool:
        """验证 Formal Revision 的 change、gate、请求、分支和目标事实一致。"""

        change_id = str(values.get("change_id") or "").strip()
        target_value = values.get("change_target")
        if not change_id or not isinstance(target_value, dict):
            return False
        try:
            target = RevisionTarget.model_validate(target_value)
        except Exception:
            return False
        request = _technical_request_from_values(values)
        pending = lifecycle.pending_revision_impact
        if pending is not None:
            return (
                pending.change_id == change_id
                and pending.interaction_id == str(boundary.gate_id or "")
                and pending.request == request
                and pending.impact.formal_branch is FormalRevisionBranch.WORKBENCH_PLAN_REVISION
                and pending.target == target
            )
        active = lifecycle.active_formal_revision
        if active is None:
            return False
        return (
            active.change_id == change_id
            and active.impact_interaction_id == str(boundary.gate_id or "")
            and active.request == request
            and active.formal_branch is FormalRevisionBranch.WORKBENCH_PLAN_REVISION
            and active.target == target
        )

    def activity_label(self) -> str:
        """返回技术规划业务名称。"""

        return "技术规划修改"

    def recovery_message(self) -> str:
        """返回技术规划恢复卡片文案。"""

        return "上一次技术规划修改被中断，已保存本轮修改要求，可以继续执行。"


def production_application_planning_recovery_contracts() -> tuple[
    ApplicationPlanningRecoveryContract, ...
]:
    """返回生产环境当前注册的 Requirement 与 TechnicalPlan Contract。"""

    return (
        RequirementCommittedInputRecoveryContract(),
        TechnicalPlanningRecoveryContract(),
    )


def _technical_request_from_values(values: dict[str, Any]) -> str:
    """按 Graph State 当前请求优先级重建 TechnicalPlan boundary 的输入摘要原文。"""

    interaction = values.get("application_planning_interaction")
    if isinstance(interaction, dict) and interaction:
        return str(interaction.get("request") or "").strip()
    return str(
        values.get("design_change_generation_request")
        or values.get("design_change_request")
        or values.get("request")
        or ""
    ).strip()


def resolve_application_planning_recovery_contract(
    *,
    source: DurableExecutionRecord,
    point: RecoveryPoint,
    snapshot: Any,
    contracts: Sequence[ApplicationPlanningRecoveryContract] | None = None,
) -> ApplicationPlanningRecoveryContract | None:
    """按稳定注册顺序返回首个能够解释当前 checkpoint 的 Contract。"""

    for contract in contracts or production_application_planning_recovery_contracts():
        if contract.match(source=source, point=point, snapshot=snapshot):
            return contract
    return None


__all__ = [
    "ApplicationPlanningLifecycleRecoveryAssessment",
    "ApplicationPlanningRecoveryContract",
    "RequirementCommittedInputRecoveryContract",
    "TechnicalPlanningRecoveryContract",
    "production_application_planning_recovery_contracts",
    "resolve_application_planning_recovery_contract",
]
