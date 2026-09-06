"""业务智能体进入开发前的确定性就绪检查。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.application_template_generation import inspect_template_generation_readiness
from app.services.development_readiness import development_readiness
from app.services.page_implementation_contract import materialize_technical_plan_runtime
from app.services.project_plan import validate_technical_plan_agent_contracts
from app.workspace.plan_documents import load_project_plan_json


def agent_contract_sha256(contract: dict[str, Any]) -> str:
    """按固定 JSON 序列化规则计算单个完整 Agent Contract 的身份哈希。"""

    payload = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def inspect_agent_development_readiness(
    workspace: str | Path,
    agent_id: str,
) -> dict[str, Any]:
    """从工作区正式产物复检指定 Agent 的 Contract、模板和实体依赖。"""

    root = Path(workspace).expanduser().resolve()
    normalized_agent_id = str(agent_id or "").strip()
    blockers: list[dict[str, str]] = []
    if not normalized_agent_id:
        return _result("", "", [], [_blocker("agent_contract", "", "请选择要开发的智能体。", "revise_technical_plan")])

    requirement_spec = _load_plan(root / ".xcodeagent/specs/requirement-spec.json")
    product_plan = _load_plan(root / ".xcodeagent/plans/product-plan.json")
    ui_designs = _load_plan(root / ".xcodeagent/specs/ui-designs.json")
    technical_plan = _load_plan(root / ".xcodeagent/plans/technical-plan.json")
    _append_artifact_blockers(root, product_plan, technical_plan, blockers)
    product_agents = [
        item
        for item in _dict_items(product_plan.get("agents"))
        if str(item.get("agentId") or "").strip() == normalized_agent_id
    ]
    technical_agents = [
        item
        for item in _dict_items(technical_plan.get("agent_contracts"))
        if str(item.get("agentId") or "").strip() == normalized_agent_id
    ]
    if len(product_agents) != 1 or len(technical_agents) != 1:
        blockers.append(
            _blocker(
                "agent_contract",
                normalized_agent_id,
                "ProductPlan 与 TechnicalPlan 无法唯一定位当前智能体。",
                "revise_technical_plan",
            )
        )
        return _result(normalized_agent_id, "", [], blockers)

    contract = technical_agents[0]
    contract_hash = agent_contract_sha256(contract)
    contract_errors = validate_technical_plan_agent_contracts(technical_plan, product_plan)
    if contract_errors:
        blockers.append(
            _blocker(
                "agent_contract",
                normalized_agent_id,
                "当前智能体契约与已确认产品规划或接口契约不一致。",
                "revise_technical_plan",
            )
        )

    template_readiness = inspect_template_generation_readiness(root)
    if not template_readiness.get("ready"):
        blockers.append(
            _blocker(
                "agent_runtime_template",
                normalized_agent_id,
                "Agent Runtime 模板尚未准备完成或来源校验失败。",
                "retry_template_generation",
            )
        )

    missing_entities = _missing_tool_entities(
        technical_plan,
        contract,
        blockers,
    )
    runtime_plan = _materialize_agent_runtime_plan(
        requirement_spec,
        product_plan,
        ui_designs,
        technical_plan,
        normalized_agent_id,
        blockers,
    )
    if runtime_plan is not None:
        _append_page_binding_blockers(
            runtime_plan,
            product_agents[0],
            contract,
            blockers,
        )
    return _result(
        normalized_agent_id,
        contract_hash,
        missing_entities,
        _deduplicated_blockers(blockers),
    )


def _append_artifact_blockers(
    root: Path,
    product_plan: dict[str, Any],
    technical_plan: dict[str, Any],
    blockers: list[dict[str, str]],
) -> None:
    """检查 Agent readiness 必需的四份当前正式产物。"""

    artifact_specs = (
        ("RequirementSpec", root / ".xcodeagent/specs/requirement-spec.json", {"confirmed"}),
        ("ProductPlan", root / ".xcodeagent/plans/product-plan.json", {"confirmed"}),
        ("UiManifest", root / ".xcodeagent/specs/ui-designs.json", {"confirmed", "skipped"}),
        ("TechnicalPlan", root / ".xcodeagent/plans/technical-plan.json", {"confirmed"}),
    )
    for label, path, statuses in artifact_specs:
        artifact = (
            product_plan
            if label == "ProductPlan"
            else technical_plan
            if label == "TechnicalPlan"
            else _load_plan(path)
        )
        if not artifact or str(artifact.get("confirmation_status") or "") not in statuses:
            blockers.append(
                _blocker(
                    "formal_artifact",
                    label,
                    f"{label} 尚未确认或已失效。",
                    "confirm_formal_artifact",
                )
            )
    if technical_plan and technical_plan.get("artifact_type") != "technical-plan":
        blockers.append(
            _blocker(
                "formal_artifact",
                "TechnicalPlan",
                "TechnicalPlan 类型不正确。",
                "revise_technical_plan",
            )
        )


def _missing_tool_entities(
    technical_plan: dict[str, Any],
    contract: dict[str, Any],
    blockers: list[dict[str, str]],
) -> list[dict[str, str]]:
    """复用 Endpoint readiness 汇总当前 Agent Tool 所需实体绑定。"""

    settings = contract.get("agentSettings") if isinstance(contract.get("agentSettings"), dict) else {}
    tools = settings.get("tools") if isinstance(settings.get("tools"), dict) else {}
    missing_by_id: dict[str, dict[str, str]] = {}
    for binding in _dict_items(tools.get("bindings")):
        endpoint = binding.get("endpoint") if isinstance(binding.get("endpoint"), dict) else {}
        endpoint_id = str(endpoint.get("endpointId") or "").strip()
        api_contract_id = str(endpoint.get("apiContractId") or "").strip()
        if not endpoint_id or not api_contract_id:
            continue
        try:
            readiness = development_readiness(
                technical_plan,
                target_type="endpoint",
                target_id=endpoint_id,
                api_contract_id=api_contract_id,
            )
        except ValueError:
            blockers.append(
                _blocker(
                    "tool_endpoint",
                    str(binding.get("toolId") or endpoint_id),
                    "智能体工具无法唯一解析到当前 Endpoint。",
                    "revise_technical_plan",
                )
            )
            continue
        for entity in readiness.get("missing_entities") or []:
            if not isinstance(entity, dict):
                continue
            entity_id = str(entity.get("entity_id") or "").strip()
            if not entity_id:
                continue
            missing_by_id[entity_id] = {
                "entity_id": entity_id,
                "entity_name": str(entity.get("entity_name") or entity_id),
            }
    for entity_id, entity in missing_by_id.items():
        blockers.append(
            _blocker(
                "entity_source_binding",
                entity_id,
                f"{entity['entity_name']} 实体尚未完成数据源绑定。",
                "start_entity_binding",
            )
        )
    return list(missing_by_id.values())


def _materialize_agent_runtime_plan(
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    ui_designs: dict[str, Any],
    technical_plan: dict[str, Any],
    agent_id: str,
    blockers: list[dict[str, str]],
) -> dict[str, Any] | None:
    """仅为 Agent 入口复用现有编译器生成运行时页面实现合同。"""

    if not all((requirement_spec, product_plan, ui_designs, technical_plan)):
        return None
    try:
        return materialize_technical_plan_runtime(
            technical_plan,
            requirement_spec,
            product_plan,
            ui_designs,
        )
    except (TypeError, ValueError):
        blockers.append(
            _blocker(
                "page_action_binding",
                agent_id,
                "智能体入口页面实现合同无法从当前正式规划编译。",
                "revise_technical_plan",
            )
        )
        return None


def _append_page_binding_blockers(
    technical_plan: dict[str, Any],
    product_agent: dict[str, Any],
    contract: dict[str, Any],
    blockers: list[dict[str, str]],
) -> None:
    """检查产品入口操作仍由页面实现合同指向当前 Agent 网关。"""

    invocation = contract.get("invocation") if isinstance(contract.get("invocation"), dict) else {}
    gateway_id = str(invocation.get("gatewayEndpointId") or "").strip()
    page_contracts = {
        str(item.get("pageId") or "").strip(): item
        for item in _dict_items(technical_plan.get("page_implementation_contracts"))
    }
    technical_pages = {
        str(item.get("pageId") or "").strip(): item
        for item in _dict_items(technical_plan.get("pages"))
    }
    for binding in _dict_items(product_agent.get("pageActionBindings")):
        page_id = str(binding.get("pageId") or "").strip()
        action_ids = {
            str(item or "").strip()
            for item in binding.get("actionIds") or []
            if str(item or "").strip()
        }
        page = technical_pages.get(page_id, {})
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        implementations = {
            str(item.get("actionId") or "").strip(): str(item.get("endpointId") or "").strip()
            for item in _dict_items(references.get("action_implementations"))
        }
        page_contract = page_contracts.get(page_id)
        required_endpoint_ids = {
            str(item or "").strip()
            for item in (page_contract or {}).get("requiredEndpointIds") or []
            if str(item or "").strip()
        }
        if (
            not page_id
            or page_contract is None
            or gateway_id not in required_endpoint_ids
            or any(implementations.get(action_id) != gateway_id for action_id in action_ids)
        ):
            blockers.append(
                _blocker(
                    "page_action_binding",
                    page_id or str(contract.get("agentId") or ""),
                    "智能体入口页面操作与当前 Java Gateway Endpoint 不一致。",
                    "revise_technical_plan",
                )
            )


def _result(
    agent_id: str,
    contract_hash: str,
    missing_entities: list[dict[str, str]],
    blockers: list[dict[str, str]],
) -> dict[str, Any]:
    """生成不暴露 Prompt、Schema、物理路径或凭据的公开就绪结果。"""

    return {
        "ready": not blockers,
        "target_type": "agent",
        "target_id": agent_id,
        "contract_hash": contract_hash,
        "missing_entities": missing_entities,
        "blockers": blockers,
    }


def _blocker(
    blocker_type: str,
    target_id: str,
    message: str,
    action: str,
) -> dict[str, str]:
    """创建稳定且可安全公开的 Agent readiness blocker。"""

    return {
        "type": blocker_type,
        "targetId": target_id,
        "message": message,
        "action": action,
    }


def _deduplicated_blockers(
    blockers: list[dict[str, str]],
) -> list[dict[str, str]]:
    """按类型、目标与动作去重，避免 UI 重复展示同一阻断项。"""

    result: dict[tuple[str, str, str], dict[str, str]] = {}
    for blocker in blockers:
        key = (blocker["type"], blocker["targetId"], blocker["action"])
        result.setdefault(key, blocker)
    return list(result.values())


def _load_plan(path: Path) -> dict[str, Any]:
    """严格读取正式 JSON；缺失或损坏统一交由 blocker 投影。"""

    try:
        loaded = load_project_plan_json(path, hydrate_detail_designs=True)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """过滤外部数组中的非对象条目。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
