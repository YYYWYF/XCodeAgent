"""T29 独立 AG-UI 的登记与失败均闭合生命周期，不暴露原始错误输入。"""

import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.protocols.runtime_acceptance import build_acceptance_stream


class AcceptanceProtocolTests(IsolatedAsyncioTestCase):
    """只调用实际动作流，不启动 Electron、模型或数据库。"""

    async def frames(self, action: dict) -> list[dict]:
        """读取测试事件帧；产品传输继续由官方 encoder/SDK 拥有。"""
        return [json.loads(chunk.removeprefix('data:').strip()) async for chunk in build_acceptance_stream(
            payload={'threadId': 'thread', 'runId': 'run', 'forwardedProps': {'runtimeAcceptance': action}}, accept=None)]

    async def test_get_has_message_result_snapshot_and_terminal(self) -> None:
        """即使没有共享 Agent，读取也发出完整生命周期而不是普通 JSON。"""
        with patch('app.protocols.runtime_acceptance.acceptance_matrix', return_value={'cases': [], 'ready': True}):
            events = await self.frames({'action': 'get', 'workspace': '/target'})
        self.assertEqual(events[0]['type'], 'RUN_STARTED')
        self.assertEqual(events[-1]['type'], 'RUN_FINISHED')
        self.assertIn('STATE_SNAPSHOT', [event['type'] for event in events])
        self.assertIn('TEXT_MESSAGE_CONTENT', [event['type'] for event in events])
        self.assertEqual(events[-1]['result']['runtimeAcceptance']['matrix']['cases'], [])

    async def test_record_binds_server_envelope_without_confirming_version(self) -> None:
        """动作只记录核验，不读取用户提供的运行身份或触发真实调用。"""
        report = {'caseId': 'a' * 64, 'fingerprint': 'b' * 64, 'source': 'real', 'outcome': 'unknown', 'checks': []}
        with patch('app.protocols.runtime_acceptance.record_acceptance', return_value={'cases': [], 'ready': False}) as record:
            events = await self.frames({'action': 'record', 'workspace': '/target', 'report': report})
        record.assert_called_once_with('/target', report, thread_id='thread', run_id='run')
        self.assertEqual(events[-1]['result']['runtimeAcceptance']['status'], 'completed')
        self.assertFalse(events[-1]['result']['runtimeAcceptance']['matrix']['ready'])

    async def test_bad_or_secret_input_is_closed_and_redacted(self) -> None:
        """非法原文不进入助手消息、业务错误、快照或终态。"""
        events = await self.frames({'action': 'record', 'workspace': '/target', 'report': {'password': 'must-not-leak'}})
        self.assertNotIn('must-not-leak', str(events))
        self.assertEqual(events[0]['type'], 'RUN_STARTED')
        self.assertEqual(events[-1]['type'], 'RUN_FINISHED')
        self.assertEqual(events[-1]['result']['runtimeAcceptance']['status'], 'failed')

    async def test_business_failure_does_not_become_success(self) -> None:
        """业务拒绝闭合官方流，客户端仍须读取结构化失败。"""
        with patch('app.protocols.runtime_acceptance.acceptance_matrix', side_effect=ValueError('当前证据不可核验')):
            events = await self.frames({'action': 'get', 'workspace': '/target'})
        self.assertEqual(events[-1]['result']['runtimeAcceptance']['status'], 'failed')

    async def test_desktop_bridge_contract_remains_independent(self) -> None:
        """扩展审阅动作不会改变原桌面领取入口或签发模型任务。"""
        with patch('app.protocols.runtime_acceptance.claim_desktop_acceptance', return_value={'job': None}) as claim:
            events = await self.frames({'action': 'claim', 'verifierSha256': 'a' * 64})
        claim.assert_called_once_with('a' * 64)
        self.assertIsNone(events[-1]['result']['runtimeAcceptance']['job'])
