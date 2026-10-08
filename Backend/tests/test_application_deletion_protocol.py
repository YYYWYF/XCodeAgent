from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from app.persistence.checkpoints import close_workflow_checkpointer, workflow_checkpointer
from app.protocols.application_deletion import (
    ApplicationDeletionCompletionRequest,
    ApplicationDeletionRequest,
    build_application_deletion_ag_ui_stream,
    complete_application_deletion,
    prepare_application_deletion,
)
from app.protocols.workflow.run_control import workflow_run_registry
from app.services.ui_design_generation_pool import get_ui_design_generation_pool
from app.services.backend_process_registry import register_backend_process
from app.services.workspace_process_registry import (
    _workspace_key as process_workspace_key,
    workspace_process_registry,
)
from app.services.workspace_bootstrap.coordinator import template_mutation_coordinator
from app.workspace.planning_recovery_documents import planning_recovery_directory


class ApplicationDeletionProtocolTests(unittest.IsolatedAsyncioTestCase):
    """验证空闲受管应用可以完成停机并取得安全移动目录许可。"""

    async def asyncTearDown(self) -> None:
        """关闭测试可能创建的 SQLite 上下文。"""

        await close_workflow_checkpointer()

    def _release_test_fences(self, workspace: Path) -> None:
        """清理成功路径按生产语义保留的 fence，避免测试进程积累临时目录键。"""

        workspace_text = str(workspace.resolve(strict=False))
        template_mutation_coordinator.cancel_deletion(workspace)
        workflow_run_registry.end_workspace_deletion(workspace_text)
        workspace_process_registry.end_workspace_deletion(workspace)
        get_ui_design_generation_pool().end_workspace_deletion(workspace_text)

    async def test_idle_managed_workspace_returns_ready_for_trash(self) -> None:
        """无活跃任务时仍应清理持久资源并发送完整成功 AG-UI 生命周期。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "managed-app"
            marker = workspace / ".devagentstudio" / "application.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}\n", encoding="utf-8")
            recovery_file = planning_recovery_directory({"workspace": str(workspace)}) / "run-delete.json"
            recovery_file.parent.mkdir(parents=True, exist_ok=True)
            recovery_file.write_text("{}\n", encoding="utf-8")
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
                self.assertFalse(recovery_file.exists())
                workspace_text = str(workspace.resolve(strict=False))
                self.assertTrue(workflow_run_registry.is_workspace_deleting(workspace_text))
                self.assertIn(str(workspace.resolve(strict=False)), template_mutation_coordinator._deleting)
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
            marker = workspace / ".devagentstudio" / "application.json"
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
            self.assertNotIn(str(workspace.resolve(strict=False)), template_mutation_coordinator._deleting)
            self.assertNotIn(
                workspace_text,
                get_ui_design_generation_pool()._deleting_workspaces,
            )

    async def test_missing_workspace_cleans_up_without_recreating_directory(self) -> None:
        """用户手动删除目录后仍取消残留任务，重复准备和完成均不重建目录。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "xc50"
            workspace_text = str(workspace.resolve(strict=False))
            task = asyncio.create_task(asyncio.Event().wait())
            workflow_run_registry.register("missing-workspace-run", task, workspace=workspace_text)
            request = ApplicationDeletionRequest(
                action="prepare", applicationId="missing-application", workspaceRoot=workspace_text,
            )
            try:
                first = await prepare_application_deletion(request)
                self.assertTrue(first["readyForTrash"])
                self.assertTrue(task.cancelled())
                self.assertIn("missing-workspace-run", first["runs"]["requestedRunIds"])
                self.assertFalse(workspace.exists())
                second = await prepare_application_deletion(request)
                self.assertTrue(second["readyForTrash"])
                self.assertFalse(workspace.exists())
                result = complete_application_deletion(ApplicationDeletionCompletionRequest(
                    action="complete", applicationId=request.application_id, workspaceRoot=workspace_text,
                ))
                self.assertTrue(result["deletionCompleted"])
                self.assertFalse(workflow_run_registry.is_workspace_deleting(workspace_text))
                self.assertFalse(workspace.exists())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                workflow_run_registry.unregister("missing-workspace-run", task)
                self._release_test_fences(workspace)

    async def test_missing_workspace_closes_cached_checkpoint_and_graphs(self) -> None:
        """外部移走目录后关闭仍持有旧 SQLite 的连接并释放对应 Graph 缓存。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "managed-app"
            saver = await workflow_checkpointer(workspace=str(workspace), project_id="cached-app")
            workspace.rename(Path(directory) / "externally-removed")
            try:
                with (
                    patch("app.protocols.application_deletion.clear_workflow_graph_cache") as workflow_cache,
                    patch("app.protocols.application_deletion.clear_application_planning_graph_cache") as planning_cache,
                    patch("app.protocols.application_deletion.clear_direct_modification_graph_cache") as direct_cache,
                ):
                    result = await prepare_application_deletion(ApplicationDeletionRequest(
                        action="prepare", applicationId="cached-app", workspaceRoot=str(workspace),
                    ))
                self.assertTrue(result["checkpoints"]["localConnectionClosed"])
                for cache in (workflow_cache, planning_cache, direct_cache):
                    cache.assert_called_once_with(cache_key=result["checkpoints"]["databasePath"])
                with self.assertRaises(ValueError):
                    await saver.conn.execute("SELECT 1")
                self.assertFalse(workspace.exists())
            finally:
                self._release_test_fences(workspace)

    async def test_missing_workspace_stops_registered_backend_process(self) -> None:
        """即使 pom.xml 随项目目录消失，也必须停止内存登记的 Java 预览进程。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "missing-backend-app"
            process = MagicMock()
            process.pid = 9876543
            process.poll.return_value = None

            def finish_process(**_kwargs: object) -> int:
                """模拟等待已终止的进程，后续存活检查返回退出码。"""

                process.poll.return_value = 0
                return 0

            process.wait.side_effect = finish_process
            register_backend_process(workspace, process)
            try:
                result = await prepare_application_deletion(ApplicationDeletionRequest(
                    action="prepare", applicationId="missing-backend-app", workspaceRoot=str(workspace),
                ))
                process.terminate.assert_called_once()
                self.assertTrue(result["preview"]["backend"]["cleanup"]["success"])
                self.assertFalse(workspace.exists())
            finally:
                self._release_test_fences(workspace)

    async def test_missing_workspace_emits_complete_ag_ui_lifecycle(self) -> None:
        """缺失目录的 AG-UI 删除准备仍输出成功消息、结果、快照和结束事件。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "missing-app"
            try:
                stream = build_application_deletion_ag_ui_stream(payload={
                    "threadId": "missing-thread", "runId": "missing-run",
                    "forwardedProps": {"applicationDeletion": {
                        "action": "prepare", "applicationId": "missing-app", "workspaceRoot": str(workspace),
                    }},
                })
                frames = "\n".join([frame async for frame in stream])
                for event_type in ("RUN_STARTED", "TEXT_MESSAGE_CONTENT", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"):
                    self.assertIn(event_type, frames)
                self.assertIn('"readyForTrash":true', frames)
                self.assertIn('"status":"completed"', frames)
                self.assertFalse(workspace.exists())
            finally:
                self._release_test_fences(workspace)

    async def test_invalid_workspace_still_rejects_deletion(self) -> None:
        """幂等缺失分支不能放行普通目录、符号链接或权限错误。"""

        with TemporaryDirectory() as directory:
            root = Path(directory)
            ordinary = root / "ordinary"
            ordinary.mkdir()
            dangling = root / "dangling"
            dangling.symlink_to(root / "absent", target_is_directory=True)
            for workspace in (ordinary, dangling):
                with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                    await prepare_application_deletion(ApplicationDeletionRequest(
                        action="prepare", applicationId="invalid-app", workspaceRoot=str(workspace),
                    ))
            with patch.object(Path, "stat", side_effect=PermissionError("permission denied")):
                with self.assertRaises(PermissionError):
                    await prepare_application_deletion(ApplicationDeletionRequest(
                        action="prepare", applicationId="invalid-app", workspaceRoot=str(root / "denied"),
                    ))

    async def test_completion_releases_fences_after_workspace_is_trashed(self) -> None:
        """目录移走后的完成确认应释放同路径新应用需要的全部删除栅栏。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "managed-app"
            marker = workspace / ".devagentstudio" / "application.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}\n", encoding="utf-8")
            application_id = "application-recreated-path"
            await prepare_application_deletion(
                ApplicationDeletionRequest(
                    action="prepare",
                    applicationId=application_id,
                    workspaceRoot=str(workspace),
                )
            )
            trashed_workspace = Path(directory) / "trashed-managed-app"
            workspace.rename(trashed_workspace)

            with self.assertRaisesRegex(ValueError, "应用标识与当前删除事务不匹配"):
                complete_application_deletion(
                    ApplicationDeletionCompletionRequest(
                        action="complete",
                        applicationId="another-application",
                        workspaceRoot=str(workspace),
                    )
                )
            self.assertTrue(
                workflow_run_registry.is_workspace_deleting(
                    str(workspace.resolve(strict=False))
                )
            )

            result = complete_application_deletion(
                ApplicationDeletionCompletionRequest(
                    action="complete",
                    applicationId=application_id,
                    workspaceRoot=str(workspace),
                )
            )

            workspace_text = str(workspace.resolve(strict=False))
            self.assertTrue(result["deletionCompleted"])
            self.assertFalse(workflow_run_registry.is_workspace_deleting(workspace_text))
            self.assertNotIn(
                str(workspace.resolve(strict=False)),
                template_mutation_coordinator._deleting,
            )
            self.assertNotIn(
                process_workspace_key(workspace),
                workspace_process_registry._deleting_workspaces,
            )
            self.assertNotIn(
                workspace_text,
                get_ui_design_generation_pool()._deleting_workspaces,
            )

            current_task = asyncio.current_task()
            self.assertIsNotNone(current_task)
            workflow_run_registry.register(
                "new-application-run",
                current_task,  # type: ignore[arg-type]
                workspace=workspace_text,
            )
            workflow_run_registry.unregister("new-application-run", current_task)

    async def test_completion_rejects_existing_workspace_and_keeps_fences(self) -> None:
        """项目目录仍存在时不得提前完成事务或重新开放工作区写入。"""

        with TemporaryDirectory() as directory:
            workspace = Path(directory) / "managed-app"
            marker = workspace / ".devagentstudio" / "application.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}\n", encoding="utf-8")
            application_id = "application-still-present"
            await prepare_application_deletion(
                ApplicationDeletionRequest(
                    action="prepare",
                    applicationId=application_id,
                    workspaceRoot=str(workspace),
                )
            )

            try:
                with self.assertRaisesRegex(ValueError, "工作区仍然存在"):
                    complete_application_deletion(
                        ApplicationDeletionCompletionRequest(
                            action="complete",
                            applicationId=application_id,
                            workspaceRoot=str(workspace),
                        )
                    )
                self.assertTrue(
                    workflow_run_registry.is_workspace_deleting(str(workspace.resolve()))
                )
            finally:
                self._release_test_fences(workspace)


if __name__ == "__main__":
    unittest.main()
