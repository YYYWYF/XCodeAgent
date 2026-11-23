"""按单个接口契约和单个页面裁剪技术规划生成上下文，共享阶段规则供目录复用。"""

import json
from typing import Any

from app.agents.main.technical_plan_feedback import technical_plan_feedback_prompt
from app.agents.main.technical_plan_catalog_repair import business_slots
from app.services.api_contracts import response_field_paths


ENTITY_DESCRIPTION_RULE = "实体 description 用一句简洁的话说明实体是什么、用于什么，避免列举或复述字段。\n"

RECORD_IDENTITY_RULE = (
    "1）先为当前实体选择合适的单字段业务标识。已有业务标识时直接沿用。需求未明确指定时，"
    "可以根据字段的业务用途选定一个稳定、每条记录都有值且适合在当前实体内保持唯一的字段，设为必填，并在 label 末尾加「（唯一标识）」。\n"
    "2）没有合适的单字段业务标识时，补充一个整数记录标识id。"
    "字段使用 name=id、label=记录ID（唯一标识）、type=number、required=true，description 写明它是整数记录标识。\n"
)

COMMON_RULES = (
    "只返回本阶段要求的一个完整 JSON 根对象，不返回 Markdown、说明或其他章节。"
    "所有用户可见名称、说明和 trigger 使用简体中文；ID、字段名、路径和类型保留技术标识。"
    "只实现已确认 ProductPlan 的业务事实，不新增业务需求。"
    "后端固定 Java8 + Springboot，数据库 MySQL8，缓存 Redis。"
    "实体只定义业务语义，不输出 data_source、data_sources、物理来源、字段映射或 SQL。"
    "不输出 authorization_manifest、角色、资源键、策略或权限绑定，平台稍后统一编译。"
    "页面布局、导航、本地交互和产品状态由上游决定，不在技术规划重复设计。\n"
)

API_RECORD_IDENTITY_RULE = (
    "操作单条实体记录时，使用该实体已声明的唯一标识，也就是 label 标注「（唯一标识）」的字段；"
    "只有业务明确使用外部标识时才使用对应的外部字段。路径参数、请求和响应中传递同一标识时，"
    "沿用其实际字段名（按 API 的 lowerCamelCase 转换）和一致的 JSON 类型，文本用 string，整数用 integer。"
    "例如唯一标识是文本 code，路径占位符用 {code}，需要返回记录标识时返回 code，并引用实际的实体 code 字段。"
    "实体未声明 id 时，不得用 id 代称已有的唯一标识，不得在路径、参数或 Schema 中补造该实体的 id。\n"
)

API_SCHEMA_RULES = (
    "Schema 只描述接口实际接收或返回的数据及约束。\n"
    "Schema 的 properties 属性名统一使用 lowerCamelCase。\n"
    "Schema 中，业务明确为正整数的数量必须使用 type=integer 且 minimum=1；"
    "明确非负的数值必须包含 minimum=0，不能只把约束写在 description 中。\n"
    "【请求必填约束】\n"
    "required 只约束请求参数和请求体。请求体的 required 数组使用 properties 中对应的字段名。"
    "响应 Schema 按业务要求定义 properties，并省略所有层级的 required，包括嵌套对象和数组元素。"
    "同一对象用于请求和响应时，如果请求需要 required，以请求为准。\n"
)

LIST_QUERY_RULE = (
    "【列表查询】\n"
    "用户没有明确要求不分页时，列表查询默认分页。\n"
    "  - 分页查询参数为 current 和 pageSize，默认值分别为 1 和 20。\n"
    "  - 分页响应采用通用泛型 PageResult<T>。响应 Schema 名称和 response_schema_ref 均使用 PageResult，list.items.$ref 指向 T\n"
    "  - PageResult 只包含 total、current、pageSize 和 list 四个同级字段。total、current 和 pageSize 为 integer，list 为数组。\n"
    "  - 列表项类型 T 由 list.items 指定，需要包含页面展示、后续操作或用户明确要求的字段，保留操作所需的记录标识。"
    "    先查找本 Contract 中已有的响应 Schema，满足条件时直接复用。如果没有符合条件的，新建以 ListItem 结尾的 Schema。\n"
    "明确不分页时，按已确认的要求定义响应。\n"
)

WRITE_RESPONSE_RULE = (
    "【新增、更新与删除响应】\n"
    "新增、更新和删除未明确要求返回业务数据时，默认使用同一 Contract 内共享的 OperationResult 响应 Schema。"
    "它只包含布尔属性 success，成功响应示例为 {\"success\":true}。\n"
    "业务错误沿用接口的 error_codes 和错误响应。明确要求返回记录标识或其他业务数据时，按业务要求定义所需字段。\n"
)


def _json(value: Any) -> str:
    """将当前阶段允许读取的业务上下文序列化为紧凑中文 JSON。"""

    return json.dumps(value, ensure_ascii=False)


def contract_prompt(requirement: dict, catalog: dict, spec: dict) -> str:
    """只提供目标 Contract 关联实体和页面，生成自包含的接口及 Schema。"""

    pages = [
        {key: page[key] for key in ("pageId", "goal", "information_items", "actions") if key in page}
        for page in requirement["confirmed_product_plan"].get("pages", [])
        if page["pageId"] in spec["page_ids"]
    ]
    entities = [entity for entity in catalog["entities"] if entity["id"] in spec["entity_ids"]]
    auth = (requirement.get("application_config") or {}).get("auth", {})
    return (
        "生成阶段：contract（单个 API Contract）。\n" + COMMON_RULES + API_SCHEMA_RULES
        + "只返回 {api_contracts: [一个完整 Contract]}。Contract 必须且只能包含 "
        "id,name,entity_ids,base_path,authentication,schemas,endpoints。"
        "保留目录中的 Contract id/name/entity_ids/base_path 以及每个 Endpoint 的 id/name/method/path，"
        "不能增加、删除、重命名 Endpoint 或修改共享实体。\n"
        "schemas 是 JSON Schema 名称到定义的字典。所有 $ref 和 request_schema_ref/response_schema_ref "
        "使用本契约内的裸 Schema 名称，禁止跨 Contract 引用。Schema 属性可独立命名。"
        "读取响应必须覆盖页面 information_items，关联记录编号/名称可以作为只读展示投影；"
        "需要展示关联记录名称时，直接提供名称。"
        "Endpoint 每项包含 id,name,method,path,summary,parameters,request_schema_ref,response_schema_ref,"
        "error_codes,authentication；parameters 每项包含 name,in,required,schema。"
        "error_codes 为字符串数组，例如 [\"INVALID_STATE\",\"NOT_FOUND\"]，不能返回 code/message 对象数组。"
        "路径占位参数必须声明。请求体由业务语义决定，无请求体时 request_schema_ref 为 null，"
        "需要从列表选择记录时，响应应提供后续操作所需的记录标识。"
        "不能仅因 POST/PUT/PATCH 就生成空请求对象。非 DELETE 接口必须定义响应 Schema。DELETE 的响应按下文的写操作规则生成。"
        "authentication 只能包含 boolean required，不携带 roles 或权限规则。\n"
        + API_RECORD_IDENTITY_RULE
        + LIST_QUERY_RULE
        + WRITE_RESPONSE_RULE
        + f"当前应用登录能力：{_json({'enabled': bool(auth.get('enable'))})}\n"
        f"固定 Contract 目录：{_json(spec)}\n"
        f"只读实体字段：{_json(entities)}\n"
        f"本模块页面需求：{_json(pages)}\n"
        + technical_plan_feedback_prompt(requirement)
    )


def page_prompt(requirement: dict, catalog: dict, page: dict, contracts: list[dict]) -> str:
    """为一个页面提供所有精简接口条目，保留跨模块复用且不灌入完整 Schema。"""

    required_slots = [{"pageId": page_id, "actionId": action_id, "stepId": step_id}
        for page_id, action_id, step_id in sorted(business_slots({"pages": [page]}), key=str)]
    endpoints = [
        {"contractId": contract["id"], "entity_ids": contract["entity_ids"], **endpoint,
            **({"response_fields": response_field_paths(contracts, endpoint["id"])}
                if endpoint["method"] == "GET" and page["pageId"] in contract["page_ids"] else {})}
        for contract in catalog["contracts"] for endpoint in contract["endpoints"]
    ]
    return (
        "生成阶段：page（单页技术引用）。\n" + COMMON_RULES
        + "根据已生成的本页动作映射组织页面技术引用。"
        "只返回 {pages: [{pageId,references: {endpoint_dependencies: [...],action_implementations: [...]}}]}，"
        "且只能返回目标页面。只能选择目录中已有 Endpoint，不能生成实体、API 或 Schema。"
        "endpoint_dependencies 每项为 {endpoint_id,usage,trigger,required_for_initial_load}，"
        "必须覆盖初始页面读取和本页全部业务操作，初始读取设 required_for_initial_load=true。"
        "同一页面的同一 endpoint_id 在 endpoint_dependencies 中必须且只能声明一次；"
        "初始读取、多个 action 或多个 step 复用同一接口时合并为一条依赖，"
        "只要初始读取需要该接口就设 required_for_initial_load=true，trigger 合并说明触发场景。"
        "直接 business action 恰好一个 {actionId,endpointId}；"
        "这里绑定主要业务接口；页面刷新若涉及主体和关联列表，必须在 endpoint_dependencies 中完整声明所有读取接口，"
        "并在每条 trigger 明确初始加载/刷新动作，不能漏掉关联数据或虚构 stepBindings。"
        "不得声称主要接口返回其他接口的数据。"
        "包含 business 步骤的 sequence 恰好一个 {actionId,stepBindings:[{stepId,endpointId}]}，"
        "覆盖每个且仅包含 business stepId。navigation/interface/external 不进入 action_implementations。"
        "actionId 只在当前 pageId 内解释。所有选择的接口也必须进入 endpoint_dependencies。"
        "只读展示页面的 action_implementations 可以为空，但仍应绑定所需的页面读取接口。\n"
        f"目标页面：{_json(page)}\n"
        f"合法业务槽位（只能为这些动作生成 action_implementations）：{_json(required_slots)}\n"
        f"本页目录动作映射：{_json([binding for binding in catalog['action_bindings'] if binding['pageId'] == page['pageId']])}\n"
        f"只读接口目录：{_json(endpoints)}\n"
        + technical_plan_feedback_prompt(requirement)
        + "只输出页面技术引用，不输出审查报告或其他章节。\n"
        "返回前按合法业务槽位逐项核对：其他 actionId 属于上游导航/界面/外部行为，不得绑定 Endpoint，"
        "即使跳转目标有读取接口也不能把导航动作写入 action_implementations。\n"
    )
