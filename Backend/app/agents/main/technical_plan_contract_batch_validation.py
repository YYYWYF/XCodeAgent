"""校验大 Contract 的冻结 Schema 目录、字段所有权和接口批次。"""

from copy import deepcopy
from pydantic import Field

from app.agents.main.technical_plan_generation_validation import (
    _Authentication, _StrictFragment, _check, _unique, validate_contract,
)
from app.agents.main.technical_plan_language import validate_technical_plan_chinese_prose
from app.services.project_plan import validate_technical_plan_api_contracts


class _SchemaSpec(_StrictFragment):
    """一个 Schema 只有一个定义位置，字段清单允许把大对象分组生成。"""

    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    base: dict
    fields: list[str]
    required: list[str]
    references: list[str]


class _EndpointRefs(_StrictFragment):
    """在接口展开前冻结请求和响应引用，跨批次不能重新发明 Schema。"""

    endpointId: str
    request_schema_ref: str | None
    response_schema_ref: str | None


class _SchemaPlan(_StrictFragment):
    """大 Contract 的紧凑内部目录，不写入正式产物。"""

    authentication: _Authentication
    schemas: list[_SchemaSpec] = Field(min_length=1)
    endpoint_refs: list[_EndpointRefs]


class SchemaPlanRepairRequired(ValueError):
    """字段发现冻结引用目录遗漏时，把修复交回有权限修改目录的阶段。"""


def schema_refs(value) -> set[str]:
    """递归收集裸 Schema 引用，非法类型也必须在缓存前被拒绝。"""

    result = set()
    if isinstance(value, dict):
        if "$ref" in value:
            if not isinstance(value["$ref"], str):
                raise ValueError("Schema $ref 必须是本契约的裸名称。")
            result.add(value["$ref"])
        for child in value.values():
            result.update(schema_refs(child))
    elif isinstance(value, list):
        for child in value:
            result.update(schema_refs(child))
    return result


def contract_body(spec: dict, plan: dict, schemas: dict, endpoints: list) -> dict:
    """只由冻结身份与校验后的片段组装 Contract，不让模型再次重写整体。"""

    return {**{key: deepcopy(spec[key]) for key in ("id", "name", "entity_ids", "base_path")},
        "authentication": deepcopy(plan["authentication"]), "schemas": deepcopy(schemas),
        "endpoints": deepcopy(endpoints)}


def validate_schema_plan(value: dict, spec: dict, catalog: dict) -> dict:
    """冻结完整接口引用及对象字段目录，拒绝重名、越界字段和跨契约引用。"""

    plan = _SchemaPlan.model_validate(value).model_dump()
    names = [schema["name"] for schema in plan["schemas"]]
    _unique(names, "Schema 名称")
    _unique([ref["endpointId"] for ref in plan["endpoint_refs"]], "Schema 目录 Endpoint")
    expected = {endpoint["id"]: endpoint for endpoint in spec["endpoints"]}
    if {ref["endpointId"] for ref in plan["endpoint_refs"]} != set(expected):
        raise ValueError("Schema 目录必须完整且仅覆盖目标 Contract 的接口。")
    for ref in plan["endpoint_refs"]:
        refs = {ref[key] for key in ("request_schema_ref", "response_schema_ref") if ref[key] is not None}
        if refs - set(names):
            raise ValueError("接口引用了 Schema 目录以外的名称。")
        if expected[ref["endpointId"]]["method"] != "DELETE" and not ref["response_schema_ref"]:
            raise ValueError("非 DELETE 接口必须规划响应 Schema。")
    for schema in plan["schemas"]:
        base = schema["base"]
        for key in ("fields", "required", "references"):
            _unique(schema[key], f"Schema {schema['name']} {key}")
        if any(not field.strip() for field in schema["fields"]):
            raise ValueError("Schema 字段名不能为空。")
        if "properties" in base or "required" in base:
            raise ValueError("Schema base 不得包含 properties/required，字段必须由唯一字段组生成。")
        if base.get("type") == "object":
            if set(schema["required"]) - set(schema["fields"]):
                raise ValueError("Schema required 必须属于冻结字段目录。")
        elif schema["fields"] or schema["required"]:
            raise ValueError("非对象 Schema 不能声明对象字段。")
        if not base.get("type") and not base.get("$ref"):
            raise ValueError("Schema base 必须明确 type 或 $ref。")
        if set(schema["references"]) - set(names) or schema_refs(base) - set(schema["references"]):
            raise ValueError("Schema 引用了未注册或未声明的名称。")
    validate_schema_semantics({schema["name"]: schema["base"] for schema in plan["schemas"]},
        catalog=catalog, spec=spec, plan=plan)
    return plan


def validate_schema_fields(value: dict, *, schema: dict, fields: list[str], catalog: dict, spec: dict, plan: dict) -> dict:
    """字段组只能写入自己的属性，并在缓存前检查实体字段和注册引用。"""

    if set(value) != {"properties"} or not isinstance(value["properties"], dict):
        raise ValueError("字段批次只能返回 {properties:{字段名:定义}}。")
    properties = value["properties"]
    if set(properties) != set(fields) or any(not isinstance(item, dict) or not item for item in properties.values()):
        raise ValueError("字段批次必须完整且仅覆盖当前冻结字段组，每项必须有定义。")
    refs = schema_refs(properties)
    registered = {item["name"] for item in plan["schemas"]}
    if refs - registered:
        raise ValueError(f"Schema {schema['name']} 字段批次引用了未注册名称：{sorted(refs - registered)}；"
            f"只能使用本 Contract 已登记的裸名称：{sorted(registered)}。"
            "必须使用 schema_ref_examples 中的原样 JSON 引用，禁止 #/components/schemas/ 或 #/definitions/ 前缀。")
    missing = refs - set(schema["references"])
    if missing:
        raise SchemaPlanRepairRequired(
            f"冻结 Schema 目录 {schema['name']} 漏声明引用：{sorted(missing)}；目标已在本 Contract 注册，"
            "字段批次无权修改冻结目录，须先重建本 Contract 的 Schema 目录，"
            f"在 {schema['name']}.references 中完整声明所需引用（包括 list.items 的引用），再生成字段。"
            "不得把已有共享对象内联或只重复生成字段来绕过引用目录。"
        )
    partial = {**schema["base"], "properties": properties,
        "required": [field for field in schema["required"] if field in fields]}
    validate_schema_semantics({schema["name"]: partial}, catalog=catalog, spec=spec, plan=plan)
    return deepcopy(value)


def validate_schema_semantics(schemas: dict, *, catalog: dict, spec: dict, plan: dict) -> None:
    """局部检查语义和中文说明，引用闭合由冻结目录与最终 Contract 校验共同保证。"""

    candidate = {"entities": catalog["entities"], "api_contracts": [contract_body(spec, plan, schemas, [])]}
    _check([*validate_technical_plan_api_contracts(candidate), *validate_technical_plan_chinese_prose(candidate)])


def validate_endpoint_batch(value: dict, *, endpoints: list, schemas: dict, plan: dict, catalog: dict, spec: dict) -> dict:
    """接口批次没有 Schema 写权限，复用冻结定义后执行现有完整局部校验。"""

    if set(value) != {"endpoints"} or not isinstance(value["endpoints"], list):
        raise ValueError("接口批次只能返回 {endpoints:[完整接口]}。")
    actual = [endpoint.get("id") for endpoint in value["endpoints"] if isinstance(endpoint, dict)]
    expected = [endpoint["id"] for endpoint in endpoints]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError(f"当前批次必须恰好返回 {len(expected)} 个接口：{expected}；不得展开其他批次。")
    pinned = {item["endpointId"]: item for item in plan["endpoint_refs"]}
    for endpoint in value["endpoints"]:
        if not isinstance(endpoint, dict) or endpoint.get("id") not in pinned:
            raise ValueError("接口批次包含未知接口。")
        for key in ("request_schema_ref", "response_schema_ref"):
            if endpoint.get(key) != pinned[endpoint["id"]][key]:
                raise ValueError("接口批次不得改变冻结 Schema 引用。")
        if schema_refs(endpoint) - set(schemas):
            raise ValueError("接口参数包含未注册的 Schema 引用。")
    body = contract_body(spec, plan, schemas, value["endpoints"])
    validated = validate_contract({"api_contracts": [body]}, catalog, {**spec, "endpoints": endpoints})
    return {"endpoints": validated["endpoints"]}
