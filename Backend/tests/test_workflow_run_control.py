from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.protocols.workflow.run_control import build_workflow_plan_control_ag_ui_stream


class WorkflowRunControlTests(unittest.IsolatedAsyncioTestCase):
    """验证不启动 Graph 的计划控制动作仍完整收口业务产物。"""

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


if __name__ == "__main__":
    unittest.main()
