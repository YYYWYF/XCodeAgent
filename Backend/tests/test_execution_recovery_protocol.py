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
from app.persistence.execution_recovery import (
    get_execution,
    insert_execution,
    list_recovery_attempts_from_source,
)
from app.protocols.execution_recovery import (
    _parse_request,
    _resolve_current_recovery_source,
    build_execution_recovery_ag_ui_stream,
)
from app.domain.application_lifecycle import ApplicationInitialization
from app.services.application_lifecycle import (
    create_application_lifecycle, write_application_lifecycle, start_workbench_execution,
)


class _RecoverySnapshot:
    """提供 Coordinator 校验 checkpoint identity 所需的最小 snapshot。"""

    def __init__(self, thread_id: str, checkpoint_id: str) -> None:
        """保存固定的 root checkpoint 与唯一后继。"""

        self.config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }
        self.next = ("B",)
        self.values = {"status": "running"}
        self.tasks: tuple[object, ...] = ()


class ExecutionRecoveryProtocolTests(unittest.IsolatedAsyncioTestCase):
    """覆盖 Native Recovery 请求 authority 与 REQUIRES_HANDLER 零副作用。"""

    def test_recovery_action_endpoint_is_registered(self) -> None:
        """恢复协议必须通过前端实际调用的 AG-UI 路径暴露，防止再次返回 404。"""

        from app.main import app

        self.assertTrue(
            any(
                getattr(route, "path", None) == "/execution-recovery/execute"
                and "POST" in getattr(route, "methods", set())
                for route in app.app.routes
            )
        )

    def setUp(self) -> None:
        """为每个协议测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放测试工作区。"""

        self._temporary_workspace.cleanup()

    async def test_generic_retry_uses_current_failed_head_from_same_session(self) -> None:
        """旧提示只在同会话当前 head 已失败时交给统一重试入口。"""

        now = datetime.now(timezone.utc)
        common = dict(
            thread_id="retry-thread", workspace=str(self.workspace), project_id="project",
            execution_kind="workbench", workflow_scope="application",
            first_node="prepare_build_tasks", current_node="prepare_build_tasks",
            started_at=now, updated_at=now, ended_at=now,
            owner_session_id="session-A",
        )
        stale = DurableExecutionRecord(
            run_id="old-run", status=DurableExecutionStatus.INTERRUPTED, **common
        )
        current = DurableExecutionRecord(
            run_id="current-run", status=DurableExecutionStatus.FAILED, **common
        )
        graph = SimpleNamespace(
            aget_state=AsyncMock(return_value=SimpleNamespace(values={"active_run_id": current.run_id}))
        )
        with patch(
            "app.protocols.execution_recovery.resolve_recovery_lineage_head",
            new=AsyncMock(return_value=SimpleNamespace(
                state=SimpleNamespace(value="RECOVERABLE_HEAD"), head=current,
            )),
        ):
            resolved = await _resolve_current_recovery_source(
                workspace=str(self.workspace), requested=stale, graph=graph,
                allow_current_failed_head=True,
            )
            with self.assertRaisesRegex(Exception, "已被新的 child execution 替代"):
                await _resolve_current_recovery_source(
                    workspace=str(self.workspace),
                    requested=stale.model_copy(update={"owner_session_id": "session-B"}),
                    graph=graph, allow_current_failed_head=True,
                )
        self.assertEqual(resolved.run_id, current.run_id)

    def test_client_cannot_supply_recovery_authority(self) -> None:
        """checkpoint、node、thread 和策略等执行 authority 必须由 Backend 决定。"""

        for field in (
            "checkpointId",
            "node",
            "resumeFrom",
            "newRunId",
            "threadId",
            "strategy",
        ):
            with self.subTest(field=field):
                with self.assertRaises(Exception) as raised:
                    _parse_request(
                        {
                            "forwardedProps": {
                                "workspaceRoot": str(self.workspace),
                                "executionRecovery": {
                                    "action": "continue",
                                    "sourceRunId": "source-run",
                                    field: "forged",
                                },
                            }
                        }
                    )
                self.assertEqual(raised.exception.code, "INVALID_EXECUTION_RECOVERY_REQUEST")

    def test_standard_ag_ui_envelope_is_accepted(self) -> None:
        """标准 AG-UI envelope 的客户端字段不能阻断 recovery authority 解析。"""

        workspace, action, source_run_id, _incident_id, _action_id = _parse_request(
            {
                "threadId": "frontend-thread",
                "runId": "frontend-run",
                "messages": [],
                "state": {},
                "tools": [],
                "context": [],
                "forwardedProps": {
                    "workspaceRoot": str(self.workspace),
                    "executionRecovery": {
                        "action": "continue",
                        "sourceRunId": "run-A",
                    },
                },
            }
        )

        self.assertEqual(workspace, str(self.workspace))
        self.assertEqual(action, "continue")
        self.assertEqual(source_run_id, "run-A")

    def test_generic_retry_action_is_accepted_without_handler_fields(self) -> None:
        """通用重试只接受统一 action 与 sourceRunId，不接收旧 handler 路由字段。"""

        workspace, action, source_run_id, _incident_id, _action_id = _parse_request(
            {
                "forwardedProps": {
                    "workspaceRoot": str(self.workspace),
                    "executionRecovery": {
                        "action": "retry_current_failure",
                        "sourceRunId": "run-A",
                    },
                }
            }
        )

        self.assertEqual(workspace, str(self.workspace))
        self.assertEqual(action, "retry_current_failure")
        self.assertEqual(source_run_id, "run-A")

        with self.assertRaises(Exception) as raised:
            _parse_request(
                {
                    "forwardedProps": {
                        "workspaceRoot": str(self.workspace),
                        "executionRecovery": {
                            "action": "retry_current_failure",
                            "sourceRunId": "run-A",
                            "handler": "retry_failed_tasks",
                        },
                    }
                }
            )
        self.assertEqual(raised.exception.code, "INVALID_EXECUTION_RECOVERY_REQUEST")

    async def test_retry_entry_replans_needs_attention_without_consuming_old_action(
        self,
    ) -> None:
        """永久 Retry Entry 每次只重解析当前失败，无法证明安全时保持零副作用。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="needs-attention-run",
            thread_id="needs-attention-thread",
            workspace=str(self.workspace),
            project_id="needs-attention-project",
            execution_kind="application_planning",
            workflow_scope="application_planning",
            first_node="technical_planning",
            current_node="technical_planning",
            status=DurableExecutionStatus.FAILED,
            started_at=now,
            updated_at=now,
            ended_at=now,
            failure=ExecutionFailureEvidence(
                origin=ExecutionFailureOrigin.MODEL_CALL,
                code="MODEL_NOT_FOUND",
                operation="technical_planning",
                http_status=404,
                replay_compatible=True,
            ),
        )
        await insert_execution(source)
        payload = {
            "forwardedProps": {
                "workspaceRoot": str(self.workspace),
                "executionRecovery": {
                    "action": "retry_current_failure",
                    "sourceRunId": source.run_id,
                },
            }
        }
        graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(
            values={"active_run_id": source.run_id}
        )))
        with patch(
            "app.protocols.execution_recovery.application_planning_graph_for_request",
            new=AsyncMock(return_value=graph),
        ):
            outputs = [
                "".join(
                    [
                        frame
                        async for frame in build_execution_recovery_ag_ui_stream(
                            payload=payload
                        )
                    ]
                )
                for _ in range(2)
            ]

        for output in outputs:
            self.assertIn("NODE_ENTRY_AUTHORITY_MISSING", output)
            self.assertIn('"type":"RUN_ERROR"', output)
        self.assertEqual(
            await list_recovery_attempts_from_source(self.workspace, source.run_id),
            [],
        )

    async def test_missing_committed_history_has_no_recovery_side_effects(self) -> None:
        """没有 committed history 时必须 fail closed，且不 claim child。"""

        now = datetime.now(timezone.utc)
        source = DurableExecutionRecord(
            run_id="source-run",
            thread_id="recovery-thread",
            workspace=str(self.workspace),
            project_id=None,
            execution_kind="workbench",
            workflow_scope=None,
            first_node="B",
            current_node="B",
            status=DurableExecutionStatus.INTERRUPTED,
            started_at=now,
            updated_at=now,
            ended_at=now,
        )
        # 先提供当前合同要求的 lifecycle ownership，才能单独验证缺少 history 的拒绝路径。
        lifecycle = create_application_lifecycle(application_id="history-app", application_name="History")
        lifecycle = lifecycle.model_copy(update={"initialization": ApplicationInitialization(
            stage="ready_for_workbench", status="completed", threadId="planning")})
        write_application_lifecycle(self.workspace, lifecycle, expected_revision=0)
        start_workbench_execution(self.workspace, scope="application", target_id="application",
            page_id=None, thread_id=source.thread_id, run_id=source.run_id, phase="B")
        await insert_execution(source)
        snapshot = _RecoverySnapshot(source.thread_id, "source-checkpoint")
        snapshot.values["active_run_id"] = source.run_id

        async def aget_state(_config: dict[str, object]) -> _RecoverySnapshot:
            """保留 direct state reader，但不提供 committed history reader。"""

            return snapshot

        graph = SimpleNamespace(aget_state=aget_state)
        payload = {
            "forwardedProps": {
                "workspaceRoot": str(self.workspace),
                "executionRecovery": {
                    "action": "continue",
                    "sourceRunId": source.run_id,
                },
            }
        }
        with patch(
            "app.protocols.execution_recovery.workflow_graph_for_request",
            new=AsyncMock(return_value=graph),
        ):
            frames = [
                frame
                async for frame in build_execution_recovery_ag_ui_stream(
                    payload=payload,
                )
            ]

        output = "".join(frames)
        self.assertIn("INTERRUPTED_CHECKPOINT_AUTHORITY_MISSING", output)
        self.assertEqual(await list_recovery_attempts_from_source(self.workspace, source.run_id), [])
        self.assertIsNone(await get_execution(self.workspace, "recovery-child"))


__all__ = ["ExecutionRecoveryProtocolTests"]
