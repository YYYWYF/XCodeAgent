"""纯文本会话的共用能力边界；正式 Host 与开发预览均不得扩大完整 T27 准入。"""

from collections.abc import Mapping
from typing import Any

from app.services.agent_ui_connection_requirements import connection_requirements


def plain_text_preview_allowed(application: Mapping[str, Any], contract: Mapping[str, Any]) -> bool:
    """仅允许公开匿名纯文本会话；描述性上下文不等同于声明业务页面数据。"""
    report = connection_requirements(application, contract)
    checks = {item['id']: item['status'] for item in report['checks']}
    settings = contract['agentSettings']
    capability = contract['interactionContract']
    return (contract.get('publicEndpoint') is True
            and checks['plain_text'] == checks['runtime_identity'] == 'supported'
            and checks['interrupts'] == 'not_required'
            and capability['publicStateSchema'] is None and not capability['outcomes']
            and not (set(settings['context']) - {'description'})
            and settings['skills']['enabled'] is False and settings['knowledge']['enabled'] is False)
