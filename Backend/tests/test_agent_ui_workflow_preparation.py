"""页面开发自动准备已确认设计，只在生产依赖变更或异常时暂停。"""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.agent_ui_build_fixtures import shared_build_target
from app.services.agent_ui_dependency_plan import target_snapshot
from app.services.agent_ui_design_transfer_plan import prepare_transfer_plan


class WorkflowPreparationTests(unittest.IsolatedAsyncioTestCase):
    """以当前正式磁盘夹具验证门禁、只读计划和服务端动作身份。"""

    async def asyncSetUp(self) -> None:
        """准备已经确认设计及完成安装的目标，但尚未初始化共享应用层。"""
        self.temp = TemporaryDirectory(prefix='Agent UI Workflow ')
        self.workspace = Path(self.temp.name).resolve()
        self.installation = shared_build_target(self.workspace, transfer=False)['installationPlanId']
        self.target = self.workspace / 'frontend'
        plan = prepare_transfer_plan(self.workspace, installation_plan_id=self.installation)
        self.source = self.workspace / plan['source']['projectRoot']
        product = json.loads((self.workspace / '.devagentstudio/plans/product-plan.json').read_text())
        self.page_id = product['pages'][0]['pageId']
        self.state = {'workspace': str(self.workspace), 'active_thread_id': 'original-thread',
                      'active_run_id': 'original-run',
                      'build_execution_scope': {'type': 'page', 'targetId': self.page_id}}

    async def asyncTearDown(self) -> None:
        """回收隔离夹具。"""
        self.temp.cleanup()

    async def prepare(self, **fields: object) -> dict | None:
        """调用主流程准备门，禁止真实初始化和构建。"""
        from app.services.agent_ui_workflow_preparation import prepare_agent_ui_workflow
        return await prepare_agent_ui_workflow({**self.state, **fields})

    def tool(self, action: str, target: Path, source_root: str, **_fields: object) -> dict:
        """只隔离公开工具进程，使用真实计划、磁盘写入、归属和证据校验。"""
        if action == 'initialize':
            files = {'.agent-ui/project.json': json.dumps({'sourceRoot': source_root}),
                     '.agent-ui/source-lock.json': json.dumps({'sourceRoot': source_root, 'items': {}}),
                     source_root + '/application/project-config.tsx': '// official default\n'}
            for name, content in files.items():
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
        return {'status': 'passed'}

    async def test_installed_page_automatically_initializes_replans_and_transfers(self) -> None:
        """开始页面开发即执行常规准备，初始化后的新计划仍需通过全部校验。"""
        progress: list[dict] = []
        with patch('app.services.agent_ui_design_transfer.run_public_tool', side_effect=self.tool), \
             patch('app.services.agent_ui_design_transfer.validate_target', return_value={'static': 'passed'}), \
             patch('app.services.agent_ui_workflow_preparation.get_stream_writer', return_value=progress.append):
            self.assertIsNone(await self.prepare())
        self.assertEqual((self.target / 'src/agent-ui/application/project-config.tsx').read_bytes(),
                         (self.source / 'src/agent-ui/application/project-config.tsx').read_bytes())
        self.assertIn('initialize', [item['stage'] for item in progress])
        self.assertIn('copy', [item['stage'] for item in progress])
        from app.services.agent_ui_build_evidence import load_build_evidence
        self.assertTrue(load_build_evidence(self.workspace))

    async def test_new_production_dependency_requires_review_without_writes(self) -> None:
        """缺少生产声明时只展示具体依赖差异，不能在点击开发时自动安装。"""
        package_path = self.target / 'package.json'
        package = json.loads(package_path.read_text())
        del package['dependencies']['zod']
        package_path.write_text(json.dumps(package))
        before = target_snapshot(self.target)
        with patch('app.services.agent_ui_workflow_preparation.execute_dependency_plan') as execute:
            result = await self.prepare()
        self.assertEqual(result['clarification']['status'], 'requires_user_input')
        self.assertEqual(result['clarification']['agentUiPreparation']['stage'], 'dependency')
        execute.assert_not_called()
        self.assertEqual(target_snapshot(self.target), before)

    async def test_existing_dependency_declarations_are_revalidated_automatically(self) -> None:
        """仅缺少有效安装凭据而生产声明不变时自动验证，不重复要求确认。"""
        from tests.test_agent_ui_dependency_install import AgentUiDependencyPlanTests
        import sys
        helper = AgentUiDependencyPlanTests()
        helper.target = self.target
        with patch('app.services.agent_ui_workflow_preparation.select_installation',
                   side_effect=[ValueError('凭据失效'), self.installation, self.installation]), \
             patch('app.services.agent_ui_dependency_install._tools', return_value=[sys.executable, __file__]), \
             patch('app.services.agent_ui_dependency_install._command', side_effect=helper.fake_command), \
             patch('app.services.agent_ui_design_transfer.run_public_tool', side_effect=self.tool), \
             patch('app.services.agent_ui_design_transfer.validate_target', return_value={'static': 'passed'}):
            self.assertIsNone(await self.prepare())

    async def test_confirmed_new_dependency_automatically_continues_full_preparation(self) -> None:
        """生产依赖只确认一次，后续真实初始化和转入不再要求逐步批准。"""
        from tests.test_agent_ui_dependency_install import AgentUiDependencyPlanTests
        import sys
        package_path = self.target / 'package.json'
        package = json.loads(package_path.read_text())
        del package['dependencies']['zod']
        package_path.write_text(json.dumps(package))
        result = await self.prepare()
        plan = result['clarification']['agentUiPreparation']['plan']
        helper = AgentUiDependencyPlanTests()
        helper.target = self.target
        with patch('app.services.agent_ui_dependency_install._tools', return_value=[sys.executable, __file__]), \
             patch('app.services.agent_ui_dependency_install._command', side_effect=helper.fake_command), \
             patch('app.services.agent_ui_design_transfer.run_public_tool', side_effect=self.tool), \
             patch('app.services.agent_ui_design_transfer.validate_target', return_value={'static': 'passed'}):
            self.assertIsNone(await self.prepare(agent_ui_preparation_action={
                'action': 'confirm', 'planId': plan['planId'], 'reviewId': plan['reviewId']}))
        self.assertEqual(json.loads(package_path.read_text())['dependencies']['zod'], '4.5.4')

    async def test_dependency_version_conflict_is_not_automatically_changed(self) -> None:
        """已有不同版本必须暂停，不能因自动准备而升级或降级生产依赖。"""
        package_path = self.target / 'package.json'
        package = json.loads(package_path.read_text())
        package['dependencies']['zod'] = '3.0.0'
        package_path.write_text(json.dumps(package))
        before = target_snapshot(self.target)
        with patch('app.services.agent_ui_workflow_preparation.execute_dependency_plan') as execute:
            result = await self.prepare()
        self.assertEqual(result['clarification']['agentUiPreparation']['stage'], 'blocked')
        self.assertIn('版本', result['clarification']['agentUiPreparation']['error'])
        self.assertEqual(target_snapshot(self.target), before)
        execute.assert_not_called()

    async def test_current_verified_evidence_continues_without_preparation(self) -> None:
        """已有效转入的页面直接继续任务规划，不重复展示或执行安装。"""
        with patch('app.services.agent_ui_workflow_preparation.load_build_evidence', return_value={'current': True}):
            self.assertIsNone(await self.prepare())

    async def test_agent_scope_never_prepares_frontend(self) -> None:
        """独立Agent开发不触发页面准备或安装。"""
        with patch('app.services.agent_ui_workflow_preparation._formal_state') as formal:
            self.assertIsNone(await self.prepare(build_execution_scope={'type': 'agent', 'targetId': 'agent-1'}))
        formal.assert_not_called()

    async def test_confirmation_cannot_bypass_formal_design_gate(self) -> None:
        """任何批准都不能替代当前正式设计确认或使过期设计进入写入。"""
        # 从fixture实际产物找到UiDesign路径，不制造另一份历史格式。
        from app.services.stage_artifacts import _ARTIFACTS
        path = self.workspace / '.devagentstudio' / _ARTIFACTS['ui_designs']
        value = json.loads(path.read_text())
        value['confirmation_status'] = 'pending'
        path.write_text(json.dumps(value))
        with patch('app.services.agent_ui_workflow_preparation.execute_transfer') as execute:
            result = await self.prepare()
        self.assertEqual(result['clarification']['agentUiPreparation']['stage'], 'blocked')
        execute.assert_not_called()
        self.assertFalse((self.target / '.agent-ui').exists())

    async def test_stale_confirmation_is_replanned_without_writes(self) -> None:
        """过期依赖批准必须停止，不能转而执行新计划。"""
        before = target_snapshot(self.target)
        result = await self.prepare(agent_ui_preparation_action={
            'action': 'confirm', 'planId': 'a' * 64, 'reviewId': 'b' * 32})
        review = result['clarification']['agentUiPreparation']
        self.assertEqual(review['stage'], 'blocked')
        self.assertIn('过期', review['error'])
        self.assertEqual(target_snapshot(self.target), before)

    async def test_confirm_uses_original_workflow_identity_and_server_stage(self) -> None:
        """依赖确认只批准当前生产依赖计划，使用原工作流身份后自动继续。"""
        from app.services.agent_ui_dependency_plan import prepare_dependency_plan
        plan = prepare_dependency_plan(self.workspace, source='local-vendor', review_id='b' * 32)
        with patch('app.services.agent_ui_workflow_preparation.execute_dependency_plan',
                   return_value={'state': 'failed', 'message': '安装失败，保留现场'}) as execute:
            result = await self.prepare(agent_ui_preparation_action={'action': 'confirm',
                'planId': plan['planId'], 'reviewId': plan['reviewId']})
        self.assertEqual(execute.call_args.kwargs['thread_id'], 'original-thread')
        self.assertEqual(execute.call_args.kwargs['run_id'], 'original-run')
        self.assertIn('安装失败', result['clarification']['agentUiPreparation']['error'])

    async def test_source_changed_after_review_cannot_execute_old_approval(self) -> None:
        """设计来源变化必须重新正式确认，旧转入批准不能替它放行。"""
        source_file = self.source / 'src/agent-ui/app-ui.json'
        source_file.write_text('{"root":{"type":"slot","plugins":["changed"]}}')
        with patch('app.services.agent_ui_workflow_preparation.execute_transfer') as execute:
            result = await self.prepare()
        self.assertEqual(result['clarification']['agentUiPreparation']['stage'], 'blocked')
        execute.assert_not_called()

    async def test_current_transfer_blockers_are_not_hidden_by_valid_installation(self) -> None:
        """依赖verified不代表转入通过，当前适配冲突必须保留在对话门内。"""
        with patch('app.services.agent_ui_workflow_preparation.prepare_transfer_plan',
                   return_value={'status': 'blocked', 'blockers': ['当前构建入口存在冲突']}):
            result = await self.prepare()
        review = result['clarification']['agentUiPreparation']
        self.assertEqual(review['stage'], 'blocked')
        self.assertIn('冲突', review['error'])

    async def test_main_adapter_prepares_before_generic_build_context_error(self) -> None:
        """主页面入口到达相同准备门，不能先被通用BuildContext错误截断。"""
        from app.graph.nodes.task_planning_adapter import create_async_workflow_planning_adapter
        adapter = create_async_workflow_planning_adapter()
        with patch('app.services.agent_ui_workflow_preparation.prepare_transfer_plan',
                   return_value={'status': 'blocked', 'blockers': ['目标冲突']}), \
             patch('app.graph.nodes.task_planning_adapter._assemble_planning_context') as assemble:
            result = await adapter(self.state)
        self.assertEqual(result['clarification']['mode'], 'agent_ui_integration_pending')
        assemble.assert_not_called()

    async def test_failed_or_unknown_preparation_stops_without_replay(self) -> None:
        """失败、未知和重复阶段不得自动继续或生成任务。"""
        for state in ('failed', 'unknown', 'stopped', 'initialized'):
            with self.subTest(state=state), \
                 patch('app.services.agent_ui_workflow_preparation.execute_transfer',
                       return_value={'state': state, 'message': '请核对现场'}) as execute:
                result = await self.prepare()
            self.assertEqual(result['clarification']['agentUiPreparation']['stage'], 'blocked')
            self.assertLessEqual(execute.call_count, 2)

    async def test_post_initialization_conflict_preserves_user_file(self) -> None:
        """初始化之后重新检测冲突，不能因自动准备而接管用户文件。"""
        from app.services.agent_ui_design_transfer import execute_transfer
        def initialize_with_conflict(workspace: Path, **fields: object) -> dict:
            """在初始化完成后的重新规划中模拟用户并发编辑。"""
            result = execute_transfer(workspace, **fields)
            if fields['action'] == 'initialize':
                (self.target / 'src/agent-ui/application/project-config.tsx').write_text('user customized')
            return result

        with patch('app.services.agent_ui_design_transfer.run_public_tool', side_effect=self.tool), \
             patch('app.services.agent_ui_workflow_preparation.execute_transfer', side_effect=initialize_with_conflict), \
             patch('app.services.agent_ui_workflow_preparation.select_installation', return_value=self.installation):
            result = await self.prepare()
        self.assertEqual(result['clarification']['agentUiPreparation']['stage'], 'blocked')
        self.assertEqual((self.target / 'src/agent-ui/application/project-config.tsx').read_text(), 'user customized')

    async def test_missing_workflow_identity_cannot_write(self) -> None:
        """自动准备同样需要真实主工作流身份，不能由裸状态触发副作用。"""
        with patch('app.services.agent_ui_workflow_preparation.execute_transfer') as execute:
            result = await self.prepare(active_run_id='')
        self.assertEqual(result['clarification']['agentUiPreparation']['stage'], 'blocked')
        execute.assert_not_called()


class PreparationRequestTests(unittest.TestCase):
    """准备动作只能经主Workflow结构化答案进入固定服务端恢复节点。"""

    def test_refresh_routes_to_prepare_and_clears_old_dag_confirmation(self) -> None:
        """重新检查不批准生产依赖变更，并清除checkpoint中的旧DAG确认。"""
        from app.protocols.workflow.request import workflow_run_inputs
        result = workflow_run_inputs({'clarificationAnswers': {'agent_ui_preparation': {'action': 'refresh'}}})
        self.assertEqual(result['resume_from'], 'prepare_build_tasks')
        self.assertEqual(result['resume_values']['agent_ui_preparation_action']['action'], 'refresh')
        self.assertEqual(result['resume_values']['build_task_plan_confirmation'], {})

    def test_unknown_fields_and_client_nodes_are_rejected(self) -> None:
        """答案不能提供源码、任意命令或指定下游Graph节点。"""
        from app.protocols.workflow.request import workflow_run_inputs
        for fields in ({'after': 'injected'}, {'command': 'install'}, {'node': 'build'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                workflow_run_inputs({'clarificationAnswers': {'agent_ui_preparation': {'action': 'refresh', **fields}}})
        with self.assertRaises(ValueError):
            workflow_run_inputs({'resume_from': 'build',
                                 'clarificationAnswers': {'agent_ui_preparation': {'action': 'refresh'}}})

    def test_transfer_confirmation_fields_are_not_part_of_main_workflow_contract(self) -> None:
        """初始化和转入已由主流程托管，不再接收独立写入确认字段。"""
        from app.protocols.workflow.request import workflow_run_inputs
        with self.assertRaises(ValueError):
            workflow_run_inputs({'clarificationAnswers': {'agent_ui_preparation': {
                'action': 'confirm', 'planId': 'a' * 64, 'installationPlanId': 'b' * 64,
                'targetSourceRoot': 'src/agent-ui'}}})


class PreparationLifecycleTests(unittest.TestCase):
    """准备确认只能原目标原对话消费当前一次性交互，不能挪用批准。"""

    def setUp(self) -> None:
        """持久化最小当前生命周期，使用真实原子接管服务。"""
        from app.domain.application_lifecycle import ApplicationLifecycle
        from app.services.application_lifecycle import write_application_lifecycle
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        lifecycle = ApplicationLifecycle.model_validate({
            'application': {'id': 'app-preparation', 'name': '页面准备'},
            'updatedAt': '2026-11-11T00:00:00Z', 'revision': 4,
            'initialization': {'stage': 'ready_for_workbench', 'status': 'completed'},
            'activeRunId': 'original-run', 'activeExecutions': {'original-run': {
                'scope': 'page', 'targetId': 'agent-chat', 'pageId': 'agent-chat',
                'threadId': 'original-thread', 'runId': 'original-run',
                'phase': 'prepare_build_tasks', 'status': 'awaiting_user',
                'pendingInteraction': {'id': 'preparation-review', 'type': 'agent_ui_integration_pending',
                    'basedOnRevision': 4, 'payload': {'mode': 'agent_ui_integration_pending'},
                    'artifactRefs': [], 'createdAt': '2026-11-11T00:00:00Z'},
                'startedAt': '2026-11-11T00:00:00Z', 'updatedAt': '2026-11-11T00:00:00Z'}}})
        write_application_lifecycle(self.temp.name, lifecycle)
        self.values = {'build_execution_scope': {'type': 'page', 'targetId': 'agent-chat'},
            'resume_execution_run_id': 'original-run',
            'agent_ui_preparation_action': {'action': 'refresh'},
            'lifecycle_interaction_submission': {'runId': 'original-run',
                'id': 'preparation-review', 'basedOnRevision': 4}}

    def begin(self, *, thread: str = 'original-thread', **fields: object) -> dict:
        """经正式workflow生命周期适配器接管原开发执行。"""
        from app.protocols.workflow.lifecycle import begin_workflow_lifecycle
        return begin_workflow_lifecycle({'workspace': self.temp.name,
            'resume_values': {**self.values, **fields}}, thread_id=thread,
            run_id='continued-run', phase='prepare_build_tasks')

    def test_original_preparation_interaction_is_consumed_with_execution(self) -> None:
        """原身份接管成功后新运行取代旧运行，准备本身不批准任务规划。"""
        result = self.begin()
        self.assertIn('continued-run', result['activeExecutions'])
        self.assertNotIn('original-run', result['activeExecutions'])

    def test_other_thread_or_target_cannot_consume_original_interaction(self) -> None:
        """错误身份不能消费令牌或写入新执行。"""
        from app.services.application_lifecycle import ApplicationLifecycleConflictError, load_application_lifecycle
        with self.assertRaises(ApplicationLifecycleConflictError):
            self.begin(thread='other-thread')
        with self.assertRaises(ApplicationLifecycleConflictError):
            self.begin(build_execution_scope={'type': 'page', 'targetId': 'other-page'})
        pending = load_application_lifecycle(self.temp.name).active_executions['original-run'].pending_interaction
        self.assertIsNone(pending.submitted_at)

    def test_missing_or_stale_interaction_cannot_start_preparation(self) -> None:
        """任何新会话或旧并发快照都不能绕过当前页面确认门。"""
        from app.services.application_lifecycle import ApplicationLifecycleConflictError
        with self.assertRaises(ApplicationLifecycleConflictError):
            self.begin(resume_execution_run_id='')
        with self.assertRaises(ApplicationLifecycleConflictError):
            self.begin(lifecycle_interaction_submission={'runId': 'original-run',
                'id': 'preparation-review', 'basedOnRevision': 3})
