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
    RecoveryPoint,
    RecoveryPointKind,
)
from app.persistence.execution_recovery import (
    get_execution,
    insert_execution,
    insert_recovery_point,
    list_recovery_attempts_from_source,
)
from app.protocols.execution_recovery import (
    _parse_request,
    build_execution_recovery_ag_ui_stream,
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

    def setUp(self) -> None:
        """为每个协议测试准备隔离工作区。"""

        self._temporary_workspace = tempfile.TemporaryDirectory()
        self.workspace = Path(self._temporary_workspace.name)

    def tearDown(self) -> None:
        """释放测试工作区。"""

        self._temporary_workspace.cleanup()

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

    async def test_requires_handler_has_no_recovery_side_effects(self) -> None:
        """默认 replay safety 未评估时只返回结构化拒绝，不 claim child 或修改 lifecycle。"""

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
        await insert_execution(source)
        await insert_recovery_point(
            workspace=self.workspace,
            point=RecoveryPoint(
                recovery_point_id="source-point",
                run_id=source.run_id,
                thread_id=source.thread_id,
                kind=RecoveryPointKind.CHECKPOINT,
                checkpoint_id="source-checkpoint",
                checkpoint_ns="",
                graph_node="B",
                next_nodes=["B"],
                captured_at=now,
            ),
        )
        snapshot = _RecoverySnapshot(source.thread_id, "source-checkpoint")

        async def aget_state(_config: dict[str, object]) -> _RecoverySnapshot:
            """返回与 RecoveryPoint 完全一致的 checkpoint。"""

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
        self.assertIn("RECOVERY_REQUIRES_HANDLER", output)
        self.assertEqual(await list_recovery_attempts_from_source(self.workspace, source.run_id), [])
        self.assertIsNone(await get_execution(self.workspace, "recovery-child"))


__all__ = ["ExecutionRecoveryProtocolTests"]
