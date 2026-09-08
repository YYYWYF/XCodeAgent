from __future__ import annotations

import asyncio
import json
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
    application_deletion_capabilities,
    _PREPARED_DELETIONS,
)
from app.protocols.application_lifecycle import build_application_lifecycle_ag_ui_stream
from app.services.application_lifecycle import load_application_lifecycle
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

    def test_capabilities_include_deletion_completion(self) -> None:
        """健康元数据公开当前删除准备及收尾动作。"""

        self.assertEqual(application_deletion_capabilities()["actions"], ["prepare", "complete"])

    def _release_test_fences(self, workspace: Path) -> None:
        """清理成功路径按生产语义保留的 fence，避免测试进程积累临时目录键。"""

        workspace_text = str(workspace.resolve(strict=False))
        _PREPARED_DELETIONS.pop(workspace_text, None)
        application_template_generation.end_application_template_deletion(workspace)
        workflow_run_registry.end_workspace_deletion(workspace_text)
        workspace_process_registry.end_workspace_deletion(workspace)
        get_ui_design_generation_pool().end_workspace_deletion(workspace_text)

    async def _complete_deletion(self, workspace: Path, application_id: str) -> str:
        """通过公开 AG-UI 动作完成删除并收集完整事件流。"""

        return "".join([frame async for frame in build_application_deletion_ag_ui_stream(
            payload={
                "threadId": "complete-thread", "runId": "complete-run",
                "forwardedProps": {"applicationDeletion": {
                    "action": "complete", "applicationId": application_id,
                    "workspaceRoot": str(workspace),
                }},
            },
        )])

    async def test_trash_then_recreate_same_path_can_create_and_run(self) -> None:
        """移动成功后解除所有封锁，同路径的新应用可创建 lifecycle 并登记运行。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "same-name"
            marker = workspace / ".xcodeagent" / "application.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}", encoding="utf-8")
            try:
                await prepare_application_deletion(ApplicationDeletionRequest(
                    action="prepare", applicationId="old-app", workspaceRoot=str(workspace),
                ))
                workspace.rename(Path(directory) / "trash")
                frames = await self._complete_deletion(workspace, "old-app")
                self.assertIn('"deletionCompleted":true', frames)
                self.assertIn("STATE_SNAPSHOT", frames)
                self.assertIn("RUN_FINISHED", frames)
                self.assertFalse(workflow_run_registry.is_workspace_deleting(str(workspace)))
                self.assertNotIn(process_workspace_key(workspace), workspace_process_registry._deleting_workspaces)
                self.assertNotIn(str(workspace), get_ui_design_generation_pool()._deleting_workspaces)
                self.assertNotIn(application_template_generation._template_workspace_key(workspace),
                                 application_template_generation._DELETING_TEMPLATE_WORKSPACES)
                # 收尾响应丢失后的重复确认不应失败。
                self.assertIn('"deletionCompleted":true', await self._complete_deletion(workspace, "old-app"))
                marker.parent.mkdir(parents=True)
                marker.write_text(json.dumps({"appName": "新应用"}), encoding="utf-8")
                stream = build_application_lifecycle_ag_ui_stream(payload={
                    "runId": "new-run", "threadId": "new-thread",
                    "forwardedProps": {"applicationLifecycle": {
                        "action": "create", "workspaceRoot": str(workspace),
                        "application": {"id": "new-app", "appName": "新应用"},
                    }},
                })
                created = "".join([frame async for frame in stream])
                self.assertIn('"status":"completed"', created)
                self.assertEqual(load_application_lifecycle(workspace).application.id, "new-app")
                current_task = asyncio.current_task()
                workflow_run_registry.register("new-planning", current_task, workspace=str(workspace))
                workflow_run_registry.unregister("new-planning", current_task)
                # 迟到的旧删除确认不能解除或触碰已重建的工作区。
                self.assertIn('"status":"failed"', await self._complete_deletion(workspace, "old-app"))
                self.assertEqual(load_application_lifecycle(workspace).application.id, "new-app")
            finally:
                self._release_test_fences(workspace)

    async def test_completion_requires_trash_success_and_matching_application(self) -> None:
        """移动失败、目录仍在或错误应用身份均不能提前解除删除封锁。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "project"
            marker = workspace / ".xcodeagent" / "application.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}", encoding="utf-8")
            try:
                await prepare_application_deletion(ApplicationDeletionRequest(
                    action="prepare", applicationId="old-app", workspaceRoot=str(workspace),
                ))
                self.assertIn('"status":"failed"', await self._complete_deletion(workspace, "old-app"))
                self.assertTrue(workflow_run_registry.is_workspace_deleting(str(workspace)))
                workspace.rename(Path(directory) / "trash")
                self.assertIn('"status":"failed"', await self._complete_deletion(workspace, "wrong-app"))
                self.assertTrue(workflow_run_registry.is_workspace_deleting(str(workspace)))
            finally:
                self._release_test_fences(workspace)

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
