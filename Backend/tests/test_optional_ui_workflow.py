"""所有流程的 UI 设计稿均为可选参考，不能充当前置门禁。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.artifact_freshness import upstream_errors
from app.services.artifact_invalidation import assert_confirmed_artifact_closure
from app.services.build_context_resolver import _materialize_page_confirmation_plan
from app.services.change_contracts import load_confirmed_contract_corpus
from app.services.stage_artifacts import _ARTIFACTS, _STAGES, _TECHNICAL_STAGES, load_stage_artifacts
from app.services.stage_prerequisites import required_artifacts, require_stage_inputs


class OptionalUiWorkflowTests(unittest.TestCase):
    """使用真实磁盘产物覆盖缺失、损坏、空稿、未确认及过期设计稿。"""

    def write(self, root, relative, value):
        """在隔离工作区写入当前合同的 JSON 文件。"""
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_every_stage_omits_ui_from_prerequisites(self):
        """规划、DAG、页面、API、测试和验收均不声明 UI 必需输入。"""
        for node in _STAGES:
            for scope in ('application', 'page', 'endpoint'):
                with self.subTest(node=node, scope=scope):
                    self.assertNotIn('ui_designs', required_artifacts(node, state={
                        'build_execution_scope': {'type': scope, 'targetId': 'target'}}))

    def test_planning_and_page_context_ignore_ui_readiness(self):
        """真实文件加载和页面合同物化不因设计稿异常中断。"""
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / '.devagentstudio'
            self.write(root, 'application.json', {})
            for key, relative in _ARTIFACTS.items():
                if key != 'ui_designs':
                    self.write(root, relative, {'confirmation_status': 'confirmed',
                        'artifact_type': 'technical-plan' if key == 'technical_plan' else key, 'pages': []})
            ui_path = root / _ARTIFACTS['ui_designs']
            cases = (None, '{', '{}', '[]', json.dumps({'confirmation_status': 'pending_user_confirmation',
                'product_plan_sha256': 'outdated', 'pages': [{'pageId': 'other', 'status': 'generation_failed'}]}))
            for content in cases:
                with self.subTest(content=content):
                    if content is not None:
                        ui_path.write_text(content)
                    for node in _TECHNICAL_STAGES | {'api_design_readiness_gate', 'prepare_build_tasks'}:
                        fields = load_stage_artifacts({'workspace': workspace}, node)
                        self.assertEqual(fields['product_plan']['confirmation_status'], 'confirmed')
                    runtime = _materialize_page_confirmation_plan(Path(workspace), {
                        'artifact_type': 'technical-plan', 'pages': [], 'api_contracts': []})
                    self.assertEqual(runtime['page_implementation_contracts'], [])
                    corpus = load_confirmed_contract_corpus(workspace)
                    self.assertTrue(corpus.coverage_complete)
                    self.assertEqual(corpus.relevant_unavailable_artifacts('调整页面布局', target={'type': 'page'}), ())

    def test_ui_hash_and_status_never_invalidate_other_artifacts(self):
        """UI 未确认和引用摘要过期不触发技术规划或 Build 的失效闭包。"""
        plan = {'confirmation_status': 'confirmed', 'ui_designs_sha256': 'outdated',
                'basedOn': [{'artifactKey': 'ui-design', 'sha256': '0' * 64}]}
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / '.devagentstudio'
            self.write(root, 'application.json', {})
            for key, relative in _ARTIFACTS.items():
                if key != 'ui_designs':
                    self.write(root, relative, plan if key == 'technical_plan' else {'confirmation_status': 'confirmed'})
            self.assertEqual(upstream_errors(root, 'technical_plan', plan), [])
            assert_confirmed_artifact_closure({'technical-plan': plan,
                'ui-design': {'confirmation_status': 'pending_user_confirmation'}}, canonical_hashes={})
            require_stage_inputs({'workspace': workspace}, 'technical_planning_begin')
            require_stage_inputs({'workspace': workspace}, 'api_design_readiness_gate')

    def test_ui_reference_changes_do_not_change_task_or_dag_inputs(self):
        """设计稿引用从有到无不改变页面覆盖摘要或 DAG 冻结业务输入。"""
        from copy import deepcopy
        from app.graph.nodes.task_planning_inputs import mainline_formal_contract_inputs
        from app.services.page_task_freshness import page_task_input_digest

        plan = {'pages': [{'pageId': 'home'}], 'page_implementation_contracts': [
            {'pageId': 'home', 'requiredEndpointIds': [], 'actionBindings': [{'actionId': 'open'}]}]}
        with_ui = deepcopy(plan)
        with_ui['ui_designs_sha256'] = 'changed'
        with_ui['page_implementation_contracts'][0]['uiDesignRef'] = {'path': 'optional.tsx', 'sha256': 'changed'}
        with_ui['page_implementation_contracts'][0]['actionBindings'][0]['uiControlRefs'] = [{'controlId': 'open'}]
        self.assertEqual(page_task_input_digest('page:home', plan), page_task_input_digest('page:home', with_ui))
        artifacts = {'product_plan': {'pages': []}}
        self.assertEqual(mainline_formal_contract_inputs(artifacts, plan, {}),
                         mainline_formal_contract_inputs(artifacts, with_ui, {}))
