"""从已确认共享应用层和当前转入证据编译页面构建合同。"""

from __future__ import annotations

from hashlib import sha256
from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.agent_ui_build_evidence import load_build_evidence


AGENT_UI_BUILD_CONTRACT_VERSION = "agent-ui-shared-build.v1"


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从数组中仅保留对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    """把任意值规范为去空白字符串。"""

    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    """为平台编译的 Agent UI 合同生成稳定摘要。"""

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()




def project_agent_ui_build_contracts(
    product_plan: dict[str, Any],
    technical_plan: dict[str, Any],
    *,
    workspace: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """只为唯一正式 Agent 投影共享入口，未转入或过期时拒绝生成旧模板合同。"""

    from app.services.runtime_agent_contracts import agent_contract_index
    pages = [page for page in project_ui_design_pages(product_plan) if page.get('agent_surfaces')]
    if not pages:
        return {}
    contracts = agent_contract_index(technical_plan)
    if len(contracts) != 1:
        raise ValueError('共享 Agent UI 仅支持唯一正式 Agent。')
    if workspace is None:
        raise ValueError('Agent 页面 Build 需要工作区及当前已确认转入证据。')
    evidence = load_build_evidence(workspace)
    host = evidence['host']
    agent_id, runtime = next(iter(contracts.items()))
    from app.services.runtime_endpoint_contracts import runtime_endpoint_capability_snapshot, runtime_endpoint_identity
    endpoint = runtime_endpoint_capability_snapshot(technical_plan, *runtime_endpoint_identity(agent_id))
    if (host['agentId'] != agent_id or host.get('connection') not in {'blocked_T27', 'plain_text', 'business'}
            or host.get('endpoint') != endpoint['invocation']['path']):
        raise ValueError('共享 Host 与当前正式 Agent 或连接准入不一致。')
    result: dict[str, dict[str, Any]] = {}
    for page in pages:
        page_id = _text(page.get("pageId"))
        surfaces = _dict_items(page.get("agent_surfaces"))
        if (not page_id or len(surfaces) != 1 or surfaces[0].get('agentId') != agent_id
                or page_id not in runtime['pageIds'] or page_id not in host['pageIds']
                or page_id not in evidence['confirmations']):
            raise ValueError('当前 Agent 页面未纳入唯一共享 Host 及正式设计确认。')
        surface = _text(surfaces[0].get('type'))
        if surface not in {'standalone_page', 'floating_panel'}:
            raise ValueError('当前页面的共享展示载体无效。')
        payload = {
            "version": AGENT_UI_BUILD_CONTRACT_VERSION,
            "mode": "shared_host",
            "pageId": page_id,
            "agentId": agent_id,
            "surface": surface,
            "host": deepcopy(host),
            "runtimeEndpoint": endpoint,
            "businessAguiPaths": sorted({item['path'] for api in technical_plan.get('api_contracts', [])
                for item in api.get('endpoints', []) if item.get('kind') == 'business'
                and (item.get('agentCall') or {}).get('responseMode') == 'ag-ui'}),
            "presentation": deepcopy(evidence['presentations'].get(page_id, {'props': {}, 'styles': []})),
            "evidence": deepcopy(evidence),
            "connection": host['connection'],
            "verificationScope": "static_source",
        }
        result[page_id] = {**payload, "sha256": _stable_hash(payload)}
    return result


def agent_ui_contract_from_source_refs(
    source_refs: Any,
    *,
    page_id: str = "",
) -> dict[str, Any]:
    """从 Unit 来源中读取直接合同，或按 pageId 选择应用级合同。"""

    refs = source_refs if isinstance(source_refs, dict) else {}
    direct = refs.get("agent_ui")
    if isinstance(direct, dict) and direct:
        return dict(direct)
    by_page = refs.get("agent_ui_by_page")
    if isinstance(by_page, dict):
        selected = by_page.get(page_id)
        if isinstance(selected, dict):
            return dict(selected)
    return {}


def current_agent_ui_build_contract(workspace: str | Path, page_id: str) -> dict[str, Any]:
    """验收从磁盘正式产物重新投影，禁止任务携带的旧快照自证来源。"""
    from app.services.agent_ui_dependency_plan import read_object, safe_path, target_root
    from app.workspace.plan_documents import load_project_plan_json
    root, _ = target_root(workspace)
    product = read_object(safe_path(root, '.devagentstudio/plans/product-plan.json'))
    technical = load_project_plan_json(safe_path(root, '.devagentstudio/plans/technical-plan.json'),
                                       hydrate_detail_designs=True)
    contract = project_agent_ui_build_contracts(product, technical, workspace=root).get(page_id)
    if not contract:
        raise ValueError('当前正式页面没有共享 Agent UI 构建合同。')
    return contract


def task_agent_ui_contract(task: dict[str, Any]) -> dict[str, Any]:
    """读取任务上由平台注入的正式 Agent UI 合同。"""

    return agent_ui_contract_from_source_refs(task.get("source_refs"))


