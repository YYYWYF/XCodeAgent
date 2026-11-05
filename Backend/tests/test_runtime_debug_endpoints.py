"""验证正式调试入口、受管 JSON 请求和状态/响应信息，不连接真实模型或业务库。"""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from app.services.agent_runtime_workbench import DebugSession, _sessions
from app.domain.development_artifacts import DevelopmentArtifacts, DevelopmentArtifactProgress
from app.services.runtime_debug_endpoints import debug_endpoints
from app.services import runtime_debug_request as service
from app.protocols.agent_runtime_workbench import runtime_workbench_capabilities


def agent_contract():
    """构造没有页面依赖的正式能力，目录只消费当前调用入口必需的事实。"""
    return {"agentId": "assistant", "name": "检测助手", "publicEndpoint": False,
            "agentSettings": {"tools": {"enabled": False, "bindings": []}},
            "interactionContract": {"inputSchema": {"type": "object"}, "resultSchema": {"type": "object"}}}


def plan_fixture():
    """包含 JSON、流式与其他 Agent 的接口，验证目录按明确调用关系过滤。"""
    return {"api_contracts": [{"id": "business", "schemas": {"Input": {"type": "object"}}, "endpoints": [
        {"id": mode, "kind": "business", "name": mode, "method": "POST", "path": "/api/" + mode,
         "request_schema_ref": "Input", "parameters": [], "agentCall": {"agentId": "assistant", "responseMode": mode}}
        for mode in ("json", "ag-ui")]}]}


def artifacts_fixture():
    """登记接口自身的完成证据，不借用智能体或页面的开发状态。"""
    progress = DevelopmentArtifactProgress(initialDevelopmentStatus="completed", completedAt="2026-11-05T00:00:00Z",
                                           completedRunId="run", completedThreadId="thread")
    return DevelopmentArtifacts(endpoints={"business": {mode: progress for mode in ("json", "ag-ui")},
                                          "tools": {"balance": progress}})


class RuntimeDebugEndpointTests(TestCase):
    """目录不引用页面，业务接口保留各自协议和输入 Schema。"""

    def test_catalog_has_internal_debug_and_only_matching_business_endpoints(self):
        """无公开入口也可独立调试；不混入工具接口或其他智能体接口。"""
        plan = plan_fixture()
        plan["api_contracts"][0]["endpoints"] += [
            {"id": "tool", "kind": "business"},
            {"id": "other", "kind": "business", "agentCall": {"agentId": "other"}}]
        result = debug_endpoints(plan, agent_contract(), artifacts_fixture())
        self.assertEqual([item["responseMode"] for item in result], ["json", "ag-ui", "ag-ui"])
        self.assertEqual(result[-1]["path"], "/agents/assistant/run")
        self.assertIn("内部能力", result[-1]["label"])
        self.assertEqual(result[0]["requestSchema"], {"type": "object"})
        self.assertEqual([item['relation'] for item in result], ['agent_call', 'agent_call', 'agent_entry'])

    def test_catalog_includes_tool_endpoints_and_deduplicates_two_way_relations(self):
        """同一 Agent 可对应多个不同协议的接口，工具接口按精确引用纳入而不是按页面过滤。"""
        agent = agent_contract()
        agent['agentSettings']['tools'] = {'enabled': True, 'bindings': [
            {'toolId': 'balance', 'apiContractId': 'tools', 'endpointId': 'balance'},
            {'toolId': 'shared', 'apiContractId': 'business', 'endpointId': 'json'}]}
        plan = plan_fixture()
        plan['api_contracts'].append({'id': 'tools', 'schemas': {}, 'endpoints': [
            {'id': 'balance', 'kind': 'business', 'method': 'POST', 'path': '/api/balance', 'summary': '余额查询'}]})
        result = debug_endpoints(plan, agent, artifacts_fixture())
        self.assertEqual(len(result), 4)
        self.assertEqual([item['relation'] for item in result], ['agent_call', 'agent_call', 'tool', 'agent_entry'])
        self.assertEqual(result[2]['responseMode'], 'json')
        self.assertEqual(result[2]['path'], '/api/balance')

    def test_catalog_excludes_unfinished_changed_and_unknown_business_endpoints(self):
        """缺记录、开发中、变更及状态未知均不能借用智能体完成事实开放调试。"""
        for status, change in [('pending', 'unchanged'), ('in_progress', 'unchanged'),
                               ('completed', 'changed'), ('completed', 'unavailable')]:
            with self.subTest(status=status, change=change):
                artifacts = artifacts_fixture()
                artifacts.endpoints['business']['json'] = artifacts.endpoints['business']['json'].model_copy(
                    update={'initial_development_status': status, 'change_status': change})
                result = debug_endpoints(plan_fixture(), agent_contract(), artifacts)
                self.assertEqual([item['endpointId'] for item in result[:-1]], ['ag-ui'])
        for artifacts in (None, DevelopmentArtifacts(), DevelopmentArtifacts(endpoints={'other': artifacts_fixture().endpoints['business']})):
            self.assertEqual([item['relation'] for item in debug_endpoints(plan_fixture(), agent_contract(), artifacts)], ['agent_entry'])

    def test_catalog_excludes_unfinished_tool_endpoint(self):
        """工具接口与调用入口共用自身开发状态门禁。"""
        agent = agent_contract()
        agent['agentSettings']['tools']['bindings'] = [{'apiContractId': 'business', 'endpointId': 'json'}]
        plan = plan_fixture()
        del plan['api_contracts'][0]['endpoints'][0]['agentCall']
        artifacts = artifacts_fixture()
        del artifacts.endpoints['business']['json']
        self.assertNotIn('json', [item['endpointId'] for item in debug_endpoints(plan, agent, artifacts)])

    def test_request_boundary_rejects_url_and_credentials(self):
        """在请求边界拒绝任意目标和身份覆盖，防止调试器变成通用 HTTP 代理。"""
        base = dict(workspaceRoot="/tmp/debug-test", agentId="assistant", launchId="launch",
                    apiContractId="business", endpointId="json")
        self.assertIn('request_debug', runtime_workbench_capabilities()['actions'])
        for extra in ({"url": "http://other"}, {"headers": {"Authorization": "other"}}, {"headers": {"X-Test": "a\r\nb"}}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                service.DebugRequest(**base, **extra)

    def test_json_response_preserves_failure_and_keeps_token_private(self):
        """HTTP 500 原样交付一次，不跟随重定向、不重试、不把会话凭据展示给用户。"""
        root = str(Path('/tmp/debug-test').resolve())
        session = DebugSession('owner', 'launch', 'http://127.0.0.1:32100', 9999999999,
                               False, ['assistant'], ['assistant'], 'source', token='private-token')
        request = service.DebugRequest(workspaceRoot=root, agentId='assistant', launchId='launch',
                                       apiContractId='business', endpointId='json', body={'value': 1}, headers={'X-Demo': 'yes'})
        with patch.dict(_sessions, {root: session}, clear=True), patch.object(service, 'backend_launch_lock', return_value=nullcontext()), patch.object(
            service, 'runtime_source_fingerprint', return_value='source'
        ), patch.object(service, 'runtime_check_plan', return_value=plan_fixture()), patch.object(
            service, 'agent_contract_index', return_value={'assistant': agent_contract()}
        ), patch.object(service, 'load_application_lifecycle',
                        return_value=SimpleNamespace(development_artifacts=artifacts_fixture())) as lifecycle_mock, \
                patch.object(service.httpx, 'Client') as client:
            transport = client.return_value.__enter__.return_value
            transport.stream.return_value.__enter__.return_value = httpx.Response(500,
                json={'error': 'business failed'}, headers={'Set-Cookie': 'private-cookie'})
            result = service.execute_debug_request(request, thread_id='owner')
            self.assertEqual(result['statusCode'], 500)
            self.assertIn('business failed', result['body'])
            self.assertNotIn('set-cookie', result['headers'])
            self.assertNotIn('private-token', str(result))
            self.assertEqual(transport.stream.call_count, 1)
            self.assertEqual(transport.stream.call_args.args, ('POST', 'http://127.0.0.1:32100/api/json'))
            self.assertEqual(transport.stream.call_args.kwargs['headers']['Authorization'], 'Bearer private-token')
            self.assertFalse(client.call_args.kwargs['follow_redirects'])
            with self.assertRaisesRegex(ValueError, '不属于当前窗口'):
                service.execute_debug_request(request, thread_id='other')
            self.assertEqual(transport.stream.call_count, 1)
            with self.assertRaisesRegex(ValueError, '正式 JSON'):
                service.execute_debug_request(request.model_copy(update={'endpointId': 'ag-ui'}), thread_id='owner')
            self.assertEqual(transport.stream.call_count, 1)
            # 前端保留旧目录也不能继续发送已失去完成资格的接口。
            lifecycle_mock.return_value.development_artifacts = DevelopmentArtifacts()
            with self.assertRaisesRegex(ValueError, '已完成开发'):
                service.execute_debug_request(request, thread_id='owner')
            self.assertEqual(transport.stream.call_count, 1)
