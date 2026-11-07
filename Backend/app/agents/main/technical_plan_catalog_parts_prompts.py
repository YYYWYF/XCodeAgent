"""将全局目录生成上下文按身份、实体、业务 Contract 和页面分别裁剪。"""

from copy import deepcopy
import json

from app.agents.main.technical_plan_feedback import technical_plan_feedback_prompt
from app.agents.main.technical_plan_generation_prompts import COMMON_RULES
from app.agents.main.technical_plan_catalog_parts_validation import catalog_registry
from app.agents.main.technical_plan_catalog_repair import business_slots


def _page_facts(page: dict) -> dict:
    """保留页面业务事实，避免把导航、控件和本地交互带入目录决策。"""

    result = {key: deepcopy(page[key]) for key in ("pageId", "module_id", "name", "goal", "information_items") if key in page}
    actions = []
    for action in page.get("actions", []):
        behavior = action.get("behavior", {})
        if behavior.get("type") not in {"business", "sequence"}:
            continue
        item = {key: deepcopy(action[key]) for key in ("actionId", "name", "description", "behavior") if key in action}
        if behavior.get("type") == "sequence":
            item["behavior"]["steps"] = [step for step in item["behavior"].get("steps", []) if step.get("type") == "business"]
            if not item["behavior"]["steps"]:
                continue
        actions.append(item)
    return {**result, "actions": actions}


def _prompt(stage: str, rules: str, context: dict, requirement: dict, repair: dict | None = None) -> str:
    """统一注入原始要求，缺口诊断仅传给本轮需要修复的片段。"""

    return (f"生成阶段：{stage}。\n" + COMMON_RULES + rules + "\n"
        + technical_plan_feedback_prompt(requirement)
        + ("共享目录修复任务：仅调整报告缺口所需的事实，未受影响身份与定义保持原样。\n" if repair else "")
        + json.dumps({**context, **({"repair": repair} if repair else {})}, ensure_ascii=False))


def registry_prompt(requirement: dict, repair: dict | None) -> str:
    """全应用只确定轻量身份与业务能力划分，不展开字段、接口及动作映射。"""

    product = requirement["confirmed_product_plan"]
    context = {"app": product.get("app", {}), "business_flows": product.get("business_flows", []),
        "acceptance": product.get("product_acceptance_criteria", []),
        "pages": [_page_facts(page) for page in product.get("pages", [])]}
    if repair:
        context["previous_registry"] = catalog_registry(repair["catalog"])
    return _prompt("catalog_registry（全局身份）", "只返回 {entities:[{id,name,description}],"
        "contracts:[{id,name,entity_ids,base_path,page_ids}]}。实体在全应用只定义一次；Contract 按业务能力划分，"
        "可供多页共享，entity_ids/page_ids 非空。所有业务动作所在页面必须被相应 Contract 覆盖。"
        "已确认的普通 business 动作不能改成 sequence 或新增步骤。页面刷新可复用多个现有读取能力，"
        "不要仅为刷新关联列表重复创建业务接口。"
        "不返回实体字段、接口或动作绑定。纯静态应用可以返回两个空数组。", context, requirement,
        {"issue": repair["issue"]} if repair else None)


def entity_prompt(requirement: dict, registry: dict, identity: dict, repair: dict | None) -> str:
    """只展开一个共享实体，读取消费它的业务页面和其他实体的轻量身份。"""

    page_ids = {page_id for contract in registry["contracts"] if identity["id"] in contract["entity_ids"] for page_id in contract["page_ids"]}
    return _prompt("catalog_entity（单实体字段）", "只返回 {fields:[{name,label,description,type,required}]}，"
        "枚举字段增加非空 enum_values。字段名 snake_case；type 为 text/long_text/number/decimal/date/datetime/enum/boolean。"
        "这里是实体语义字段，不是 API JSON Schema。数量、库存等整数使用 type=number，"
        "正整数、非负等约束写入 description；不得输出 integer、minimum、maximum 或 properties。"
        "只定义当前实体的完整业务语义字段，不重新声明其他实体，不凭空增加产品没有的枚举值。",
        {"entity": identity, "registry": registry["entities"], "pages": [_page_facts(page)
            for page in requirement["confirmed_product_plan"].get("pages", []) if page["pageId"] in page_ids]}, requirement, repair)


def endpoints_prompt(requirement: dict, identity: dict, entities: list, completed: list, repair: dict | None, *, registry: dict) -> str:
    """只规划目标业务能力接口，同时提供已冻结全局路由防止重复创建。"""

    return _prompt("catalog_endpoints（单 Contract 接口目录）", "只返回 {endpoints:[{id,name,method,path,summary}]}。"
        "完整覆盖当前 Contract 的页面读取、business 动作及 sequence 的 business 步骤，接口可跨页复用。"
        "不返回 Schema、参数、Contract 身份或动作绑定。用户原始要求描述整个应用，不代表本次需要返回全应用接口。"
        "本次只生成 contract 指定的业务能力，其他 Contract 由独立任务生成；不得把其他能力的接口一起输出。"
        "Endpoint 路径必须位于当前 base_path 的完整路径段范围内，不能占用其他 Contract 更具体的路径范围。"
        "Endpoint ID 全局唯一，推荐使用 ContractId.能力名；禁止使用其他 Contract 的 ID 命名空间。"
        "同一 method/path 只定义一次，不能重复创建已冻结接口。GET 只能读取，创建不能代替更新已有记录。"
        "普通 business 动作只绑定一个主要业务接口，不能虚构步骤。页面刷新涉及主体和关联列表时，"
        "复用已有的真实读取接口，后续页面阶段通过 endpoint_dependencies 的 trigger 声明完整刷新。"
        "只有现有接口确实缺少业务操作或所需数据时才补齐能力，不为解决映射格式错误重复造接口。",
        {"contract": identity, "entities": [entity for entity in entities if entity["id"] in identity["entity_ids"]],
            "other_contracts": [{"id": item["id"], "name": item["name"], "base_path": item["base_path"]}
                for item in registry["contracts"] if item["id"] != identity["id"]],
            "pages": [_page_facts(page) for page in requirement["confirmed_product_plan"].get("pages", []) if page["pageId"] in identity["page_ids"]],
            "reserved_endpoints": [{"contractId": contract["id"], "page_ids": contract["page_ids"], **endpoint}
                for contract in completed for endpoint in contract["endpoints"]]}, requirement, repair)


def bindings_prompt(requirement: dict, page: dict, catalog: dict, repair: dict | None) -> str:
    """逐页核对真实接口能力，输出业务槽位映射或精确缺口报告。"""

    return _prompt("catalog_bindings（单页动作映射）", "只返回 {action_bindings:[{pageId,actionId,stepId,endpointId}]}。"
        "完整且仅覆盖当前页面每个 business action 以及 sequence 的每个 business step，直接 action 的 stepId=null。"
        "navigation/interface/external 不生成映射。同一槽位恰好映射一个现有接口，允许多个槽位复用同一接口。"
        "required_slots 是已确认产品定义的完整合法槽位，pageId/actionId/stepId 必须逐字一致，不能增删或虚构步骤。"
        "先核对 method/path/summary 是否真正满足业务结果，GET 不能实现写入，创建不能实现更新。"
        "一个普通 business 动作即使刷新多类数据，也仍只有一个 stepId=null 的槽位，绑定主体的主要接口；"
        "相关数据已有读取接口时，后续页面阶段在 endpoint_dependencies 中声明这些读取及刷新 trigger，"
        "不能把页面重读虚构成新的业务步骤，也不能声称主要接口会返回其他接口的数据。"
        "仅当真实业务操作或所需读取数据缺失时，报告原动作的 business_action 缺口，stepId=null。"
        "不能修改已确认 ProductPlan。"
        "接口能力不足时只返回 {catalog_issue:{pageId,kind,actionId,stepId,reason}}，kind=business_action 或 initial_load。"
        "业务缺口必须引用本页真实业务槽位；首次加载缺口 actionId/stepId=null；reason 使用中文。禁止虚构接口或强行匹配。",
        {"page": _page_facts(page),
            "required_slots": [{"pageId": page_id, "actionId": action_id, "stepId": step_id}
                for page_id, action_id, step_id in sorted(business_slots({"pages": [page]}), key=str)],
            "endpoints": [{"contractId": contract["id"], **endpoint}
            for contract in catalog["contracts"] if page["pageId"] in contract["page_ids"] for endpoint in contract["endpoints"]]}, requirement, repair)
