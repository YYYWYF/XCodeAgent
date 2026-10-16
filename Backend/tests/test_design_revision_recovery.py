"""设计正式修订批准窗口的 durable 输入与恢复归属回归。"""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import StateGraph, START, END
from app.graph.state import ProjectState
from app.graph.application_planning_revision import analyze_design_intent
from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.domain.application_revision import RevisionImpact, RevisionTarget
from app.domain.execution_recovery import RecoveryPlan, RecoveryLifecycleOwnershipMode
from app.protocols.application_page_planning import _prepare_start_design_revision_payload
from app.protocols.workflow.projection import _public_workflow_state
from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle, load_application_lifecycle, claim_application_planning_run_for_recovery
from app.services.application_revision_lifecycle import register_revision_impact
from app.services.design_revision_admission import design_revision_input, approve_design_revision_from_checkpoint
from app.services.execution_recovery_executor import _apply_application_planning_transaction_authority


def setup_revision(workspace):
    """创建正常开发阶段和待批准影响，不改写任何 Graph checkpoint。"""
    lifecycle = create_application_lifecycle(application_id='app', application_name='恢复测试', initialization_thread_id='thread')
    lifecycle = lifecycle.model_copy(update={'initialization': lifecycle.initialization.model_copy(update={
        'stage': ApplicationLifecycleStage.READY_FOR_WORKBENCH, 'status': ApplicationLifecycleStatus.COMPLETED})})
    write_application_lifecycle(workspace, lifecycle)
    return register_revision_impact(workspace, interaction_id='impact', source_thread_id='source', source_run_id='conversation',
        request='增加静态说明', target=RevisionTarget(type='application'), impact=RevisionImpact(
            formalBranch='design_stage_revision', revisionType='requirement_scope_change', earliestArtifact='requirement-spec',
            affectedArtifacts=['requirement-spec', 'product-plan', 'technical-plan'], affectedResources=['application'], reason='新增展示规则'))


class DesignRevisionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """协议不提前批准，节点入场和批准后中断均保留恢复归属。"""

    def test_protocol_only_freezes_input(self):
        """HTTP 入场不得消费 pending impact，内部凭据不得成为公开状态。"""
        with tempfile.TemporaryDirectory() as workspace:
            pending = setup_revision(workspace)
            request = {'source': 'conversation_handoff', 'formalBranch': 'design_stage_revision', 'target': {'type': 'application'}, 'request': pending.request, 'confirmedImpact': {'interactionId': 'impact'}}
            result = _prepare_start_design_revision_payload({'forwardedProps': {'workspaceRoot': workspace, 'workflowAction': 'start_design_revision'}}, request)
            current = load_application_lifecycle(workspace)
            self.assertIsNone(current.active_formal_revision)
            self.assertEqual(current.pending_revision_impact.change_id, pending.change_id)
            values = design_revision_input(result['forwardedProps']['_designRevisionIntent'], workspace=workspace, request=pending.request, workflow_scope='application_planning', resume_from='design_intent_analysis')
            self.assertEqual(values['change_id'], pending.change_id)
            self.assertNotIn('design_revision_intent', _public_workflow_state(values))

    def test_recovery_claim_then_approval_is_idempotent(self):
        """恢复先领取 ownership 仍能批准原 changeId，重入不会重复写入或接受别笔修订。"""
        with tempfile.TemporaryDirectory() as workspace:
            pending = setup_revision(workspace)
            intent = {'changeId': pending.change_id, 'interactionId': pending.interaction_id}
            current = load_application_lifecycle(workspace)
            claim_application_planning_run_for_recovery(workspace, new_run_id='child', thread_id='thread', expected_lifecycle_revision=current.revision)
            first = approve_design_revision_from_checkpoint(workspace, intent, 'thread')
            approved = load_application_lifecycle(workspace)
            self.assertEqual(approve_design_revision_from_checkpoint(workspace, intent, 'thread'), first)
            self.assertEqual(first.change_id, pending.change_id)
            self.assertEqual(load_application_lifecycle(workspace).revision, approved.revision)
            self.assertIsNone(approved.pending_revision_impact)
            with self.assertRaises(ValueError):
                approve_design_revision_from_checkpoint(workspace, {**intent, 'changeId': 'other'}, 'thread')

    async def test_approval_window_allows_preownership(self):
        """批准前后均从冻结的原节点输入领取 child，修订归属漂移不被接受。"""
        with tempfile.TemporaryDirectory() as workspace:
            pending = setup_revision(workspace)
            intent = {'changeId': pending.change_id, 'interactionId': pending.interaction_id}

            async def read_state(_config):
                """提供标准 Graph reader 返回的已验证入口快照。"""
                return SimpleNamespace(values={'active_run_id': 'run', 'design_revision_intent': intent})

            source = SimpleNamespace(execution_kind='application_planning', thread_id='thread', run_id='run')
            for approved in [False, True]:
                if approved:
                    approve_design_revision_from_checkpoint(workspace, intent, 'thread')
                current = load_application_lifecycle(workspace)
                plan = RecoveryPlan(source_run_id='run', thread_id='thread', target_node='design_intent_analysis', checkpoint_id='checkpoint', lifecycle_revision=current.revision)
                result = await _apply_application_planning_transaction_authority(workspace=workspace, source=source, graph=SimpleNamespace(aget_state=read_state), plan=plan)
                self.assertEqual(result.lifecycle_ownership_mode, RecoveryLifecycleOwnershipMode.PRE_OWNERSHIP)
                with patch('app.services.execution_recovery_executor.design_revision_matches', return_value=False):
                    rejected = await _apply_application_planning_transaction_authority(workspace=workspace, source=source, graph=SimpleNamespace(aget_state=read_state), plan=plan)
                self.assertEqual(rejected.lifecycle_ownership_mode, RecoveryLifecycleOwnershipMode.SOURCE_OWNED)

    async def test_real_graph_keeps_input_when_approval_finishes_before_node_commit(self):
        """真实 SQLite checkpointer 保留批准前输入；批准后节点失败可幂等恢复而不重复消费影响。"""
        with tempfile.TemporaryDirectory() as workspace:
            pending = setup_revision(workspace)
            intent = {'changeId': pending.change_id, 'interactionId': pending.interaction_id}

            def approve_then_fail(*args):
                """只在真实批准后抛出节点异常，不改写检查点或生成结果。"""
                approve_design_revision_from_checkpoint(*args)
                raise RuntimeError('批准落盘后尚未进入需求节点')

            async with AsyncSqliteSaver.from_conn_string(str(Path(workspace) / 'graph.sqlite')) as saver:
                builder = StateGraph(ProjectState)
                builder.add_node('design_intent_analysis', analyze_design_intent)
                builder.add_edge(START, 'design_intent_analysis')
                builder.add_edge('design_intent_analysis', END)
                graph = builder.compile(checkpointer=saver)
                config = {'configurable': {'thread_id': 'thread'}}
                with patch('app.graph.application_planning_revision.approve_design_revision_from_checkpoint', side_effect=approve_then_fail):
                    with self.assertRaisesRegex(RuntimeError, '批准落盘'):
                        await graph.ainvoke({'request': pending.request, 'workspace': workspace,
                            'active_thread_id': 'thread', 'active_run_id': 'run',
                            'workflow_scope': 'application_planning', 'design_revision_intent': intent}, config)
                snapshot = await graph.aget_state(config)
                self.assertEqual(snapshot.next, ('design_intent_analysis',))
                self.assertEqual(snapshot.values['design_revision_intent'], intent)
                self.assertEqual(load_application_lifecycle(workspace).active_formal_revision.change_id, pending.change_id)
                restored = await graph.ainvoke(None, config)
                self.assertEqual(restored['design_revision_intent'], {})
                self.assertEqual(restored['design_change_target'], 'requirements')
