"""验证验收决定的权威持久化与最终执行资源释放。"""

import tempfile
import unittest

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
    WorkbenchExecutionStatus,
)
from app.protocols.workflow.lifecycle import project_workflow_lifecycle_boundary
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    start_workbench_execution,
    update_workbench_execution,
    write_application_lifecycle,
)


class AcceptanceDecisionProjectionTests(unittest.TestCase):
    """覆盖等待验收与明确验收通过两个不同业务边界。"""

    def test_approval_is_durable_and_finalize_releases_execution(self):
        """等待时不声明通过；决定落盘后由原完成边界释放资源且保留结果。"""
        with tempfile.TemporaryDirectory() as workspace:
            lifecycle = create_application_lifecycle(application_id="app", application_name="验收")
            lifecycle = lifecycle.model_copy(update={
                "initialization": lifecycle.initialization.model_copy(update={
                    "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                    "status": ApplicationLifecycleStatus.COMPLETED,
                }),
                "extensions": {"unrelated": "preserved"},
            })
            write_application_lifecycle(workspace, lifecycle, expected_revision=0)
            start_workbench_execution(
                workspace, scope="application", target_id="application", page_id=None,
                thread_id="thread", run_id="run", phase="acceptance",
            )
            project_workflow_lifecycle_boundary(
                workspace, run_id="run", node_name="acceptance",
                update={"status": "requires_user_input", "accepted": False,
                        "clarification": {"mode": "page_acceptance"}},
            )
            waiting = load_application_lifecycle(workspace)
            self.assertNotIn("acceptanceStatus", waiting.extensions)
            self.assertEqual(waiting.active_executions["run"].status, "awaiting_user")
            project_workflow_lifecycle_boundary(
                workspace, run_id="run", node_name="acceptance",
                update={"status": "completed", "accepted": True},
            )
            self.assertEqual(load_application_lifecycle(workspace).extensions["acceptanceStatus"], "passed")
            project_workflow_lifecycle_boundary(
                workspace, run_id="run", node_name="finalize_project",
                update={"status": "completed"},
            )
            completed = load_application_lifecycle(workspace)
            self.assertEqual(completed.active_executions, {})
            self.assertIsNone(completed.active_run_id)
            self.assertIsNone(completed.resource_locks.application)
            self.assertEqual(completed.extensions["acceptanceStatus"], "passed")
            self.assertEqual(completed.extensions["testExecutionStatus"], "passed")
            self.assertEqual(completed.extensions["reviewStatus"], "passed")
            self.assertEqual(completed.extensions["unrelated"], "preserved")
            # 只读分析不撤销结果；真实代码修改后，旧验收结果永久失效。
            start_workbench_execution(workspace, scope="application", target_id="conversation", page_id=None,
                                      thread_id="next", run_id="next", phase="scan_workspace_code")
            self.assertEqual(load_application_lifecycle(workspace).extensions["acceptanceStatus"], "passed")
            update_workbench_execution(workspace, run_id="next", phase="execute_frontend", status=WorkbenchExecutionStatus.RUNNING)
            self.assertEqual(load_application_lifecycle(workspace).extensions["acceptanceStatus"], "pending")
