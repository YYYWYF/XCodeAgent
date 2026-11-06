"""当前共享 Agent UI 的只读连接诊断；不发送业务请求，不改变正式转入证据。"""

from collections.abc import Mapping
import hashlib
from pathlib import Path
from typing import Any, Literal, TypedDict

from app.services.agent_runtime_health import runtime_health
from app.services.agent_ui_connection_requirements import ConnectionCheck, connection_requirements
from app.services.database_credentials import DatabaseCredentialError, load_application_json
from app.services.python_runtime_checks import runtime_check_plan
from app.services.runtime_agent_contracts import agent_contract_index
from app.services.agent_ui_plain_text_preview import plain_text_preview_allowed


class ProxyHealth(TypedDict):
    """只表示本次 GET 健康结果，不代表浏览器 Cookie 或业务调用验证。"""

    status: Literal['ready', 'blocked', 'not_verified']
    reason: str


class RuntimeConnectionSnapshot(TypedDict):
    """独立于持久化静态转入合同，实时发布当前诊断且永不授权发送。"""

    agentId: str | None
    endpoint: str | None
    verificationScope: Literal['live_proxy_health']
    sendEnabled: Literal[False]
    plainTextPreview: bool
    checks: list[ConnectionCheck]
    proxyHealth: ProxyHealth


def connection_snapshot(workspace: Path, frontend: Mapping[str, Any],
                        runtime: Mapping[str, Any]) -> RuntimeConnectionSnapshot:
    """从当前正式配置/规划与受管进程事实派生报告，探测仅访问本机固定健康路径。"""
    root = workspace.resolve()
    result: RuntimeConnectionSnapshot = {
        'agentId': None, 'endpoint': None, 'verificationScope': 'live_proxy_health',
        'sendEnabled': False, 'plainTextPreview': False, 'checks': [],
        'proxyHealth': {'status': 'not_verified', 'reason': '当前正式 Agent 合同不可用，请检查已确认的技术规划。'},
    }
    try:
        plan = runtime_check_plan(root, {})
        if plan is None:
            return result
        contracts = agent_contract_index(plan)
        if len(contracts) != 1:
            result['proxyHealth']['reason'] = '共享 Agent UI 需要唯一正式 Agent，当前合同不满足。'
            return result
        contract = next(iter(contracts.values()))
        application = load_application_json(root)
        requirements = connection_requirements(application, contract)
    except (DatabaseCredentialError, OSError, ValueError, KeyError, TypeError):
        # 不回传原异常，避免将文件路径、配置或业务内容混入实时状态。
        return result
    result.update(agentId=requirements['agentId'], endpoint=requirements['endpoint'], checks=requirements['checks'])
    port = frontend.get('port')
    launch_id = runtime.get('launchId')
    if (frontend.get('status') != 'running' or runtime.get('status') != 'running'
            or type(port) is not int or not 0 < port < 65536
            or not isinstance(launch_id, str) or not launch_id):
        result['proxyHealth']['reason'] = '当前前端和 Python 服务尚未同时就绪，请沿原服务入口启动或重启。'
        return result
    # 不使用任意 URL 或来自旧记录的 Agent 集合，限定当前本机端口和正式身份。
    ready, reason = runtime_health(f'http://127.0.0.1:{port}', launch_id,
        health_path='/__runtime/health', database_required=runtime.get('databaseRequired') is True,
        expected_agent_ids=[requirements['agentId']], expected_application=root / 'agent-runtime',
        expected_namespace='preview:' + hashlib.sha256(str(root).encode()).hexdigest()[:24])
    result['proxyHealth'] = {'status': 'ready' if ready else 'blocked', 'reason': reason}
    marker = frontend.get('agentUiPreview')
    result['plainTextPreview'] = (ready and isinstance(marker, Mapping)
        and dict(marker) in ({'mode': mode, 'agentId': requirements['agentId']} for mode in ('real-text', 'formal-text'))
        and plain_text_preview_allowed(application, contract))
    return result
