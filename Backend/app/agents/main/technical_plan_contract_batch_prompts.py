"""大 Contract 的内部 Schema 目录、字段组和接口批次提示词。"""

import json

from app.agents.main.technical_plan_feedback import technical_plan_feedback_prompt
from app.agents.main.technical_plan_generation_prompts import API_SCHEMA_RULES, COMMON_RULES
from app.agents.main.technical_plan_contract_batch_validation import schema_refs


def _context(requirement: dict, catalog: dict, spec: dict, endpoint_ids: set | None = None) -> str:
    """只投射本业务 Contract 所需实体、产品事实和公开登录能力。"""

    pages = [{key: page[key] for key in ("pageId", "goal", "information_items", "actions") if key in page}
        for page in requirement["confirmed_product_plan"].get("pages", []) if page["pageId"] in spec["page_ids"]]
    if endpoint_ids is not None:
        # 接口批次只读取关联动作，防止模型被整页动作诱导重新展开整个 Contract。
        slots = {(binding["pageId"], binding["actionId"]) for binding in catalog["action_bindings"]
            if binding["endpointId"] in endpoint_ids}
        pages = [{**page, "actions": [action for action in page.get("actions", [])
            if (page["pageId"], action["actionId"]) in slots]} for page in pages]
    return COMMON_RULES + API_SCHEMA_RULES + "\n" + json.dumps({
        "contract": {key: spec[key] for key in ("id", "name", "entity_ids", "base_path")},
        "entities": [entity for entity in catalog["entities"] if entity["id"] in spec["entity_ids"]],
        "pages": pages, "authentication_enabled": bool((requirement.get("application_config") or {}).get("auth", {}).get("enable")),
    }, ensure_ascii=False) + "\n" + technical_plan_feedback_prompt(requirement)


def schema_plan_prompt(requirement: dict, catalog: dict, spec: dict) -> str:
    """先固定共享 Schema 名称和字段所有权，目录不展开每个属性的定义。"""

    return ("生成阶段：contract_schema_plan（同一 Contract 的内部 Schema 目录）。\n"
        + _context(requirement, catalog, spec)
        + "只返回 {authentication:{required:boolean},schemas:[{name,base,fields,required,references}],"
        "endpoint_refs:[{endpointId,request_schema_ref,response_schema_ref}]}。"
        "name 使用字母开头的字母数字下划线。同一个 Schema 只声明一次，多个接口共享同一份定义。"
        "base 是 JSON Schema 根属性，不得包含 properties/required；对象 base.type=object，fields 只列完整属性名，"
        "required 列必填属性名；属性定义将在后续字段批次生成。references 列该 Schema 的所有裸 $ref 名。"
        "references 必须包含未来字段内的嵌套引用，不能只检查 base；例如分页 list.items 引用 WorkOrder，"
        "分页 Schema 的 references 就必须包含 WorkOrder，不能因为 base 只有 type=object 而填空数组。"
        "复杂嵌套对象提取为另一个具名 Schema 并通过裸 $ref 引用，避免一个属性内展开巨大对象。"
        "实体响应以已声明字段为准，不自动补充未声明的创建时间、更新时间或操作者等审计字段。"
        "响应必须覆盖 ProductPlan information_items 要展示的数据。关联记录的编号、名称等展示投影可以是只读传输字段，"
        "不必伪造成当前实体字段；页面要展示名称时，只有关联 id 不足以完成展示。"
        "应在当前读取响应中提供必要展示字段，或明确复用页面初始加载的其他读取；弹窗选择接口不能冒充初始展示数据来源。"
        "记录操作参数按业务要求使用内部 id、业务编号或上游已提供的身份。"
        "需要从列表选择记录时，Schema 应提供对应身份；不要仅凭路径参数名强制为所有查询添加 id。"
        "非对象 Schema 在 base 中给出完整定义（如 array 的 items），fields/required 为空。"
        "所有引用只能使用本目录的裸 Schema 名，禁止跨 Contract。"
        "endpoint_refs 完整且仅覆盖下面接口；没有请求体用 null，非 DELETE 必须有响应。"
        "分页响应包含 total/pageSize/current/list 四个同级必填字段，前三者 integer，list 为 array。"
        "对象目录的结构示例：{\"name\":\"Item\",\"base\":{\"type\":\"object\"},"
        "\"fields\":[\"id\"],\"required\":[\"id\"],\"references\":[]}；"
        "fields/required/references 与 base 同级，base 内不得出现 properties 或 required。"
        "不要返回接口参数或完整 Contract。\n冻结接口目录：" + json.dumps(spec["endpoints"], ensure_ascii=False)
        + "\n以上是只读输入。只输出 authentication、schemas、endpoint_refs 三个根键的目录对象；不展开任何属性定义。")


def schema_fields_prompt(requirement: dict, catalog: dict, spec: dict, plan: dict, schema: dict, fields: list[str]) -> str:
    """为一个对象生成指定属性组，共享引用和必填性均由目录固定。"""

    # 引用目录是唯一合法来源；显式列举可选项，避免模型为常见时间字段猜测实体映射。
    allowed_refs = [entity["id"] + "." + field for entity in catalog["entities"] if entity["id"] in spec["entity_ids"]
        for field in dict.fromkeys(["id", *[item["name"] for item in entity["fields"]]])]
    return ("生成阶段：contract_schema_fields（同一 Schema 的字段组）。\n" + _context(requirement, catalog, spec)
        + "只返回 {properties:{当前字段名:JSON Schema定义}}，完整且仅包含当前字段组。"
        "必须保留最外层 properties 键，不得把字段字典直接作为根对象。"
        "直接取自实体的属性必须带 entity_field_ref=EntityId.field_name，说明使用中文。"
        "entity_field_ref 只能从 allowed_entity_field_refs 原样选择，不得用字段名拼接或猜测引用。"
        "记录 id 必须标注实际所属实体的 EntityId.id，同一 Contract 内的其他实体 id 不能代替当前记录身份。"
        "只有真实的计算、聚合、分页或传输元信息可以省略 entity_field_ref；不能为了绑定 Schema 字段而虚构实体字段。"
        "如果校验反馈引用不存在，先按允许目录核对来源；无实体来源的传输元信息应省略该键，禁止重复输出错误引用。"
        "使用注册的裸 $ref 引用复杂对象，不重复展开共享定义，不更改字段名和必填目录。\n"
        "schema_ref_examples 给出本 Contract 所有合法引用的完整 JSON 写法，必须逐字选用其中的 $ref 值。"
        "例如注册 WorkOrder 时必须写 {\"$ref\":\"WorkOrder\"}，禁止写 "
        "{\"$ref\":\"#/components/schemas/WorkOrder\"} 或 #/definitions/WorkOrder。\n"
        "当前 schema.references 是冻结的直接引用清单；若字段确实需要本 Contract 已注册但清单遗漏的共享对象，"
        "仍输出真实的裸 $ref，平台会拒绝当前字段并交回 Schema 目录重建；不得内联共享对象绕过目录。\n"
        + json.dumps({"schema": schema, "fields": fields, "allowed_entity_field_refs": allowed_refs,
            "schema_ref_examples": [{"$ref": item["name"]} for item in plan["schemas"]],
            "registry": [{"name": item["name"], "base": item["base"], "fields": item["fields"]} for item in plan["schemas"]]}, ensure_ascii=False)
        + "\n以上是只读输入。响应根对象必须且只能包含 properties，不得回传 schema、fields 或 registry。")


def endpoint_batch_prompt(requirement: dict, catalog: dict, spec: dict, plan: dict, schemas: dict, endpoints: list) -> str:
    """只展开当前接口及其 Schema 引用闭包，禁止重复生成或覆盖 Schema。"""

    ids = {endpoint["id"] for endpoint in endpoints}
    refs = [item for item in plan["endpoint_refs"] if item["endpointId"] in ids]
    needed = {item[key] for item in refs for key in ("request_schema_ref", "response_schema_ref") if item[key]}
    pending = list(needed)
    while pending:
        name = pending.pop()
        for child in schema_refs(schemas[name]) - needed:
            needed.add(child)
            pending.append(child)
    boundary = (f"本次只生成 {len(endpoints)} 个接口，返回的接口 ID 集合必须恰好为："
        + json.dumps([endpoint["id"] for endpoint in endpoints], ensure_ascii=False) + "。\n")
    return ("生成阶段：contract_endpoints（同一 Contract 的接口批次）。\n" + boundary
        + _context(requirement, catalog, spec, ids)
        + "只返回 {endpoints:[当前批次完整接口]}，不得返回 schemas 或其他章节。"
        "每个接口完整包含 id,name,method,path,summary,parameters,request_schema_ref,response_schema_ref,error_codes,authentication。"
        "error_codes 为字符串数组，例如 [\"INVALID_STATE\",\"NOT_FOUND\"]，不能返回 code/message 对象数组。"
        "保留冻结 id/name/method/path 和 Schema 引用；parameters 每项包含 name,in,required,schema，路径占位参数必须声明。"
        "分页接口使用 current/pageSize 查询参数，不能新增分页业务事实。authentication 只含 required:boolean。\n"
        + boundary
        + "只读输入（不是响应模板）：\n"
        + json.dumps({"endpoints": endpoints, "endpoint_refs": refs, "authentication": plan["authentication"],
            "schemas": {name: schemas[name] for name in schemas if name in needed}}, ensure_ascii=False)
        + "\n以上目录和 Schema 仅用于读取，严禁原样回传。响应根必须且只能为 {\"endpoints\":[当前批次完整接口]}。"
        "根对象不得包含 schemas、endpoint_refs、authentication 或 api_contracts；"
        "authentication 只放在每个接口内部，Schema 通过已冻结的裸名称引用。\n" + boundary)
