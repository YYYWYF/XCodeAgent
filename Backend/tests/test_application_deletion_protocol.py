from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.persistence.checkpoints import close_workflow_checkpointer
from app.protocols.application_deletion import (
    ApplicationDeletionRequest,
    build_application_deletion_ag_ui_stream,
    prepare_application_deletion,
)
from app.protocols.workflow.run_control import workflow_run_registry
from app.services import application_template_generation
from app.services.ui_design_generation_pool import get_ui_design_generation_pool
from app.services.workspace_process_registry import (
    _workspace_key as process_workspace_key,
    workspace_process_registry,
)


class ApplicationDeletionProtocolTests(unittest.IsolatedAsyncioTestCase):
    """验证空闲受管应用可以完成停机并取得安全移动目录许可。"""

    async def asyncTearDown(self) -> None:
        """关闭测试可能创建的 SQLite 上下文。"""

        await close_workflow_checkpointer()

    def _release_test_fences(self, workspace: Path) -> None:
        """清理成功路径按生产语义保留的 fence，避免测试进程积累临时目录键。"""

        workspace_text = str(workspace.resolve(strict=False))
        application_template_generation.end_application_template_deletion(workspace)
        workflow_run_registry.end_workspace_deletion(workspace_text)
        workspace_process_registry.end_workspace_deletion(workspace)
        get_ui_design_generation_pool().end_workspace_deletion(workspace_text)

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

            try:
                frames = "\n".join([frame async for frame in stream])

                self.assertIn('"status":"completed"', frames)
                self.assertIn('"readyForTrash":true', frames)
                self.assertIn('"localConnectionClosed":true', frames)
                self.assertIn("RUN_FINISHED", frames)
                workspace_text = str(workspace.resolve(strict=False))
                self.assertTrue(workflow_run_registry.is_workspace_deleting(workspace_text))
                self.assertIn(
                    application_template_generation._template_workspace_key(workspace),
                    application_template_generation._DELETING_TEMPLATE_WORKSPACES,
                )
                self.assertIn(
                    process_workspace_key(workspace),
                    workspace_process_registry._deleting_workspaces,
                )
                self.assertIn(
                    workspace_text,
                    get_ui_design_generation_pool()._deleting_workspaces,
                )
            finally:
                self._release_test_fences(workspace)

    async def test_stop_failure_releases_all_workspace_deletion_fences(self) -> None:
        """可逆停机失败后四类删除栅栏都应解除，项目可以继续启动运行和命令。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "managed-app"
            marker = workspace / ".xcodeagent" / "application.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}\n", encoding="utf-8")
            request = ApplicationDeletionRequest(
                action="prepare",
                applicationId="application-failed-stop",
                workspaceRoot=str(workspace),
            )

            with patch(
                "app.protocols.application_deletion.stop_project_preview",
                return_value={"status": "failed", "message": "preview stop failed"},
            ):
                with self.assertRaisesRegex(RuntimeError, "preview stop failed"):
                    await prepare_application_deletion(request)

            workspace_text = str(workspace.resolve(strict=False))
            current_task = asyncio.current_task()
            self.assertIsNotNone(current_task)
            workflow_run_registry.register(
                "retry-run",
                current_task,  # type: ignore[arg-type]
                workspace=workspace_text,
            )
            workflow_run_registry.unregister("retry-run", current_task)
            process = workspace_process_registry.run(
                [sys.executable, "-c", "pass"],
                workspace=workspace,
            )
            self.assertEqual(process.returncode, 0)
            self.assertNotIn(
                application_template_generation._template_workspace_key(workspace),
                application_template_generation._DELETING_TEMPLATE_WORKSPACES,
            )
            self.assertNotIn(
                workspace_text,
                get_ui_design_generation_pool()._deleting_workspaces,
            )


if __name__ == "__main__":
    unittest.main()
