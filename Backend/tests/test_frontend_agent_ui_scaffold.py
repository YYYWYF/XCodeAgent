"""共享 Build 准入从磁盘复核，不再复制固定资产。"""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from app.services.agent_ui_build_contract import current_agent_ui_build_contract
from app.services.frontend_agent_ui_scaffold import verify_frontend_agent_ui_build
from tests.agent_ui_build_fixtures import shared_build_target


class FrontendAgentUiScaffoldTests(TestCase):
    """覆盖旧来源、共享定制和遗漏任务来源的准入边界。"""

    def test_current_shared_contract_is_read_only_and_repeatable(self) -> None:
        """已确认共享合同可重复进入 Build，不重建 BASE 或覆写定制。"""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shared_build_target(root)
            contract = current_agent_ui_build_contract(root, 'chat')
            task = {'source_refs': {'agent_ui': contract}}
            for _ in range(2):
                verify_frontend_agent_ui_build(root, [task])
            self.assertEqual(current_agent_ui_build_contract(root, 'chat'), contract)
            self.assertFalse((root / 'frontend/src/components/AgentConversation').exists())

    def test_stale_task_and_modified_shared_source_are_rejected(self) -> None:
        """任务旧摘要及转入后的源码变化均在调度前阻断。"""
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            shared_build_target(root)
            contract = current_agent_ui_build_contract(root, 'chat')
            with self.assertRaisesRegex(ValueError, '任务合同已过期'):
                verify_frontend_agent_ui_build(root, [{'source_refs': {'agent_ui': {**contract, 'sha256': 'stale'}}}])
            (root / 'frontend/src/正式助手/design/SharedApplication.tsx').write_text('export {}')
            with self.assertRaises(ValueError):
                verify_frontend_agent_ui_build(root, [])

    def test_old_fixed_evidence_cannot_substitute_for_transfer(self) -> None:
        """旧组件字节和确认状态不能替代当前受控交接，失败保留用户文件。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'frontend/src/apis/agentConversationRuntime.ts'
            target.parent.mkdir(parents=True)
            target.write_text('// user-owned legacy output')
            plan = root / '.devagentstudio/plans/product-plan.json'
            plan.parent.mkdir(parents=True)
            plan.write_text(json.dumps({'agents': [{'agentId': 'assistant', 'pageActionBindings': [{
                'pageId': 'chat', 'surface': {'type': 'standalone_page', 'enabled': True}}]}],
                'pages': [{'pageId': 'chat'}]}))
            with self.assertRaises((ValueError, OSError)):
                verify_frontend_agent_ui_build(root, [])
            self.assertEqual(target.read_text(), '// user-owned legacy output')
