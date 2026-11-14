"""从当前正式事实派生模板扩展接线，结果必须进入原逐文件审阅和 CAS。"""

from __future__ import annotations

import json
from pathlib import Path
import posixpath
from typing import Any
from uuid import uuid4

from app.services.agent_ui_dependency_plan import safe_path
from app.services.agent_ui_design_project import command_environment
from app.services.agent_ui_tool_environment import resolve_node_executable
from app.services.agent_ui_connection_requirements import (
    connection_requirements, connection_blocking_reason, runtime_connection_allowed,
)
from app.services.agent_ui_plain_text_preview import plain_text_preview_allowed
from app.services.runtime_agent_contracts import agent_contract_index, agent_invocation
from app.services.workspace_process_registry import workspace_process_registry

REGISTRY = 'src/extensions/providers.ts'
PROVIDER = 'src/extensions/AgentUiHostProvider.tsx'


def current_host_contract(workspace: Path) -> dict[str, Any]:
    """从当前有效转入凭据取得只读页面接入合同，过期或未接线不得进入生成。"""
    from app.services.agent_ui_dependency_plan import read_object
    from app.services.agent_ui_transfer_records import read_transfer
    pointer = safe_path(workspace, '.devagentstudio/runtime/agent-ui-transfer/current.json')
    if not pointer.is_file():
        raise ValueError('共享 Agent UI 尚未转入正式目标，请先审阅并确认转入。')
    record = read_transfer(workspace, read_object(pointer)['planId'])
    host = record.get('plan', {}).get('hostIntegration', {})
    if record.get('state') != 'verified' or host.get('status') != 'reviewable':
        raise ValueError('共享 Host 接线缺失或转入证据已过期，请重新审阅当前目标。')
    root = record['plan']['target']['sourceRoot']
    return {**host, 'componentModule': '@/' + root.removeprefix('src/') + '/design/SharedApplication',
        'providerPath': 'frontend/' + PROVIDER, 'transferPlanId': record['planId']}


def _provider_code(source_root: str, *, authenticated: bool, page_ids: list[str], agent_id: str,
                   endpoint: str, description: str = '', input_schema: dict[str, Any] | None = None) -> str:
    """使用模板的路由树及身份上下文，不更改 Router/Shell 或复制身份实现。"""
    module = posixpath.relpath(source_root + '/design/SharedApplication', 'src/extensions')
    identity_import = "import { useContext } from 'react'\nimport { IdentityContext } from '@/platform/identity/IdentityContext'\n" if authenticated else ''
    identity = '  const { identity } = useContext(IdentityContext)\n' if authenticated else ''
    session = 'identity?.userId ?? null' if authenticated else "'anonymous'"
    user = 'identity' if authenticated else 'null'
    return (
        "import type { PropsWithChildren, ReactElement } from 'react'\n"
        + identity_import
        + "import { useLocation } from 'react-router-dom'\n"
        "import { APP_PAGE_ROUTE_TREE } from '@/routes/pageRegistry'\n"
        "import type { ResolvedPageRoute } from '@/utils/pageRouteTree'\n"
        + f"import {{ SharedAgentApplication }} from {json.dumps(module, ensure_ascii=False)}\n\n"
        + 'const pageIds: readonly string[] = ' + json.dumps(page_ids, ensure_ascii=False) + '\n\n'
        + '/** 读取路由唯一配置，支持嵌套目录；未知或系统页面只隐藏助手。 */\n'
        'function selectedPage(nodes: readonly ResolvedPageRoute[], pathname: string): string {\n'
        '  for (const node of nodes) {\n'
        "    if (node.kind === 'page' && node.internalPath === pathname && pageIds.includes(node.definition.path)) return node.definition.path\n"
        '    const child = selectedPage(node.children, pathname)\n'
        '    if (child) return child\n'
        '  }\n'
        "  return ''\n"
        '}\n\n'
        '/** 模板 AppProviders 在路由外常驻；身份变化释放旧会话，展示资料不作为授权。 */\n'
        'export function SharedAgentHostProvider({ children }: PropsWithChildren): ReactElement {\n'
        + identity
        + '  const { pathname } = useLocation()\n'
        "  const pageId = selectedPage(APP_PAGE_ROUTE_TREE, pathname.replace(/\\/$/, ''))\n"
        + ('  // 使用已审阅同源业务连接，页面经公开 hook 声明当前记录与输入。\n' if endpoint else
           '  // T27 连接条件尚未闭合；正式 endpoint 记录于转入计划，发送保持禁用。\n')
        + f'  return <SharedAgentApplication agentId={json.dumps(agent_id)} sessionKey={{{session}}} user={{{user}}} pageId={{pageId}} contextDescription={json.dumps(description, ensure_ascii=False)} inputSchema={{{json.dumps(input_schema or {}, ensure_ascii=False)}}} endpoint={json.dumps(endpoint)}>\n'
        '    {children}\n'
        '  </SharedAgentApplication>\n'
        '}\n'
    )


def plan_host_integration(state: dict[str, Any], *, target: Path, source: Path,
                          source_root: str, page_ids: list[str], workspace: Path) -> dict[str, Any]:
    """只读生成 Host 扩展差异；缺公共模板 hook 或真实连接能力明确返回边界。"""
    registry = safe_path(target, REGISTRY)
    if not registry.is_file():
        return {'status': 'unavailable', 'files': {}, 'reason': '目标没有公开 extensionProviders 扩展入口；保留 Router/Shell。'}
    try:
        if not all(safe_path(source, 'src/design/' + name).is_file() for name in (
                'SharedApplication.tsx', 'SharedPage.tsx', 'runtimeResults.ts', 'useRuntimeResult.ts',
                'runtimeContext.tsx', 'runtimeRequest.ts', 'RuntimeBusinessPanel.tsx', 'RuntimeInput.tsx')):
            raise ValueError('已确认设计缺少当前共享 Host 入口，请按原流程重新生成并确认；不能用默认 Host 替代。')
        for name in ('src/providers/AppProviders.tsx', 'src/routes/pageRegistry.ts',
                     'src/utils/pageRouteTree.ts'):
            if not safe_path(target, name).is_file():
                raise ValueError('目标缺少当前模板的路由/Provider 公共入口。')
        app_providers = safe_path(target, 'src/providers/AppProviders.tsx').read_text()
        if '@/extensions/providers' not in app_providers:
            raise ValueError('模板未接入 extensionProviders，不能用未挂载的文件冒充 Host 接线。')
        config = state['application_config']
        authenticated = config.get('auth', {}).get('enable')
        if not isinstance(authenticated, bool):
            raise ValueError('application.json 必须明确提供 auth.enable。')
        if authenticated and (not safe_path(target, 'src/platform/identity/IdentityContext.tsx').is_file()
                              or 'IdentityProvider' not in app_providers):
            raise ValueError('当前模板缺少明确的身份生命周期入口。')
        contracts = agent_contract_index(state['technical_plan'])
        if len(contracts) != 1:
            raise ValueError('共享 Host 仅支持唯一正式 Agent，不能自动选择多个会话 owner。')
        agent_id, contract = next(iter(contracts.items()))
        if not page_ids or len(set(page_ids)) != len(page_ids) or set(page_ids) - set(contract['pageIds']):
            raise ValueError('设计页面不属于唯一 Agent 的正式入口。')
        requirements = connection_requirements(config, contract)
        plain_text = plain_text_preview_allowed(config, contract)
        allowed = runtime_connection_allowed(config, contract)
        node = resolve_node_executable()
        completed = workspace_process_registry.run(
            [str(node), str(Path(__file__).with_name('agent_ui_host_registry.mjs'))],
            workspace=workspace, run_id='agent-ui-host-inspection:' + uuid4().hex,
            cwd=source, env=command_environment(), capture_output=True, text=True, timeout=30, check=False,
            input=json.dumps({'projectRoot': str(source), 'code': registry.read_text(encoding='utf-8'),
                'appProviders': app_providers}))
        if completed.returncode:
            raise ValueError('Host 注册表 TypeScript 分析失败。')
        result = json.loads(completed.stdout)
        if result.get('error'):
            raise ValueError(result['error'])
        return {'status': 'reviewable', 'endpoint': agent_invocation(agent_id)['path'],
            'agentId': agent_id, 'pageIds': page_ids, 'identitySource': 'IdentityContext' if authenticated else 'anonymous-session',
            'connection': ('plain_text' if plain_text else 'business') if allowed else 'blocked_T27', 'connectionRequirements': requirements,
            'reason': ('已接入同源 Runtime；标准 Context、业务结果、工具及交互按正式声明执行，运行情况须联调验收。'
                       if allowed else connection_blocking_reason(requirements)),
            'files': {REGISTRY: result['after'], PROVIDER: _provider_code(source_root, authenticated=authenticated,
                page_ids=page_ids, agent_id=agent_id, endpoint=requirements['endpoint'] if allowed else '',
                description=str(contract['agentSettings']['context'].get('description', '')), input_schema=contract['interactionContract']['inputSchema'])}}
    except (OSError, ValueError, KeyError) as exc:
        return {'status': 'blocked', 'files': {}, 'reason': str(exc)}
