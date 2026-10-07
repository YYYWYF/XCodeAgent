"""真实主图恢复 Diff 审查时不再被旧文件指纹阻断。"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.graph.workflow import build_graph
from app.persistence.checkpoints import workflow_checkpointer
from app.persistence.execution_recovery import get_execution, mark_execution_interrupted
from app.services.application_lifecycle import create_application_lifecycle, start_workbench_execution, write_application_lifecycle
from app.services.execution_recovery import observe_execution_started, observe_execution_failed
from app.services.execution_recovery_executor import prepare_native_recovery
from app.services.execution_lease_heartbeat import stop_execution_heartbeat
from app.services.node_recovery_context import bind_recovery_runtime
from app.services.workflow_reentry import InterruptedTargetResolver, FailureTargetResolver
from app.services.workspace_inspector import workspace_inventory


class CodeReviewNativeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """保留真实 checkpoint、handoff 和节点包装器，仅替换模型和扫描工具。"""

    async def test_interrupted_diff_review_reenters_current_source(self):
        """模拟模型调用期间后端退出且文件改变，验证恢复后回到审查确认门。"""

        await self.exercise(interrupted=True)

    async def test_escaped_failure_reenters_current_source(self):
        """异常失败走同一兜底事务，源码变化不能阻断精确节点重入。"""

        await self.exercise(interrupted=False)

    async def exercise(self, *, interrupted):
        """串联真实主图入口、Durable 来源、恢复解析和审查包装器。"""

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_file = root / "backend" / "Demo.java"
            source_file.parent.mkdir()
            source_file.write_text("before")
            old_revision = workspace_inventory(root)[1]
            lifecycle = create_application_lifecycle(application_id="review", application_name="review")
            write_application_lifecycle(root, lifecycle.model_copy(update={"initialization": lifecycle.initialization.model_copy(update={
                "stage": ApplicationLifecycleStage.READY_FOR_WORKBENCH, "status": ApplicationLifecycleStatus.COMPLETED,
            })}))
            start_workbench_execution(root, scope="application", target_id="application", page_id=None,
                                     thread_id="thread", run_id="source", phase="code_review")
            await observe_execution_started(workspace=raw, project_id=None, thread_id="thread", run_id="source",
                                            workflow_scope="application", first_node="code_review")
            graph = build_graph(checkpointer=await workflow_checkpointer(workspace=raw))
            state = {
                "workspace": raw, "active_run_id": "source", "active_thread_id": "thread",
                "status": "completed", "quality_gate_passed": True, "workspace_revision": old_revision,
                "code_review_mode": "diff", "code_review_repair_iteration": 2,
                "code_review_max_repair_iterations": 3,
                "resume_from": "review_phase_confirmation",
                "review_phase_confirmation": {"action": "confirm", "reviewMode": "diff"},
            }
            # 真实执行到模型节点再中断，确保起点拥有确定的 checkpoint writer。
            with patch("app.graph.nodes.code_review.development_review_files", return_value=["backend/Demo.java"]), patch(
                "app.graph.workflow.nodes.code_review", side_effect=RuntimeError("backend shutdown"),
            ), self.assertRaisesRegex(RuntimeError, "backend shutdown"):
                await graph.ainvoke(state, {"configurable": {"thread_id": "thread"}})
            source_file.write_text("after")
            current_revision = workspace_inventory(root)[1]
            self.assertNotEqual(current_revision, old_revision)
            if interrupted:
                await mark_execution_interrupted(workspace=root, run_id="source", interrupted_at=datetime.now(timezone.utc))
            else:
                await observe_execution_failed(workspace=raw, run_id="source", thread_id="thread", workflow_scope="application",
                                               exception=RuntimeError("backend shutdown"), authoritative_node="code_review")
            source = await get_execution(raw, "source")
            if interrupted:
                resolution = await InterruptedTargetResolver().resolve(workspace=raw, source=source, graph=graph)
                self.assertEqual(resolution.kind, "continue")
                plan = resolution.reentry_plan
            else:
                plan = await FailureTargetResolver().resolve(workspace=raw, source=source, graph=graph)
            self.assertEqual(plan.target_node, "code_review")
            context = await prepare_native_recovery(workspace=raw, source_run_id="source", graph=graph,
                                                   reentry_plan=plan)
            try:
                with bind_recovery_runtime(context.node_recovery_context()), patch(
                    "app.graph.nodes.workspace_inspection.inspect_workspace", return_value={"workspace_revision": current_revision},
                ), patch("app.graph.workflow.nodes.code_review", return_value={
                    "phase": "code_review", "status": "requires_user_input",
                    "clarification": {"mode": "code_review_repair_confirmation"}, "code_review_next_action": "await_user_input",
                }) as review:
                    result = await graph.ainvoke(None, config=context.fork_config)
                received = review.call_args.args[0]
                self.assertEqual(received["workspace_revision"], current_revision)
                self.assertEqual(received["code_review_mode"], "diff")
                self.assertEqual(received["code_review_repair_iteration"], 2)
                self.assertEqual(result["status"], "requires_user_input")
                self.assertEqual(result["phase"], "code_review")
            finally:
                await stop_execution_heartbeat(context.heartbeat_task)
                if context.workspace_lease is not None:
                    context.workspace_lease.release()
