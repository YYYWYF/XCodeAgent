"""完成标记故障不得把成功改为失败，也不能把下游的新失败重新锚回旧节点。"""

import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langgraph.graph import START, END
from langgraph.checkpoint.memory import InMemorySaver
from app.graph.state import ProjectState
from app.services.stage_artifacts import ArtifactStateGraph
from app.services.checkpoint_first_retry import CheckpointFirstRetryGraph
from app.services.business_node_retry import read_retry_hint
from app.services.retry_origin import RetryOrigin, _path, load_retry_origin, save_retry_origin
from tests import test_checkpoint_first_retry as checkpoint_fixtures
from tests import test_business_node_retry as retry_fixtures


class RetryCompletionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """覆盖真实 Graph 的 checkpoint 续跑、全新节点重执行及后续失败定位。"""

    async def test_completion_write_failure_keeps_success_and_eventually_persists(self):
        """两种重试路径业务只成功执行一次，辅助落盘失败不改变公开成功输出。"""
        for native in (False, True):
            with self.subTest(native=native), tempfile.TemporaryDirectory() as root:
                def behavior(count, state):
                    """首次制造原始失败，重试返回真实 Graph 的成功结果。"""
                    if count == 1:
                        raise RuntimeError("original failure")
                    return {"status": "completed"}

                fixture = checkpoint_fixtures.CheckpointFirstRetryTests()
                graph, origin, context, state, options, received = await fixture.fixture(root, behavior)
                save_retry_origin(root, "child", origin)
                with patch("app.services.retry_origin.save_retry_origin", side_effect=OSError("completion storage unavailable")):
                    chunks = [chunk async for chunk in CheckpointFirstRetryGraph(
                        graph, context, origin, native).astream(state, **options)]
                    self.assertEqual(len(received), 2)
                    self.assertTrue(any(isinstance(chunk[-1], dict) and
                        chunk[-1].get("build", {}).get("status") == "completed" for chunk in chunks))
                    self.assertIsNone(load_retry_origin(root, "child", "thread"))
                    self.assertFalse(json.loads(_path(root, "child").read_text())["node_completed"])
                # 存储恢复后无需再次点击重试，现有后台补写自动完成。
                async def wait_persisted():
                    """等待辅助完成标记补齐，不再调用业务 Graph。"""
                    while not json.loads(_path(root, "child").read_text())["node_completed"]:
                        await asyncio.sleep(.01)
                await asyncio.wait_for(wait_persisted(), 2)
                self.assertEqual(len(received), 2)

    async def test_later_failure_uses_new_node_and_supersedes_pending_completion(self):
        """Build 已成功但标记未落盘时，审查失败仍定位审查，旧补写不得覆盖新锚点。"""
        with tempfile.TemporaryDirectory() as root:
            calls = []

            def build(state):
                """模拟已成功完成的业务节点。"""
                calls.append("build")
                return {"status": "running"}

            def review(state):
                """在下游节点制造独立失败，检查恢复入口不会回退到 Build。"""
                raise RuntimeError("new review failure")

            builder = ArtifactStateGraph(ProjectState)
            builder.add_node("build", build)
            builder.add_node("code_review", review)
            builder.add_edge(START, "build")
            builder.add_edge("build", "code_review")
            builder.add_edge("code_review", END)
            graph = builder.compile(checkpointer=InMemorySaver())
            origin = RetryOrigin(thread_id="thread", source_run_id="source", node="build", request="original request")
            save_retry_origin(root, "child", origin)
            context = SimpleNamespace(workspace=root, new_run_id="child", recovery_plan=SimpleNamespace(target_node="build"))
            config = {"configurable": {"thread_id": "thread"}}

            def save(origin_workspace, run_id, value):
                """只使旧节点完成标记失败，允许下游新失败正常登记。"""
                if value.node_completed:
                    raise OSError("completion write failed")
                save_retry_origin(origin_workspace, run_id, value)

            with patch("app.services.retry_origin.save_retry_origin", side_effect=save):
                with self.assertRaisesRegex(RuntimeError, "new review failure"):
                    async for _ in CheckpointFirstRetryGraph(graph, context, origin, False).astream(
                            {"workspace": root, "active_run_id": "child", "request": origin.request},
                            config=config, stream_mode=["updates"]):
                        pass
                source = retry_fixtures.BusinessNodeRetryTests().source(root).model_copy(
                    update={"run_id": "child", "current_node": "code_review"})
                hint = await read_retry_hint(source, graph)
                self.assertEqual(hint.node, "code_review")
                newer = origin.model_copy(update={"source_run_id": "child", "node": "code_review"})
                save_retry_origin(root, "child", newer)
                await asyncio.sleep(.3)
                self.assertEqual(load_retry_origin(root, "child", "thread"), newer)
                self.assertEqual(calls, ["build"])
