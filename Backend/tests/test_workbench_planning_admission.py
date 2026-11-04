"""验证需求和技术规划未结束时拒绝开发，并通过 AG-UI 发布可导航的权威阶段。"""

import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from langgraph.checkpoint.memory import InMemorySaver

from app.domain.application_lifecycle import ApplicationInitialization
from app.graph.direct_modification_workflow import build_direct_modification_graph
from app.protocols.direct_modification import build_conversation_ag_ui_stream
from app.services.application_lifecycle import (
    ApplicationPlanningInProgressError,
    create_application_lifecycle,
    load_application_lifecycle,
    start_workbench_execution,
    write_application_lifecycle,
)


class WorkbenchPlanningAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """使用真实开发准入、Graph 与 AG-UI 包装器，确保拒绝发生在生成代码之前。"""

    async def test_requirement_and_plan_rejection_preserves_current_task(self):
        """生成及确认中的阶段均返回明确原因，不登记新执行或改变旧生命周期。"""
        for stage, status, message, code in [
            ("generating_requirement_document", "running", "需求阶段正在运行，请先完成或结束当前需求任务", "APPLICATION_REQUIREMENT_IN_PROGRESS"),
            ("generating_technical_plan", "running", "计划阶段正在运行，请先完成或结束当前计划", "APPLICATION_PLAN_IN_PROGRESS"),
            ("awaiting_technical_plan_confirmation", "awaiting_user", "计划阶段正在运行，请先完成或结束当前计划", "APPLICATION_PLAN_IN_PROGRESS"),
            ("generating_requirement_document", "failed", "需求阶段尚未完成，请先完成或结束当前需求任务", "APPLICATION_REQUIREMENT_IN_PROGRESS"),
        ]:
            with self.subTest(stage=stage, status=status), tempfile.TemporaryDirectory() as workspace:
                state = create_application_lifecycle(application_id="app", application_name="阶段准入")
                state = state.model_copy(update={"initialization": ApplicationInitialization(
                    stage=stage, status=status, threadId="current-planning-thread")})
                write_application_lifecycle(workspace, state)
                state = load_application_lifecycle(workspace)
                with self.assertRaises(ApplicationPlanningInProgressError) as raised:
                    start_workbench_execution(workspace, scope="application", target_id="conversation",
                        page_id=None, thread_id="new-development-thread", run_id="new-run", phase="scan_workspace_code")
                self.assertEqual(str(raised.exception), message)
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(load_application_lifecycle(workspace), state)

    async def test_ag_ui_failure_includes_readable_message_and_current_lifecycle(self):
        """迟到请求被服务端拒绝后，前端可用失败快照返回当前需求或计划线程。"""
        for stage, code in [
            ("generating_requirement_document", "APPLICATION_REQUIREMENT_IN_PROGRESS"),
            ("generating_technical_plan", "APPLICATION_PLAN_IN_PROGRESS"),
        ]:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as workspace:
                state = create_application_lifecycle(application_id="app", application_name="阶段准入")
                state = state.model_copy(update={"initialization": ApplicationInitialization(
                    stage=stage, status="running", threadId="current-planning-thread")})
                write_application_lifecycle(workspace, state)
                state = load_application_lifecycle(workspace)
                graph = build_direct_modification_graph(checkpointer=InMemorySaver())
                with (
                    patch("app.protocols.direct_modification.direct_modification_graph_for_request", AsyncMock(return_value=graph)),
                    patch.object(graph, "astream", side_effect=AssertionError("准入拒绝后不能执行 Graph")) as execute,
                ):
                    frames = []
                    async for chunk in build_conversation_ag_ui_stream(payload={
                        "threadId": "new-development-thread", "runId": "new-run",
                        "messages": [{"role": "user", "content": "修改首页文案"}],
                        "forwardedProps": {"sessionId": "new-session", "conversation": {"workspaceRoot": workspace}},
                    }):
                        frames.extend(json.loads(line[5:]) for line in chunk.splitlines() if line.startswith("data:"))
                execute.assert_not_called()
                self.assertEqual(frames[0]["type"], "RUN_STARTED")
                self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
                self.assertIn("STATE_SNAPSHOT", [frame["type"] for frame in frames])
                result = frames[-1]["result"]["conversation"]
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["summary"]["errorCode"], code)
                self.assertEqual(result["summary"]["message"], str(ApplicationPlanningInProgressError(state)))
                self.assertEqual(result["summary"]["lifecycle"]["initialization"]["threadId"], "current-planning-thread")
                self.assertEqual(load_application_lifecycle(workspace), state)
