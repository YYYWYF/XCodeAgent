"""设计调整必须按当前源码所有权分流，并拒绝越界或不完整的模型范围。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from app.services.agent_ui_design_edit_scope import _parse_tasks, resolve_design_edit_tasks
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_generator import UiDesignStreamCancelled
from tests.test_agent_ui_design_generator import formal_facts


class DesignEditScopeTests(unittest.TestCase):
    """真实读取源码上下文，仅隔离无副作用的模型边界，不依靠关键词伪造分流器。"""

    def setUp(self) -> None:
        """准备含浮窗首页和纯会话页的最小所有权事实。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        (self.project / 'src/pages').mkdir(parents=True)
        (self.project / 'src/pages/page-0.tsx').write_text('export default function Page() { return <h1>首页标题</h1> }')
        (self.project / 'src/agent-ui').mkdir()
        (self.project / 'src/agent-ui/app-ui.json').write_text('{"root": {"type": "column"}}')
        (self.project / 'src/design').mkdir()
        (self.project / 'src/design/presentation.json').write_text('{}')
        self.pages = project_agent_design_pages(*formal_facts())

    def test_home_body_with_agent_exclusion_uses_current_source_context(self) -> None:
        """含“浮窗”且明确保留它的请求，模型上下文仍明确区分实际正文 owner。"""
        response = SimpleNamespace(content=json.dumps({'tasks': [
            {'owner': 'business_page', 'pageIds': ['home'], 'instruction': '优化首页标题，保留浮窗'}],
            'clarification': ''}))
        with patch('app.services.ui_design_generator._create_ui_design_model'), \
                patch('app.services.ui_design_generator._invoke_ui_design_model', return_value=response) as invoke:
            tasks = resolve_design_edit_tasks(self.project, self.pages, ['home'], '优化首页标题，保留浮窗')
        self.assertEqual(tasks[0].owner, 'business_page')
        prompt = invoke.call_args.args[1]
        for expected in ('src/pages/page-0.tsx', '首页标题', 'src/agent-ui/app-ui.json', 'NOT an Agent UI edit'):
            self.assertIn(expected, prompt)

    def test_pure_conversation_page_has_only_agent_owner(self) -> None:
        """纯会话页没有业务正文，不为不存在的 owner 调用分流模型。"""
        with patch('app.services.ui_design_generator._invoke_ui_design_model') as invoke:
            tasks = resolve_design_edit_tasks(self.project, self.pages, ['chat'], '调整消息间距')
        invoke.assert_not_called()
        self.assertEqual(tasks[0].owner, 'agent_ui')

    def test_mixed_tasks_are_ordered_without_merging_instructions(self) -> None:
        """普通正文先调整，两个执行方仅收到各自明确的子任务。"""
        tasks = _parse_tasks(json.dumps({'tasks': [
            {'owner': 'agent_ui', 'pageIds': ['home'], 'instruction': '调整聊天消息间距'},
            {'owner': 'business_page', 'pageIds': ['home'], 'instruction': '修改首页标题'}],
            'clarification': ''}), ['home'], {'home'})
        self.assertEqual([task.owner for task in tasks], ['business_page', 'agent_ui'])
        self.assertEqual([task.instruction for task in tasks], ['修改首页标题', '调整聊天消息间距'])

    def test_uncertain_or_invalid_scope_never_falls_back_to_creator(self) -> None:
        """澄清、重复、缺页、越界与未知执行方均停止，不自动升级到 Creator。"""
        valid = {'owner': 'business_page', 'pageIds': ['home'], 'instruction': '修改标题'}
        invalid = [
            {'tasks': [], 'clarification': '请明确是首页标题还是助手欢迎语？'},
            {'tasks': [], 'clarification': ''},
            {'tasks': [valid, valid], 'clarification': ''},
            {'tasks': [{**valid, 'pageIds': ['other']}], 'clarification': ''},
            {'tasks': [{**valid, 'owner': 'unknown'}], 'clarification': ''},
            {'tasks': [{**valid, 'pageIds': ['chat']}], 'clarification': ''},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                _parse_tasks(json.dumps(value), ['home', 'chat'], {'home'})
        with self.assertRaises(ValueError):
            _parse_tasks('{"tasks":[],"tasks":[],"clarification":""}', ['home'], {'home'})
        with self.assertRaises(ValueError):
            _parse_tasks(json.dumps({'tasks': [valid], 'clarification': ''}), ['home', 'chat'], {'home'})

    def test_cancellation_stops_before_model_call(self) -> None:
        """取消不会留下额外模型调用或继续委托。"""
        with patch('app.services.ui_design_generator._invoke_ui_design_model') as invoke:
            with self.assertRaises(UiDesignStreamCancelled):
                resolve_design_edit_tasks(self.project, self.pages, ['home'], '优化标题', should_cancel=lambda: True)
        invoke.assert_not_called()
