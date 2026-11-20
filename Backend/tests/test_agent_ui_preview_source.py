"""来源切换必须显式、有界、先停后启，JSONL 经过真实官方解析后才能保存。"""

import asyncio
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from app.protocols.preview_runtime import build_preview_runtime_stream
from app.services.agent_ui_preview_source import prepare_preview_source, replay_path
from app.services.preview_runtime_state import begin_attempt
from app.services.project_launcher import launch_project_preview, _launch_project_preview


class SourceImportTests(TestCase):
    """使用当前成品与原生 Node 验证脱敏存储，不调用模型或网络。"""

    def test_official_parser_sanitizes_before_persistence_and_rejects_tampering(self) -> None:
        """原始正文和身份不落盘；内容摘要变化后拒绝重启回放。"""
        repository = Path(__file__).resolve().parents[2]
        installation = repository / 'tools/agent-ui'
        metadata = installation / 'node_modules/@za38.03/agent-ui-creator/package.json'
        inspection = SimpleNamespace(installation_root=installation, node_executable=Path(shutil.which('node')),
            creator_version='0.0.1', package_json_sha256=hashlib.sha256(metadata.read_bytes()).hexdigest())
        text = '\n'.join(json.dumps(event) for event in [
            {'type': 'RUN_STARTED', 'threadId': 'original', 'runId': 'original-run'},
            {'type': 'TEXT_MESSAGE_START', 'messageId': 'message', 'role': 'assistant'},
            {'type': 'TEXT_MESSAGE_CONTENT', 'messageId': 'message', 'delta': 'Bearer private-token'},
            {'type': 'TEXT_MESSAGE_END', 'messageId': 'message'},
            {'type': 'RUN_FINISHED', 'threadId': 'original', 'runId': 'original-run'}])
        with TemporaryDirectory() as directory, patch('app.services.agent_ui_design_workflow.design_environment',
                return_value=(inspection, None)):
            root = Path(directory)
            result = prepare_preview_source(root, 'replay', text)
            path = replay_path(root, result['replaySha256'])
            self.assertNotIn('private-token', path.read_text())
            self.assertNotIn('original-run', path.read_text())
            self.assertEqual(result['eventCount'], 5)
            with self.assertRaises(ValueError):
                prepare_preview_source(root, 'replay', '{invalid')
            self.assertEqual(len(list(path.parent.glob('*.jsonl'))), 1)
            path.write_text('tampered')
            with self.assertRaises(ValueError):
                replay_path(root, result['replaySha256'])
            with self.assertRaises(ValueError):
                replay_path(root, '../../outside')

    def test_source_restart_waits_for_all_old_services_and_refuses_stop_failure(self) -> None:
        """受管 stop 真正结束后才启动；任何停止失败均不建立第二条来源连接。"""
        for failed in (False, True):
            with self.subTest(failed=failed), TemporaryDirectory() as directory:
                root = Path(directory)
                order = []

                def stopped(*_args, **_kwargs):
                    """记录旧服务确实经过停止阶段。"""
                    order.append('stop')
                    return {'status': 'failed' if failed else 'stopped'}

                def launched(*_args, **kwargs):
                    """核对新来源只有在全部停止后才进入原启动 owner。"""
                    self.assertEqual(order, ['stop'] * 3)
                    self.assertEqual(kwargs['preview_source'], {'source': 'official-mock'})
                    order.append('launch')
                    return {'status': 'running', 'preview_url': 'http://localhost:3000'}

                with patch('app.services.project_launcher.stop_frontend_project', side_effect=stopped), \
                     patch('app.services.project_launcher.stop_workspace_backend_project', side_effect=stopped), \
                     patch('app.services.project_launcher.stop_runtime', side_effect=stopped), \
                     patch('app.services.project_launcher._launch_project_preview', side_effect=launched) as dispatch:
                    result = launch_project_preview(root, force_restart=True, preview_source={'source': 'official-mock'})
                self.assertEqual(result['status'], 'failed' if failed else 'running')
                self.assertEqual(dispatch.call_count, 0 if failed else 1)

    def test_simulated_sources_never_start_or_require_python_runtime(self) -> None:
        """真实 Runtime 不可用时仍可观察模拟/回放，不把模拟页面连向真实业务。"""
        for source in ('official-mock', 'replay'):
            with TemporaryDirectory() as directory, self.subTest(source=source), \
                    patch('app.services.project_launcher.find_backend_project_root', return_value=None), \
                    patch('app.services.project_launcher.runtime_check_plan', return_value={}), \
                    patch('app.services.project_launcher.launch_runtime') as runtime, \
                    patch('app.services.project_launcher.launch_frontend_project',
                          return_value={'status': 'running', 'preview_url': 'http://localhost:3000'}) as frontend:
                result = _launch_project_preview(directory, force_restart=True, preview_source={'source': source})
                self.assertEqual(result['status'], 'running')
                self.assertEqual(result['agent_runtime']['status'], 'skipped')
                runtime.assert_not_called()
                self.assertEqual(frontend.call_args.kwargs['preview_source'], {'source': source})


class SourceProtocolTests(IsolatedAsyncioTestCase):
    """实际 AG-UI stream 覆盖陈旧启动、非法来源与成功提交的生命周期。"""

    async def test_switch_binds_current_attempt_and_never_dispatches_stale_or_invalid_selection(self) -> None:
        """只有显式来源操作可携带录制；过期 attempt 不停止当前服务。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.devagentstudio').mkdir()
            (root / '.devagentstudio/application.json').write_text('{}')
            attempt = begin_attempt(root)
            for fields, expected in [
                ({'action': 'switch-source', 'source': 'official-mock', 'attemptId': 'stale'}, False),
                ({'action': 'restart', 'source': 'official-mock'}, False),
                ({'action': 'switch-source', 'source': 'unknown', 'attemptId': attempt, 'recording': 'private-token'}, False),
                ({'action': 'switch-source', 'source': 'official-mock', 'attemptId': attempt}, True),
            ]:
                with self.subTest(fields=fields), patch('app.services.project_launcher.launch_project_preview',
                        return_value={'status': 'running'}) as launch:
                    payload = {'threadId': 'source-control', 'runId': 'source-run',
                               'forwardedProps': {'previewRuntime': {'workspace': str(root), **fields}}}
                    events = []
                    async for frame in build_preview_runtime_stream(payload=payload):
                        events.extend(json.loads(line[6:]) for line in frame.splitlines() if line.startswith('data: '))
                    self.assertEqual(events[0]['type'], 'RUN_STARTED')
                    self.assertEqual(events[-1]['type'], 'RUN_FINISHED')
                    self.assertIn('STATE_SNAPSHOT', [item['type'] for item in events])
                    result = events[-1]['result']['previewRuntime']
                    self.assertEqual(result['status'], 'completed' if expected else 'failed')
                    self.assertEqual(launch.call_count, int(expected))
                    self.assertNotIn('private-token', json.dumps(events))
                    if expected:
                        self.assertEqual(launch.call_args.kwargs['preview_source'], {'source': 'official-mock'})
                    await asyncio.sleep(0)
