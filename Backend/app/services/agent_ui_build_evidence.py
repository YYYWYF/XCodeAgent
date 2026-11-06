"""复核共享构建来源，只允许普通页面在转入后继续开发，不沿用旧构建结果。"""

from pathlib import Path
from typing import Any

from app.services.agent_ui_dependency_plan import digest, formal_snapshot, read_object, safe_path, target_root, target_snapshot
from app.services.agent_ui_design_transfer import require_current_source
from app.services.agent_ui_design_transfer_plan import _formal_state, _public_identity
from app.services.agent_ui_dependency_evidence import installed_evidence
from app.services.agent_ui_transfer_records import transfer_directory
from app.services.agent_ui_connection_requirements import connection_requirements
from app.services.agent_ui_plain_text_preview import plain_text_preview_allowed
from app.services.runtime_agent_contracts import agent_contract_index


def _protected_files(snapshot: dict[str, str], source_root: str) -> dict[str, str]:
    """保护共享源码、BASE、依赖和模板装配；普通页面与本轮 dist 不充当转入凭据。"""
    wiring = {
        'src/main.tsx', 'src/App.tsx', 'src/providers/AppProviders.tsx',
        'src/extensions/providers.ts', 'src/extensions/AgentUiHostProvider.tsx',
        'src/platform/identity/IdentityContext.tsx', 'src/routes/pageRegistry.ts',
        'src/utils/pageRouteTree.ts',
    }
    return {name: value for name, value in snapshot.items()
        if name.startswith((source_root + '/', '.agent-ui/')) or name in wiring
        or not name.startswith('src/')}


def load_build_evidence(workspace: str | Path) -> dict[str, Any]:
    """重读正式确认、实际运行包及逐文件归属，不信任任务自己携带的来源声明。"""
    root, target = target_root(workspace)
    pointer = safe_path(root, '.devagentstudio/runtime/agent-ui-transfer/current.json')
    if not pointer.is_file():
        raise ValueError('共享 Agent UI 尚未转入正式目标。')
    plan_id = read_object(pointer)['planId']
    record_path = safe_path(transfer_directory(root, plan_id), 'record.json')
    record = read_object(record_path)
    plan = record['plan']
    if (record.get('state') != 'verified' or record.get('planId') != plan_id
            or plan.get('workspace') != str(root)
            or digest({key: value for key, value in plan.items() if key != 'planId'}) != plan_id
            or record.get('validation', {}).get('static') != 'passed'):
        raise ValueError('共享转入尚未通过当前静态验证。')
    if formal_snapshot(root) != plan['formal']:
        raise ValueError('正式规划或确认已变化，请重新审阅转入。')
    state = _formal_state(root)
    require_current_source(root, plan)
    host = plan['hostIntegration']
    if host.get('status') != 'reviewable':
        raise ValueError('共享 Host 接线缺失或连接准入合同无效。')
    contracts = agent_contract_index(state['technical_plan'])
    if len(contracts) != 1 or host.get('agentId') not in contracts:
        raise ValueError('共享 Host 必须对应唯一正式 Agent。')
    application = read_object(root / '.devagentstudio/application.json')
    contract = contracts[host['agentId']]
    requirements = connection_requirements(application, contract)
    expected_connection = 'plain_text' if plain_text_preview_allowed(application, contract) else 'blocked_T27'
    if (host.get('connection') != expected_connection or host.get('connectionRequirements') != requirements
            or host.get('endpoint') != requirements['endpoint']):
        raise ValueError('连接条件与当前应用配置、正式能力或公开包接入限制不一致，请重新审阅转入。')
    source_root = plan['target']['sourceRoot']
    safe_path(target, source_root)
    snapshot = target_snapshot(target)
    protected = _protected_files(record['after'], source_root)
    if not protected or _protected_files(snapshot, source_root) != protected:
        raise ValueError('共享源码、ownership、依赖或 Host 接线已变化，原转入证据失效。')
    required = {source_root + '/design/' + name for name in (
        'SharedApplication.tsx', 'SharedHost.tsx', 'SharedPage.tsx', 'presentation.json', 'surfaces.ts')}
    required.update({'src/extensions/providers.ts', 'src/extensions/AgentUiHostProvider.tsx',
                     'src/providers/AppProviders.tsx'})
    if not required <= protected.keys():
        raise ValueError('共享目标缺少完整的已审阅 Host 和展示资产。')
    changes = {item['targetPath']: item['afterSha256'] for item in plan['changes']}
    if (record.get('owned') != changes or any(snapshot.get(name) != value for name, value in changes.items())):
        raise ValueError('逐文件来源或转入 ownership 不匹配。')
    installation = read_object(safe_path(root,
        '.devagentstudio/runtime/agent-ui-dependencies/' + record['installationPlanId'] + '/record.json'))
    installed = installed_evidence(target, installation['plan'])
    if installed != plan['installation']['evidence']:
        raise ValueError('实际运行包或锁身份已变化。')
    pages = [page for page in state['ui_designs']['pages'] if page.get('design_asset')]
    public = _public_identity(target, pages[0]['asset_confirmation'])
    if public != plan['publicApi']:
        raise ValueError('实际公开包、catalog 或类型身份已变化。')
    presentations = read_object(safe_path(target, source_root + '/design/presentation.json'))
    # 前后复核避免把并发编辑的多个时刻拼成同一份来源；不运行安装、模型或构建。
    if (target_snapshot(target) != snapshot or formal_snapshot(root) != plan['formal']
            or read_object(pointer).get('planId') != plan_id or read_object(record_path) != record):
        raise ValueError('构建来源在读取期间变化，请重新读取。')
    return {
        'transferPlanId': plan_id, 'sourceRoot': source_root,
        'host': {**host, 'componentModule': '@/' + source_root.removeprefix('src/') + '/design/SharedApplication',
                 'providerPath': 'frontend/src/extensions/AgentUiHostProvider.tsx'},
        'source': plan['source'], 'publicApi': public, 'installation': installed,
        'ownership': protected, 'presentations': presentations,
        'confirmations': {page['pageId']: digest(page['asset_confirmation']) for page in pages},
        'formalSha256': digest(plan['formal']),
    }
