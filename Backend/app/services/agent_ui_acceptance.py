"""T29 当前目标的分层人工核验证据；不调用模型、不写业务数据、不批准版本。"""

from datetime import UTC, datetime
import json
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from app.services.agent_ui_acceptance_cases import acceptance_cases
from app.services.agent_ui_dependency_plan import read_object, safe_path, target_root
from app.services.agent_ui_observation_identity import digest, tree_digest
from app.domain.agent_ui_acceptance import AcceptanceReport, RecordedAcceptance

_LOCK = RLock()
_RECORD = '.devagentstudio/runtime/agent-ui-acceptance/evidence.json'
_DEPENDENCIES = {
    'code': ('frontend', 'runtime', 'formal', 'packages', 'lock'),
    'visual': ('frontend', 'presentation', 'packages', 'lock'),
    'connection': ('frontend', 'runtime', 'formal', 'config', 'packages', 'lock'),
    'business': ('frontend', 'runtime', 'formal', 'config', 'packages', 'lock'),
}


def acceptance_contexts(workspace: Path) -> list[dict[str, Any]]:
    """复用正式转入及 T28 字节身份；页面或合同不闭合时不出具可登记摘要。"""
    from app.services.agent_ui_build_contract import project_agent_ui_build_contracts
    from app.services.agent_ui_observation import capture_identity
    from app.services.runtime_agent_contracts import agent_contract_index
    from app.services.python_runtime_checks import runtime_check_plan
    from app.services.ui_design_agent_surfaces import project_ui_design_pages
    from app.services.product_plan import require_current_product_plan
    from app.services.agent_ui_acceptance_contracts import formal_dependencies
    product_path = safe_path(workspace, '.devagentstudio/plans/product-plan.json')
    if not product_path.is_file():
        raise ValueError('当前正式产品规划缺失，不能判断验收范围。')
    product = read_object(product_path)
    requirement = read_object(safe_path(workspace, '.devagentstudio/specs/requirement-spec.json'))
    if any(item.get('confirmation_status') != 'confirmed' for item in (product, requirement)):
        raise ValueError('请先联合确认当前需求和产品规划，再读取验收范围。')
    require_current_product_plan(product, requirement)
    pages = {page['pageId']: page for page in project_ui_design_pages(product)}
    if not any(page.get('agent_surfaces') for page in pages.values()):
        return []
    root, _ = target_root(workspace)
    formal = runtime_check_plan(root, {})
    if formal is None:
        raise ValueError('当前正式技术规划不可用。')
    contracts = project_agent_ui_build_contracts(product, formal, workspace=root)
    agents = agent_contract_index(formal)
    identity = capture_identity(root)
    if identity['status'] != 'available':
        raise ValueError('当前源码、锁或实际包身份不可核验。')
    from app.config import Settings
    settings = Settings.from_env()
    config = digest({'application': read_object(safe_path(root, '.devagentstudio/application.json')),
                     'provider': settings.model_provider, 'model': settings.model_api_name,
                     'base': settings.model_base_url, 'credential': digest(settings.model_api_key or ''),
                     'headers': digest(settings.model_custom_headers), 'temperature': settings.default_temperature,
                     'maxTokens': settings.default_max_tokens, 'timeout': settings.model_timeout_seconds,
                     'retries': settings.model_max_retries, 'trustEnv': settings.model_trust_env,
                     'goHeaders': settings.model_opencode_go_headers_enabled, 'anthropicVersion': settings.anthropic_api_version})
    inputs = {'frontend': tree_digest(root / 'frontend'), 'runtime': tree_digest(root / 'agent-runtime'),
              'config': config, 'packages': digest(identity['packages']), 'lock': identity['lockSha256']}
    return [{'pageId': page_id, 'pageLabel': pages[page_id].get('name') or pages[page_id].get('label') or 'Agent 页面',
             'agent': agents[contract['agentId']],
             'inputs': {**inputs, 'formal': digest({'agent': agents[contract['agentId']],
                 'page': pages[page_id],
                 'dependencies': formal_dependencies(root, formal, page_id),
                 'confirmation': contract['evidence']['confirmations'][page_id]}),
                 'presentation': digest({'pageId': page_id, 'presentation': contract['presentation'],
                     'confirmation': contract['evidence']['confirmations'][page_id],
                     'interactionContract': agents[contract['agentId']]['interactionContract']})}}
            for page_id, contract in sorted(contracts.items())]


def live_observation(workspace: Path) -> dict[str, Any]:
    """只读取原受管进程的健康和同一公开 Host 身份，不发业务请求。"""
    from app.services.preview_runtime_state import read_record
    from app.services.agent_ui_runtime_connection import connection_snapshot
    record = read_record(workspace)
    connection = connection_snapshot(workspace, record.get('frontend') or {}, record.get('agent_runtime') or {})
    return connection.get('observation') or {}


def _read_records(workspace: Path) -> dict[str, Any]:
    """有界读取唯一当前证据文件；损坏记录不可当成通过或自动迁移。"""
    path = safe_path(workspace, _RECORD)
    if not path.exists():
        return {}
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('验收记录不可读取或超过上限。')
    from pydantic import ValidationError
    try:
        records = read_object(path)
        if any(not isinstance(key, str) or key != value.get('caseId') for key, value in records.items()
               if isinstance(value, dict)) or any(not isinstance(value, dict) for value in records.values()):
            raise ValueError('验收记录身份不一致。')
        return {key: RecordedAcceptance.model_validate(value).model_dump() for key, value in records.items()}
    except (ValidationError, ValueError):
        raise ValueError('当前验收记录不完整或损坏，请重新核对记录，不能沿用通过状态。') from None


def _validate_case_report(report: AcceptanceReport, case: dict[str, Any]) -> None:
    """提交与读取共用当前用例规则，磁盘中的通过值同样不能越过核验项和来源。"""
    expected = {item['id'] for item in case['checks']}
    checks = set(report.checks)
    if not checks <= expected or len(checks) != len(report.checks):
        raise ValueError('核验项不属于当前正式用例或重复。')
    if report.outcome == 'passed' and checks != expected:
        raise ValueError('通过必须逐项核对全部证据，运行终态不能替代业务结果。')
    if (case['layer'] == 'code') != (report.source == 'code'):
        raise ValueError('代码检查与界面运行核验必须分别登记。')
    if case['layer'] in {'connection', 'business'} and report.source != 'real':
        raise ValueError('真实连接与业务验收不能由 Mock 或回放通过。')


def _validate_pass_source(report: AcceptanceReport, case: dict[str, Any], observation: dict[str, Any]) -> None:
    """通过记录必须来自相符的当前来源；重读只验证原记录身份，不要求旧服务仍在线。"""
    if report.outcome != 'passed' or case['layer'] == 'code':
        return
    modes = {'real': {'formal-text', 'formal-business'}, 'official-mock': {'official-mock'}, 'replay': {'replay'}}
    if (observation.get('source') not in modes[report.source] or not observation.get('sessionId')
            or not observation.get('launchId') and report.source == 'real'):
        raise ValueError('所选来源与当前预览或运行身份不一致，不能登记通过。')
    if case['layer'] == 'business' and observation['source'] != 'formal-business':
        raise ValueError('业务用例必须使用正式业务目标。')


def acceptance_matrix(workspace: str | Path) -> dict[str, Any]:
    """重新投影四层现状，输入变化失效相关证据；不改变正式验收确认。"""
    root = Path(workspace).expanduser().absolute()
    if not root.is_dir() or root.resolve() != root:
        raise ValueError('工作区必须是无链接的实际目录。')
    contexts = acceptance_contexts(root)
    records = _read_records(root)
    live = live_observation(root) if contexts else {}
    cases = []
    for context in contexts:
        for case in acceptance_cases(context['agent']):
            case_id = digest([context['pageId'], context['agent']['agentId'], case['scenario']])
            dependencies = {key: context['inputs'][key] for key in _DEPENDENCIES[case['layer']]}
            # 仅审阅身份绑定本轮 launch；既有证据按代码/配置失效，重启不抹掉已完成事实。
            evidence_key = digest({'workspace': str(root), 'case': case, 'inputs': dependencies})
            fingerprint = digest({'evidenceKey': evidence_key, 'session': live.get('sessionId'),
                                  'launch': live.get('launchId'), 'source': live.get('source')})
            previous = records.get(case_id)
            evidence = previous if isinstance(previous, dict) else None
            if evidence is not None and evidence['evidenceKey'] == evidence_key and case['applicable']:
                report = RecordedAcceptance.model_validate(evidence)
                _validate_case_report(report, case)
                _validate_pass_source(report, case, {'source': report.runtimeSource,
                    'sessionId': report.sessionId, 'launchId': report.launchId})
            status = ('not_applicable' if not case['applicable'] else 'pending' if evidence is None
                      else 'stale' if evidence.get('evidenceKey') != evidence_key else evidence['outcome'])
            cases.append({**case, 'caseId': case_id, 'pageId': context['pageId'],
                          'pageLabel': context.get('pageLabel', 'Agent 页面'),
                          'agentId': context['agent']['agentId'], 'fingerprint': fingerprint,
                          'evidenceKey': evidence_key, 'status': status, 'evidence': evidence})
    return {'cases': cases, 'ready': all(case['status'] in {'passed', 'not_applicable'} for case in cases),
            'method': 'user_observed', 'automaticExecution': False, 'observation': live,
            'layers': [{'id': layer, 'status': 'not_applicable' if not any(
                case['layer'] == layer and case['applicable'] for case in cases) else 'passed' if all(
                    case['status'] in {'passed', 'not_applicable'} for case in cases if case['layer'] == layer)
                else 'pending'} for layer in _DEPENDENCIES]}


def record_acceptance(workspace: str | Path, report: dict[str, Any], *, thread_id: str, run_id: str) -> dict[str, Any]:
    """只记录用户显式核验的有界勾选事实；不能提交原始业务值、任意事件或测试命令。"""
    request = AcceptanceReport.model_validate(report)
    root = Path(workspace).expanduser().absolute()
    with _LOCK:
        matrix = acceptance_matrix(root)
        case = next((item for item in matrix['cases'] if item['caseId'] == request.caseId), None)
        if case is None or not case['applicable'] or case['fingerprint'] != request.fingerprint:
            raise ValueError('用例不存在、不适用或审阅输入已变化，请重新读取。')
        _validate_case_report(request, case)
        live = matrix['observation']
        _validate_pass_source(request, case, live)
        if request.outcome == 'passed' and case['layer'] != 'code' and not live.get('identityMatches'):
            raise ValueError('当前预览源码与运行身份尚未闭合，不能登记。')
        records = _read_records(root)
        # 只保留当前声明的用例；不实现历史读取、兼容或迁移。
        records = {item['caseId']: records[item['caseId']] for item in matrix['cases'] if item['caseId'] in records}
        records[request.caseId] = {**request.model_dump(), 'evidenceKey': case['evidenceKey'],
            'method': 'user_observed', 'threadId': thread_id, 'runId': run_id,
            'checkedAt': datetime.now(UTC).isoformat(),
            'sessionId': live.get('sessionId'), 'launchId': live.get('launchId'), 'runtimeSource': live.get('source')}
        RecordedAcceptance.model_validate(records[request.caseId])
        current = acceptance_matrix(root)
        if next((item['fingerprint'] for item in current['cases'] if item['caseId'] == request.caseId), None) != request.fingerprint:
            raise ValueError('登记期间输入变化，请重新核验。')
        path = safe_path(root, _RECORD)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = safe_path(root, _RECORD + '.' + uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(records, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        temporary.replace(path)
        return acceptance_matrix(root)
