"""将全局目录生成上下文按身份、实体、业务 Contract 和页面分别裁剪。"""

from copy import deepcopy
import json

from app.agents.main.technical_plan_feedback import technical_plan_feedback_prompt
from app.agents.main.technical_plan_generation_prompts import (
    API_RECORD_IDENTITY_RULE, COMMON_RULES, ENTITY_DESCRIPTION_RULE, LIST_QUERY_RULE, RECORD_IDENTITY_RULE,
)
from app.agents.main.technical_plan_catalog_repair import business_slots


CONTRACT_BASE_PATH_RULE = (
    "Contract 的 base_path 必须以 /api/ 开头，并包含业务资源路径，例如 /api/products。\n"
)


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


def _registry_page_facts(page: dict) -> dict:
    """为全局目录保留页面身份、信息项标签及业务操作摘要。"""

    facts = _page_facts(page)
    actions = []
    for action in facts["actions"]:
        behavior = action["behavior"]
        summary = {key: action[key] for key in ("name", "description") if key in action}
        summary["type"] = behavior["type"]
        expected_result = behavior.get("expectedResult")
        if expected_result and expected_result != summary.get("description"):
            summary["expectedResult"] = expected_result
        if behavior["type"] == "sequence":
            summary["steps"] = [step["expectedResult"] for step in behavior["steps"] if step.get("expectedResult")]
        actions.append(summary)
    return {
        **{key: facts[key] for key in ("pageId", "name", "goal") if key in facts},
        "information_labels": list(dict.fromkeys(item["label"] for item in facts.get("information_items", []) if item.get("label"))),
        "actions": actions,
    }


def _prompt(stage: str, rules: str, context: dict, requirement: dict) -> str:
    """统一注入原始要求与当前片段的校验反馈。"""

    return (f"生成阶段：{stage}。\n" + COMMON_RULES + rules + "\n"
        + technical_plan_feedback_prompt(requirement)
        + json.dumps(context, ensure_ascii=False))


def _catalog_context(requirement: dict) -> dict:
    """提取全局实体和 Contract 分组共同使用的已确认业务事实。"""

    product = requirement["confirmed_product_plan"]
    return {
        "app": product.get("app", {}),
        "business_flows": product.get("business_flows", []),
        "acceptance": product.get("product_acceptance_criteria", []),
        "pages": [_registry_page_facts(page) for page in product.get("pages", [])],
    }


def relationships_prompt(requirement: dict) -> str:
    """根据业务用途确定实体身份和直接从属关系。"""

    rules = (
        "【1. 识别与命名实体】\n"
        "实体在全应用只定义一次。\n"
        f"{ENTITY_DESCRIPTION_RULE}\n"
        "【2. 确定实体从属关系】\n\n"
        "判断实体是否从属于其他实体，核心依据是**实际业务用途和使用场景**，而不是资源是否具有独立的维护入口。\n\n"
        "1. **识别实际使用场景**\n\n"
        "   从已确认的业务流程和页面操作中，识别资源的实际使用方式，例如：\n\n"
        "   - 主体在新增或编辑时，将资源作为复杂属性进行选择。\n"
        "   - 主体列表使用该资源进行分类、筛选或查询。\n"
        "   - 资源作为主体的扩展信息或辅助配置被使用。\n\n"
        "   需求中明确描述的实际使用方式，可以直接作为判断实体从属关系的依据。\n\n"
        "2. **判断是否仅服务于同一主体类型**\n\n"
        "   根据资源的实际使用者，判断其业务用途是否仅指向某一种实体类型：\n\n"
        "   - 如果资源的所有已确认用途都服务于同一种实体类型，且主要用于描述、扩展、分类或筛选该主体，则将其视为该主体的从属实体，将资源 ID 写入主体的 `owned_entity_ids`。\n"
        "   - 如果资源的已确认用途涉及不同实体类型，则视为跨主体共用，按独立实体处理，不归属于某一个主体。\n\n"
        "   注意：判断的是使用者的实体类型，而不是记录数量。同一类型的多条记录共用资源，仍然属于同一主体类型使用，不算跨主体共用。\n\n"
        "3. **区分业务归属与维护方式**\n\n"
        "   独立页面、独立 Contract，以及资源自身的增删改查，只能说明资源具有独立的维护方式，不能单独作为其业务独立的依据。\n\n"
        "   如果资源的业务用途始终依附于某一种主体，即使它拥有独立的维护页面和 CRUD 操作，仍应判定为该主体的从属实体。\n\n"
        "   只有当资源具有脱离该主体的独立业务用途，或已确认被不同实体类型共同使用时，才应按独立实体处理。\n\n"
        "4. **示例说明**\n\n"
        "   例如：系统支持多张工单选择同一个标签，工单列表也可以按照标签进行分类和筛选，同时标签还拥有独立的维护页面。\n\n"
        "   分析其实际用途：\n\n"
        "   - 标签仅用于工单的标记、分类和筛选。\n"
        "   - 所有已确认的使用场景都指向 `ticket` 这一实体类型。\n"
        "   - 标签的独立维护页面仅体现维护方式，不代表其具有独立的业务用途。\n\n"
        "   因此，`ticket_tag` 应归属于 `ticket`，输出结果为：\n\n"
        "   - `ticket` 的 `owned_entity_ids`：`[\"ticket_tag\"]`\n"
        "   - `ticket_tag` 的 `owned_entity_ids`：`[]`\n\n"
        "5. **输出**\n\n"
        "   对照实体描述、业务流程和页面操作，检查从属关系是否与实际使用场景一致。\n\n"
        "   - 能够从已确认的实际用途判断归属关系时，应填写主体的 `owned_entity_ids`，不要因为资源具有独立维护页面而遗漏从属关系。\n"
        "   - 每个实体的 `owned_entity_ids` 只填写本次实体目录中直接从属于它的实体 ID，不填写间接从属实体。\n"
        "   - 没有直接从属实体时，填写 `[]`。\n\n"
        "只返回 {entities:[{id,name,description,owned_entity_ids}]}。纯静态应用可以返回空数组。"
    )
    return _prompt("catalog_relationships（实体身份与从属关系）", rules, _catalog_context(requirement), requirement)


def registry_prompt(requirement: dict, entities: list[dict]) -> str:
    """使用已确定的实体生成全应用业务 Contract 分组。"""

    context = {**_catalog_context(requirement), "entities": entities}
    rules = (
        "【1. 确定每个动作管理的对象】\n"
        "entities 是前一阶段确定的实体身份和从属关系，本次直接使用其中的实体 ID。\n"
        "先确定操作所属的业务主体。引用关联实体不改变操作主体，例如订单引用商品不代表订单与商品必须合为一组。\n"
        "已确认的普通 business 动作不能改成 sequence 或新增步骤。页面刷新可复用多个现有读取能力，"
        "不要仅为刷新关联列表重复创建业务接口。\n\n"
        "【2. 归并操作并命名 Contract】\n"
        "Contract 按业务实体或业务聚合划分，分组名称体现业务主体。将该主体的新增、列表查询、详情查询、修改、删除"
        "及其他业务操作归入同一 Contract。例如新增商品和查询商品详情都属于“商品”分组，不要拆成“商品新增”和“商品查询”。\n"
        "不能按操作类型、读写差异、页面或生成批次拆分同一主体。内容较多时由平台在同一 Contract 内分批生成。\n\n"
        "【3. 确定 Contract 基础路径】\n"
        f"{CONTRACT_BASE_PATH_RULE}\n"
        "【4. 汇总页面并输出目录】\n"
        "Contract 可供多页共享。"
        "page_ids 汇总该主体操作涉及的页面，page_ids 非空。"
        "entity_ids 汇总该能力涉及的实体，若能力不涉及实体，则将 entity_ids 填为 []。"
        "所有业务动作所在页面必须被相应 Contract 覆盖。\n"
        "只返回 {contracts:[{id,name,entity_ids,base_path,page_ids}]}。纯静态应用可以返回空数组。"
    )
    return _prompt("catalog_registry（全局 Contract 分组）", rules, context, requirement)


def entity_prompt(requirement: dict, registry: dict, identity: dict, *, owned_entities: list[dict] | None = None) -> str:
    """只展开一个实体，读取相关业务页面和已生成的直接从属实体字段。"""

    page_ids = {
        page_id
        for contract in registry["contracts"]
        if identity["id"] in contract["entity_ids"]
        for page_id in contract["page_ids"]
    }
    rules = (
        "【1. 生成业务字段】\n"
        "只定义当前实体的完整业务语义字段，不重新声明其他实体，不凭空增加产品没有的枚举值。\n"
        "目录的 owned_entity_ids 表示业务从属。根据业务操作确定哪个实体记录关联，以及是否需要对应字段。\n"
        "owned_entities 提供已生成的直接从属实体及其字段，供本轮只读参考。"
        "当前实体确实需要保存从属实体的记录标识时，使用该实体已声明的唯一标识及其字段类型。\n\n"
        "【2. 确定唯一标识】\n"
        f"{RECORD_IDENTITY_RULE}\n"
        "【3. 命名和输出】\n"
        "字段名使用 snake_case。实体自身的属性直接按属性含义命名，不重复当前实体的名称或 ID。"
        "例如，商品分类自身的编码用 code，不用 category_code；引用其他实体的字段可保留用于区分关联对象的前缀。\n"
        "type 为 text/long_text/number/decimal/date/datetime/enum/boolean。\n"
        "这里是实体语义字段，不是 API JSON Schema。数量、库存等整数使用 type=number，正整数、非负等约束写入 description。"
        "不得输出 API JSON Schema 的 integer、minimum、maximum 或 properties。\n"
        "只返回 {fields:[{name,label,description,type,required}]}，枚举字段增加非空 enum_values。"
    )
    context = {
        "entity": identity,
        "registry": registry["entities"],
        "owned_entities": owned_entities or [],
        "pages": [
            _page_facts(page)
            for page in requirement["confirmed_product_plan"].get("pages", [])
            if page["pageId"] in page_ids
        ],
    }
    return _prompt("catalog_entity（单实体字段）", rules, context, requirement)


def endpoints_prompt(requirement: dict, identity: dict, entities: list, completed: list, *, registry: dict) -> str:
    """只规划目标业务能力接口，同时提供已冻结全局路由防止重复创建。"""

    rules = (
        "【1. 确定接口范围】\n"
        "本次只生成 contract 指定的业务实体或聚合的接口。entities 提供该 Contract 关联的实体及已生成字段。\n"
        "用户原始要求描述整个应用。其他业务主体的接口由各自的 Contract 生成。\n\n"
        "【2. 覆盖业务操作并复用接口】\n"
        "检查 pages，完整覆盖当前主体的页面读取、business 动作及 sequence 中的 business 步骤，兼顾所需的读取与写入。\n"
        "接口可跨页复用。普通 business 动作只使用一个主要业务接口，不能虚构步骤。\n"
        "页面刷新涉及主体和关联列表时，复用已有读取接口，后续页面阶段通过 endpoint_dependencies 的 trigger 声明完整刷新。\n"
        "现有接口确实缺少业务操作或所需数据时才补齐能力，不为解决映射格式错误重复创建接口。\n\n"
        f"{LIST_QUERY_RULE}"
        "在 summary 中写明列表是否分页。分页参数和响应 Schema 由契约阶段生成。\n\n"
        "【3. 确定请求方法、路径和记录标识】\n"
        "GET 只能读取，创建不能代替更新已有记录。\n"
        "path 输出以 /api/ 开头的完整接口路径，沿用 base_path 中已有的 /api 前缀，避免生成 /api/api/。"
        "例如 base_path 为 /api/products 时，列表用 /api/products，详情可用 /api/products/{code}。\n"
        "路径必须位于当前 base_path 的完整路径段范围内，"
        "不能占用 other_contracts 中更具体的路径范围。\n"
        f"{API_RECORD_IDENTITY_RULE}\n"
        "【4. 复核并输出接口目录】\n"
        "Endpoint ID 全局唯一，推荐使用 ContractId.能力名。禁止使用其他 Contract 的 ID 命名空间。\n"
        "对照 reserved_endpoints 检查冲突，同一 method/path 只定义一次，不能重复创建已冻结接口。\n"
        "只返回 {endpoints:[{id,name,method,path,summary}]}，不返回 Schema、参数、Contract 身份或动作绑定。"
    )
    context = {
        "contract": identity,
        "entities": [entity for entity in entities if entity["id"] in identity["entity_ids"]],
        "other_contracts": [
            {"id": item["id"], "name": item["name"], "base_path": item["base_path"]}
            for item in registry["contracts"]
            if item["id"] != identity["id"]
        ],
        "pages": [
            _page_facts(page)
            for page in requirement["confirmed_product_plan"].get("pages", [])
            if page["pageId"] in identity["page_ids"]
        ],
        "reserved_endpoints": [
            {"contractId": contract["id"], "page_ids": contract["page_ids"], **endpoint}
            for contract in completed
            for endpoint in contract["endpoints"]
        ],
    }
    return _prompt("catalog_endpoints（单 Contract 接口目录）", rules, context, requirement)


def bindings_prompt(requirement: dict, page: dict, catalog: dict) -> str:
    """根据已确认业务动作和接口目录生成单页动作映射。"""

    return _prompt("catalog_bindings（单页动作映射）", "只返回 {action_bindings:[{pageId,actionId,stepId,endpointId}]}。"
        "完整且仅覆盖当前页面每个 business action 以及 sequence 的每个 business step，直接 action 的 stepId=null。"
        "navigation/interface/external 不生成映射。同一槽位恰好映射一个现有接口，允许多个槽位复用同一接口。"
        "required_slots 是已确认产品定义的完整合法槽位，pageId/actionId/stepId 必须逐字一致，不能增删或虚构步骤。"
        "一个普通 business 动作即使刷新多类数据，也仍只有一个 stepId=null 的槽位，绑定主体的主要接口；"
        "相关数据已有读取接口时，后续页面阶段在 endpoint_dependencies 中声明这些读取及刷新 trigger，"
        "不能把页面重读虚构成新的业务步骤，也不能声称主要接口会返回其他接口的数据。"
        "不能修改已确认 ProductPlan。"
        "只输出本页动作映射，不输出审查报告或其他章节。",
        {"page": _page_facts(page),
            "required_slots": [{"pageId": page_id, "actionId": action_id, "stepId": step_id}
                for page_id, action_id, step_id in sorted(business_slots({"pages": [page]}), key=str)],
            "endpoints": [{"contractId": contract["id"], **endpoint}
            for contract in catalog["contracts"] if page["pageId"] in contract["page_ids"] for endpoint in contract["endpoints"]]}, requirement)
