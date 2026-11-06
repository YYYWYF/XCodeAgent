"""Creator 草稿修订必须按真实文件差异隔离页面并保留连续编辑基线。"""

from __future__ import annotations

import json
import http.client
import os
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

from app.services.agent_ui_design_project import AgentUiDesignProject, source_digest, tree_digest
from app.services.agent_ui_design_revision import revise_agent_ui_design
from app.services.agent_ui_design_mock import AgentUiDesignMock, design_mock_options
from app.services.agent_ui_design_preview import AgentUiDesignPreviews
from app.services.agent_ui_tool_host import inspect_agent_ui_tool_host
from tests.test_agent_ui_design_generator import formal_facts


class DesignCreatorRevisionTests(unittest.TestCase):
    """保留真实磁盘复制、范围复核和修订登记，仅隔离 SDK 与昂贵构建命令。"""

    def setUp(self) -> None:
        """准备有两页的当前工程和按调用创建的新草稿。"""
        temporary = TemporaryDirectory(prefix='Creator 修订 ')
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.requirement, self.product = formal_facts()
        self.current = self.prepare(self.workspace / '.devagentstudio/current')
        source = self.current.project_dir / 'src'
        (source / 'agent-ui/application').mkdir(parents=True)
        (source / 'agent-ui/plugins/conversation-thread-list').mkdir(parents=True)
        (source / 'agent-ui/application/project-config.tsx').write_text('export const agentUIProject = {}')
        (source / 'agent-ui/app-ui.json').write_text('{"root":{"type":"row","sizes":["280px","1fr"],"children":[]}}')
        (source / 'agent-ui/plugins/conversation-thread-list/index.tsx').write_text('export const showSearch = true')
        (source / 'design').mkdir()
        (source / 'design/presentation.json').write_text('{}')
        from app.services.agent_ui_design_generator import _host_files
        from app.services.ui_design_agent_surfaces import project_agent_design_pages
        for path, code in _host_files(project_agent_design_pages(self.requirement, self.product)).items():
            target = self.current.project_dir / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code)
        (source / 'pages').mkdir(exist_ok=True)
        (source / 'pages/page-0.tsx').write_text('preserve home business')
        self.inspection = SimpleNamespace(node_executable=Path('/node'))
        self.enterContext(patch('app.services.agent_ui_design_revision.validate_design_project'))
        self.enterContext(patch('app.services.agent_ui_design_revision.prepare_agent_ui_design_project',
            side_effect=lambda _inspection, **options: self.prepare(options['draft_root'])))
        self.enterContext(patch('app.services.agent_ui_design_revision._validate_files', return_value={}))
        self.creator = self.enterContext(patch('app.services.agent_ui_design_revision.run_design_creator'))
        self.lint = self.enterContext(patch('app.services.agent_ui_design_revision._run'))
        self.enterContext(patch('app.services.agent_ui_design_revision.build_agent_ui_design_project',
            side_effect=lambda project, **_options: replace(project, source_digest=source_digest(project.project_dir))))

    def prepare(self, root: Path) -> AgentUiDesignProject:
        """创建本轮独立工程，不链接或重用旧稿依赖和输出。"""
        root = root / 'agent-ui-project'
        target = root / 'tools/agent-ui-consumer'
        (target / 'src').mkdir(parents=True)
        (target / 'src/agent-ui').mkdir()
        (target / 'src/pages').mkdir()
        (target / '.agent-ui').mkdir()
        (target / '.agent-ui/project.json').write_text('{"sourceRoot":"src/agent-ui"}')
        (target / 'src/App.tsx').write_text("import { Agent } from './agent-ui'\n<Agent {...agentProps} />")
        for name in ('package.json', 'pnpm-lock.yaml', '.pnpmfile.cjs', 'tsconfig.json',
                     'eslint.config.mjs', 'vite.config.ts', 'index.html'):
            (target / name).write_text('{}')
        (target / 'dist').mkdir()
        (target / 'dist/index.html').write_text('built')
        return AgentUiDesignProject(self.workspace, root, target, 'environment',
            source_digest(target), tree_digest(target / 'dist'))

    def revise(self, instruction: str, number: int = 1):
        """调用真实修订服务，目标始终为独立页，业务首页必须保留。"""
        return revise_agent_ui_design(self.inspection, current_project=self.current,
            draft_root=self.workspace / f'.devagentstudio/drafts/{number}', pnpm_cli=Path('/pnpm.cjs'),
            requirement_spec=self.requirement, product_plan=self.product, targets=['chat'],
            instruction=instruction, run_id='revision-test')

    def test_layout_and_plugin_requests_both_reach_creator_and_keep_prior_revision(self) -> None:
        """第一轮改 AppUIModel，第二轮改公开组件 props，连续修改及非目标页均不丢失。"""
        def first(_inspection, project: Path, request: str, **_options):
            """模拟 Creator 使用自己的工具修改目标页 JSON 布局。"""
            self.assertIn('侧栏改为 320px', request)
            self.assertTrue(request.startswith('侧栏改为 320px\n'))
            self.assertNotIn('不要修改', request)
            self.assertNotIn('只读', request)
            self.assertIn('protectedPaths', request)
            self.assertEqual(json.loads((project / '.agent-ui/project.json').read_text())['sourceRoot'],
                'src/agent-ui')
            path = project / 'src/agent-ui/app-ui.json'
            path.write_text(path.read_text().replace('280px', '320px'))
            return {'type': 'completed'}

        self.creator.side_effect = first
        old = (self.current.project_dir / 'src/agent-ui/app-ui.json').read_text()
        design = self.revise('侧栏改为 320px')
        self.assertEqual((self.current.project_dir / 'src/agent-ui/app-ui.json').read_text(), old)
        self.assertIn('320px', (design.project.project_dir / 'src/agent-ui/app-ui.json').read_text())
        self.assertEqual((design.project.project_dir / 'src/pages/page-0.tsx').read_text(), 'preserve home business')
        self.assertFalse((design.project.project_dir / 'src/design/agent-pages').exists())
        self.assertEqual(set(design.affected_page_ids), {'home', 'chat'})
        self.current = design.project

        def second(_inspection, project: Path, request: str, **_options):
            """第二轮必须以第一轮结果为基线，不能重新初始化目标页。"""
            self.assertIn('不要显示搜索框', request)
            self.assertIn('320px', (project / 'src/agent-ui/app-ui.json').read_text())
            (project / 'src/agent-ui/plugins/conversation-thread-list/index.tsx').write_text(
                'export const showSearch = false')
            return {'type': 'completed'}

        self.creator.side_effect = second
        final = self.revise('不要显示搜索框', 2)
        self.assertEqual(self.creator.call_count, 2)
        evidence = json.loads((final.project.root / 'generation.json').read_text())
        self.assertEqual(evidence['executor'], 'creator')
        self.assertEqual(evidence['confirmation_status'], 'pending_user_confirmation')
        self.assertNotEqual(design.revision, final.revision)
        self.assertIn('--no-ignore', self.lint.call_args.args[0])
        self.assertIn('src/agent-ui/plugins/conversation-thread-list/index.tsx',
                      self.lint.call_args.args[0])
        self.assertEqual(json.loads((final.project.project_dir / '.agent-ui/project.json').read_text()),
            {'sourceRoot': 'src/agent-ui'})

    def test_creator_cannot_change_other_pages_or_dependencies(self) -> None:
        """即使 Creator 声称完成，修改非目标页也拒绝整个新草稿。"""
        def edit(_inspection, project: Path, _request: str, **_options):
            """模拟执行方越过本页范围。"""
            (project / 'src/pages/page-0.tsx').write_text('changed unrelated home')
            return {'type': 'completed'}

        self.creator.side_effect = edit
        with self.assertRaisesRegex(ValueError, '超出当前设计范围'):
            self.revise('调整独立页布局')
        self.assertEqual((self.current.project_dir / 'src/pages/page-0.tsx').read_text(), 'preserve home business')

    def test_page_props_keep_shared_application_and_only_invalidate_target(self) -> None:
        """欢迎语属于页面展示配置，不改变共享应用，也不撤销其他页确认。"""
        def edit(_inspection, project: Path, request: str, **_options):
            """通过公开 props 的 JSON 投影修改本页欢迎语。"""
            self.assertIn('shared-application', request)
            (project / 'src/design/presentation.json').write_text(
                '{"chat":{"props":{"welcome":{"title":"欢迎咨询"}},"styles":[]}}')
            return {'type': 'completed'}

        self.creator.side_effect = edit
        design = self.revise('独立页欢迎语改成欢迎咨询')
        self.assertEqual(design.affected_page_ids, ('chat',))
        self.assertEqual((design.project.project_dir / 'src/agent-ui/app-ui.json').read_text(),
                         (self.current.project_dir / 'src/agent-ui/app-ui.json').read_text())

    def test_independent_variants_require_explicit_regeneration(self) -> None:
        """已有独立应用不自动压平成共享应用，避免丢弃已接受的定制。"""
        variant = self.current.project_dir / 'src/design/agent-pages/page-1'
        variant.mkdir(parents=True)
        (variant / 'app-ui.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, '独立.*重新生成'):
            self.revise('调整欢迎语')
        self.creator.assert_not_called()

    def test_built_revision_is_admitted_by_real_mock_identity_validation(self) -> None:
        """成功构建必须登记当前页，真实预览准入仍拒绝错误页面、修订和构建。"""
        self.inspection = SimpleNamespace(workspace=self.workspace, node_executable=Path('/node'),
            installation_root=self.workspace / 'tool', creator_version='fixture', package_json_sha256='fixture')

        def edit(_inspection, project: Path, _request: str, **_options):
            """用实际文件修改产生可复核的新修订。"""
            (project / 'src/agent-ui/app-ui.json').write_text('{"root":{"type":"column","children":[]}}')
            return {'type': 'completed'}

        self.creator.side_effect = edit
        design = self.revise('调整布局')
        mock = AgentUiDesignMock(self.inspection, 'chat', design.revision)
        with patch('app.services.agent_ui_design_mock.inspect_agent_ui_tool_host', return_value=self.inspection):
            options = design_mock_options(design.project, mock, Path('/node'), 'preview-regression')
            self.assertEqual(options['binding']['pageId'], 'chat')
            for invalid in (replace(mock, page_id='unknown'), replace(mock, revision='wrong')):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, '身份不匹配'):
                    design_mock_options(design.project, invalid, Path('/node'), 'preview-regression')
            with self.assertRaisesRegex(ValueError, '身份不匹配'):
                design_mock_options(replace(design.project, build_digest='wrong'), mock, Path('/node'), 'preview-regression')
        self.assertEqual(design.pages[1]['pageId'], 'chat')
        self.assertEqual(design.pages[0]['path'], 'src/pages/page-0.tsx')
        self.assertIsNone(design.pages[1]['path'])

    def test_reply_without_file_changes_is_not_a_success(self) -> None:
        """纯文字完成回执不能让新建的页面隔离副本冒充实际修改。"""
        self.creator.return_value = {'type': 'completed'}
        with self.assertRaisesRegex(ValueError, '未产生可核对'):
            self.revise('修改建议卡片')

    @unittest.skipUnless(os.environ.get('AGENT_UI_TEST_NODE'), '需显式提供合规 AGENT_UI_TEST_NODE')
    def test_revision_record_starts_real_official_mock_preview(self) -> None:
        """从修订实际产出的记录启动官方 Mock，不能用手写页面清单绕开生产者遗漏。"""
        self.inspection = inspect_agent_ui_tool_host(workspace=self.workspace,
            installation_root=Path(__file__).resolve().parents[2] / 'tools/agent-ui',
            node_executable=Path(os.environ['AGENT_UI_TEST_NODE']), creator_version='0.0.1')

        def edit(_inspection, project: Path, _request: str, **_options):
            """仅隔离模型与构建；预览准入、官方 Mock 和原生 HTTP 服务使用真实实现。"""
            (project / 'src/agent-ui/app-ui.json').write_text('{"root":{"type":"column","children":[]}}')
            return {'type': 'completed'}

        self.creator.side_effect = edit
        design = self.revise('调整布局')
        previews = AgentUiDesignPreviews()
        self.addCleanup(previews.close_all)
        launch = previews.start(design.project, node_executable=self.inspection.node_executable,
            mock=AgentUiDesignMock(self.inspection, 'chat', design.revision))
        self.assertEqual(launch.mock_evidence['source'], 'official-mock')
        self.assertEqual(launch.mock_evidence['binding']['revision'], design.revision)
        parsed = urlsplit(launch.url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request('GET', parsed.path)
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b'agent-ui-design-preview', response.read())
        body = json.dumps({'threadId': 'revision-thread', 'runId': 'revision-run', 'state': {},
                          'messages': [], 'tools': [], 'context': [], 'forwardedProps': {}})
        connection.request('POST', parsed.path + 'agent', body=body,
            headers={'Content-Type': 'application/json', 'Origin': f'http://{parsed.netloc}'})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertIn(b'RUN_FINISHED', response.read())
        self.assertEqual(json.loads((design.project.root / 'generation.json').read_text())['confirmation_status'],
                         'pending_user_confirmation')

    def test_current_global_css_cannot_overwrite_other_page_customizations(self) -> None:
        """共享展示 JSON 的可写范围严格限定目标页。"""
        def edit(_inspection, project: Path, _request: str, **_options):
            """模拟执行方借共享文件更改其他页。"""
            (project / 'src/design/presentation.json').write_text('{"home":{"props":{},"styles":[]}}')
            return {'type': 'completed'}

        self.creator.side_effect = edit
        with self.assertRaisesRegex(ValueError, '非目标页'):
            self.revise('调整独立页样式')

    def test_registry_cancellation_records_cancelled_without_a_page_callback(self) -> None:
        """进程登记器触发的取消即使没有页面回调，也须记录为取消而非生成失败。"""
        from app.services.ui_design_generator import UiDesignStreamCancelled

        self.creator.side_effect = UiDesignStreamCancelled('cancelled')
        with self.assertRaises(UiDesignStreamCancelled):
            self.revise('调整独立页布局')
        record = self.workspace / '.devagentstudio/drafts/1/agent-ui-project/generation.json'
        self.assertEqual(json.loads(record.read_text())['status'], 'cancelled')
