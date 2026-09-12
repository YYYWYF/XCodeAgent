"""Application Planning 各业务事务的统一 replay safety Contract 注册表。"""

from __future__ import annotations

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
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryDecision,
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

    def lifecycle_compatible(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> bool:
        """验证业务事务允许的 lifecycle revision 与阶段窗口。"""

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

    def lifecycle_compatible(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> bool:
        """复用 P0.3A 已验证的 Requirement lifecycle 两窗口规则。"""

        return application_planning_committed_input_lifecycle_compatible(
            source=source,
            point=point,
            snapshot=snapshot,
            lifecycle=lifecycle,
        )

    def activity_label(self) -> str:
        """返回需求分析业务名称。"""

        return "需求分析"

    def recovery_message(self) -> str:
        """返回需求回答中断恢复文案。"""

        return "上一次需求分析被中断，已保存你提交的回答，可以继续执行。"


class TechnicalPlanningRecoveryContract:
    """定义 TechnicalPlan 四个 durable boundary 的 Native Replay 规则。"""

    _NEXT_BY_BOUNDARY = {
        ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED.value: "technical_planning_begin",
        ApplicationPlanningRecoveryBoundary.GENERATION_READY.value: "technical_planning_generate",
        ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED.value: "technical_planning_commit",
        ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED.value: "technical_planning_review",
    }

    def match(self, *, source: DurableExecutionRecord, point: RecoveryPoint, snapshot: Any) -> bool:
        """仅识别带完整技术规划 boundary、同线程 owner 和合法 successor 的现场。"""

        if (
            source.execution_kind != "application_planning"
            or source.status is not DurableExecutionStatus.INTERRUPTED
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
        if str(values.get("active_run_id") or "").strip() != source.run_id:
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
        """将完整 boundary 的四个 successor 统一标记为 Native Recoverable。"""

        if not self.match(source=source, point=point, snapshot=snapshot):
            raise ValueError("当前 checkpoint 不是可验证的 TechnicalPlan boundary。")
        return RecoveryStrategyAssessment(
            decision=RecoveryDecision.READY_NATIVE,
            strategy=RecoveryStrategy.NATIVE_CHECKPOINT,
            reason_code="APPLICATION_PLANNING_TECHNICAL_BOUNDARY_REPLAY_SAFE",
            reason="TechnicalPlan 事务边界已通过 deterministic、replay-safe Contract 校验。",
        )

    def lifecycle_compatible(
        self,
        *,
        source: DurableExecutionRecord,
        point: RecoveryPoint,
        snapshot: Any,
        lifecycle: ApplicationLifecycle,
    ) -> bool:
        """只放行同 thread、同 activeRunId 以及预期的一步 lifecycle 推进。"""

        if (
            lifecycle.initialization.thread_id != source.thread_id
            or lifecycle.active_run_id != source.run_id
        ):
            return False
        values = getattr(snapshot, "values", {})
        values = values if isinstance(values, dict) else {}
        boundary = parse_application_planning_boundary(
            values.get("application_planning_recovery_boundary")
        )
        if boundary is None:
            return False
        current_stage = lifecycle.initialization.stage
        current_status = lifecycle.initialization.status
        expected_revision = point.lifecycle_revision
        if expected_revision is not None and lifecycle.revision not in {
            expected_revision,
            expected_revision + 1,
        }:
            return False
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED:
            if current_stage is ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY:
                return (
                    boundary.operation is ApplicationPlanningOperation.INITIAL
                    and current_status is ApplicationLifecycleStatus.AWAITING_USER
                )
            if current_stage is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION:
                return (
                    boundary.operation
                    in {
                        ApplicationPlanningOperation.REVISE,
                        ApplicationPlanningOperation.REPAIR,
                    }
                    and current_status is ApplicationLifecycleStatus.AWAITING_USER
                )
            return (
                current_stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
                and current_status is ApplicationLifecycleStatus.RUNNING
            )
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.GENERATION_READY:
            return (
                current_stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN
                and current_status is ApplicationLifecycleStatus.RUNNING
                and (
                    expected_revision is None
                    or lifecycle.revision == expected_revision
                )
            )
        if boundary.boundary is ApplicationPlanningRecoveryBoundary.CANDIDATE_COMMITTED:
            if current_stage is ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN:
                return current_status is ApplicationLifecycleStatus.RUNNING
            return (
                current_stage is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
                and current_status is ApplicationLifecycleStatus.AWAITING_USER
            )
        return (
            boundary.boundary is ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED
            and current_stage
            is ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION
            and current_status is ApplicationLifecycleStatus.AWAITING_USER
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
    "ApplicationPlanningRecoveryContract",
    "RequirementCommittedInputRecoveryContract",
    "TechnicalPlanningRecoveryContract",
    "production_application_planning_recovery_contracts",
    "resolve_application_planning_recovery_contract",
]
