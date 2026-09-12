"""TechnicalPlan Recovery Contract 的严格身份与错误边界测试。"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.domain.application_lifecycle import (
    ApplicationInitialization,
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation,
    ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
    application_planning_sha256,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    RecoveryPoint,
    RecoveryPointKind,
)
from app.services.application_lifecycle import create_application_lifecycle
from app.services.application_planning_recovery_contracts import TechnicalPlanningRecoveryContract


def _technical_plan() -> dict[str, object]:
    """构造一个可用于 boundary 摘要校验的最小 TechnicalPlan。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "app": {"name": "事务边界测试"},
    }


def _source() -> DurableExecutionRecord:
    """构造与 checkpoint 完全绑定的中断 application-planning source。"""

    now = datetime.now(timezone.utc)
    return DurableExecutionRecord(
        run_id="technical-run",
        thread_id="technical-thread",
        workspace="/tmp/technical-contract",
        project_id="app-1",
        execution_kind="application_planning",
        workflow_scope="application_planning",
        first_node="technical_planning_begin",
        current_node="technical_planning_generate",
        status=DurableExecutionStatus.INTERRUPTED,
        started_at=now,
        updated_at=now,
    )


def _snapshot_and_point(
    *,
    boundary: ApplicationPlanningRecoveryBoundary,
    next_nodes: list[str] | None = None,
    clarification: dict[str, object] | None = None,
) -> tuple[SimpleNamespace, RecoveryPoint, DurableExecutionRecord, object]:
    """构造带真实恢复模型的 TechnicalPlan boundary 快照。"""

    source = _source()
    plan = _technical_plan()
    payload = application_planning_boundary_payload(
        operation_id="technical-plan:transaction-1",
        operation=ApplicationPlanningOperation.REVISE,
        boundary=boundary,
        request="增加分页参数",
        gate_id="impact-1",
        baseline=plan,
    )
    values: dict[str, object] = {
        "active_run_id": source.run_id,
        "request": "增加分页参数",
        "technical_plan": plan,
        "application_planning_recovery_boundary": payload,
    }
    if clarification is not None:
        values["clarification"] = clarification
    now = datetime.now(timezone.utc)
    point = RecoveryPoint(
        recovery_point_id="point-technical-1",
        run_id=source.run_id,
        thread_id=source.thread_id,
        kind=RecoveryPointKind.CHECKPOINT,
        checkpoint_id="checkpoint-technical-1",
        checkpoint_ns="",
        graph_node="technical_planning_generate",
        completed_node="technical_planning_generate",
        next_nodes=next_nodes or ["technical_planning_review"],
        lifecycle_revision=7,
        captured_at=now,
    )
    lifecycle = create_application_lifecycle(
        application_id="app-1",
        application_name="事务边界测试",
        initialization_thread_id=source.thread_id,
        active_run_id=source.run_id,
    ).model_copy(
        update={
            "revision": 7,
            "initialization": ApplicationInitialization(
                stage=ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
                status=ApplicationLifecycleStatus.RUNNING,
                threadId=source.thread_id,
            ),
        }
    )
    snapshot = SimpleNamespace(
        config={
            "configurable": {
                "thread_id": source.thread_id,
                "checkpoint_id": point.checkpoint_id,
                "checkpoint_ns": point.checkpoint_ns,
            }
        },
        next=tuple(point.next_nodes),
        tasks=(),
        values=values,
    )
    return snapshot, point, source, lifecycle


class TechnicalPlanningRecoveryContractTests(unittest.TestCase):
    """覆盖 REVIEW_READY 以及 checkpoint 身份漂移的 fail-closed 规则。"""

    def test_review_ready_routes_to_review_without_model_replay(self) -> None:
        """生成失败边界必须只恢复审阅节点，不得重新调用模型节点。"""

        snapshot, point, source, lifecycle = _snapshot_and_point(
            boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
            clarification={
                "mode": "technical_plan_generation_error",
                "status": "requires_user_input",
                "errors": ["候选未通过校验"],
            },
        )
        contract = TechnicalPlanningRecoveryContract()

        self.assertTrue(contract.match(source=source, point=point, snapshot=snapshot))
        self.assertTrue(
            contract.lifecycle_compatible(
                source=source,
                point=point,
                snapshot=snapshot,
                lifecycle=lifecycle,
            )
        )
        self.assertEqual(
            contract._NEXT_BY_BOUNDARY[
                ApplicationPlanningRecoveryBoundary.REVIEW_READY.value
            ],
            "technical_planning_review",
        )

    def test_review_ready_rejects_invalid_clarification_or_successor(self) -> None:
        """错误模式、错误 successor 或边界摘要漂移都必须拒绝恢复。"""

        contract = TechnicalPlanningRecoveryContract()
        cases = (
            (
                "wrong_mode",
                {"mode": "technical_plan_confirmation", "status": "requires_user_input"},
                None,
            ),
            (
                "wrong_successor",
                {
                    "mode": "technical_plan_generation_error",
                    "status": "requires_user_input",
                },
                ["technical_planning_generate"],
            ),
        )
        for name, clarification, successor in cases:
            with self.subTest(name=name):
                snapshot, point, source, _lifecycle = _snapshot_and_point(
                    boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
                    next_nodes=successor,
                    clarification=clarification,
                )
                self.assertFalse(contract.match(source=source, point=point, snapshot=snapshot))

        snapshot, point, source, _lifecycle = _snapshot_and_point(
            boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
            clarification={
                "mode": "technical_plan_generation_error",
                "status": "requires_user_input",
            },
        )
        snapshot.values["technical_plan"] = {**_technical_plan(), "drift": True}
        self.assertNotEqual(
            application_planning_sha256(snapshot.values["technical_plan"]),
            snapshot.values["application_planning_recovery_boundary"]["baselineSha256"],
        )
        self.assertFalse(contract.match(source=source, point=point, snapshot=snapshot))

    def test_native_interrupt_and_owner_drift_have_priority_over_contract(self) -> None:
        """Native Interrupt、run 或 thread 身份漂移不能被业务 boundary 越权接管。"""

        contract = TechnicalPlanningRecoveryContract()
        snapshot, point, source, _lifecycle = _snapshot_and_point(
            boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
            clarification={
                "mode": "project_plan_dependency_validation_error",
                "status": "requires_user_input",
            },
        )
        snapshot.tasks = (
            SimpleNamespace(
                interrupts=(
                    SimpleNamespace(
                        id="native-interrupt",
                        value={"type": "application_planning_review"},
                    ),
                )
            ),
        )
        self.assertFalse(contract.match(source=source, point=point, snapshot=snapshot))

        snapshot, point, source, _lifecycle = _snapshot_and_point(
            boundary=ApplicationPlanningRecoveryBoundary.REVIEW_READY,
            clarification={
                "mode": "technical_plan_generation_error",
                "status": "requires_user_input",
            },
        )
        wrong_point = point.model_copy(update={"thread_id": "other-thread"})
        self.assertFalse(contract.match(source=source, point=wrong_point, snapshot=snapshot))


__all__ = ["TechnicalPlanningRecoveryContractTests"]
