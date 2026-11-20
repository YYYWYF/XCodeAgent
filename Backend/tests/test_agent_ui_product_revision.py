"""产品修订后，旧设计只作为新草稿基底，不能沿用旧确认或预览。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.services.agent_ui_design_adjustment import _adjust_project
from app.services.agent_ui_design_baseline import revision_project
from app.services.agent_ui_design_project import SEED_FILES, source_digest, tree_digest
from app.services.agent_ui_design_projection import current_project
from app.services.agent_ui_design_workflow import generate_agent_project, group_agent_tasks
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_generation_pool import UiDesignGenerationTask
from app.workspace.spec_documents import load_ui_designs_json, ui_designs_json_path, write_ui_designs_json
from tests.test_agent_ui_design_generator import formal_facts


class ProductRevisionDesignTests(unittest.TestCase):
    """使用真实资产与摘要复现跨产品修订，只隔离模型、构建和进程启动。"""

    def setUp(self) -> None:
        """创建已构建旧设计和同页面集合的新产品，保留旧源码供修订。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        requirement, product = formal_facts()
        self.state = {"workspace": str(self.workspace), "requirement_spec": requirement, "product_plan": product}
        facts = hashlib.sha256(json.dumps({"requirement": requirement, "product": product},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        root = self.workspace / '.devagentstudio/ui-design/drafts' / ('a' * 32) / 'agent-ui-project'
        project = root / 'tools/agent-ui-consumer'
        (project / 'src/pages').mkdir(parents=True)
        (project / 'src/pages/page-0.tsx').write_text('preserved business layout')
        (project / 'dist').mkdir()
        (project / 'dist/index.html').write_text('old build')
        for name in (*SEED_FILES, 'vite.config.ts', 'index.html'):
            (project / name).write_text('{}')
        self.asset = {'kind': 'agent-ui-project', 'draft': 'a' * 32, 'revision': 'old',
            'factsDigest': facts, 'environmentIdentity': 'environment',
            'sourceDigest': source_digest(project), 'buildDigest': tree_digest(project / 'dist'),
            'pageIds': ['home', 'chat']}
        (root / 'generation.json').write_text(json.dumps({**self.asset, 'status': 'built',
            'pages': [{'pageId': key} for key in self.asset['pageIds']]}))
        self.pages = [{'pageId': key, 'status': 'confirmed', 'design_asset': self.asset,
            'asset_confirmation': {'revision': 'old'}} for key in self.asset['pageIds']]
        write_ui_designs_json(self.state, {'pages': self.pages, 'product_plan_sha256': 'new-product'})
        self.pages = load_ui_designs_json(ui_designs_json_path(self.state))['pages']
        self.old_source = project / 'src/pages/page-0.tsx'
        product['pages'][0]['description'] = '新版产品使用异常恢复卡片'
        self.design = SimpleNamespace(project=SimpleNamespace(workspace=self.workspace, project_dir=project,
            identity='environment', source_digest='new-source', build_digest='new-build'),
            revision='new', facts_digest='new-facts', affected_page_ids=('chat',))

    def test_adjustment_accepts_old_facts_only_as_revision_baseline(self) -> None:
        """原预览继续拒绝旧事实；调整覆盖所有依赖页，撤销旧确认且不改旧文件。"""
        with self.assertRaisesRegex(ValueError, '产品事实已变化'):
            current_project(self.state, self.pages[0])
        with patch('app.services.agent_ui_design_adjustment.design_environment',
                return_value=(SimpleNamespace(node_executable=Path('/node')), Path('/pnpm'))), \
                patch('app.services.agent_ui_design_adjustment.revise_agent_ui_design', return_value=self.design) as edit, \
                patch('app.services.agent_ui_design_adjustment.agent_project_page_bindings', return_value={}), \
                patch('app.services.agent_ui_design_adjustment.agent_ui_design_previews.start'):
            result = _adjust_project(self.state, self.pages, ['chat'], '同步新版产品并保留布局', threading.Event())
        self.assertEqual(edit.call_args.kwargs['targets'], ['home', 'chat'])
        self.assertTrue(edit.call_args.kwargs['synchronize_product'])
        self.assertTrue(all(page['status'] == 'ready' and not page.get('asset_confirmation') for page in result))
        self.assertEqual(self.old_source.read_text(), 'preserved business layout')

    def test_regenerate_after_product_revision_uses_existing_design_editor(self) -> None:
        """点击生成也能从旧稿修订，新事实下不能保留未重新审阅的页面确认。"""
        self.pages = [{**page, 'status': 'generation_failed', 'error': '产品事实已变化'} for page in self.pages]
        write_ui_designs_json(self.state, {'pages': self.pages, 'product_plan_sha256': 'new-product'})
        pages = project_agent_design_pages(self.state['requirement_spec'], self.state['product_plan'])
        task = UiDesignGenerationTask(workspace=str(self.workspace), project_id='test',
            project_dir=str(self.workspace), page_id='chat', spec_page=pages[1], page_key='Chat',
            action='regenerate', requirement_spec=self.state['requirement_spec'], product_plan=self.state['product_plan'])
        task = group_agent_tasks([task])[0]
        with patch('app.services.agent_ui_design_workflow.design_environment',
                return_value=(SimpleNamespace(node_executable=Path('/node')), Path('/pnpm'))), \
                patch('app.services.agent_ui_design_revision.revise_agent_ui_design', return_value=self.design) as edit, \
                patch('app.services.agent_ui_design_workflow.generate_agent_ui_design') as fresh, \
                patch('app.services.agent_ui_design_workflow.agent_project_page_bindings', return_value={}), \
                patch('app.services.agent_ui_design_workflow.agent_ui_design_previews'):
            result = generate_agent_project(task, None)
        edit.assert_called_once()
        fresh.assert_not_called()
        self.assertEqual(edit.call_args.kwargs['targets'], ['home', 'chat'])
        self.assertTrue(edit.call_args.kwargs['synchronize_product'])
        self.assertEqual(set(result['affected_page_ids']), {'home', 'chat'})
        self.assertTrue(all(page['status'] == 'ready' and not page.get('asset_confirmation')
                            and not page.get('error') for page in result['project_pages']))

    def test_revision_rejects_modified_source_build_and_generation_record(self) -> None:
        """允许旧产品事实不等于允许篡改、损坏或未构建的旧稿。"""
        root = self.old_source.parents[4]
        paths = [self.old_source, self.old_source.parents[2] / 'dist/index.html', root / 'generation.json']
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b' changed')
                    with self.assertRaises((ValueError, json.JSONDecodeError)):
                        revision_project(self.state, self.pages[0])
                finally:
                    path.write_bytes(original)

    def test_revision_rejects_unconfirmed_documents_and_reordered_pages(self) -> None:
        """新产品必须已确认，旧文件索引不能映射到调整顺序后的另一页。"""
        state = deepcopy(self.state)
        state['product_plan']['confirmation_status'] = 'pending_user_confirmation'
        with self.assertRaisesRegex(ValueError, '联合确认'):
            revision_project(state, self.pages[0])
        state = deepcopy(self.state)
        state['product_plan']['pages'].reverse()
        with self.assertRaisesRegex(ValueError, '页面集合或顺序'):
            revision_project(state, self.pages[0])

    def test_product_change_during_adjustment_preserves_old_manifest_and_preview(self) -> None:
        """生成期间产品指纹改变，即使页面数组相同也不能发布旧候选。"""
        def change_product(*_args, **_options):
            """模拟另一轮产品确认使当前生成输入失效。"""
            write_ui_designs_json(self.state, {'pages': self.pages, 'product_plan_sha256': 'newer-product'})
            return self.design

        with patch('app.services.agent_ui_design_adjustment.design_environment',
                return_value=(SimpleNamespace(node_executable=Path('/node')), Path('/pnpm'))), \
                patch('app.services.agent_ui_design_adjustment.revise_agent_ui_design', side_effect=change_product), \
                patch('app.services.agent_ui_design_adjustment.agent_ui_design_previews.start') as preview:
            with self.assertRaisesRegex(ValueError, '状态已变化'):
                _adjust_project(self.state, self.pages, ['chat'], '同步产品', threading.Event())
        preview.assert_not_called()
        self.assertEqual(load_ui_designs_json(ui_designs_json_path(self.state))['product_plan_sha256'], 'newer-product')


if __name__ == '__main__':
    unittest.main()
