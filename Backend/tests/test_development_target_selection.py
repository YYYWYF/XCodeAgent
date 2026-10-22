"""验证无具体目标时停止配置交接，不能生成整应用 DAG。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from langgraph.checkpoint.memory import InMemorySaver

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.graph.nodes.api_design import api_design_readiness_gate
from app.graph.nodes.task_planning_adapter import create_async_workflow_planning_adapter
from app.graph.workflow import build_graph, route_api_design_readiness, route_prepare_build_tasks
from app.protocols.workflow.lifecycle import project_workflow_lifecycle_boundary
from app.protocols.workflow.projection import _workflow_next_nodes, _workflow_summary
from app.domain.application_revision import ActiveFormalRevision
from app.services.application_lifecycle import (
    create_application_lifecycle, load_application_lifecycle, start_workbench_execution,
    write_application_lifecycle,
)
from tests.test_api_design_node import _plan, _write_plan, _empty_design
from app.workspace.endpoint_design_documents import write_endpoint_design


class DevelopmentTargetSelectionTests(unittest.IsolatedAsyncioTestCase):
    """覆盖真实 Graph 终止、规划旁路与生命周期释放。"""

    async def test_ready_application_and_missing_target_stop_before_inspection(self):
        """应用或无目标全部就绪后真实 Graph 不执行检查或 DAG。"""

        with tempfile.TemporaryDirectory() as workspace:
            plan = _plan()
            _write_plan(workspace, plan)
            for endpoint in ("orders.list", "orders.create"):
                write_endpoint_design(workspace, _empty_design(workspace, endpoint))
            for scope in ({}, {"type": "application", "targetId": "application"}):
                with self.subTest(scope=scope):
                    planning = AsyncMock(side_effect=AssertionError("不应生成全量任务"))
                    graph = build_graph(checkpointer=InMemorySaver(), prepare_build_tasks_node=planning)
                    result = await graph.ainvoke({
                        "workspace": workspace, "project_plan": plan,
                        "build_execution_scope": scope,
                    }, config={"configurable": {"thread_id": "selection"}})
                    self.assertEqual(result["clarification"]["mode"], "development_target_selection")
                    self.assertNotIn("inspect_workspace", result.get("timeline", []))
                    planning.assert_not_awaited()
                    self.assertEqual(route_api_design_readiness(result), "await_user_input")
                    self.assertEqual(_workflow_next_nodes("api_design_readiness_gate", result), [])
                    self.assertIn("你想开发哪个页面或 API", _workflow_summary(result, [])["message"])

    async def test_direct_planning_entry_does_not_generate_application_dag(self):
        """恢复直接到规划也必须返回配置检查或目标选择，不调用生成服务。"""

        planning = AsyncMock(side_effect=AssertionError("不应生成全量任务"))
        adapter = create_async_workflow_planning_adapter(planning_service=planning)
        result = await adapter({
            "workspace": "/unused", "project_plan": {"api_contracts": []},
            "build_execution_scope": {"type": "application", "targetId": "application"},
        })
        self.assertEqual(result["clarification"]["mode"], "development_target_selection")
        self.assertEqual(route_prepare_build_tasks(result), "await_user_input")
        self.assertEqual(_workflow_next_nodes("prepare_build_tasks", result), [])
        planning.assert_not_awaited()

    async def test_selection_releases_execution_without_completing_development(self):
        """目标选择前释放应用执行，允许随后启动单个页面且不写开发完成。"""

        with tempfile.TemporaryDirectory() as workspace:
            lifecycle = create_application_lifecycle(application_id="selection", application_name="选择开发目标")
            lifecycle = lifecycle.model_copy(update={"initialization": lifecycle.initialization.model_copy(update={
                "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH,
                "status": ApplicationLifecycleStatus.COMPLETED,
            })})
            write_application_lifecycle(workspace, lifecycle)
            start_workbench_execution(workspace, scope="application", target_id="application", page_id=None,
                                      thread_id="selection", run_id="selection", phase="api_design_readiness_gate")
            current = load_application_lifecycle(workspace)
            active_revision = ActiveFormalRevision(
                changeId="selection", formalBranch="design_stage_revision", sourceThreadId="selection",
                sourceRunId="source", request="修改应用", target={"type": "application"},
                impactInteractionId="impact", planningThreadId="planning", status="building",
            )
            write_application_lifecycle(workspace, current.model_copy(update={"active_formal_revision": active_revision}))
            update = api_design_readiness_gate({"workspace": workspace, "project_plan": {"api_contracts": []}})
            project_workflow_lifecycle_boundary(workspace, run_id="selection", node_name="api_design_readiness_gate", update=update)
            current = load_application_lifecycle(workspace)
            self.assertEqual(current.active_executions, {})
            self.assertIsNone(current.active_formal_revision)
            self.assertIsNone(current.resource_locks.application)
            self.assertEqual(current.resource_locks.pages, {})
            self.assertNotIn("application_revision_completion", update)
            self.assertFalse((Path(workspace) / ".devagentstudio/build/build-task-plan.json").exists())
            start_workbench_execution(workspace, scope="page", target_id="orders", page_id="orders",
                                      thread_id="page", run_id="page", phase="api_design_readiness_gate")
