"""统一重试无需辅助完成标记，后续失败按新 Run 的实际节点定位。"""
import tempfile
import unittest
from langgraph.graph import START, END
from langgraph.checkpoint.memory import InMemorySaver
from app.graph.state import ProjectState
from app.services.stage_artifacts import ArtifactStateGraph
from app.services.business_node_retry import read_retry_hint
from tests.test_business_node_retry import BusinessNodeRetryTests


class RetryCompletionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """成功节点不依赖辅助补写，后续失败独立定位。"""

    async def test_downstream_failure_uses_current_node(self):
        """Build 已成功时，审查异常定位审查，不重放上游。"""
        with tempfile.TemporaryDirectory() as root:
            calls = []
            def build(state):
                """提交本轮完成的业务信息。"""
                calls.append('build')
                return {'status': 'in_progress', 'backend_handoff': {'summary': '已完成'}}
            def review(state):
                """制造下游独立失败。"""
                raise RuntimeError('new review failure')
            builder = ArtifactStateGraph(ProjectState)
            builder.add_node('build', build); builder.add_node('code_review', review)
            builder.add_edge(START, 'build'); builder.add_edge('build', 'code_review'); builder.add_edge('code_review', END)
            graph = builder.compile(checkpointer=InMemorySaver())
            with self.assertRaisesRegex(RuntimeError, 'new review failure'):
                await graph.ainvoke({'active_run_id': 'child', 'request': 'original request'}, {'configurable': {'thread_id': 'thread'}})
            source = BusinessNodeRetryTests().source(root).model_copy(update={'run_id': 'child'})
            self.assertEqual((await read_retry_hint(source, graph)).node, 'code_review')
            self.assertEqual(calls, ['build'])
