"""Application Planning 薄恢复投影的 authority 边界回归测试。"""

from __future__ import annotations

import tempfile
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation,
    ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload,
)
from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
)
from app.protocols.application_page_planning import (
    _build_application_planning_recovery_projection,
    _build_application_planning_recovery_ag_ui_stream,
)
from app.services.application_lifecycle import (
    create_application_lifecycle,
    transition_application_lifecycle,
    write_application_lifecycle,
)


class ApplicationPlanningRecoveryProjectionTests(unittest.IsolatedAsyncioTestCase):
    """锁定 Lifecycle 与 inputCommitted 都不能成为 FAILED recovery gate。"""

    def setUp(self) -> None:
        """创建隔离工作区与可进入 Generic FAILED recovery 的 source。"""

        self.temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_workspace.name)
        now = datetime.now(timezone.utc)
        self.source = DurableExecutionRecord(
            run_id="planning-failed",
            thread_id="planning-thread",
            workspace=str(self.workspace),
            project_id="planning-app",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="requirements",
            current_node="technical_planning_generate",
            status=DurableExecutionStatus.FAILED,
            started_at=now,
            updated_at=now,
            ended_at=now,
            failure=ExecutionFailureEvidence(
                origin=ExecutionFailureOrigin.MODEL_CALL,
                code="MODEL_FAILED",
                operation="technical_planning_generate",
            ),
        )

    def tearDown(self) -> None:
        """释放隔离工作区。"""

        self.temporary_workspace.cleanup()

    async def test_no_checkpoint_returns_normal_completed_action(self) -> None:
        """没有存档正常结束只读动作，不返回错误、恢复动作或运行 Graph。"""
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(values={}, config={})))
        with patch("app.protocols.application_page_planning.reconcile_workspace_recovery", new=AsyncMock()):
            frames = [frame async for frame in _build_application_planning_recovery_ag_ui_stream(
                graph=graph, payload={"threadId": "empty-thread", "runId": "read-empty"},
                recovery_input={"action": "get", "workspaceRoot": str(self.workspace),
                                "applicationId": "planning-app"}, accept=None,
            )]
        events = [json.loads(line[6:]) for frame in frames for line in frame.splitlines()
                  if line.startswith("data: ")]
        result = next(event["value"] for event in events if event["type"] == "CUSTOM")
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["checkpointAvailable"])
        self.assertNotIn("error", result)
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")

    async def test_existing_empty_checkpoint_does_not_block_main_flow(self) -> None:
        """无业务执行的空 checkpoint 不再生成阻塞错误。"""
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(
            values={}, config={"configurable": {"checkpoint_id": "broken-checkpoint"}},
        )))
        with patch("app.protocols.application_page_planning.reconcile_workspace_recovery", new=AsyncMock()):
            frames = [frame async for frame in _build_application_planning_recovery_ag_ui_stream(
                graph=graph, payload={"threadId": "broken-thread", "runId": "read-broken"},
                recovery_input={"action": "get", "workspaceRoot": str(self.workspace),
                                "applicationId": "planning-app"}, accept=None,
            )]
        self.assertIn('"checkpointAvailable":false', "".join(frames))
        self.assertNotIn('"type":"RUN_ERROR"', "".join(frames))

    async def test_released_failed_owner_still_gets_missing_entry_notice(self) -> None:
        """先读 lifecycle 释放 owner 后，同一失败 thread 仍显示无入口提示。"""
        lifecycle = create_application_lifecycle(application_id="planning-app", application_name="验收",
            initialization_thread_id=self.source.thread_id)
        lifecycle = lifecycle.model_copy(update={"initialization": lifecycle.initialization.model_copy(
            update={"stage": ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                    "status": ApplicationLifecycleStatus.FAILED})})
        write_application_lifecycle(str(self.workspace), lifecycle)

        async def empty_history(config):
            """模拟 checkpoint 查询成功但来源 Run 没有任何已提交现场。"""
            if False:
                yield None

        graph = SimpleNamespace(
            aget_state=AsyncMock(return_value=SimpleNamespace(values={}, config={})),
            aget_state_history=empty_history,
        )
        with (patch("app.protocols.application_page_planning.reconcile_workspace_recovery", new=AsyncMock()),
              patch("app.persistence.execution_recovery.list_executions_for_thread", new=AsyncMock(return_value=[self.source])),
              patch("app.persistence.execution_recovery.get_execution", new=AsyncMock(return_value=self.source))):
            frames = [frame async for frame in _build_application_planning_recovery_ag_ui_stream(
                graph=graph, payload={"threadId": self.source.thread_id, "runId": "read-released"},
                recovery_input={"action": "get", "workspaceRoot": str(self.workspace),
                                "applicationId": "planning-app"}, accept=None)]
        content = "".join(frames)
        self.assertIn("RETRY_ENTRY_NOT_FOUND", content)
        self.assertIn("找不到恢复入口，请手动发起流程。", content)
        self.assertNotIn('"type":"RUN_ERROR"', content)

    def _snapshot(self, boundary: ApplicationPlanningRecoveryBoundary) -> object:
        """构造仅改变 inputCommitted 展示边界的同一 FAILED checkpoint。"""

        return SimpleNamespace(
            values={
                "active_run_id": self.source.run_id,
                "application_planning_recovery_boundary": (
                    application_planning_boundary_payload(
                        operation_id="technical-plan-operation",
                        operation=ApplicationPlanningOperation.INITIAL,
                        boundary=boundary,
                        request="创建任务中心",
                    )
                ),
            }
        )

    async def _project(self, boundary: ApplicationPlanningRecoveryBoundary):
        """固定 Generic resolver/action 结果并调用公开 Workflow 投影边界。"""

        action_plan = MagicMock()
        action_plan.primary_action = object()
        action_plan.reason_code = "FAILED_NODE_REENTRY_READY"
        action_plan.message = "可以重试失败节点。"
        action_plan.model_dump.return_value = {
            "status": "recoverable",
            "primaryAction": {
                "kind": "retry_failed_node",
                "targetNode": self.source.current_node,
            },
        }
        with (
            patch(
                "app.protocols.application_page_planning.FailureTargetResolver.resolve",
                new=AsyncMock(return_value=object()),
            ),
            patch(
                "app.services.business_node_retry.retry_action_plan",
                new=AsyncMock(return_value=action_plan),
            ),
        ):
            return await _build_application_planning_recovery_projection(
                workspace=str(self.workspace),
                thread_id=self.source.thread_id,
                graph=object(),
                snapshot=self._snapshot(boundary),
                source=self.source,
            )

    async def test_stale_awaiting_user_lifecycle_cannot_veto_failed_recovery(self) -> None:
        """stale AWAITING_USER mirror 存在时 FAILED 仍由 Generic resolver 决定。"""

        lifecycle = create_application_lifecycle(
            application_id="planning-app",
            application_name="任务中心",
            initialization_thread_id=self.source.thread_id,
            active_run_id=self.source.run_id,
        )
        for stage, status in (
            (ApplicationLifecycleStage.ANALYZING_REQUIREMENT, ApplicationLifecycleStatus.RUNNING),
            (
                ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
                ApplicationLifecycleStatus.RUNNING,
            ),
            (
                ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
                ApplicationLifecycleStatus.AWAITING_USER,
            ),
        ):
            lifecycle = transition_application_lifecycle(
                lifecycle,
                stage=stage,
                status=status,
            )
        write_application_lifecycle(self.workspace, lifecycle)

        projection = await self._project(
            ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED
        )

        self.assertEqual(projection.classification, "ready_to_continue")
        self.assertTrue(projection.can_continue)

    async def test_input_committed_changes_only_public_presentation(self) -> None:
        """同一 FAILED authority 的 action 不随 inputCommitted 展示事实变化。"""

        committed = await self._project(
            ApplicationPlanningRecoveryBoundary.INPUT_COMMITTED
        )
        generation_ready = await self._project(
            ApplicationPlanningRecoveryBoundary.GENERATION_READY
        )

        self.assertTrue(committed.input_committed)
        self.assertFalse(generation_ready.input_committed)
        self.assertEqual(
            committed.recovery_action_plan,
            generation_ready.recovery_action_plan,
        )
        expected_fields = {
            "schemaVersion",
            "classification",
            "sourceRunId",
            "threadId",
            "canContinue",
            "userActionRequired",
            "inputCommitted",
            "reasonCode",
            "message",
            "failureDiagnostic",
            "recoveryActionPlan",
            "uiGenerationRecovery",
        }
        self.assertEqual(set(committed.to_payload()), expected_fields)
        self.assertEqual(set(generation_ready.to_payload()), expected_fields)
