"""普通正文调整复用既有模型预算，候选通过校验前不能写入工程。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.agent_ui_design_body_revision import generate_business_revision
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from tests.test_agent_ui_design_generator import formal_facts


class BusinessDesignRevisionTests(unittest.TestCase):
    """保留真实源码读取与候选解析，模型和后续构建独立隔离。"""

    def setUp(self) -> None:
        """准备两份页面源码，便于验证模型乱序与失败时的写入边界。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        (self.project / 'src/pages').mkdir(parents=True)
        self.paths = ('src/pages/page-0.tsx', 'src/pages/page-1.tsx')
        for path in self.paths:
            (self.project / path).write_text('accepted current source')
        self.pages = project_agent_design_pages(*formal_facts())
        self.enterContext(patch('app.services.ui_design_generator._create_ui_design_model'))
        self.invoke = self.enterContext(patch('app.services.ui_design_generator._invoke_ui_design_model'))
        self.enterContext(patch('app.services.agent_ui_design_body_revision.Settings.from_env',
                               return_value=SimpleNamespace(ui_design_max_retries=1)))

    def test_candidate_order_follows_page_contract_and_does_not_write(self) -> None:
        """返回对象顺序必须跟随正式页面，单纯生成候选不能修改原稿。"""
        self.invoke.return_value = SimpleNamespace(content=json.dumps({'files': {
            self.paths[1]: 'second candidate', self.paths[0]: 'first candidate'}}))
        validate = Mock()
        result = generate_business_revision(self.project, self.pages, self.paths, '调整两页正文', validate=validate)
        self.assertEqual(list(result), list(self.paths))
        validate.assert_called_once_with(result)
        for path in self.paths:
            self.assertEqual((self.project / path).read_text(), 'accepted current source')

    def test_binding_error_is_fed_back_before_writing(self) -> None:
        """业务绑定校验失败使用既有重试预算反馈给 UI 模型，成功才交回执行方。"""
        self.invoke.side_effect = [SimpleNamespace(content=json.dumps({'files': {self.paths[0]: code}}))
                                   for code in ('invalid binding', 'correct binding')]
        validate = Mock(side_effect=[ValueError('缺少 data-control-id'), None])
        result = generate_business_revision(self.project, self.pages[:1], self.paths[:1], '调整首页标题',
                                            validate=validate)
        self.assertEqual(result[self.paths[0]], 'correct binding')
        self.assertIn('缺少 data-control-id', self.invoke.call_args.args[1])
        self.assertEqual((self.project / self.paths[0]).read_text(), 'accepted current source')

    def test_foreign_file_is_rejected_without_running_validator(self) -> None:
        """正文模型无权返回共享 Agent 源码或依赖，即使候选声称调整已完成。"""
        self.invoke.return_value = SimpleNamespace(content=json.dumps({'files': {
            self.paths[0]: 'candidate', 'src/agent-ui/app-ui.json': '{}'}}))
        validate = Mock()
        with self.assertRaises(ValueError):
            generate_business_revision(self.project, self.pages[:1], self.paths[:1], '调整首页标题',
                                       validate=validate)
        validate.assert_not_called()
        self.assertEqual((self.project / self.paths[0]).read_text(), 'accepted current source')
