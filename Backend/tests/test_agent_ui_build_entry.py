"""Build 入口只消费当前受控转入，不注入旧固定资产。"""

import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tests.agent_ui_build_fixtures import shared_build_target
from app.services.agent_ui_build_contract import current_agent_ui_build_contract


class AgentUiBuildEntryTests(TestCase):
    """从真实正式来源覆盖入口放行、缺失和过期情况。"""

    def test_agent_scope_does_not_require_ui_or_validate_unrelated_page_tasks(self) -> None:
        """独立 Agent 只检查执行切片，不因产品含页面或其他页面旧任务而要求 UI 转入。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / '.devagentstudio/plans/product-plan.json'
            product.parent.mkdir(parents=True)
            product.write_text(json.dumps({'agents': [{'agentId': 'assistant', 'pageActionBindings': [{
                'pageId': 'chat', 'surface': {'type': 'standalone_page', 'enabled': True}}]}],
                'pages': [{'pageId': 'chat'}]}))
            state = {'workspace': directory,
                'build_execution_scope': {'type': 'agent', 'targetId': 'assistant'},
                'build_task_plan': {'unit_graph': {'nodes': ['agent:assistant', 'page:chat'],
                    'edges': [{'from': 'agent:assistant', 'to': 'page:chat', 'type': 'depends_on'}]}},
                'tasks': [{'id': 'agent', 'unit_id': 'agent:assistant', 'owner': 'agent'},
                    {'id': 'page', 'unit_id': 'page:chat', 'owner': 'frontend',
                     'source_refs': {'agent_ui': {'mode': 'shared_host', 'sha256': 'stale'}}}]}
            module = importlib.import_module('app.graph.subgraphs.build')
            with patch.object(module, 'run_build_scheduler', return_value={'status': 'scheduled'}) as scheduler:
                self.assertEqual(module.build(state), {'status': 'scheduled'})
                scheduler.assert_called_once_with(state)
            self.assertFalse((root / 'frontend').exists())

    def test_page_scope_cannot_bypass_transfer_by_omitting_task_reference(self) -> None:
        """页面即使漏带 agent_ui 来源，也必须按正式产品事实检查转入前置。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            product = root / '.devagentstudio/plans/product-plan.json'
            product.parent.mkdir(parents=True)
            product.write_text(json.dumps({'agents': [{'agentId': 'assistant', 'pageActionBindings': [{
                'pageId': 'chat', 'surface': {'type': 'standalone_page', 'enabled': True}}]}],
                'pages': [{'pageId': 'chat'}]}))
            state = {'workspace': directory,
                'build_execution_scope': {'type': 'page', 'targetId': 'chat'},
                'build_task_plan': {'unit_graph': {'nodes': ['page:chat'], 'edges': []}},
                'tasks': [{'id': 'page', 'unit_id': 'page:chat', 'owner': 'frontend'}]}
            module = importlib.import_module('app.graph.subgraphs.build')
            with patch.object(module, 'run_build_scheduler') as scheduler:
                with self.assertRaises((ValueError, OSError)):
                    module.build(state)
                scheduler.assert_not_called()

    def test_shared_transfer_reaches_scheduler_without_writing_assets(self) -> None:
        """有效 T23/T25 来源应放行调度，重复执行不改写任何共享文件。"""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shared_build_target(root)
            module = importlib.import_module('app.graph.subgraphs.build')
            state = {'workspace': str(root),
                'build_task_plan': {'unit_graph': {'nodes': ['page:chat'], 'edges': []}},
                'tasks': [{'id': 'page', 'unit_id': 'page:chat',
                           'source_refs': {'agent_ui': current_agent_ui_build_contract(root, 'chat')}}]}
            before = {path: path.read_bytes() for path in (root / 'frontend/src').rglob('*') if path.is_file()}
            with patch.object(module, 'run_build_scheduler', return_value={'status': 'scheduled'}) as scheduler:
                self.assertEqual(module.build(state), {'status': 'scheduled'})
                scheduler.assert_called_once_with(state)
            self.assertEqual(before, {path: path.read_bytes() for path in before})
            self.assertFalse((root / 'frontend/src/components/AgentConversation').exists())

    def test_missing_transfer_blocks_before_scheduler(self) -> None:
        """任务自带合同不能代替当前磁盘转入来源。"""
        with TemporaryDirectory() as directory:
            module = importlib.import_module('app.graph.subgraphs.build')
            with patch.object(module, 'run_build_scheduler') as scheduler:
                with self.assertRaises(ValueError):
                    module.build({'workspace': directory,
                        'build_task_plan': {'unit_graph': {'nodes': ['page:chat'], 'edges': []}},
                        'tasks': [{'id': 'page', 'unit_id': 'page:chat',
                                   'source_refs': {'agent_ui': {'mode': 'shared_host'}}}]})
                scheduler.assert_not_called()

    def test_normal_page_build_does_not_require_agent_ui(self) -> None:
        """无 Agent Surface 的普通任务保留原调度入口。"""
        module = importlib.import_module('app.graph.subgraphs.build')
        with patch.object(module, 'run_build_scheduler', return_value={}) as scheduler:
            module.build({'tasks': [{'source_refs': {}}]})
            scheduler.assert_called_once()
