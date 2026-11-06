"""联合确认的需求文档必须在确认前可读，并继续使用引用化 AG-UI 展示合同。"""

import tempfile
import unittest
from pathlib import Path

from app.protocols.workflow.projection import _workflow_confirmation_artifact, _workflow_visual_payload
from app.workspace.content_artifacts import read_artifact


class RequirementDocumentProjectionTests(unittest.TestCase):
    """隔离 Markdown 产物验证当前联合确认阶段与引用读取，不调用模型。"""

    def setUp(self) -> None:
        """准备待联合确认的需求草稿和产品规划草稿。"""
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.directory.name)
        self.spec = self.workspace / '.devagentstudio/drafts/specs/requirement-spec.md'
        self.plan = self.workspace / '.devagentstudio/drafts/plans/product-plan.md'
        for path, content in [(self.spec, '# 需求文档\n\n记录个人工作。'), (self.plan, '# 产品规划\n\n工作记录列表与详情。')]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        self.state = {
            'workspace': str(self.workspace), 'phase': 'requirement_document',
            'status': 'requires_user_input', 'requirements_confirmed': False,
            'requirement_spec_path': str(self.spec), 'product_plan_path': str(self.plan),
            'requirement_spec': {'pages': [{'pageId': 'work_logs'}]},
            'product_plan': {'pages': [{'pageId': 'work_logs', 'actions': []}]},
            'clarification': {'mode': 'requirement_document_confirmation', 'status': 'requires_user_input'},
        }

    def tearDown(self) -> None:
        """回收隔离的测试文档与内容地址文件。"""
        self.directory.cleanup()

    def test_joint_gate_exposes_both_markdown_drafts_before_confirmation(self) -> None:
        """完整需求文档合并两份草稿，产品规划标题降级且不改变确认状态。"""
        artifact = _workflow_confirmation_artifact(self.state)
        self.assertIsNotNone(artifact)
        self.assertEqual(artifact['id'], 'product_plan')
        self.assertIn('记录个人工作。', artifact['content'])
        self.assertIn('## 产品规划', artifact['content'])
        self.assertIn('工作记录列表与详情。', artifact['content'])
        self.assertFalse(self.state['requirements_confirmed'])

    def test_public_view_preserves_document_references_and_readable_markdown(self) -> None:
        """标准公开投影保留结构化引用与合并 Markdown 引用，正文不重复嵌入。"""
        payload = _workflow_visual_payload(run_id='run', thread_id='thread',
            summary={'phase': 'requirement_document', 'status': 'requires_user_input'},
            events=[], result=self.state, workspace=str(self.workspace))
        refs = payload['state']['artifactRefs']
        self.assertNotIn('requirement_spec', payload['state'])
        self.assertNotIn('content', payload['confirmationArtifact'])
        self.assertEqual(read_artifact(self.workspace, refs['requirement_spec']), self.state['requirement_spec'])
        self.assertEqual(read_artifact(self.workspace, refs['product_plan']), self.state['product_plan'])
        content = read_artifact(self.workspace, payload['confirmationArtifact']['contentRef'])
        self.assertIn('记录个人工作。', content)
        self.assertIn('工作记录列表与详情。', content)

    def test_only_current_joint_gate_and_draft_path_can_publish_document(self) -> None:
        """其他阶段、完成态及正式路径均不能冒充当前待确认草稿。"""
        for update in [
            {'phase': 'requirements'}, {'phase': 'ui_confirmation'}, {'status': 'completed'},
            {'clarification': {'mode': 'requirement_document_confirmation', 'status': 'clear'}},
            {'product_plan_path': str(self.workspace / '.devagentstudio/plans/product-plan.md')},
        ]:
            with self.subTest(update=update):
                self.assertIsNone(_workflow_confirmation_artifact({**self.state, **update}))
