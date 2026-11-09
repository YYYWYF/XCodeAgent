"""验证技术规划生成片段的身份、局部引用和业务绑定，拒绝静默覆盖。"""

from typing import Literal
import re

from pydantic import BaseModel, ConfigDict, Field
from app.agents.main.technical_plan_catalog_repair import CatalogBinding, raise_catalog_issue, validate_catalog_coverage

from app.agents.main.technical_plan_language import validate_technical_plan_chinese_prose
from app.services.api_contract_validation import validate_api_contract_definitions
from app.services.page_implementation_contract import _validate_technical_action_implementations
from app.services.project_plan import (
    validate_technical_plan_api_contracts,
    validate_technical_plan_model_authentication,
    validate_technical_plan_model_authorization,
)


class _StrictFragment(BaseModel):
    """片段模型只接受当前生成合同，不转换类型或保留未声明字段。"""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class _EntityField(_StrictFragment):
    """定义共享实体字段的模型输出边界。"""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    type: Literal["text", "long_text", "number", "decimal", "date", "datetime", "enum", "boolean"]
    required: bool
    enum_values: list[str] = Field(default_factory=list)


class _Entity(_StrictFragment):
    """统一实体身份和完整语义字段，不接受物理来源。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    fields: list[_EntityField]


class _Endpoint(_StrictFragment):
    """在展开 Schema 前冻结接口身份和业务用途。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(pattern=r"^/")
    summary: str = Field(min_length=1)


class _Contract(_StrictFragment):
    """声明单个生成模块拥有的实体、页面和接口清单。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    entity_ids: list[str] = Field(min_length=1)
    base_path: str = Field(pattern=r"^/")
    page_ids: list[str] = Field(min_length=1)
    endpoints: list[_Endpoint] = Field(min_length=1)


class _Catalog(_StrictFragment):
    """共享目录不包含完整 Contract 或页面实现。"""

    entities: list[_Entity]
    contracts: list[_Contract]
    action_bindings: list[CatalogBinding]


class _Authentication(_StrictFragment):
    """登录声明只允许 required，不承载模型生成的授权规则。"""

    required: bool


class _FullEndpoint(_Endpoint):
    """在局部语义校验前拒绝会被归一化吞掉的错误字段类型。"""

    parameters: list[dict]
    request_schema_ref: str | None
    response_schema_ref: str | None
    error_codes: list[str]
    authentication: _Authentication


class _FullContract(_StrictFragment):
    """完整 Contract 片段不允许生成目录的页面归属或其他越界字段。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    entity_ids: list[str] = Field(min_length=1)
    base_path: str = Field(pattern=r"^/")
    authentication: _Authentication
    schemas: dict[str, dict]
    endpoints: list[_FullEndpoint]


class _Dependency(_StrictFragment):
    """保留页面依赖的严格类型，避免错误值被最终归一化隐式转换。"""

    endpoint_id: str = Field(min_length=1)
    usage: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    required_for_initial_load: bool


class _DirectBinding(_StrictFragment):
    """直接业务动作只选择一个 Endpoint。"""

    actionId: str = Field(min_length=1)
    endpointId: str = Field(min_length=1)


class _StepBinding(_StrictFragment):
    """组合业务步骤保留所属动作内的稳定步骤身份。"""

    stepId: str = Field(min_length=1)
    endpointId: str = Field(min_length=1)


class _SequenceBinding(_StrictFragment):
    """组合动作只声明业务步骤的接口选择。"""

    actionId: str = Field(min_length=1)
    stepBindings: list[_StepBinding]


class _References(_StrictFragment):
    """页面只增加接口依赖和业务实现，不重复产品或 UI 事实。"""

    endpoint_dependencies: list[_Dependency]
    action_implementations: list[_DirectBinding | _SequenceBinding]


class _Page(_StrictFragment):
    """限制单页片段的写入位置和引用字段。"""

    pageId: str = Field(min_length=1)
    references: _References


def _unique(values: list, label: str) -> None:
    """在建立字典前拒绝重复身份，防止合并时覆盖先前片段。"""

    if len(values) != len(set(values)):
        raise ValueError(f"技术规划 {label} 存在重复值。")


def _check(errors: list[str]) -> None:
    """汇总局部校验诊断，交给现有三轮总预算重试当前片段。"""

    if errors:
        raise ValueError("；".join(errors[:12]))


def validate_catalog(value: dict, product: dict) -> dict:
    """检查共享身份、实体绑定、页面归属及全局 HTTP 路由冲突。"""

    catalog = _Catalog.model_validate(value).model_dump(exclude_unset=True)
    entities, contracts = catalog["entities"], catalog["contracts"]
    _unique([entity["id"] for entity in entities], "实体 ID")
    _unique([contract["id"] for contract in contracts], "Contract ID")
    known_entities = {entity["id"] for entity in entities}
    known_pages = {page["pageId"] for page in product.get("pages", [])}
    endpoint_ids, routes = [], []
    for entity in entities:
        _unique([field["name"] for field in entity["fields"]], f"实体 {entity['id']} 字段")
        for field in entity["fields"]:
            if field["type"] == "enum" and not field.get("enum_values"):
                raise ValueError(f"实体 {entity['id']} 的枚举字段 {field['name']} 缺少 enum_values。")
    for contract in contracts:
        _unique(contract["entity_ids"], "Contract 实体引用")
        _unique(contract["page_ids"], "Contract 页面引用")
        if set(contract["entity_ids"]) - known_entities or set(contract["page_ids"]) - known_pages:
            raise ValueError(f"Contract {contract['id']} 引用了未知实体或页面。")
        for endpoint in contract["endpoints"]:
            endpoint_ids.append(endpoint["id"])
            # 参数名不同但结构相同的路径仍属于同一 HTTP 路由。
            routes.append((endpoint["method"], re.sub(r"\{[^}]+\}", "{}", endpoint["path"])))
            if not re.search(r"[\u3400-\u9fff]", endpoint["name"]):
                raise ValueError(f"Endpoint {endpoint['id']} 名称必须包含中文。")
        if not re.search(r"[\u3400-\u9fff]", contract["name"]):
            raise ValueError(f"Contract {contract['id']} 名称必须包含中文。")
    _unique(endpoint_ids, "Endpoint ID")
    _unique(routes, "Endpoint method/path")
    _check(validate_technical_plan_chinese_prose({"entities": entities, "api_contracts": contracts}))
    validate_catalog_coverage(catalog, product)
    return catalog


def validate_contract(value: dict, catalog: dict, spec: dict) -> dict:
    """只接纳指定 Contract 的完整替换片段，确保所有 Schema 在契约内闭合。"""

    items = value.get("api_contracts")
    if set(value) != {"api_contracts"} or not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError("Contract 生成必须且只能返回 api_contracts 中的一个对象。")
    contract = _FullContract.model_validate(items[0]).model_dump()
    items = [contract]
    for key in ("id", "name", "entity_ids", "base_path"):
        if contract.get(key) != spec[key]:
            raise ValueError(f"Contract {spec['id']} 不得改变目录字段 {key}。")
    endpoints = contract.get("endpoints")
    if not isinstance(endpoints, list) or any(not isinstance(item, dict) for item in endpoints):
        raise ValueError("Contract endpoints 必须是对象数组。")
    _unique([item.get("id") for item in endpoints], "Contract Endpoint ID")
    expected = {item["id"]: item for item in spec["endpoints"]}
    if {item.get("id") for item in endpoints} != set(expected):
        raise ValueError(f"Contract {spec['id']} 必须完整且仅覆盖目录中的 Endpoint。")
    endpoint_keys = {"id", "name", "method", "path", "summary", "parameters", "request_schema_ref", "response_schema_ref", "error_codes", "authentication"}
    for endpoint in endpoints:
        if set(endpoint) != endpoint_keys:
            raise ValueError(f"Endpoint {endpoint['id']} 字段必须完整且符合当前合同。")
        for key in ("name", "method", "path"):
            if endpoint.get(key) != expected[endpoint["id"]][key]:
                raise ValueError(f"Endpoint {endpoint['id']} 不得改变目录字段 {key}。")
    plan = {"entities": catalog["entities"], "api_contracts": items}
    _check([
        *validate_technical_plan_model_authentication(plan),
        *validate_technical_plan_model_authorization(plan),
        *validate_technical_plan_api_contracts(plan),
        *validate_api_contract_definitions(plan),
        *validate_technical_plan_chinese_prose(plan),
    ])
    return contract


def validate_page(value: dict, page: dict, contracts: list[dict]) -> dict:
    """检查单页身份、业务动作和步骤覆盖，避免依赖最终归一化掩盖片段错误。"""

    if "catalog_issue" in value:
        raise_catalog_issue(value, page)
    items = value.get("pages")
    if set(value) != {"pages"} or not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError("页面生成必须且只能返回 pages 中的一个对象。")
    item = _Page.model_validate(items[0]).model_dump()
    if item["pageId"] != page["pageId"]:
        raise ValueError("页面片段不得改变目标 pageId 或添加产品字段。")
    references = item["references"]
    dependencies = references["endpoint_dependencies"]
    _unique([entry["endpoint_id"] for entry in dependencies], "页面 Endpoint 依赖")
    plan = {"artifact_type": "technical-plan", "api_contracts": contracts, "pages": items}
    endpoint_ids = {endpoint["id"] for contract in contracts for endpoint in contract["endpoints"]}
    dependency_ids = {entry.get("endpoint_id") for entry in dependencies}
    errors = validate_technical_plan_chinese_prose(plan)
    if dependency_ids - endpoint_ids:
        errors.append("页面依赖引用了目录中不存在的 Endpoint。")
    # 此处只验证本阶段生成的业务绑定；完整 UI 本地效果在组装后的原校验入口检查。
    _validate_technical_action_implementations(
        item, page, page_id=page["pageId"], endpoint_ids=endpoint_ids,
        required_endpoint_ids=dependency_ids, errors=errors,
    )
    _check(errors)
    return item
