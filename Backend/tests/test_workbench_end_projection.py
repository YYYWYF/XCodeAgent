"""验证 End 回执同步撤下已结束 Pending 的应用级占用。"""

import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.protocols.workflow.run_control import build_workflow_plan_control_ag_ui_stream
from app.services.application_lifecycle import start_workbench_execution
from app.services.planning_refresh_recovery import resolve_planning_refresh_state
from app.workspace.task_documents import (
    load_pending_build_task_plan,
    write_pending_build_task_plan_atomic,
)
from tests.test_pending_build_task_plan_documents import _validated_plan
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class WorkbenchEndProjectionTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 Pending、生命周期和 AG-UI 控制流检查结束回执。"""

    async def test_end_and_repeated_end_publish_idle_pending_projection(self):
        """结束及幂等结束都明确清除确认占用，同时保留草稿供原任务继续。"""
        with tempfile.TemporaryDirectory() as workspace:
            _write_ready_workbench_lifecycle(workspace)
            start_workbench_execution(
                workspace, scope="application", target_id="application", page_id=None,
                thread_id="end-thread", owner_session_id="end-session",
                run_id="end-source", phase="prepare_build_tasks",
            )
            plan = _validated_plan()
            tasks = list(plan["task_registry"])
            state = {"workspace": workspace}
            write_pending_build_task_plan_atomic(
                state, plan, owner_session_id="end-session", planning_run_id="end-planning",
                workflow_run_id="end-source", base_confirmed_plan_digest=None,
                input_fingerprint="a" * 64,
                build_execution_scope={"type": "application", "targetId": "application"},
                created_at="2026-11-10T00:00:00+00:00",
                planning_provenance={
                    "schema_version": "planning-provenance.v2",
                    "review_task_ids": tasks, "platform_task_ids": [],
                    "new_task_ids": tasks, "reused_task_ids": [],
                },
            )
            self.assertEqual(resolve_planning_refresh_state(workspace)["status"], "awaiting_confirmation")
            pending = load_pending_build_task_plan(state)
            durable = SimpleNamespace(execution_kind="workbench", workspace=workspace, run_id="end-source")
            with patch("app.protocols.workflow.run_control.get_execution", new=AsyncMock(return_value=durable)), patch(
                "app.protocols.workflow.run_control.workflow_run_registry.cancel_and_wait",
                new=AsyncMock(return_value="not_running"),
            ):
                for status in ("ended", "already_ended"):
                    events = [json.loads(frame.removeprefix("data: ").strip()) async for frame in
                        build_workflow_plan_control_ag_ui_stream(
                            action="end", workspace=workspace, target_run_id="end-source",
                            thread_id="end-thread", run_id=f"control-{status}",
                        )]
                    lifecycle = next(event["value"] for event in events
                        if event.get("name") == "application-lifecycle")
                    self.assertEqual(lifecycle["activeExecutions"], {})
                    self.assertEqual(lifecycle["extensions"].get("planningRefresh", {}).get("status"), "idle")
                    self.assertEqual(lifecycle["extensions"]["planningRefresh"]["source"], "none")
                    final = events[-1]
                    self.assertEqual(final["type"], "RUN_FINISHED")
                    workflow = final["result"]["workflow"]
                    self.assertEqual(workflow["summary"]["planControl"]["status"], status)
                    self.assertEqual(workflow["state"]["lifecycle"], lifecycle)
                    self.assertEqual(load_pending_build_task_plan(state), pending)
