"""当前 TechnicalPlan 内七模块 Agent 合同及精确依赖，不增加另一份正式产物。"""

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any

from pydantic import ValidationError

from app.domain.runtime_endpoint_contract import AgentExecutionContract
from app.services.agent_runtime_template_policy import AGENT_RUNTIME_MODULES
from app.services.planning_frozen import plain_json
from app.services.runtime_business_contract import serves_python_business
from app.services.runtime_agent_editable_config import PromptSettings, ProjectModelSettings
from app.services.runtime_agent_resources import agent_resource_files

MODULE_LABELS = {"prompt": "提示词", "model": "模型", "memory": "记忆", "tools": "工具",
                 "skills": "技能", "knowledge": "知识", "context": "上下文"}
_SECRET_KEYS = {"apikey", "api_key", "password", "secret", "token", "credential", "credentials"}


def agent_invocation(agent_id: str, *, public: bool = True) -> dict[str, str]:
    """从正式 Agent 身份派生公开调用契约，供文档和执行包共用而不新增存储来源。"""

    if re.fullmatch(r"[a-z][a-z0-9_]*", agent_id) is None or agent_id == "runtime":
        raise ValueError("Agent 调用契约必须使用有效的正式 agentId。")
    return {
        "protocol": "ag-ui", "transport": "sse", "serviceId": "agent-runtime",
        "endpointId": f"agent.{agent_id}.run", "method": "POST",
        "path": f"/agents/{agent_id}/run", "exposure": "public" if public else "internal",
        "requestType": "RunAgentInput", "responseType": "BaseEvent",
        "principalSource": "runtime_anonymous_session",
    }


def agent_fact_digest(value: Any) -> str:
    """对正式模块事实生成稳定摘要，不把生成时间或执行状态当成业务输入。"""

    return "sha256:" + sha256(json.dumps(plain_json(value), ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()


def _reject_secrets(value: Any) -> None:
    """模型和凭据来自项目运行配置，不允许写入 Agent 正式产物。"""

    if isinstance(value, Mapping):
        if any(str(key).casefold() in _SECRET_KEYS for key in value):
            raise ValueError("Agent 合同不能包含模型或数据源凭据。")
        for item in value.values():
            _reject_secrets(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_secrets(item)


def validate_agent_interaction_contract(
    value: Any, *, agent_id: str, field_path: str,
) -> dict[str, Any]:
    """严格复用会话模型，将错误转换为带正式身份、候选路径及预期值的修复诊断。"""

    try:
        return AgentExecutionContract.model_validate(value).model_dump(mode="json", by_alias=True)
    except ValidationError as exc:
        expected: dict[tuple[str, ...], str] = {
            ("outcomes",): "必须为 []",
        }
        diagnostics: list[str] = []
        for error in exc.errors(include_url=False):
            location = tuple(error["loc"])
            # outcomes 子项报错仍定位到整个空数组约束，不能误导模型改成业务结果对象。
            if location and location[0] == "outcomes":
                location = ("outcomes",)
            current: Any = value
            path = field_path
            for part in location:
                path += f"[{part}]" if isinstance(part, int) else f".{part}"
                if isinstance(current, Mapping) and part in current:
                    current = current[part]
                elif isinstance(current, (list, tuple)) and isinstance(part, int) and 0 <= part < len(current):
                    current = current[part]
                else:
                    current = "（缺失）"
            rendered = json.dumps(plain_json(current), ensure_ascii=False, default=str)
            if len(rendered) > 200:
                rendered = rendered[:200] + "…"
            requirement = expected.get(location, str(error["msg"]))
            diagnostics.append(f"Agent {agent_id}：{path} 当前值 {rendered}；{requirement}。")
        raise ValueError(
            "\n".join(dict.fromkeys(diagnostics))
            + "\n用户文本通过 AG-UI messages 传递；普通文本回复由 textOutput=true 声明，"
            "不是结构化业务输入或 outcomes。仅局部修复上述字段，保留其他 Agent 配置。"
        ) from exc


def agent_contract_index(plan: Mapping[str, Any], *, require_runtime: bool = True) -> dict[str, dict]:
    """校验七模块完整性、页面和 Tool 的精确引用；无 Agent 的 Java 应用保持原流程。"""

    raw = plan.get("agent_contracts", ())
    if not isinstance(raw, (list, tuple)):
        raise ValueError("TechnicalPlan.agent_contracts 必须是数组。")
    if raw and require_runtime and not serves_python_business(plan):
        raise ValueError("Agent 七模块 DAG 必须绑定显式 Agent Runtime 服务图。")
    pages = {item["pageId"] for item in plan.get("pages", ()) if isinstance(item, Mapping) and item.get("pageId")}
    endpoints = {(contract.get("id"), endpoint.get("id"))
                 for contract in plan.get("api_contracts", ()) for endpoint in contract.get("endpoints", ())}
    result = {}
    for agent_position, item in enumerate(raw):
        contract = plain_json(item)
        if not isinstance(contract, dict) or set(contract) != {"agentId", "name", "purpose", "pageIds", "publicEndpoint", "agentSettings", "interactionContract"}:
            raise ValueError("Agent 合同必须包含 agentId/name/purpose/pageIds/publicEndpoint/agentSettings/interactionContract。")
        if type(contract["publicEndpoint"]) is not bool:
            raise ValueError("Agent publicEndpoint 必须显式声明为 boolean。")
        agent_id = contract["agentId"]
        if not isinstance(agent_id, str) or len(agent_id) > 128 or re.fullmatch(r"[a-z][a-z0-9_]*", agent_id) is None or agent_id == "runtime" or agent_id in result:
            raise ValueError("Agent 身份必须是唯一 lower_snake_case，且不能占用 runtime。")
        contract["interactionContract"] = validate_agent_interaction_contract(
            contract["interactionContract"], agent_id=agent_id,
            field_path=f"agent_contracts[{agent_position}].interactionContract",
        )
        if any(not isinstance(contract[key], str) or not contract[key].strip() for key in ("name", "purpose")):
            raise ValueError(f"Agent {agent_id} 缺少名称或业务职责。")
        bindings = contract["pageIds"]
        if (not isinstance(bindings, list) or any(not isinstance(key, str) for key in bindings)
                or len(set(bindings)) != len(bindings) or set(bindings) - pages):
            raise ValueError(f"Agent {agent_id} 的页面绑定必须唯一且引用存在的页面；后台 Agent 使用空数组。")
        if bindings and not contract["publicEndpoint"]:
            raise ValueError(f"Agent {agent_id} 有直接页面入口，必须启用 publicEndpoint。")
        settings = contract["agentSettings"]
        if (not isinstance(settings, dict) or set(settings) != set(AGENT_RUNTIME_MODULES)
                or any(not isinstance(settings[module], dict) for module in AGENT_RUNTIME_MODULES)):
            raise ValueError(f"Agent {agent_id} 必须提供七个完整的模块配置对象。")
        if not isinstance(settings["prompt"].get("systemPrompt"), str) or not settings["prompt"]["systemPrompt"].strip():
            raise ValueError(f"Agent {agent_id} 缺少正式 systemPrompt。")
        if settings["model"].get("source") != "project":
            raise ValueError(f"Agent {agent_id} 必须继承当前项目模型配置。")
        PromptSettings.model_validate(settings["prompt"])
        ProjectModelSettings.model_validate(settings["model"])
        for module in ("tools", "skills", "knowledge"):
            if not isinstance(settings[module].get("enabled"), bool):
                raise ValueError(f"Agent {agent_id} 的 {module}.enabled 必须显式声明。")
        tools = settings["tools"].get("bindings")
        if not isinstance(tools, list) or settings["tools"]["enabled"] != bool(tools):
            raise ValueError(f"Agent {agent_id} 的 tools.enabled 必须与 bindings 一致。")
        tool_ids = set()
        for tool in tools:
            if not isinstance(tool, dict) or set(tool) != {"toolId", "apiContractId", "endpointId"}:
                raise ValueError(f"Agent {agent_id} 的 Tool 必须精确引用一个当前 Endpoint。")
            tool_id = tool["toolId"]
            if not isinstance(tool_id, str) or re.fullmatch(r"[a-z][a-z0-9_]*", tool_id) is None or tool_id in tool_ids:
                raise ValueError(f"Agent {agent_id} 的 Tool 身份无效或重复。")
            if (not isinstance(tool["apiContractId"], str) or not isinstance(tool["endpointId"], str)
                    or (tool["apiContractId"], tool["endpointId"]) not in endpoints):
                raise ValueError(f"Agent {agent_id} 的 Tool 引用了不存在的 Endpoint。")
            if sum(key[1] == tool["endpointId"] for key in endpoints) != 1:
                raise ValueError(f"Agent {agent_id} 的 Tool Endpoint 在页面范围内存在身份歧义。")
            tool_ids.add(tool_id)
        _reject_secrets(settings)
        for module in ("skills", "knowledge"):
            agent_resource_files(module, settings[module])
        write_keys = {(api.get("id"), endpoint.get("id")) for api in plan.get("api_contracts", ())
            for endpoint in api.get("endpoints", ()) if endpoint.get("method") not in {"GET", "HEAD", "OPTIONS"}}
        if any((tool["apiContractId"], tool["endpointId"]) in write_keys for tool in tools) and not (
                set(contract["interactionContract"]["interactionKinds"]) & {"confirmation", "approval_required"}):
            affected = [f"{tool['toolId']} → {tool['apiContractId']}/{tool['endpointId']}"
                        for tool in tools if (tool["apiContractId"], tool["endpointId"]) in write_keys]
            kinds = list(contract["interactionContract"]["interactionKinds"])
            raise ValueError(
                f"Agent {agent_id} 的写入 Tool 必须显式声明 confirmation 或 approval_required，不能绕过执行前确认。"
                f"涉及 Tool：{'；'.join(affected)}。"
                f"请局部修改 agent_contracts[{agent_position}].interactionContract.interactionKinds："
                f"当前为 {json.dumps(kinds, ensure_ascii=False)}，可补充为 {json.dumps([*kinds, 'confirmation'], ensure_ascii=False)}；"
                "保留现有交互及 Tool 绑定，不要删除工具、禁用能力或篡改接口 method 来绕过校验。"
            )
        if any(endpoint.get("kind") == "agent_runtime" for api in plan.get("api_contracts", ())
               for endpoint in api.get("endpoints", ()) if (api.get("id"), endpoint.get("id")) in {
                   (tool["apiContractId"], tool["endpointId"]) for tool in tools}):
            raise ValueError(f"Agent {agent_id} 的 Tool 必须引用业务 Endpoint，不能递归绑定 Agent 运行入口。")
        result[agent_id] = deepcopy(contract)
    from app.services.runtime_agent_calls import validate_agent_calls
    validate_agent_calls(plan, result)
    return result


def agent_endpoint_keys(contract: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Tool 复用当前 Endpoint 的 ApplicationService，只为其正式实现 Unit 建立前置依赖。"""

    return tuple(dict.fromkeys((tool["apiContractId"], tool["endpointId"])
                              for tool in contract["agentSettings"]["tools"]["bindings"]))


def scoped_agent_ids(plan: Mapping[str, Any], target_type: str, target_id: str,
                     api_contract_id: str | None = None) -> tuple[str, ...]:
    """页面带入其绑定 Agent，业务 Endpoint 开发不反向扩大为全部 Agent 开发。"""

    contracts = agent_contract_index(plan)
    if target_type == "agent":
        if target_id not in contracts:
            raise ValueError(f"正式技术规划不存在智能体 {target_id}。")
        return (target_id,)
    if target_type == "application":
        return tuple(contracts)
    if target_type == "page":
        return tuple(key for key, contract in contracts.items() if target_id in contract["pageIds"])
    if target_type == "endpoint":
        matches = [endpoint for api in plan.get("api_contracts", ())
                   if api.get("id") == api_contract_id for endpoint in api.get("endpoints", ())
                   if endpoint.get("id") == target_id]
        if len(matches) != 1:
            raise ValueError("Endpoint 目标必须提供唯一正式复合身份。")
        from app.services.runtime_agent_calls import endpoint_agent_call
        call = endpoint_agent_call(matches[0])
        return (matches[0]["agent_id"],) if matches[0].get("kind") == "agent_runtime" else ((call["agentId"],) if call else ())
    return ()


def page_agent_endpoint_keys(plan: Mapping[str, Any], page_id: str) -> tuple[tuple[str, str], ...]:
    """返回当前页面的 Tool 实现依赖，不把它们当成页面直接调用的 API。"""

    return tuple(dict.fromkeys(key for contract in agent_contract_index(plan).values()
                               if page_id in contract["pageIds"] for key in agent_endpoint_keys(contract)))
