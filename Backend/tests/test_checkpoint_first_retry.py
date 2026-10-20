"""统一重试只执行一次：完整上下文、最新磁盘输入、原节点及确认门。"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END
from langgraph.types import Command, interrupt
from app.graph.state import ProjectState
from app.services.stage_artifacts import ArtifactStateGraph
from app.services.business_node_retry import prepare_business_node_retry, read_retry_hint
from app.services.retry_preparation import abandon_prepared_retry
from app.services.application_lifecycle import start_workbench_execution
from app.protocols.workflow.lifecycle import fail_workflow_lifecycle
from tests.test_workflow_run_control import _write_ready_workbench_lifecycle
from tests.test_business_node_retry import BusinessNodeRetryTests


class CheckpointFirstRetryTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 Graph 检查统一节点重执行，不再存在恢复后自动降级分支。"""

    async def fixture(self, workspace, behavior):
        """创建真实失败 checkpoint，供节点重试及交接边界回归共用。"""
        received = []

        def build(state):
            """按测试行为执行，并保留实际节点输入供断言。"""
            received.append(dict(state))
            return behavior(len(received), state)

        builder = ArtifactStateGraph(ProjectState)
        builder.add_node('build', build)
        builder.add_edge(START, 'build')
        builder.add_edge('build', END)
        graph = builder.compile(checkpointer=InMemorySaver())
        with self.assertRaises(RuntimeError):
            await graph.ainvoke({'active_run_id': 'source', 'request': '原始问题 ORIGINAL',
                'workspace': workspace, 'clarification': {'old': True},
                'selected_skill_names': ['original-skill'], 'direct_modification_summary': '原对话上下文',
                'backend_handoff': {'changedFiles': ['backend/App.java']},
                'technical_plan_candidate': {'draft': 'not-on-disk'},
                'build_task_plan_confirmation': {'action': 'confirm'}},
                {'configurable': {'thread_id': 'thread'}})
        return graph, None, None, None, None, received

    async def prepare(self, workspace, behavior):
        """建立真实失败生命周期并准备一次统一重试。"""
        _write_ready_workbench_lifecycle(workspace)
        start_workbench_execution(workspace, scope='page', target_id='page-a', page_id='page-a',
            thread_id='thread', run_id='source', phase='build', owner_session_id='session')
        fail_workflow_lifecycle(workspace, run_id='source', phase='build', error=RuntimeError('failed'))
        graph, _, _, _, _, received = await self.fixture(workspace, behavior)
        source = BusinessNodeRetryTests().source(workspace)
        context = await prepare_business_node_retry(source, graph)
        self.addAsyncCleanup(abandon_prepared_retry, workspace=workspace, source=source,
            before=context.retry_lifecycle_before_handoff, new_run_id=context.new_run_id, lease=context.workspace_lease)
        return graph, context, received

    async def test_full_context_latest_disk_and_new_identity(self):
        """完整保留请求、技能、交接和候选，磁盘输入覆盖旧值，旧确认不复用。"""
        def behavior(count, state):
            """首次失败，重试成功。"""
            if count == 1:
                raise RuntimeError('original failure')
            from langchain_core.runnables.config import ensure_config
            self.assertEqual(ensure_config()['metadata']['run_id'], state['active_run_id'])
            return {'status': 'completed'}

        with tempfile.TemporaryDirectory() as root:
            graph, context, received = await self.prepare(root, behavior)
            file = Path(root, '.devagentstudio/specs/requirement-spec.json')
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('{"latest":true}')
            with patch.object(graph, 'aupdate_state', side_effect=AssertionError('禁止 fork')):
                await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
            state = received[-1]
            self.assertEqual(len(received), 2)
            self.assertEqual(state['requirement_spec'], {'latest': True})
            self.assertEqual(state['selected_skill_names'], ['original-skill'])
            self.assertEqual(state['backend_handoff'], {'changedFiles': ['backend/App.java']})
            self.assertEqual(state['technical_plan_candidate'], {'draft': 'not-on-disk'})
            self.assertEqual(state['build_task_plan_confirmation'], {})
            self.assertEqual(state['request'], '原始问题 ORIGINAL')
            self.assertEqual(state['active_run_id'], context.new_run_id)

    async def test_failure_is_published_once_without_automatic_second_attempt(self):
        """本次重试失败直接交回原协议收口，不隐藏失败再执行第二次。"""
        def behavior(count, state):
            """每次均失败，用计数证明一次点击只进入节点一次。"""
            raise RuntimeError('business still failed')

        with tempfile.TemporaryDirectory() as root:
            graph, context, received = await self.prepare(root, behavior)
            with self.assertRaisesRegex(RuntimeError, 'business still failed'):
                await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
            self.assertEqual(len(received), 2)
            child = BusinessNodeRetryTests().source(root).model_copy(update={'run_id': context.new_run_id})
            self.assertEqual((await read_retry_hint(child, graph)).node, 'build')

    async def test_direct_repair_keeps_original_evidence_and_completed_changes(self):
        """二次修改从失败修复节点继续，保留授权、返修证据、前端上下文和已完成修改。"""
        from app.graph.direct_modification_workflow import build_direct_modification_graph
        with tempfile.TemporaryDirectory() as root:
            _, initial, _ = await self.prepare(root, self.original_failure)
            calls = []

            def repair(state):
                """首次模拟修复调用失败，重试检查完整上下文后完成修复。"""
                calls.append(dict(state))
                if len(calls) == 1:
                    raise RuntimeError('修复模型连接失败')
                self.assertEqual(state['revision_requests'], [{'reason': '当前代码编译失败'}])
                self.assertEqual(state['direct_modification_approved_paths'], ['frontend/src/Page.tsx'])
                self.assertEqual(state['direct_code_change_sets'], [{'files': [{'path': 'frontend/src/Page.tsx'}]}])
                self.assertEqual(state['backend_handoff'], {'summary': '后端已完成'})
                self.assertEqual(state['repair_iteration'], 1)
                self.assertEqual(state['small_task_tasks'][0]['status'], 'completed')
                self.assertEqual(state['small_task_tasks'][1]['status'], 'pending')
                return {'status': 'in_progress', 'integration_next_action': 'validate_direct_fix'}

            def validate(state):
                """修复完成后回到原验证节点，不重新分类或修改上游代码。"""
                return {'status': 'completed', 'quality_gate_passed': True}

            with patch('app.graph.direct_modification_workflow.direct_modification_repair', repair), \
                 patch('app.graph.direct_modification_workflow.validate_direct_fix', validate):
                graph = build_direct_modification_graph(checkpointer=InMemorySaver())
            state = {**initial.fork_snapshot.values, 'active_run_id': initial.new_run_id,
                'direct_modification_resume_node': 'direct_modification_repair',
                'revision_requests': [{'reason': '当前代码编译失败'}],
                'direct_modification_approved_paths': ['frontend/src/Page.tsx'],
                'direct_code_change_sets': [{'files': [{'path': 'frontend/src/Page.tsx'}]}],
                'backend_handoff': {'summary': '后端已完成'}, 'repair_iteration': 1,
                'small_task_tasks': [{'id': 'done', 'status': 'completed'}, {'id': 'failed', 'status': 'failed'}]}
            with self.assertRaisesRegex(RuntimeError, '修复模型连接失败'):
                await graph.ainvoke(state, {'configurable': {'thread_id': 'thread'}})
            source = initial.child_execution.model_copy(update={'workflow_scope': 'conversation', 'status': 'failed'})
            context = await prepare_business_node_retry(source, graph)
            self.addAsyncCleanup(abandon_prepared_retry, workspace=root, source=source,
                before=context.retry_lifecycle_before_handoff, new_run_id=context.new_run_id, lease=context.workspace_lease)
            self.assertEqual(context.recovery_plan.target_node, 'direct_modification_repair')
            await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
            self.assertEqual(len(calls), 2)

    async def test_progress_arrives_before_node_finishes(self):
        """新执行直接流式交付进度，消费者收到前节点不能结束。"""
        from langgraph.config import get_stream_writer
        for subgraphs in (False, True):
            with self.subTest(subgraphs=subgraphs), tempfile.TemporaryDirectory() as root:
                progress_received = asyncio.Event()
                graph, context, _ = await self.prepare(root, self.original_failure)

                async def generate(state):
                    """发出进度后等待消费者，验证不再缓冲整段执行。"""
                    get_stream_writer()({'type': 'progress', 'message': '正在生成'})
                    await asyncio.wait_for(progress_received.wait(), 2)
                    return {'status': 'completed'}

                builder = ArtifactStateGraph(ProjectState)
                builder.add_node('build', generate)
                builder.add_edge(START, 'build'); builder.add_edge('build', END)
                streaming = builder.compile(checkpointer=InMemorySaver())
                chunks = []
                async for chunk in streaming.astream(context.fork_snapshot.values, config=context.fork_config,
                        stream_mode=['updates', 'custom'], subgraphs=subgraphs):
                    chunks.append(chunk)
                    if chunk[-2] == 'custom':
                        progress_received.set()
                self.assertEqual(sum(chunk[-2] == 'custom' for chunk in chunks), 1)

    @staticmethod
    def original_failure(count, state):
        """构造原失败，后续执行成功。"""
        if count == 1:
            raise RuntimeError('source failure')
        return {'status': 'completed'}

    async def test_new_artifact_still_requires_explicit_confirmation(self):
        """旧确认动作已清除，重新生成后必须停在原生 interrupt，不能进入下游。"""
        with tempfile.TemporaryDirectory() as root:
            _, context, _ = await self.prepare(root, self.original_failure)
            downstream = []

            def generate(state):
                """确认本次没有旧批准动作，并生成新产物。"""
                self.assertEqual(state['build_task_plan_confirmation'], {})
                return {'status': 'requires_user_input'}

            def review(state):
                """用户必须单独确认新版本。"""
                interrupt({'message': '请确认新版本'})
                return {'status': 'in_progress'}

            def next_node(state):
                """只有明确确认后才允许执行。"""
                downstream.append(True)
                return {'status': 'completed'}

            builder = ArtifactStateGraph(ProjectState)
            builder.add_node('build', generate); builder.add_node('review', review); builder.add_node('next', next_node)
            builder.add_edge(START, 'build'); builder.add_edge('build', 'review')
            builder.add_edge('review', 'next'); builder.add_edge('next', END)
            graph = builder.compile(checkpointer=InMemorySaver())
            result = await graph.ainvoke(context.fork_snapshot.values, context.fork_config)
            self.assertIn('__interrupt__', result)
            self.assertFalse(downstream)
            current = {'configurable': {'thread_id': context.thread_id}}
            await graph.ainvoke(Command(resume={'action': 'confirm'}), current)
            self.assertEqual(downstream, [True])
