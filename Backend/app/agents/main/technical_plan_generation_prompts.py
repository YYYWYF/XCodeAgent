"""按单个接口契约和单个页面裁剪技术规划生成上下文，共享阶段规则供目录复用。"""

import json
from typing import Any

from app.agents.main.technical_plan_feedback import technical_plan_feedback_prompt
from app.services.api_contracts import response_field_paths


COMMON_RULES = (
    "只返回本阶段要求的一个完整 JSON 根对象，不返回 Markdown、说明或其他章节。"
    "所有用户可见名称、说明和 trigger 使用简体中文；ID、字段名、路径和类型保留技术标识。"
    "只实现已确认 ProductPlan 的业务事实，不新增业务需求。"
    "后端固定 Java8 + Springboot，数据库 MySQL8，缓存 Redis。"
    "实体只定义业务语义，不输出 data_source、data_sources、物理来源、字段映射或 SQL。"
    "平台实体的隐式主键 id 在 API JSON 中统一为 string；数据库 BIGINT 不等于 JSON number。"
    "引用这些实体主键的关联字段在实体目录中使用 text，在响应、请求和路径参数中均使用 string，跨 Contract 不得改变同一身份的类型。"
    "业务明确为正整数的数量必须使用 type=integer 且 minimum=1；明确非负的数值必须包含 minimum=0，不能只把约束写在 description 中。"
    "不输出 authorization_manifest、角色、资源键、策略或权限绑定，平台稍后统一编译。"
    "页面布局、导航、本地交互和产品状态由上游决定，不在技术规划重复设计。\n"
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
        "生成阶段：contract（单个 API Contract）。\n" + COMMON_RULES
        + "只返回 {api_contracts: [一个完整 Contract]}。Contract 必须且只能包含 "
        "id,name,entity_ids,base_path,authentication,schemas,endpoints。"
        "保留目录中的 Contract id/name/entity_ids/base_path 以及每个 Endpoint 的 id/name/method/path，"
        "不能增加、删除、重命名 Endpoint 或修改共享实体。\n"
        "schemas 是 JSON Schema 名称到定义的字典。所有 $ref 和 request_schema_ref/response_schema_ref "
        "使用本契约内的裸 Schema 名称，禁止跨 Contract 引用。Schema 属性可独立命名；"
        "直接取自实体字段的属性使用 entity_field_ref=EntityId.field_name；计算、聚合和传输字段可省略。"
        "Endpoint 每项包含 id,name,method,path,summary,parameters,request_schema_ref,response_schema_ref,"
        "error_codes,authentication；parameters 每项包含 name,in,required,schema。"
        "error_codes 为字符串数组，例如 [\"INVALID_STATE\",\"NOT_FOUND\"]，不能返回 code/message 对象数组。"
        "路径占位参数必须声明。请求体由业务语义决定，无请求体时 request_schema_ref 为 null，"
        "列表/详情返回实体记录时必须提供稳定记录身份；同一集合有路径 {id} 操作时，列表响应的记录必须包含 id 或映射实体 id 的属性。"
        "记录 id 应明确标注实际所属实体的 entity_field_ref=EntityId.id；同一 Contract 关联多个实体时，不得混用各自的 id。"
        "不能仅因 POST/PUT/PATCH 就生成空请求对象。非 DELETE 接口必须定义响应 Schema。"
        "分页接口使用 current/pageSize 查询参数；分页响应恰好包含 total/pageSize/current/list "
        "四个同级必填字段，前三者为 integer，list 为 array。"
        "authentication 只能包含 boolean required，不携带 roles 或权限规则。\n"
        f"当前应用登录能力：{_json({'enabled': bool(auth.get('enable'))})}\n"
        f"固定 Contract 目录：{_json(spec)}\n"
        f"只读实体字段：{_json(entities)}\n"
        f"本模块页面需求：{_json(pages)}\n"
        + technical_plan_feedback_prompt(requirement)
    )


def page_prompt(requirement: dict, catalog: dict, page: dict, contracts: list[dict]) -> str:
    """为一个页面提供所有精简接口条目，保留跨模块复用且不灌入完整 Schema。"""

    endpoints = [
        {"contractId": contract["id"], "entity_ids": contract["entity_ids"], **endpoint,
            **({"response_fields": response_field_paths(contracts, endpoint["id"])}
                if endpoint["method"] == "GET" and page["pageId"] in contract["page_ids"] else {})}
        for contract in catalog["contracts"] for endpoint in contract["endpoints"]
    ]
    return (
        "生成阶段：page（单页技术引用）。\n" + COMMON_RULES
        + "先独立核对目录能否真实执行本页全部业务动作，不预设目录一定正确。"
        "若存在能力缺口，本次任务是报告缺口，直接返回下述 catalog_issue，不能生成 pages；"
        "尤其是确认/更新已有记录不能绑定 GET 查询或 POST 创建接口。"
        "只有确认目录能力足够时，才返回 {pages: [{pageId,references: {endpoint_dependencies: [...],action_implementations: [...]}}]}，"
        "且只能返回目标页面。只能选择目录中已有 Endpoint，不能生成实体、API 或 Schema。"
        "endpoint_dependencies 每项为 {endpoint_id,usage,trigger,required_for_initial_load}，"
        "必须覆盖初始页面读取和本页全部业务操作，初始读取设 required_for_initial_load=true。"
        "同一页面的同一 endpoint_id 在 endpoint_dependencies 中必须且只能声明一次；"
        "初始读取、多个 action 或多个 step 复用同一接口时合并为一条依赖，"
        "只要初始读取需要该接口就设 required_for_initial_load=true，trigger 合并说明触发场景。"
        "直接 business action 恰好一个 {actionId,endpointId}；"
        "包含 business 步骤的 sequence 恰好一个 {actionId,stepBindings:[{stepId,endpointId}]}，"
        "覆盖每个且仅包含 business stepId。navigation/interface/external 不进入 action_implementations。"
        "actionId 只在当前 pageId 内解释。所有选择的接口也必须进入 endpoint_dependencies。"
        "只读展示页面的 action_implementations 可以为空，但仍应绑定所需的页面读取接口。\n"
        "如果目录接口无法满足已确认的业务动作/步骤或首次加载，不得强行选择不合适的接口或虚构接口 ID。"
        "必须先按接口 method、path 和 summary 核对真实能力：GET 只能查询，不能实现新增、确认、更新或删除；"
        "创建接口不能代替更新已有记录。不得通过改写 usage/trigger 声称接口具备目录没有的能力。"
        "此时只返回 {catalog_issue:{pageId,kind,actionId,stepId,reason}}，kind 为 business_action 或 initial_load；"
        "业务缺口引用本页真实 actionId 和业务 stepId（直接动作 stepId=null）；"
        "首次加载缺口的 actionId/stepId 都为 null。reason 用中文说明当前接口为什么无法满足产品事实。"
        "普通 JSON、绑定漏项、重复依赖等自身输出错误应直接修正，不报告目录缺口。\n"
        f"目标页面：{_json(page)}\n"
        f"只读接口目录：{_json(endpoints)}\n"
        + technical_plan_feedback_prompt(requirement)
        + "再次检查：如任何业务动作没有真正可用的接口，优先返回 catalog_issue；"
        "不能省略该动作，也不能为满足绑定覆盖校验而选择无法实现业务结果的接口。\n"
        "response_fields 来自已验证的完整读取 Schema；以该字段列表判断记录身份来源，不能仅因精简摘要未重复字段就认定读取接口缺少 id。\n"
    )
