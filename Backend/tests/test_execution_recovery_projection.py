from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.execution_recovery import (
    DurableExecutionRecord,
    DurableExecutionStatus,
    ExecutionFailureEvidence,
    ExecutionFailureOrigin,
)
from app.services.execution_recovery_projection import (
    recovery_failure_diagnostic,
    resolve_execution_recovery_projection,
)


class ExecutionRecoveryProjectionTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 lifecycle GET 的 canonical interrupted execution 安全投影。"""

    def setUp(self) -> None:
        """为投影测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放投影测试的临时工作区。"""

        self._temporary_workspace.cleanup()

    def _record(self, run_id: str, thread_id: str = "thread-A") -> DurableExecutionRecord:
        """构造一条只包含公开投影来源字段的中断 execution。"""

        now = datetime.now(timezone.utc)
        return DurableExecutionRecord(
            run_id=run_id,
            thread_id=thread_id,
            owner_session_id="session-A",
            workspace=str(self.workspace),
            project_id=None,
            execution_kind="workbench",
            workflow_scope=None,
            first_node="build",
            current_node="build",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )

    async def test_empty_projection_when_no_interrupted_execution_exists(self) -> None:
        """没有中断 execution 时返回空 candidates。"""

        with patch(
            "app.services.execution_recovery_projection.list_recovery_projection_candidates",
            new=AsyncMock(return_value=[]),
        ):
            projection = await resolve_execution_recovery_projection(str(self.workspace))

        self.assertEqual(projection.candidates, [])
        self.assertIsNone(projection.error)

    async def test_candidate_query_failure_is_not_successful_empty_projection(self) -> None:
        """候选查询失败必须显式标记，不能把停服重连后的未知状态当成无任务。"""
        with patch("app.services.execution_recovery_projection.list_recovery_projection_candidates",
                   new=AsyncMock(side_effect=OSError("store unavailable"))):
            projection = await resolve_execution_recovery_projection(str(self.workspace))
        self.assertEqual(projection.candidates, [])
        self.assertEqual(projection.error.code, "RECOVERY_PROJECTION_READ_FAILED")

    async def test_single_candidate_failure_marks_projection_incomplete(self) -> None:
        """单个候选解析失败同样不能授权清除原恢复信息。"""
        with (
            patch("app.services.execution_recovery_projection.list_recovery_projection_candidates",
                  new=AsyncMock(return_value=[self._record("run-A")])),
            patch("app.services.execution_recovery_projection._resolve_candidate",
                  new=AsyncMock(side_effect=OSError("checkpoint unavailable"))),
        ):
            projection = await resolve_execution_recovery_projection(str(self.workspace))
        self.assertEqual(projection.error.code, "RECOVERY_PROJECTION_READ_FAILED")

    async def test_current_retry_only_exposes_public_fields(self) -> None:
        """当前重试只公开节点动作，不泄漏 checkpoint 或旧恢复 authority。"""
        from app.services.business_node_retry import RetryHint
        record = self._record("run-A")
        with patch("app.services.execution_recovery_projection.list_recovery_projection_candidates", AsyncMock(return_value=[record])), \
                patch("app.services.checkpoint_execution.resolve_checkpoint_source", AsyncMock(return_value=record)), \
                patch("app.services.execution_recovery_projection.workflow_graph_for_request", AsyncMock(return_value=object())), \
                patch("app.services.business_node_retry.read_retry_hint", AsyncMock(return_value=RetryHint("build", "original question"))):
            projection = await resolve_execution_recovery_projection(str(self.workspace))
        candidate = projection.candidates[0].model_dump(mode="json", by_alias=True)
        self.assertTrue(candidate["canContinue"])
        self.assertEqual(candidate["availability"], "ready")
        self.assertEqual(candidate["ownerSessionId"], "session-A")
        for forbidden in ("checkpointId", "checkpointNs", "workspaceRevision", "workspaceSnapshotHash", "recoveryPointId", "strategy"):
            self.assertNotIn(forbidden, candidate)

    async def test_missing_node_produces_dismissible_notice(self) -> None:
        """没有节点时签发当前可关闭提示并收口原执行，不依赖已移除的精确恢复 resolver。"""
        record = self._record("run-invalid")
        with patch("app.services.execution_recovery_projection.list_recovery_projection_candidates", AsyncMock(return_value=[record])), \
                patch("app.services.checkpoint_execution.resolve_checkpoint_source", AsyncMock(return_value=record)), \
                patch("app.services.execution_recovery_projection.workflow_graph_for_request", AsyncMock(return_value=object())), \
                patch("app.services.business_node_retry.read_retry_hint", AsyncMock(return_value=None)), \
                patch("app.services.business_node_retry.release_unretryable_execution", AsyncMock()) as release:
            projection = await resolve_execution_recovery_projection(str(self.workspace))
        self.assertEqual(projection.candidates[0].reason_code, "RETRY_ENTRY_NOT_FOUND")
        self.assertFalse(projection.candidates[0].can_continue)
        release.assert_awaited_once_with(record)

    async def test_unowned_record_is_not_assigned_owner_from_checkpoint(self) -> None:
        """当前合同不支持历史 owner 补造，checkpoint 只用于节点与提问定位。"""
        from app.services.business_node_retry import RetryHint
        record = self._record("run-unowned").model_copy(update={"owner_session_id": None})
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(
            values={"active_run_id": record.run_id, "owner_session_id": "checkpoint-owner", "request": "original"},
            next=("build",), tasks=(), config={})))
        with patch("app.services.execution_recovery_projection.list_recovery_projection_candidates", AsyncMock(return_value=[record])), \
                patch("app.services.execution_recovery_projection.workflow_graph_for_request", AsyncMock(return_value=graph)), \
                patch("app.services.business_node_retry.read_retry_hint", AsyncMock(return_value=RetryHint("build", "original question"))):
            projection = await resolve_execution_recovery_projection(str(self.workspace))
        self.assertEqual(projection.candidates, [])
        self.assertIsNone(record.owner_session_id)
        graph.aget_state.assert_awaited_once()

    async def test_business_failure_uses_only_matching_lifecycle_error(self) -> None:
        """无异常 evidence 时补充同 Run/Thread 原始业务错误，不读取其他会话错误。"""
        from types import SimpleNamespace

        record = self._record("run-business").model_copy(update={"status": DurableExecutionStatus.FAILED})
        execution = SimpleNamespace(thread_id=record.thread_id, phase="unit_test",
            error=SimpleNamespace(code="workbench_execution_failed", message="真实单测错误"))
        with patch("app.services.execution_recovery_projection.load_application_lifecycle",
            return_value=SimpleNamespace(active_executions={record.run_id: execution})):
            self.assertEqual(recovery_failure_diagnostic(record)["message"], "真实单测错误")
            execution.thread_id = "other-thread"
            self.assertIsNone(recovery_failure_diagnostic(record))

    async def test_failure_diagnostic_uses_public_camel_case_contract(self) -> None:
        """真实失败证据只能通过公共 mapper 暴露，不能直接泄漏 domain 字段名。"""

        record = self._record("run-failure").model_copy(
            update={
                "status": DurableExecutionStatus.FAILED,
                "ended_at": datetime.now(timezone.utc),
                "failure": ExecutionFailureEvidence(
                    origin=ExecutionFailureOrigin.MODEL_CALL,
                    code="MODEL_NOT_FOUND",
                    operation="code_review",
                    dependency="model-provider",
                    provider="openai",
                    model="missing-model",
                    http_status=404,
                    diagnostic_message="model not found",
                ),
            }
        )

        self.assertEqual(
            recovery_failure_diagnostic(record),
            {
                "sourceRunId": "run-failure",
                "origin": "model_call",
                "code": "MODEL_NOT_FOUND",
                "operation": "code_review",
                "dependency": "model-provider",
                "provider": "openai",
                "model": "missing-model",
                "httpStatus": 404,
                "message": "model not found",
            },
        )


if __name__ == "__main__":
    unittest.main()
