"""标准受管预览选择真实纯文本或官方 Mock，不修改目标正式源码。"""

import json
from pathlib import Path
from uuid import uuid4

from app.services.agent_ui_dependency_plan import read_object, safe_path, sha256
from app.services.agent_ui_host_integration import PROVIDER
from app.services.agent_ui_transfer_records import transfer_directory, transfer_ownership
from app.services.agent_ui_connection_requirements import runtime_connection_allowed
from app.services.database_credentials import load_application_json
from app.services.python_runtime_checks import runtime_check_plan
from app.services.runtime_agent_contracts import agent_contract_index, agent_invocation
from app.services.agent_ui_observation import capture_identity


def managed_preview_command(workspace: Path, target: Path, script: str, *,
                            runtime_public_url: str | None = None) -> list[str] | None:
    """复核归属和正式能力；真实模式只使用原受管 Runtime，不启动另一个聊天服务。"""
    provider = safe_path(target, PROVIDER)
    if not provider.is_file():
        return None
    code = provider.read_text(encoding='utf-8')
    pointer = safe_path(workspace, ".devagentstudio/runtime/agent-ui-transfer/current.json")
    if not pointer.is_file():
        if 'endpoint=""' not in code:
            return None
        raise ValueError('空连接 Host 缺少当前转入记录，请先审阅接线。')
    if script.strip() != "rsbuild dev":
        raise ValueError("共享 Agent UI 的开发预览需要当前 rsbuild dev 脚本。")
    record = read_object(safe_path(transfer_directory(workspace, read_object(pointer)["planId"]), "record.json"))
    connection = record['plan']['hostIntegration'].get('connection')
    formal_text = connection in {'plain_text', 'business'}
    if not formal_text and 'endpoint=""' not in code:
        return None
    source_root = record["plan"]["target"]["sourceRoot"]
    owned = transfer_ownership(workspace, source_root)
    if (PROVIDER not in owned or owned[PROVIDER] != record['owned'].get(PROVIDER)
            or record["plan"]["hostIntegration"].get("connection") not in {"blocked_T27", "plain_text", "business"}):
        raise ValueError("空连接 Host 已变化或缺少转入归属，请重新审阅接线后启动预览。")
    # 页面开发会新增页面正文；这里只复验仍在使用的转入源码，不重签正式转入证据。
    for name, digest in record["owned"].items():
        if name.startswith(source_root + "/") and owned.get(name) != digest:
            raise ValueError("已转入的共享 UI 源码发生变化，请先审阅当前设计。")
    from app.services.agent_ui_design_workflow import design_environment

    inspection, _ = design_environment(str(workspace))
    agent_id = record["plan"]["hostIntegration"]["agentId"]
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("Mock 开发预览缺少当前转入的 Agent 身份。")
    options = {"target": str(target), "providerSha256": sha256(provider.read_bytes()), "agentId": agent_id,
        "installationRoot": str(inspection.installation_root), "creatorVersion": inspection.creator_version,
        "packageJsonSha256": inspection.package_json_sha256, "mode": "official-mock", "sessionId": uuid4().hex}
    options['observation'] = {'sessionId': options['sessionId'], 'identity': capture_identity(workspace)}
    if runtime_public_url is not None:
        plan = runtime_check_plan(workspace, {})
        contracts = agent_contract_index(plan) if plan is not None else {}
        contract = contracts.get(agent_id)
        if len(contracts) == 1 and contract is not None and runtime_connection_allowed(load_application_json(workspace), contract):
            endpoint = agent_invocation(agent_id)['path']
            if formal_text and (record.get('state') != 'verified'
                                or f'endpoint={json.dumps(endpoint)}' not in code):
                raise ValueError('正式 Agent UI Host 未通过当前转入验证，请重新审阅。')
            options.update(mode=("formal-business" if connection == "business" else "formal-text") if formal_text else "real-text", endpoint=endpoint)
    if formal_text and options['mode'] not in {'formal-text', 'formal-business'}:
        raise ValueError('正式 Agent UI能力或当前 Runtime 不满足接线条件，请重新审阅，不回退 Mock。')
    return [str(inspection.node_executable), str(Path(__file__).with_name("agent_ui_managed_preview.mjs")),
            json.dumps(options, ensure_ascii=False)]
