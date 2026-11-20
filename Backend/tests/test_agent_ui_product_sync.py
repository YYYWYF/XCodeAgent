"""正式产品同步不应再次要求用户解释已确认的修改范围。"""

from __future__ import annotations

import unittest
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from app.services.agent_ui_design_generator import AgentUiGeneratedDesign
from app.services.agent_ui_design_revision import revise_agent_ui_design
from tests import test_agent_ui_design_revision as revision_fixtures


def classify_requests(requests: list[str], project: Path) -> list[dict[str, object]]:
    """以固定模型替身调用已安装 Creator 的真实权限分类器，不连接真实模型。"""
    script = Path(__file__).resolve().parents[2] / 'tools/agent-ui/tests/creator-request-routing.mjs'
    result = subprocess.run([shutil.which('node'), str(script), str(project)], input=json.dumps(requests),
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ProductSyncTests(unittest.TestCase):
    """保留真实草稿复制、owner 边界和生成记录，隔离昂贵外部执行。"""

    def setUp(self) -> None:
        """复用已有双页工程夹具，不运行其测试方法。"""
        self.fixture = revision_fixtures.DesignCreatorRevisionTests('test_reply_without_file_changes_is_not_a_success')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def synchronize(self) -> AgentUiGeneratedDesign:
        """模拟全部生成的产品同步输入，进入真实编辑器而不截断其范围分析。"""
        fixture = self.fixture
        return revise_agent_ui_design(fixture.inspection, current_project=fixture.current,
            draft_root=fixture.workspace / '.devagentstudio/drafts/sync', pnpm_cli=Path('/pnpm.cjs'),
            requirement_spec=fixture.requirement, product_plan=fixture.product,
            targets=['home', 'chat'], instruction='同步已确认产品，保留其他布局', run_id='product-sync',
            synchronize_product=True)

    def test_sync_uses_confirmed_facts_without_scope_classification(self) -> None:
        """正文与共享助手由确定性所有权分派；无需用户补充已删除动作清单。"""
        fixture = self.fixture
        fixture.product['pages'][0]['description'] = '已移除独立待办检查按钮'
        fixture.product['agents'][0]['purpose'] = '通过异常恢复卡片检查原线程待办'

        def execute(_inspection, project, _request, **_options):
            """Creator 仍能读取完整事实，但这些事实不再混进权限分类文本。"""
            context = (project / '.agent-ui/design-revision-context.json').read_text()
            self.assertIn('通过异常恢复卡片检查原线程待办', context)
            return {'type': 'completed'}

        fixture.creator.side_effect = execute
        with patch('app.services.agent_ui_design_revision.resolve_design_edit_tasks',
                side_effect=AssertionError('产品同步不应调用范围分类模型')), \
                patch('app.services.agent_ui_design_revision.generate_business_revision',
                    return_value={'src/pages/page-0.tsx': 'updated home business'}) as body:
            design = self.synchronize()
        self.assertIn('updated home', (design.project.project_dir / 'src/pages/page-0.tsx').read_text())
        self.assertIn('preserve home', (fixture.current.project_dir / 'src/pages/page-0.tsx').read_text())
        fixture.creator.assert_called_once()
        self.assertTrue(fixture.creator.call_args.kwargs['allow_verified_unchanged'])
        self.assertIn('已移除独立待办检查按钮', body.call_args.args[3])
        self.assertEqual(set(design.affected_page_ids), {'home', 'chat'})

    def test_sync_still_rejects_creator_writing_business_body(self) -> None:
        """确定性分派不放宽文件所有权；Creator 不得代写正文。"""
        def violate(_inspection, project, _request, **_options):
            """模拟 Creator 越过只读正文边界。"""
            (project / 'src/pages/page-0.tsx').write_text('illegal overwrite')
            return {'type': 'completed'}

        self.fixture.creator.side_effect = violate
        with patch('app.services.agent_ui_design_revision.generate_business_revision',
                return_value={'src/pages/page-0.tsx': 'updated home business'}):
            with self.assertRaisesRegex(ValueError, '超出当前设计范围'):
                self.synchronize()
        self.assertEqual((self.fixture.current.project_dir / 'src/pages/page-0.tsx').read_text(),
                         'preserve home business')

    @unittest.skipUnless(shutil.which('node'), '本机未提供 Node')
    def test_product_read_only_semantics_do_not_reclassify_platform_mutation(self) -> None:
        """产品中的只读业务语义和正文权限限制不能把已授权设计同步降为检查。"""
        self.fixture.product['pages'][0]['description'] = '待办状态只读检查，不要修改订单数据'

        def execute(_inspection, project, request, **_options):
            """验证平台最终下发文本通过真实 SDK 权限门，事实仍完整保留在工程上下文。"""
            routes = classify_requests([request, '只检查当前布局，不要修改任何文件'], project)
            self.assertFalse(routes[0]['readOnly'])
            self.assertGreater(routes[0]['modelCalls'], 0)
            self.assertEqual(routes[1], {'readOnly': True, 'modelCalls': 0})
            context = project / '.agent-ui/design-revision-context.json'
            self.assertIn('/.agent-ui/design-revision-context.json', request)
            content = context.read_text()
            self.assertIn('待办状态只读检查，不要修改订单数据', content)
            self.assertIn('protectedPaths', content)
            self.assertIn('src/pages/page-0.tsx', content)
            return {'type': 'completed'}

        self.fixture.creator.side_effect = execute
        with patch('app.services.agent_ui_design_revision.generate_business_revision',
                return_value={'src/pages/page-0.tsx': 'updated home business'}):
            design = self.synchronize()
        self.assertFalse((design.project.project_dir / '.agent-ui/design-revision-context.json').exists())

    def test_creator_context_is_immutable_and_removed_on_failure(self) -> None:
        """上下文遭改写或 Creator 失败均不能发布新稿，且临时输入必须清理。"""
        contexts: list[Path] = []

        def corrupt(_inspection, project, _request, **_options):
            """模拟执行方修改平台下发事实。"""
            context = project / '.agent-ui/design-revision-context.json'
            contexts.append(context)
            context.write_text('{}')
            return {'type': 'completed'}

        self.fixture.creator.side_effect = corrupt
        with patch('app.services.agent_ui_design_revision.generate_business_revision',
                return_value={'src/pages/page-0.tsx': 'updated home business'}):
            with self.assertRaisesRegex(ValueError, '修改了平台设计上下文'):
                self.synchronize()
        self.assertFalse(contexts[0].exists())
        self.assertEqual((self.fixture.current.project_dir / 'src/pages/page-0.tsx').read_text(),
                         'preserve home business')




    def test_sync_without_source_changes_still_requires_current_bindings(self) -> None:
        """允许已满足要求的 owner 无修改，但旧控件未满足新事实时必须阻断发布。"""
        self.fixture.creator.return_value = {'type': 'completed'}
        with patch('app.services.agent_ui_design_revision.generate_business_revision',
                return_value={'src/pages/page-0.tsx': 'preserve home business'}), \
                patch('app.services.agent_ui_design_revision._validate_files',
                    side_effect=ValueError('旧待办按钮不属于当前产品动作')), \
                patch('app.services.agent_ui_design_revision.build_agent_ui_design_project') as build:
            with self.assertRaisesRegex(ValueError, '旧待办按钮'):
                self.synchronize()
        build.assert_not_called()
        self.assertEqual((self.fixture.current.project_dir / 'src/pages/page-0.tsx').read_text(),
                         'preserve home business')
