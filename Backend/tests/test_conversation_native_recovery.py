"""真实二次修改 Graph 通过现有统一执行器恢复节点的回归。"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from app.domain.application_lifecycle import ApplicationInitialization
from app.persistence.execution_recovery import get_execution
from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle, load_application_lifecycle
from app.protocols.direct_modification import build_conversation_ag_ui_stream
from app.protocols.execution_recovery import build_execution_recovery_ag_ui_stream
from app.services.execution_recovery_projection import resolve_execution_recovery_projection
from app.graph.direct_modification_workflow import build_direct_modification_graph
from app.domain.application_revision import RevisionImpact, RevisionTarget
from app.services.application_revision_lifecycle import register_revision_impact, submit_revision_impact
from app.services.application_lifecycle import start_workbench_execution, ApplicationLifecycleConflictError
from app.services.conversation_execution import ConversationExecution


class ConversationNativeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 checkpoint、SQLite claim、lifecycle handoff 和 AG-UI 流。"""

    async def test_formal_handoff_keeps_confirmation_valid(self):
        """两种正式分支释放来源执行后仍可批准或拒绝，外部 revision 漂移仍拒绝。"""

        for branch in ('design_stage_revision', 'workbench_plan_revision'):
            for decision in ('approved', 'rejected', 'stale'):
                with self.subTest(branch=branch, decision=decision), tempfile.TemporaryDirectory() as directory:
                    lifecycle = create_application_lifecycle(application_id='app', application_name='修改交接')
                    lifecycle = lifecycle.model_copy(update={'initialization': ApplicationInitialization(
                        stage='ready_for_workbench', status='completed', threadId='planning')})
                    write_application_lifecycle(directory, lifecycle)
                    start_workbench_execution(directory, scope='application', target_id='conversation',
                        page_id=None, thread_id='thread', run_id='source', phase='classify_intent')
                    pending = register_revision_impact(directory, interaction_id='impact',
                        source_thread_id='thread', source_run_id='source', request='修改正式计划',
                        target=RevisionTarget(type='application'), impact=RevisionImpact(
                            formalBranch=branch, revisionType='technical_contract_change',
                            earliestArtifact='technical-plan', affectedArtifacts=['technical-plan'],
                            affectedResources=['application'], reason='正式计划变化'))
                    execution = ConversationExecution(directory, 'thread', 'source', None)
                    execution.registered = True
                    await execution.finish({'status': 'requires_user_input',
                        'clarification': {'mode': 'revision_impact_confirmation'}})
                    current = load_application_lifecycle(directory)
                    self.assertFalse(current.active_executions)
                    self.assertEqual(current.pending_revision_impact.change_id, pending.change_id)
                    self.assertEqual(current.pending_revision_impact.based_on_lifecycle_revision, current.revision)
                    if decision == 'stale':
                        write_application_lifecycle(directory, current.model_copy(update={'revision': current.revision + 1}))
                        with self.assertRaises(ApplicationLifecycleConflictError):
                            submit_revision_impact(directory, interaction_id='impact', decision='approved')
                    else:
                        active = submit_revision_impact(directory, interaction_id='impact', decision=decision)
                        self.assertEqual(active is not None, decision == 'approved')

    async def run_recovery(self, *, interrupted=False, first_node=False, confirmation=False, business_failure=False, seed_old_confirmation=False):
        """构造一次失败或停服现场，要求恢复原节点并保留已完成前置步骤。"""

        calls = {"scan": 0, "classify": 0, "reply": 0}
        fail = True
        node_entered = asyncio.Event()

        async def scan(state):
            """记录前置扫描次数，并可模拟首业务节点失败。"""
            calls["scan"] += 1
            if seed_old_confirmation:
                self.assertEqual(state.get("clarification"), {})
                self.assertEqual(state.get("conversation_intent"), "")
                self.assertEqual(state.get("conversation_response"), "")
                self.assertEqual(state.get("direct_modification_result"), {})
            if first_node and fail:
                raise RuntimeError("扫描连接中断")
            return {"status": "completed", "phase": "scan_workspace_code"}

        async def classify(state):
            """首次模拟模型中断，重入时保留原请求和修复额度。"""
            calls["classify"] += 1
            if fail:
                if interrupted:
                    node_entered.set()
                    await asyncio.Event().wait()
                if business_failure:
                    return {"status": "failed", "phase": "classify_intent", "message": "分类暂时失败"}
                raise RuntimeError("分类模型连接中断")
            self.assertEqual(state["request"], "修改按钮文字")
            self.assertEqual(state["max_repair_iterations"], 3)
            if confirmation:
                return {"status": "requires_user_input", "phase": "classify_intent",
                        "clarification": {"mode": "implementation_fix_confirmation", "message": "请确认修改范围"}}
            return {"status": "in_progress", "conversation_intent": "casual_chat"}

        async def reply(state):
            """模拟回复，无模型、网络或文件副作用。"""
            calls["reply"] += 1
            return {"status": "completed", "message": "修改完成"}

        async def finalize(state):
            """保留确认门及失败状态，模拟原收口节点。"""
            return {}

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            lifecycle = create_application_lifecycle(application_id="app", application_name="修改恢复")
            lifecycle = lifecycle.model_copy(update={"initialization": ApplicationInitialization(
                stage="ready_for_workbench", status="completed", threadId="planning")})
            write_application_lifecycle(workspace, lifecycle, expected_revision=0)
            with (
                patch("app.graph.direct_modification_workflow.scan_workspace_code", scan),
                patch("app.graph.direct_modification_workflow.classify_direct_modification", classify),
                patch("app.graph.direct_modification_workflow.respond_to_casual_conversation", reply),
                patch("app.graph.direct_modification_workflow.finalize_direct_modification", finalize),
            ):
                graph = build_direct_modification_graph(checkpointer=InMemorySaver())

            async def factory(**kwargs):
                """只替换 Graph 构造，统一恢复观察、解析和执行均使用真实实现。"""
                return graph

            with (
                patch("app.protocols.direct_modification.direct_modification_graph_for_request", factory),
                patch("app.graph.direct_modification_workflow.direct_modification_graph_for_request", factory),
            ):
                if seed_old_confirmation:
                    # 在真实同 thread checkpoint 中保留上一轮确认，验证协议新输入主动清空。
                    await graph.aupdate_state({"configurable": {"thread_id": "thread"}}, {
                        "request": "上一轮问题", "active_run_id": "previous",
                        "clarification": {"mode": "direct_modification_clarification", "message": "旧确认"},
                        "conversation_intent": "clarification", "conversation_response": "旧回复",
                        "direct_modification_result": {"summary": "旧结果"},
                    }, as_node="finalize_direct_modification")
                frames = []
                stream = build_conversation_ag_ui_stream(payload={
                    "threadId": "thread", "runId": "source",
                    "messages": [{"role": "user", "content": "修改按钮文字"}],
                    "forwardedProps": {"sessionId": "session", "conversation": {"workspaceRoot": directory}},
                })
                async def consume():
                    """消费真实协议流，外部取消模拟服务任务被停止。"""
                    async for frame in stream:
                        frames.append(frame)
                if interrupted:
                    task = asyncio.create_task(consume())
                    await asyncio.wait_for(node_entered.wait(), timeout=3)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                else:
                    await consume()
                source = await get_execution(directory, "source")
                self.assertEqual(source.status.value, "interrupted" if interrupted else "failed", str(source.failure))
                self.assertEqual(source.workflow_scope, "conversation")
                self.assertEqual(source.current_node, "scan_workspace_code" if first_node else "classify_intent", str(source.failure) + "\n" + "".join(frames)[-2000:])
                fail = False
                projection = await resolve_execution_recovery_projection(directory,
                    active_workbench_run_ids={"source"})
                self.assertEqual(len(projection.candidates), 1)
                candidate = projection.candidates[0]
                self.assertEqual(candidate.recovery_action_plan.status.value, "recoverable", str(candidate.recovery_action_plan))
                action = candidate.recovery_action_plan.primary_action
                self.assertEqual(candidate.owner_session_id, "session")
                # 过期或伪造 action 不能越过统一签发规则执行任何节点。
                before_stale = dict(calls)
                rejected = [frame async for frame in build_execution_recovery_ag_ui_stream(payload={
                    "forwardedProps": {"workspaceRoot": directory, "executionRecovery": {
                        "action": "execute", "incidentId": candidate.recovery_action_plan.incident_id,
                        "actionId": "stale-action",
                    }}})]
                self.assertIn('RUN_ERROR', "".join(rejected))
                self.assertEqual(calls, before_stale)
                recovered = [frame async for frame in build_execution_recovery_ag_ui_stream(payload={
                    "forwardedProps": {"workspaceRoot": directory, "executionRecovery": {
                        "action": "execute", "incidentId": candidate.recovery_action_plan.incident_id,
                        "actionId": action.action_id,
                    }}})]
                self.assertNotIn('"type":"RUN_ERROR"', "".join(recovered))
                self.assertIn('RUN_FINISHED', "".join(recovered))
                self.assertEqual(calls["scan"], 2 if first_node else 1)
                self.assertEqual(calls["classify"], 2 if not first_node else 1)
                self.assertEqual(calls["reply"], 0 if confirmation else 1)
                current = load_application_lifecycle(directory)
                if confirmation:
                    self.assertEqual(len(current.active_executions), 1)
                    self.assertEqual(next(iter(current.active_executions.values())).status.value, "awaiting_user")
                else:
                    self.assertFalse(current.active_executions)

    async def test_new_request_clears_previous_confirmation(self):
        """同 thread 新请求的扫描和重试节点均不得继承上一轮确认。"""
        await self.run_recovery(seed_old_confirmation=True)

    async def test_failure_reenters_original_node(self):
        """异常重试只回到分类入口，不重新扫描。"""
        await self.run_recovery()

    async def test_interruption_uses_shared_continue(self):
        """中断使用统一 CONTINUE_CHECKPOINT，不重发原请求。"""
        await self.run_recovery(interrupted=True)

    async def test_first_node_has_reentry_checkpoint(self):
        """第一个业务节点也有 synthetic entry 生成的精确重入证据。"""
        await self.run_recovery(first_node=True)

    async def test_recovery_stops_at_confirmation(self):
        """恢复后的新确认门仍阻断下游执行。"""
        await self.run_recovery(confirmation=True)

    async def test_handled_failure_uses_shared_business_retry(self):
        """已处理的业务失败沿用 BUSINESS_RETRY，不伪造 escaped exception。"""
        await self.run_recovery(business_failure=True)
