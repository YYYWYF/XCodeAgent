"""统一取消旧指纹门禁后，规划和验收使用当前工作区的恢复回归。"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.execution_recovery import WorkflowReentryReason
from app.graph.workflow import build_graph
from app.graph.subgraphs.acceptance import run_acceptance_subgraph
from app.persistence.checkpoints import workflow_checkpointer
from app.persistence.execution_recovery import get_execution, mark_execution_interrupted
from app.services.application_lifecycle import create_application_lifecycle, start_workbench_execution, write_application_lifecycle
from app.services.execution_recovery import observe_execution_started
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.node_recovery_context import NodeRecoveryContext, bind_recovery_runtime
from app.services.workflow_reentry import InterruptedTargetResolver
from app.services.workspace_inspector import workspace_inventory


class CurrentWorkspaceRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """通过真实主图和恢复事务确认规划输入刷新。"""

    async def test_planning_recovery_refreshes_stale_workspace_snapshot(self):
        """源码变化和不存在的旧缓存不阻止恢复，规划接收刷新后的输入。"""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tracked = root / "source.py"
            tracked.write_text("before")
            old_revision = workspace_inventory(root)[1]
            lifecycle = create_application_lifecycle(application_id="planning", application_name="planning")
            write_application_lifecycle(root, lifecycle.model_copy(update={"initialization": lifecycle.initialization.model_copy(update={
                "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH, "status": ApplicationLifecycleStatus.COMPLETED,
            })}))
            start_workbench_execution(root, scope="application", target_id="application", page_id=None,
                                     thread_id="thread", run_id="source", phase="prepare_build_tasks")
            await observe_execution_started(workspace=raw, project_id=None, thread_id="thread", run_id="source",
                                            workflow_scope="application", first_node="prepare_build_tasks")
            received = []

            async def planner(state):
                """首次模拟后端退出，恢复调用保存真正交给规划器的输入。"""

                received.append(state)
                if len(received) == 1:
                    raise RuntimeError("backend shutdown")
                return {"status": "requires_user_input", "phase": "prepare_build_tasks"}

            graph = build_graph(checkpointer=await workflow_checkpointer(workspace=raw), prepare_build_tasks_node=planner)
            state = {"workspace": raw, "active_run_id": "source", "active_thread_id": "thread",
                     "resume_from": "prepare_build_tasks", "workspace_revision": old_revision,
                     "workspace_snapshot_hash": "obsolete", "workspace_snapshot_path": "/missing/cache.json"}
            with self.assertRaisesRegex(RuntimeError, "backend shutdown"):
                await graph.ainvoke(state, {"configurable": {"thread_id": "thread"}})
            tracked.write_text("after")
            current_revision = workspace_inventory(root)[1]
            self.assertNotEqual(old_revision, current_revision)
            await mark_execution_interrupted(workspace=root, run_id="source", interrupted_at=datetime.now(timezone.utc))
            source = await get_execution(raw, "source")
            resolution = await InterruptedTargetResolver().resolve(workspace=raw, source=source, graph=graph)
            self.assertEqual(resolution.kind, "continue")
            context = await prepare_native_recovery(workspace=raw, source_run_id="source", graph=graph, reentry_plan=resolution.reentry_plan)
            try:
                with bind_recovery_runtime(context.node_recovery_context()), patch(
                    "app.graph.workflow.nodes.inspect_workspace", return_value={"workspace_revision": current_revision,
                    "workspace_snapshot_path": "/current/cache.json", "workspace_snapshot_hash": "current"},
                ):
                    result = await graph.ainvoke(None, context.fork_config)
                self.assertEqual(received[-1]["workspace_revision"], current_revision)
                self.assertEqual(received[-1]["workspace_snapshot_path"], "/current/cache.json")
                self.assertEqual(received[-1]["workspace_snapshot"], {})
                self.assertEqual(result["status"], "requires_user_input")
            finally:
                await stop_execution_heartbeat(context.heartbeat_task)
                if context.workspace_lease is not None:
                    context.workspace_lease.release()


class AcceptanceCurrentWorkspaceTests(unittest.TestCase):
    """验收重入不能把旧预览 URL 当作当前服务就绪证据。"""

    def run_reentry(self, *, accepted=False):
        """在真实 Native 节点上下文中调用验收子图，并替换外部启动工具。"""

        state = {"workspace": "/workspace", "active_run_id": "child", "active_thread_id": "thread",
                 "launch_result": {"status": "completed", "preview_url": "http://old-preview"},
                 "preview_url": "http://old-preview", "acceptance_decision": "accepted" if accepted else ""}
        context = NodeRecoveryContext(source_run_id="source", execution_run_id="child", thread_id="thread",
                                      target_node="acceptance", checkpoint_id="entry", reentry_reason=WorkflowReentryReason.INTERRUPTED_CONTINUE)
        with bind_recovery_runtime(context), patch("app.graph.nodes.lifecycle.launch_project", return_value={
            "status": "completed", "launch_result": {"status": "completed", "preview_url": "http://current-preview"},
            "preview_url": "http://current-preview", "acceptance_request": {},
        }) as launcher:
            result = run_acceptance_subgraph(state)
        return result, launcher

    def test_recovery_rechecks_project_despite_old_preview(self):
        """旧启动快照不阻止确定性启动器重新检查当前工程。"""

        result, launcher = self.run_reentry()
        launcher.assert_called_once()
        self.assertEqual(result["preview_url"], "http://current-preview")
        self.assertEqual(result["status"], "requires_user_input")

    def test_explicit_acceptance_decision_is_not_reexecuted(self):
        """已经提交的人工验收通过仍按原合同消费，不重新启动项目。"""

        result, launcher = self.run_reentry(accepted=True)
        launcher.assert_not_called()
        self.assertTrue(result["accepted"])
