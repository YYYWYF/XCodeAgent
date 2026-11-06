"""API 映射确认必须能接管正常等待门，同时保持原会话、目标和并发凭据边界。"""

import copy
import tempfile
import unittest
from unittest.mock import patch

from app.domain.application_lifecycle import PendingInteractionType, WorkbenchExecutionStatus
from app.protocols.workflow.lifecycle import begin_workflow_lifecycle, project_workflow_lifecycle_boundary
from app.protocols.workflow.request import workflow_run_inputs
from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError, application_lifecycle_path, load_application_lifecycle,
    start_workbench_execution, update_workbench_execution,
)
from app.graph.nodes.api_design import api_design_readiness_gate
from app.workspace.endpoint_design_documents import write_endpoint_design
from tests.test_api_design_node import _plan, _write_plan, _empty_design
from tests.test_execution_resource_locks import _write_ready_lifecycle


class ApiDesignGateContinuationTests(unittest.TestCase):
    """覆盖浏览器按钮的当前引用请求、真实生命周期接管与磁盘映射复检。"""

    def setUp(self) -> None:
        """隔离生命周期和正式映射文件，不操作用户项目。"""
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = self.directory.name
        self.plan = _plan()
        _write_plan(self.workspace, self.plan)
        _write_ready_lifecycle(self.workspace)

    def tearDown(self) -> None:
        """回收本用例的隔离工作区。"""
        self.directory.cleanup()

    def _waiting(self, scope_type: str = 'endpoint') -> dict:
        """通过真实门禁建立待配置状态，并构造与前端按钮一致的引用请求。"""
        target = 'orders.list' if scope_type == 'endpoint' else 'orders' if scope_type == 'page' else 'application'
        scope = {'type': scope_type, 'targetId': target}
        if scope_type == 'endpoint':
            scope['apiContractId'] = 'orders-api'
        start_workbench_execution(self.workspace, scope=scope_type, target_id=target,
            page_id=target if scope_type == 'page' else None,
            thread_id='thread', owner_session_id='owner', run_id='source', phase='api_design_readiness_gate')
        state = {'workspace': self.workspace, 'project_plan': self.plan, 'build_execution_scope': scope,
                 'active_run_id': 'source', 'active_thread_id': 'thread', 'owner_session_id': 'owner'}
        if scope_type == 'page':
            state['selectedPageId'] = target
        elif scope_type == 'endpoint':
            state.update(selected_api_contract_id='orders-api', selected_endpoint_id=target, detail_target_type='endpoint')
        waiting = api_design_readiness_gate(state)
        state.update(waiting)
        state['lifecycle'] = project_workflow_lifecycle_boundary(self.workspace, run_id='source',
            node_name='api_design_readiness_gate', update=waiting)
        action = {'action': 'refresh', 'targetType': scope_type, 'targetId': target}
        if scope_type == 'endpoint':
            action['apiContractId'] = 'orders-api'
        # 正式产物合同由 API 节点用例覆盖，此处固定加载结果以聚焦请求身份和生命周期接管。
        with patch('app.protocols.workflow.request._project_plan_start_values', return_value={'project_plan': self.plan}):
            return workflow_run_inputs({'request': '确认当前 API 映射并开始检测。', 'forwardedProps': {
                'workspaceRoot': self.workspace, 'sessionId': 'owner',
                'resumeReference': {'runId': 'source', 'threadId': 'thread'},
                'clarificationAnswers': {'api_design_gate': action},
            }}, server_state=state)

    def test_refresh_reference_resumes_waiting_application_page_and_endpoint(self) -> None:
        """三种门禁范围均接受显式 refresh，重新检测仍保留原始目标。"""
        for scope_type in ['application', 'page', 'endpoint']:
            with self.subTest(scope=scope_type), tempfile.TemporaryDirectory() as workspace:
                self.workspace = workspace
                _write_plan(workspace, self.plan)
                _write_ready_lifecycle(workspace)
                inputs = self._waiting(scope_type)
                self.assertEqual(inputs['resume_values']['resume_execution_run_id'], 'source')
                payload = begin_workflow_lifecycle(inputs, thread_id='thread', run_id='next', phase='api_design_readiness_gate')
                self.assertNotIn('source', payload['activeExecutions'])
                self.assertEqual(payload['activeExecutions']['next']['scope'], scope_type)
                self.assertEqual(payload['activeExecutions']['next']['status'], 'running')
                self.assertEqual(api_design_readiness_gate({**inputs['resume_values'], 'workspace': workspace,
                    'project_plan': self.plan})['status'], 'requires_user_input')

    def test_saved_mapping_is_rechecked_after_lifecycle_admission(self) -> None:
        """映射保存后显式检测能进入节点并从磁盘读取当前正式配置。"""
        inputs = self._waiting()
        write_endpoint_design(self.workspace, _empty_design(self.workspace, 'orders.list'))
        begin_workflow_lifecycle(inputs, thread_id='thread', run_id='next', phase='api_design_readiness_gate')
        result = api_design_readiness_gate({**inputs['resume_values'], 'workspace': self.workspace, 'project_plan': self.plan})
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['api_design_readiness']['ready'])
        self.assertEqual(result['clarification'], {})

    def test_current_server_gate_can_supply_missing_checkpoint_interaction(self) -> None:
        """checkpoint 缺少最新 lifecycle 凭据时仍绑定当前服务端原确认门。"""
        inputs = self._waiting()
        inputs['resume_values'].pop('lifecycle_interaction_submission', None)
        payload = begin_workflow_lifecycle(inputs, thread_id='thread', run_id='next', phase='api_design_readiness_gate')
        self.assertEqual(payload['activeExecutions']['next']['status'], 'running')
        with self.assertRaisesRegex(ApplicationLifecycleConflictError, '已不存在'):
            begin_workflow_lifecycle(inputs, thread_id='thread', run_id='duplicate', phase='api_design_readiness_gate')

    def test_invalid_actions_threads_targets_and_credentials_leave_original_gate_untouched(self) -> None:
        """普通恢复、错目标/Contract/线程以及过期凭据均不能消费当前确认门。"""
        inputs = self._waiting()
        path = application_lifecycle_path(self.workspace)
        original = path.read_bytes()
        variants = []
        for update in [None, {'action': 'save'}, {'targetId': 'orders.create'}, {'apiContractId': 'other-api'}]:
            candidate = copy.deepcopy(inputs)
            if update is None:
                candidate['resume_values'].pop('api_design_gate_action')
            else:
                candidate['resume_values']['api_design_gate_action'].update(update)
            variants.append((candidate, 'thread', 'api_design_readiness_gate'))
        variants.append((copy.deepcopy(inputs), 'another-thread', 'api_design_readiness_gate'))
        variants.append((copy.deepcopy(inputs), 'thread', 'prepare_build_tasks'))
        stale = copy.deepcopy(inputs)
        stale['resume_values']['lifecycle_interaction_submission']['basedOnRevision'] += 1
        variants.append((stale, 'thread', 'api_design_readiness_gate'))
        missing_reference = copy.deepcopy(inputs)
        missing_reference['resume_values'].pop('resume_execution_run_id')
        variants.append((missing_reference, 'thread', 'api_design_readiness_gate'))
        for candidate, thread, phase in variants:
            with self.subTest(thread=thread, phase=phase, action=candidate['resume_values'].get('api_design_gate_action')):
                with self.assertRaises(ApplicationLifecycleConflictError):
                    begin_workflow_lifecycle(candidate, thread_id=thread, run_id='invalid', phase=phase)
                self.assertEqual(path.read_bytes(), original)

    def test_admission_failure_keeps_confirmation_retryable(self) -> None:
        """入场失败不提前写 submittedAt，原请求可在故障解除后正常重试。"""
        inputs = self._waiting()
        path = application_lifecycle_path(self.workspace)
        original = path.read_bytes()
        with patch('app.services.development_artifacts.reconcile_development_artifacts', side_effect=RuntimeError('入场失败')):
            with self.assertRaisesRegex(RuntimeError, '入场失败'):
                begin_workflow_lifecycle(inputs, thread_id='thread', run_id='next', phase='api_design_readiness_gate')
        self.assertEqual(path.read_bytes(), original)
        self.assertIsNone(load_application_lifecycle(self.workspace).active_executions['source'].pending_interaction.submitted_at)
        begin_workflow_lifecycle(inputs, thread_id='thread', run_id='next', phase='api_design_readiness_gate')

    def test_running_execution_cannot_be_taken_over_by_refresh(self) -> None:
        """正在运行的执行不能借映射检测动作被接管。"""
        inputs = self._waiting()
        update_workbench_execution(self.workspace, run_id='source', phase='api_design_readiness_gate', status=WorkbenchExecutionStatus.RUNNING)
        original = application_lifecycle_path(self.workspace).read_bytes()
        with self.assertRaises(ApplicationLifecycleConflictError):
            begin_workflow_lifecycle(inputs, thread_id='thread', run_id='invalid', phase='api_design_readiness_gate')
        self.assertEqual(application_lifecycle_path(self.workspace).read_bytes(), original)

    def test_refresh_cannot_consume_another_confirmation_gate(self) -> None:
        """API refresh 不能被其他等待类型的通用确认白名单放行。"""
        inputs = self._waiting()
        current = update_workbench_execution(self.workspace, run_id='source', phase='prepare_build_tasks',
            status=WorkbenchExecutionStatus.AWAITING_USER, pending_type=PendingInteractionType.TASK_PLAN_CONFIRMATION,
            pending_payload={'mode': 'build_task_plan_confirmation'})
        pending = current.active_executions['source'].pending_interaction
        inputs['resume_values']['lifecycle_interaction_submission'] = {
            'runId': 'source', 'id': pending.id, 'basedOnRevision': pending.based_on_revision,
        }
        original = application_lifecycle_path(self.workspace).read_bytes()
        with self.assertRaisesRegex(ApplicationLifecycleConflictError, '不属于当前等待确认'):
            begin_workflow_lifecycle(inputs, thread_id='thread', run_id='invalid', phase='api_design_readiness_gate')
        self.assertEqual(application_lifecycle_path(self.workspace).read_bytes(), original)
