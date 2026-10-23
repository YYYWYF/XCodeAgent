"""显式运行的真实模板联调；仅使用合成凭据，报告不含配置值。"""
from __future__ import annotations

import asyncio
import json
import tempfile
import httpx
from uuid import uuid4
from pathlib import Path

from app.config import Settings
from app.services.application_config.repository import ApplicationConfigRepository
from app.services.application_config.schema import normalize_application_configuration
from app.services.application_config_orchestrator import ApplicationConfigOrchestrator
from app.services.template_reconcile.desired import requested_config_from_application_config
from app.services.template_reconcile.state_v3 import load_template_state_v3
from app.services.workspace_bootstrap.materializer import WorkspaceMaterializer
from app.services.workspace_bootstrap.models import ArchiveLimits
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient
from app.services.workspace_bootstrap.template_package import validate_template_package


async def apply_via_ag_ui(root: Path, operation_id: str, revision: int, changes: dict, *, resume: bool = False) -> dict:
    """通过实际后端 HTTP AG-UI 端点验收事件生命周期，而非绕过协议直调服务。"""
    payload = {'threadId': f'config-e2e:{root.name}', 'runId': str(uuid4()), 'messages': [],
               'forwardedProps': {'applicationConfig': {'action': 'apply', 'workspaceRoot': str(root),
                    'operationId': operation_id, 'expectedConfigRevision': revision, 'changes': changes}}}
    if resume:
        payload['forwardedProps']['applicationConfig'] = {'action': 'resume', 'workspaceRoot': str(root), 'operationId': operation_id}
    async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
        response = await client.post('http://127.0.0.1:8000/application-config/run', json=payload, headers={'Accept': 'text/event-stream'})
        response.raise_for_status()
    # 仅测试夹具解码 SSE 以断言协议，不用于任何产品调用路径。
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    kinds = {event['type'] for event in events}
    assert {'RUN_STARTED', 'TEXT_MESSAGE_START', 'TEXT_MESSAGE_CONTENT', 'TEXT_MESSAGE_END', 'CUSTOM', 'STATE_SNAPSHOT', 'RUN_FINISHED'} <= kinds
    result = next(event['value'] for event in events if event['type'] == 'CUSTOM' and event.get('name') == 'application-config')
    if result['status'] != 'completed':
        raise RuntimeError('AG-UI 配置操作失败；请读取对应的脱敏 operation 记录。')
    return result['operation']


async def main() -> None:
    """在真实 Engine 上生成全新 Base，验证顺序与同时安装两条链路。"""
    settings = Settings.from_env()
    report = []
    for chain in ('sequential', 'combined'):
        root = Path(tempfile.mkdtemp(prefix=f'config-stability-{chain}-')).resolve()
        (root / '.devagentstudio').mkdir()
        config = normalize_application_configuration({
            'schemaVersion': 6, 'configRevision': 1,
            'appName': 'Config Stability Test', 'appIcon': 'AppstoreOutlined', 'theme': {'primaryColor': '#9254de'},
            'datasource': {'type': 'database'},
            'auth': {'enable': False, 'yht': dict.fromkeys(('clientId', 'privateKey', 'publicKey', 'centerPublicKey'), 'synthetic-test-value')},
            'authorization': {'enabled': False, 'initialAdministratorSubjects': []},
        })
        repository = ApplicationConfigRepository(root)
        repository.save(config)
        client = TemplateEngineClient(base_url=settings.template_engine_base_url, connect_timeout=5, read_timeout=60, max_package_bytes=settings.template_package_max_bytes)
        download = await client.generate(requested_config_from_application_config(config))
        try:
            validated = validate_template_package(download.temporary_path, ArchiveLimits(settings.template_package_max_bytes, settings.template_package_max_files, settings.template_package_max_extracted_bytes))
            WorkspaceMaterializer().materialize(workspace=root, archive_path=download.temporary_path, template_state=validated.template_state)
        finally:
            download.temporary_path.unlink(missing_ok=True)
        service = ApplicationConfigOrchestrator(settings)
        steps = []
        targets = ({'auth.enable': True}, {'authorization.enabled': True, 'authorization.initialAdministratorSubjects': ['synthetic-admin'], 'authorization.api.baseUrl': '', 'authorization.api.viewId': ''}) if chain == 'sequential' else ({'auth.enable': True, 'authorization.enabled': True, 'authorization.initialAdministratorSubjects': ['synthetic-admin'], 'authorization.api.baseUrl': '', 'authorization.api.viewId': ''},)
        for index, changes in enumerate(targets):
            revision = repository.load()['configRevision']
            try:
                result = await apply_via_ag_ui(root, f'real-{chain}-{index}', revision, changes)
                replay = await apply_via_ag_ui(root, f'real-{chain}-{index}', revision, changes)
                assert replay['configRevision'] == result['configRevision']
                resumed = await apply_via_ag_ui(root, f'real-{chain}-{index}', revision, {}, resume=True)
                assert resumed['configRevision'] == result['configRevision']
                steps.append({'status': result['status'], 'stage': result.get('progressStage'), 'configRevision': result.get('configRevision'), 'stateSequence': load_template_state_v3(root).stateSequence})
            except Exception as exc:
                operation = service.get_operation(root, f'real-{chain}-{index}')
                steps.append({'status': 'FAILED', 'failureStage': operation.get('failureStage'), 'failureCode': operation.get('failureCode'), 'failureTarget': operation.get('failureTarget'), 'stateSequence': load_template_state_v3(root).stateSequence})
                break
        parameter_result = None
        if len(steps) == len(targets) and all(step['status'] == 'APPLIED' for step in steps):
            profile = root / 'backend/src/main/resources/application-dev.yml'
            main_yaml = root / 'backend/src/main/resources/application.yml'
            unmanaged = b'\n# synthetic user block\nother:\n  client-id: keep-user-client\n  base-url: keep-user-url\n'
            profile.write_bytes(profile.read_bytes() + unmanaged)
            main_before = main_yaml.read_bytes()
            sequence = load_template_state_v3(root).stateSequence
            result = await apply_via_ag_ui(root, f'real-parameters-{chain}', repository.load()['configRevision'], {
                'theme.primaryColor': '#722ed1', 'auth.yht.clientId': 'synthetic-updated-client',
                'auth.yht.centerUri': 'https://example.invalid/center', 'authorization.api.baseUrl': 'https://example.invalid/api'})
            assert result['status'] == 'APPLIED'
            assert profile.read_bytes().endswith(unmanaged)
            assert main_yaml.read_bytes() == main_before
            assert load_template_state_v3(root).stateSequence == sequence
            parameter_result = {'status': result['status'], 'stateSequenceUnchanged': True, 'unmanagedPreserved': True}
        report.append({'chain': chain, 'workspace': str(root), 'templateRevision': load_template_state_v3(root).templateRevision, 'steps': steps, 'parameterUpdate': parameter_result})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if any(len(item['steps']) != (2 if item['chain'] == 'sequential' else 1) or any(step['status'] != 'APPLIED' for step in item['steps']) for item in report):
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
