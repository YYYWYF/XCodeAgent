from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.persistence.checkpoints import close_workflow_checkpointer
from app.protocols.application_deletion import build_application_deletion_ag_ui_stream


class ApplicationDeletionProtocolTests(unittest.IsolatedAsyncioTestCase):
    """验证空闲受管应用可以完成停机并取得安全移动目录许可。"""

    async def asyncTearDown(self) -> None:
        """关闭测试可能创建的 SQLite 上下文。"""

        await close_workflow_checkpointer()

    async def test_idle_managed_workspace_returns_ready_for_trash(self) -> None:
        """无活跃任务时仍应清理持久资源并发送完整成功 AG-UI 生命周期。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "managed-app"
            marker = workspace / ".xcodeagent" / "application.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}\n", encoding="utf-8")
            stream = build_application_deletion_ag_ui_stream(
                payload={
                    "threadId": "deletion-thread",
                    "runId": "deletion-run",
                    "forwardedProps": {
                        "applicationDeletion": {
                            "action": "prepare",
                            "applicationId": "application-test",
                            "workspaceRoot": str(workspace),
                        }
                    },
                }
            )

            frames = "\n".join([frame async for frame in stream])

            self.assertIn('"status":"completed"', frames)
            self.assertIn('"readyForTrash":true', frames)
            self.assertIn('"localConnectionClosed":true', frames)
            self.assertIn("RUN_FINISHED", frames)


if __name__ == "__main__":
    unittest.main()
