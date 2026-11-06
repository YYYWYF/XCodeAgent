"""T27 连接条件的只读投影；当前成品能力和静态合同均不能授权真实发送。"""

from collections.abc import Mapping
from typing import Any, Literal, TypedDict

from app.services.runtime_agent_contracts import agent_invocation


class ConnectionCheck(TypedDict):
    """单项条件区分静态支持、未验证、不适用和已知阻断。"""

    id: str
    status: Literal['supported', 'blocked', 'not_verified', 'not_required']
    reason: str


class ConnectionRequirements(TypedDict):
    """审阅/构建共用条件，不保存配置副本、业务内容或临时凭据。"""

    agentId: str
    endpoint: str
    method: Literal['POST']
    principalSource: Literal['runtime_anonymous_session']
    healthPath: Literal['/__runtime/health']
    verificationScope: Literal['static_contract']
    sendEnabled: Literal[False]
    checks: list[ConnectionCheck]


def connection_requirements(application: Mapping[str, Any], contract: Mapping[str, Any]) -> ConnectionRequirements:
    """从应用配置和已校验的唯一正式 Agent 派生条件，不联网、不启动、不发送。"""
    agent_id: str = contract['agentId']
    invocation = agent_invocation(agent_id)
    auth = application.get('auth')
    authorization = application.get('authorization')
    anonymous = (isinstance(auth, Mapping) and auth.get('enable') is False
                 and isinstance(authorization, Mapping) and authorization.get('enabled') is False)
    capability: Mapping[str, Any] = contract['interactionContract']
    settings: Mapping[str, Any] = contract['agentSettings']
    # 纯文本样例不替用户删减正式业务能力；声明了输入、结果或 Tool 就另列待接入。
    plain_text = (capability['mode'] == 'conversation' and capability['textOutput'] is True
                  and capability['inputSchema'] == {'type': 'object', 'properties': {},
                      'required': [], 'additionalProperties': False}
                  and capability.get('resultSchema') is None
                  and settings['tools']['enabled'] is False and not settings['tools']['bindings'])
    checks: list[ConnectionCheck] = [
        {'id': 'plain_text', 'status': 'supported' if plain_text else 'blocked',
         'reason': '正式合同支持无业务输入/Tool 的纯文本会话' if plain_text else
                   '正式合同含任务、业务输入/结果或 Tool，不能按纯文本样例开放'},
        {'id': 'runtime_identity', 'status': 'supported' if anonymous else 'blocked',
         'reason': '应用配置明确采用当前模板支持的匿名会话模式' if anonymous else
                   '当前 Runtime 仅支持 auth.enable=false 且 authorization.enabled=false；缺失或开启均不可连接'},
        {'id': 'same_origin_session', 'status': 'not_verified',
         'reason': '目标同源代理及匿名会话尚未验证；须沿现有受管预览核对本轮 launchId、Agent 注册和会话'},
        {'id': 'context', 'status': 'blocked' if settings['context'] else 'not_required',
         'reason': '正式上下文尚未对齐标准 Context' if settings['context'] else '当前 Agent 未声明业务上下文'},
        {'id': 'interrupts', 'status': 'blocked' if capability['interactionKinds'] else 'not_required',
         'reason': '正式交互的 interrupt/resume 与只读查询尚未接通' if capability['interactionKinds'] else
                   '当前 Agent 未声明用户交互'},
        # 当前已核对成品未填 canonical run.id、无统一 veto；包资源身份由原计划绑定。
        {'id': 'run_identity', 'status': 'blocked',
         'reason': '当前成品公开快照未提供可信 run.id，业务结果归属无法核验'},
        {'id': 'send_admission', 'status': 'blocked',
         'reason': '当前成品缺少覆盖所有发送/恢复入口的公开准入能力'},
        {'id': 'real_conversation', 'status': 'not_verified',
         'reason': '实际生成并注册的 Python Agent 两轮对话、失败、停止和断流尚未验收'},
    ]
    return {'agentId': agent_id, 'endpoint': invocation['path'], 'method': 'POST',
            'principalSource': 'runtime_anonymous_session', 'healthPath': '/__runtime/health',
            'verificationScope': 'static_contract', 'sendEnabled': False, 'checks': checks}


def connection_blocking_reason(requirements: ConnectionRequirements) -> str:
    """为既有转入审阅提示聚合未闭合条件，不把静态支持写成运行通过。"""
    return '；'.join(check['reason'] for check in requirements['checks']
                    if check['status'] in {'blocked', 'not_verified'}) + '。真实发送保持禁用。'
