"""请求准备中的磁盘错误也必须进入有 checkpoint、原提问和明确生成入口的恢复流程。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END

from app.graph.state import ProjectState
from app.protocols.workflow.request import workflow_run_inputs
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.persistence.execution_recovery import get_execution
from app.services.application_lifecycle import load_application_lifecycle
from app.services.business_node_retry import read_retry_hint, retry_action_plan
from app.services.stage_artifacts import ArtifactStateGraph
from app.services.workflow_input_artifacts import pregraph_artifact_values
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle


class PregraphArtifactRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """验证正式请求适配器和真实 Graph 在损坏输入下不丢失恢复上下文。"""

    def inputs(self, root):
        """建立真实正式应用的最小磁盘输入，并故意损坏产品计划。"""
        _write_ready_workbench_lifecycle(root)
        base = Path(root, ".devagentstudio")
        for relative, value in {
            "application.json": {"id": "app"},
            "specs/requirement-spec.json": {"pages": [], "summary": "现有需求"},
            "plans/product-plan.json": {"pages": []},
            "specs/ui-designs.json": {"pages": []},
            "plans/technical-plan.json": {"artifact_type": "technical-plan", "pages": []},
        }.items():
            path = base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
        broken = base / "plans/product-plan.json"
        broken.write_text('{"incomplete":')
        return broken

    def payload(self, root):
        """固定请求身份和原提问，不用重试按钮文本替代业务目标。"""
        return {"threadId": "pregraph-thread", "runId": "pregraph-run",
                "request": "保留原提问 PREGRAPH-ORIGINAL", "resumeFrom": "prepare_build_tasks",
                "forwardedProps": {"workspaceRoot": root, "sessionId": "pregraph-session"}}

    async def test_adapter_defers_bad_product_and_keeps_identity(self):
        """损坏文件不能在协议建流前抛错，本次身份、范围和提问仍完整。"""
        with tempfile.TemporaryDirectory() as root:
            broken = self.inputs(root)
            result = workflow_run_inputs(self.payload(root))
            self.assertEqual(result["request"], "保留原提问 PREGRAPH-ORIGINAL")
            self.assertEqual(result["resume_from"], "prepare_build_tasks")
            self.assertEqual(result["resume_values"]["owner_session_id"], "pregraph-session")
            self.assertNotIn("project_plan", result["resume_values"])
            self.assertEqual(broken.read_text(), '{"incomplete":')

    async def test_full_protocol_records_failure_before_agent_and_issues_regeneration(self):
        """真实 AG-UI 失败留下 checkpoint 和原问题，并签发产品文档重新生成而非无效重试。"""
        with tempfile.TemporaryDirectory() as root:
            broken = self.inputs(root)
            agent_calls = []

            def entry(state):
                """模拟生产 Graph 的无副作用输入提交入口。"""
                return {}

            def planning(state):
                """不完整上游必须在调用 Agent 前由平台包装拦截。"""
                agent_calls.append(state)
                return {"status": "completed"}

            builder = ArtifactStateGraph(ProjectState)
            builder.add_node("workflow_entry", entry)
            builder.add_node("prepare_build_tasks", planning)
            builder.add_edge(START, "workflow_entry")
            builder.add_edge("workflow_entry", "prepare_build_tasks")
            builder.add_edge("prepare_build_tasks", END)
            graph = builder.compile(checkpointer=InMemorySaver())
            frames = "".join([frame async for frame in build_workflow_ag_ui_stream(
                graph=graph, payload=self.payload(root))])
            self.assertIn("product-plan.json", frames)
            self.assertNotIn("JSONDecodeError", frames)
            self.assertEqual(agent_calls, [])
            source = await get_execution(root, "pregraph-run")
            self.assertEqual(source.status.value, "failed")
            hint = await read_retry_hint(source, graph)
            self.assertEqual((hint.node, hint.request), ("prepare_build_tasks", "保留原提问 PREGRAPH-ORIGINAL"))
            action = await retry_action_plan(source, graph)
            self.assertEqual(action.primary_action.target_node, "product_planning")
            self.assertEqual(action.primary_action.label, "重新生成产品计划")
            self.assertNotIn("product-plan.json", action.message)
            self.assertIn("产品计划不完整", action.message)
            self.assertIn("product-plan.json", source.failure.diagnostic_message)
            self.assertIn("Expecting", source.failure.diagnostic_message)
            self.assertNotIn("Expecting", source.failure.user_message)
            locks = load_application_lifecycle(root).resource_locks.model_dump()
            self.assertTrue(all(not value for value in locks.values()))
            self.assertEqual(broken.read_text(), '{"incomplete":')

    def test_unrelated_read_error_is_not_hidden_as_artifact_regeneration(self):
        """无法定位到正式产物的 I/O 错误继续明确上报，不静默吞掉。"""
        reader = Mock(side_effect=OSError("unrelated storage failure"))
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(OSError, "unrelated storage failure"):
            pregraph_artifact_values(root, "prepare_build_tasks", reader)
