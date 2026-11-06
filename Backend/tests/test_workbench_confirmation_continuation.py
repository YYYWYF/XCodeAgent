"""覆盖最终验收和数据库审批的继续入口，不调用模型或执行数据库操作。"""

import copy
import tempfile
import unittest

from app.domain.application_lifecycle import PendingInteractionType, WorkbenchExecutionStatus
from app.graph.subgraphs.acceptance import acceptance_review
from app.graph.subgraphs.build import _database_approval_payload
from app.protocols.workflow.lifecycle import begin_workflow_lifecycle, project_workflow_lifecycle_boundary
from app.protocols.workflow.request import workflow_run_inputs
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    application_lifecycle_path,
    start_workbench_execution,
    update_workbench_execution,
)
from tests.test_execution_resource_locks import _write_ready_lifecycle


class WorkbenchConfirmationContinuationTests(unittest.TestCase):
    """使用真实请求适配器与生命周期文件验证两个等待门的继续语义。"""

    def _waiting(self, kind: str, answers: dict, *, include_lifecycle: bool = True) -> tuple[str, dict, dict]:
        """建立真实待确认交互，再按前端当前合同只提交 Run/Thread 引用及答案。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        workspace = directory.name
        _write_ready_lifecycle(workspace)
        phase = 'acceptance' if kind == 'page_acceptance' else 'build'
        start_workbench_execution(
            workspace, scope='page', target_id='orders', page_id='orders',
            thread_id='thread', owner_session_id='owner', run_id='source', phase=phase,
        )
        state = {
            'workspace': workspace, 'active_run_id': 'source', 'active_thread_id': 'thread',
            'owner_session_id': 'owner', 'selectedPageId': 'orders',
            'build_execution_scope': {'type': 'page', 'targetId': 'orders'},
            'request': '开发订单页面',
        }
        if kind == 'page_acceptance':
            waiting = acceptance_review({**state, 'preview_url': 'http://preview.local'})
        else:
            clarification = _database_approval_payload({
                'database_approval': {'id': 'approval', 'tool': 'database.execute'},
                'database_change_plan': {'statements': ['ALTER TABLE orders ADD COLUMN note TEXT']},
            })
            waiting = {'phase': 'build', 'status': 'requires_user_input', 'clarification': clarification}
        state.update(waiting)
        lifecycle = project_workflow_lifecycle_boundary(
            workspace, run_id='source', node_name=phase, update=waiting,
        )
        if include_lifecycle:
            state['lifecycle'] = lifecycle
        inputs = workflow_run_inputs({
            'request': '提交当前确认后继续',
            'forwardedProps': {
                'workspaceRoot': workspace, 'sessionId': 'owner',
                'resumeReference': {'runId': 'source', 'threadId': 'thread'},
                'clarificationAnswers': answers,
            },
        }, server_state=state)
        self.assertEqual(inputs['resume_values']['resume_execution_run_id'], 'source')
        return workspace, inputs, state

    def test_final_preview_acceptance_can_continue_to_accepted(self) -> None:
        """点击验收通过可接替 PAGE_ACCEPTANCE 等待门，并消费结构化 accepted 答案。"""
        _, inputs, state = self._waiting('page_acceptance', {'page_acceptance': 'accepted'})
        self.assertEqual(inputs['resume_from'], 'acceptance')
        payload = begin_workflow_lifecycle(inputs, thread_id='thread', run_id='next', phase='acceptance')
        self.assertNotIn('source', payload['activeExecutions'])
        self.assertEqual(payload['activeExecutions']['next']['targetId'], 'orders')
        self.assertEqual(payload['activeExecutions']['next']['status'], 'running')
        result = acceptance_review({**state, **inputs['resume_values']})
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['accepted'])

    def test_acceptance_reentry_without_decision_still_waits_for_user(self) -> None:
        """允许继续读取当前验收门，不代表普通文本能冒充验收通过。"""
        _, inputs, state = self._waiting('page_acceptance', {})
        begin_workflow_lifecycle(inputs, thread_id='thread', run_id='next', phase='acceptance')
        result = acceptance_review({**state, **inputs['resume_values']})
        self.assertEqual(result['status'], 'requires_user_input')
        self.assertFalse(result['accepted'])

    def test_database_approval_decisions_can_continue_original_build(self) -> None:
        """批准和拒绝的既有审批答案都可返回原 Build，让原数据库审批逻辑处理结果。"""
        for answer in ['同意执行，仅本次', '同意执行，后续相同操作不再询问', '拒绝执行']:
            with self.subTest(answer=answer):
                _, inputs, _ = self._waiting('agent_approval', {'database_approval': answer})
                self.assertEqual(inputs['resume_from'], 'build')
                payload = begin_workflow_lifecycle(inputs, thread_id='thread', run_id='next', phase='build')
                self.assertNotIn('source', payload['activeExecutions'])
                self.assertEqual(payload['activeExecutions']['next']['threadId'], 'thread')
                self.assertEqual(payload['activeExecutions']['next']['targetId'], 'orders')
                self.assertEqual(payload['activeExecutions']['next']['status'], 'running')

    def test_other_thread_or_target_cannot_take_over_either_gate(self) -> None:
        """新增放行不允许跨线程、跨目标或跨范围接管原执行。"""
        for kind in ['page_acceptance', 'agent_approval']:
            for change in ['thread', 'target', 'scope']:
                with self.subTest(kind=kind, change=change):
                    workspace, inputs, _ = self._waiting(kind, {}, include_lifecycle=False)
                    inputs = copy.deepcopy(inputs)
                    thread = 'other-thread' if change == 'thread' else 'thread'
                    if change == 'target':
                        inputs['resume_values']['selectedPageId'] = 'another-page'
                        inputs['resume_values']['build_execution_scope']['targetId'] = 'another-page'
                    elif change == 'scope':
                        inputs['resume_values'].pop('selectedPageId', None)
                        inputs['resume_values']['build_execution_scope'] = {'type': 'application', 'targetId': 'application'}
                    path = application_lifecycle_path(workspace)
                    original = path.read_bytes()
                    with self.assertRaises(ApplicationLifecycleConflictError):
                        begin_workflow_lifecycle(inputs, thread_id=thread, run_id='invalid', phase=inputs['resume_from'])
                    self.assertEqual(path.read_bytes(), original)

    def test_running_execution_cannot_be_taken_over(self) -> None:
        """两个新增分支仅放行 awaiting_user，不能接管仍在运行的执行。"""
        for kind in ['page_acceptance', 'agent_approval']:
            with self.subTest(kind=kind):
                workspace, inputs, _ = self._waiting(kind, {}, include_lifecycle=False)
                update_workbench_execution(workspace, run_id='source', phase=inputs['resume_from'], status=WorkbenchExecutionStatus.RUNNING)
                with self.assertRaisesRegex(ApplicationLifecycleConflictError, '只有已停止或失败'):
                    begin_workflow_lifecycle(inputs, thread_id='thread', run_id='invalid', phase=inputs['resume_from'])

    def test_stale_confirmation_credentials_are_still_rejected(self) -> None:
        """旧交互版本继续由现有校验拒绝，不消费当前确认门。"""
        for kind in ['page_acceptance', 'agent_approval']:
            with self.subTest(kind=kind):
                workspace, inputs, _ = self._waiting(kind, {})
                inputs['resume_values']['lifecycle_interaction_submission']['basedOnRevision'] += 1
                path = application_lifecycle_path(workspace)
                original = path.read_bytes()
                with self.assertRaises(ApplicationLifecycleConflictError):
                    begin_workflow_lifecycle(inputs, thread_id='thread', run_id='invalid', phase=inputs['resume_from'])
                self.assertEqual(path.read_bytes(), original)

    def test_unrelated_waiting_interaction_remains_blocked(self) -> None:
        """不把所有 awaiting_user 状态整体放开，未支持的交互仍然拒绝普通继续。"""
        workspace, inputs, _ = self._waiting('page_acceptance', {}, include_lifecycle=False)
        update_workbench_execution(workspace, run_id='source', phase='acceptance',
            status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=PendingInteractionType.PLAN_ADJUSTMENT,
            pending_payload={'mode': 'plan_adjustment'})
        with self.assertRaisesRegex(ApplicationLifecycleConflictError, '只有已停止或失败'):
            begin_workflow_lifecycle(inputs, thread_id='thread', run_id='invalid', phase='acceptance')
