from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.protocols.workflow.run_control import (
    build_workflow_cancellation_ag_ui_stream,
    build_workflow_plan_control_ag_ui_stream,
    WorkflowRunAlreadyActiveError,
    WorkflowRunRegistry,
)


class WorkflowRunControlTests(unittest.IsolatedAsyncioTestCase):
    """验证不启动 Graph 的计划控制动作仍完整收口业务产物。"""

    async def test_registry_rejects_duplicate_active_task_without_overwriting_owner(self) -> None:
        """活跃 owner 存在时，重复 register 必须保留旧 task。"""

        registry = WorkflowRunRegistry()
        old_started = asyncio.Event()
        old_release = asyncio.Event()
        new_release = asyncio.Event()

        async def old_workflow() -> None:
            """阻塞旧任务，模拟仍在执行的 Workflow。"""

            old_started.set()
            await old_release.wait()

        async def new_workflow() -> None:
            """阻塞重复任务，验证它不会成为 registry owner。"""

            await new_release.wait()

        old_task = asyncio.create_task(old_workflow())
        new_task = asyncio.create_task(new_workflow())
        registry.register("run-duplicate", old_task)
        await old_started.wait()

        try:
            with self.assertRaises(WorkflowRunAlreadyActiveError) as context:
                registry.register("run-duplicate", new_task)
            self.assertEqual(context.exception.code, "WORKFLOW_RUN_ALREADY_ACTIVE")
            self.assertTrue(registry.is_active("run-duplicate"))
            self.assertTrue(registry.cancel("run-duplicate"))
            await asyncio.gather(old_task, return_exceptions=True)
            self.assertTrue(old_task.cancelled())
            self.assertFalse(new_task.cancelled())
        finally:
            old_release.set()
            new_release.set()
            await asyncio.gather(old_task, new_task, return_exceptions=True)
            registry.unregister("run-duplicate", old_task)

    async def test_registry_reclaims_done_stale_task(self) -> None:
        """已结束的 stale entry 可以被新 task 清理并重新登记。"""

        registry = WorkflowRunRegistry()

        async def completed_workflow() -> None:
            """立即完成，制造可回收的旧登记。"""

        old_task = asyncio.create_task(completed_workflow())
        await old_task
        new_task = asyncio.create_task(completed_workflow())
        await new_task
        try:
            registry.register("run-stale", old_task)
            registry.register("run-stale", new_task)
            self.assertFalse(registry.is_active("run-stale"))
        finally:
            registry.unregister("run-stale", new_task)
            await new_task

    async def test_end_execution_does_not_implicitly_abandon_any_plan(self) -> None:
        """结束 execution 只更新 lifecycle，不再隐式改写或删除 Build plan。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = {"revision": 2, "activeExecutions": {}}
            stream = build_workflow_plan_control_ag_ui_stream(
                action="end", workspace=directory, target_run_id="run-dag",
                thread_id="thread-control", run_id="request-control",
            )

            with patch(
                "app.protocols.workflow.run_control.end_workbench_execution",
                return_value=lifecycle,
            ) as end, patch(
                "app.protocols.workflow.run_control.application_lifecycle_payload",
                return_value=lifecycle,
            ), patch(
                "app.protocols.workflow.run_control.abandon_pending_build_task_plan"
            ) as abandon:
                frames = [frame async for frame in stream]

            end.assert_called_once_with(directory, run_id="run-dag")
            abandon.assert_not_called()
            self.assertTrue(frames)

    async def test_stop_execution_does_not_call_abandon(self) -> None:
        """暂停 execution 与 Pending Abandon 保持独立。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = SimpleNamespace(model_dump=lambda **_kwargs: {})
            stream = build_workflow_plan_control_ag_ui_stream(
                action="stop", workspace=directory, target_run_id="run-active",
                thread_id="thread-control", run_id="request-control",
            )

            with patch(
                "app.protocols.workflow.run_control.stop_workbench_execution",
                return_value=lifecycle,
            ) as stop, patch(
                "app.protocols.workflow.run_control.application_lifecycle_payload",
                return_value={},
            ), patch(
                "app.protocols.workflow.run_control.abandon_pending_build_task_plan"
            ) as abandon:
                frames = [frame async for frame in stream]

            stop.assert_called_once_with(directory, run_id="run-active")
            abandon.assert_not_called()
            self.assertTrue(frames)

    async def test_workflow_cancel_does_not_call_pending_abandon(self) -> None:
        """Workflow task cancellation 只取消活动运行，不删除 PendingPlan。"""

        stream = build_workflow_cancellation_ag_ui_stream(
            thread_id="thread-control",
            run_id="cancel-request",
            target_run_id="run-active",
        )
        with patch(
            "app.protocols.workflow.run_control.workflow_run_registry.cancel",
            return_value=True,
        ) as cancel, patch(
            "app.protocols.workflow.run_control.abandon_pending_build_task_plan",
        ) as abandon:
            frames = [frame async for frame in stream]

        cancel.assert_called_once_with("run-active")
        abandon.assert_not_called()
        self.assertIn("cancel_requested", "".join(frames))


if __name__ == "__main__":
    unittest.main()
