"""在当前 Runtime 合同上重编译可编辑配置，不引入旧 Gateway 派生合同。"""

from copy import deepcopy
from math import isfinite
from hashlib import sha256
from typing import Any

from app.services.runtime_agent_contracts import agent_contract_index


def validate_technical_plan_agent_contracts(plan: dict[str, Any], product: dict[str, Any]) -> list[str]:
    """核验技术智能体与已确认产品入口，防止遗漏、增生或入口漂移。"""

    try:
        contracts = agent_contract_index(plan, require_runtime=False)
        expected = {
            item["agentId"]: item for item in product.get("agents", [])
            if any(binding.get("surface", {}).get("enabled") is True
                   for binding in item.get("pageActionBindings", []))
        }
        if set(contracts) != set(expected):
            return ["TechnicalPlan.agent_contracts 必须覆盖且仅覆盖 ProductPlan 已启用的智能体。"]
        for agent_id, contract in contracts.items():
            item = expected[agent_id]
            page_ids = [binding["pageId"] for binding in item["pageActionBindings"]
                        if binding["surface"]["enabled"]]
            if (contract["name"] != item["name"] or contract["purpose"] != item["purpose"]
                    or contract["pageIds"] != page_ids):
                return [f"智能体 {agent_id} 的身份、职责和页面入口必须保持已确认产品事实。"]
    except (KeyError, TypeError, ValueError) as exc:
        return [f"智能体正式合同无效：{exc}"]
    return []


def recompile_technical_plan_agent_settings(
    plan: dict[str, Any], product: dict[str, Any], *, agent_id: str,
    prompt: dict[str, Any] | None, temperature: float | None,
) -> dict[str, Any]:
    """只修改所选智能体的 Prompt 和 Temperature，其余模块与工程合同原样保留。"""

    result = deepcopy(plan)
    contracts = agent_contract_index(result)
    if agent_id not in contracts:
        raise ValueError("当前技术规划不存在所选智能体。")
    settings = next(item["agentSettings"] for item in result["agent_contracts"] if item["agentId"] == agent_id)
    if prompt is not None:
        editable = {key: deepcopy(prompt[key]) for key in ("systemPrompt", "persona", "constraints") if key in prompt}
        settings["prompt"] = {**settings["prompt"], **editable}
    if temperature is not None:
        if not isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("Temperature 必须在 0 到 2 之间。")
        settings["model"].setdefault("generation", {})["temperature"] = temperature
    errors = validate_technical_plan_agent_contracts(result, product)
    if errors:
        raise ValueError("；".join(errors))
    return result


def synchronize_settings_markdown(plan: dict[str, Any], markdown: str,
                                  product: dict[str, Any], agent_id: str, *,
                                  generated_markdown_sha256: str) -> dict[str, Any]:
    """同步实际编辑过的 Markdown，只采用目标配置字段并保留所有隐藏工程细节。"""

    from app.agents.main.document_sync import _invoke_sync_model
    # JSON 保存会排序键并切换草稿状态；用原生成正文摘要识别编辑，避免误触发模型调用。
    if sha256(markdown.encode("utf-8")).hexdigest() == generated_markdown_sha256:
        return plan
    candidate = _invoke_sync_model(artifact_name="TechnicalPlan", structured_document=plan,
                                   edited_markdown=markdown, datasource_type="database")
    for field in ("architecture", "entities", "api_contracts", "pages"):
        if field in candidate and candidate[field] != plan.get(field):
            raise ValueError("智能体配置修订不能同时修改其他技术规划章节，请使用正式规划修订入口。")
    original = agent_contract_index(plan)
    proposed = agent_contract_index({**plan, "agent_contracts": candidate.get("agent_contracts")})
    if set(original) != set(proposed) or any(original[key] != proposed[key] for key in original if key != agent_id):
        raise ValueError("智能体配置修订不能改变其他智能体。")
    old = original[agent_id]
    new = proposed[agent_id]
    if any(old[key] != new[key] for key in old if key != "agentSettings"):
        raise ValueError("智能体配置修订不能改变产品身份或入口。")
    for module in ("memory", "tools", "skills", "knowledge", "context"):
        if old["agentSettings"][module] != new["agentSettings"][module]:
            raise ValueError("此配置入口仅支持修改 Prompt 和 Temperature。")
    if ({key: value for key, value in old["agentSettings"]["model"].items() if key != "generation"}
            != {key: value for key, value in new["agentSettings"]["model"].items() if key != "generation"}):
        raise ValueError("模型来源必须保持当前项目配置。")
    return recompile_technical_plan_agent_settings(plan, product, agent_id=agent_id,
        prompt=new["agentSettings"]["prompt"],
        temperature=new["agentSettings"]["model"].get("generation", {}).get("temperature"))
