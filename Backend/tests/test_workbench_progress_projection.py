"""验证工作台重入投影只读取当前 Run，并复用实时进度协议。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.application_lifecycle import WorkbenchExecution
from app.services.workbench_progress_projection import project_workbench_progress
from app.workspace.planning_run_documents import project_planning_run
from tests.planning_run_fixtures import AT, run


class WorkbenchProgressProjectionTests(unittest.TestCase):
    """覆盖归属隔离、快照损坏及无副作用的只读恢复。"""

    def test_current_run_and_thread_only(self):
        """只附加匹配的 PlanningRun，不修改 lifecycle 本体。"""
        execution = WorkbenchExecution(
            scope="page", targetId="orders", threadId="thread-1", runId="workflow-1",
            ownerSessionId="session-1", phase="prepare_build_tasks", status="running",
            startedAt=AT, updatedAt=AT,
        )
        state = SimpleNamespace(active_executions={execution.run_id: execution})
        with patch("app.services.workbench_progress_projection.load_planning_run", return_value=project_planning_run(run())):
            result = project_workbench_progress("/workspace", state)
            self.assertEqual(result[execution.run_id]["dagGeneration"]["planningRunId"], "run-1")
            self.assertNotIn("dagGeneration", execution.model_dump())
            execution.thread_id = "other-thread"
            self.assertNotIn("dagGeneration", project_workbench_progress("/workspace", state)[execution.run_id])
            execution.thread_id = "thread-1"
            state.active_executions = {"other-run": execution}
            self.assertNotIn("dagGeneration", project_workbench_progress("/workspace", state)["other-run"])

    def test_bad_snapshot_preserves_execution(self):
        """进度文件损坏时仍返回实际运行状态。"""
        execution = WorkbenchExecution(
            scope="page", targetId="orders", threadId="thread-1", runId="workflow-1",
            ownerSessionId="session-1", phase="prepare_build_tasks", status="running",
            startedAt=AT, updatedAt=AT,
        )
        with patch("app.services.workbench_progress_projection.load_planning_run", side_effect=ValueError("invalid")):
            result = project_workbench_progress("/workspace", SimpleNamespace(active_executions={execution.run_id: execution}))
        self.assertEqual(result[execution.run_id]["status"], "running")
        self.assertNotIn("dagGeneration", result[execution.run_id])
