"""验证全局轻量身份、逐实体字段、逐 Contract 目录及逐页动作映射。"""

from copy import deepcopy
import re

from pydantic import Field

from app.agents.main.technical_plan_catalog_repair import CatalogBinding, business_slots, raise_catalog_issue, validate_catalog_coverage
from app.agents.main.technical_plan_generation_validation import (
    _StrictFragment, _Entity, _Endpoint, _check, _unique, validate_catalog,
)
from app.agents.main.technical_plan_language import validate_technical_plan_chinese_prose


class EntityIdentity(_StrictFragment):
    """全局阶段只冻结实体身份，字段在独立请求中展开。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ContractIdentity(_StrictFragment):
    """固定业务能力、实体及页面归属，不在全局请求展开接口。"""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    entity_ids: list[str] = Field(min_length=1)
    base_path: str = Field(pattern=r"^/")
    page_ids: list[str] = Field(min_length=1)


class _Registry(_StrictFragment):
    """轻量全局目录不接受实体字段、接口或动作映射。"""

    entities: list[EntityIdentity]
    contracts: list[ContractIdentity]


class _Endpoints(_StrictFragment):
    """每个业务 Contract 只生成自己的精简接口条目。"""

    endpoints: list[_Endpoint] = Field(min_length=1)


def catalog_registry(catalog: dict) -> dict:
    """从已组装目录投射轻量身份，修复时不回灌全应用完整字段与映射。"""

    return {"entities": [EntityIdentity.model_validate({key: entity[key] for key in ("id", "name", "description")}).model_dump()
        for entity in catalog["entities"]],
        "contracts": [{key: deepcopy(contract[key]) for key in ("id", "name", "entity_ids", "base_path", "page_ids")}
            for contract in catalog["contracts"]]}


def validate_registry(value: dict, product: dict) -> dict:
    """检查全局身份及业务页面归属，空目录不能进入实体或接口生成。"""

    registry = _Registry.model_validate(value).model_dump()
    _unique([entity["id"] for entity in registry["entities"]], "实体 ID")
    _unique([contract["id"] for contract in registry["contracts"]], "Contract ID")
    entity_ids = {entity["id"] for entity in registry["entities"]}
    pages = {page["pageId"] for page in product.get("pages", [])}
    claimed = set()
    for contract in registry["contracts"]:
        _unique(contract["entity_ids"], "Contract 实体引用")
        _unique(contract["page_ids"], "Contract 页面引用")
        if set(contract["entity_ids"]) - entity_ids or set(contract["page_ids"]) - pages:
            raise ValueError("全局身份目录引用了未知实体或页面。")
        claimed.update(contract["page_ids"])
    if {slot[0] for slot in business_slots(product)} - claimed:
        raise ValueError("目录遗漏业务页面的 Contract 归属。")
    _check(validate_technical_plan_chinese_prose({"entities": registry["entities"], "api_contracts": registry["contracts"]}))
    # Contract 的名称校验沿用完整目录规则，实体名称不额外引入新的命名政策。
    if any(not re.search(r"[\u3400-\u9fff]", contract["name"]) for contract in registry["contracts"]):
        raise ValueError("Contract 名称必须包含中文。")
    return registry


def validate_entity_fields(value: dict, identity: dict) -> dict:
    """只允许补充指定实体的语义字段，枚举与中文说明沿用现有校验。"""

    if set(value) != {"fields"}:
        raise ValueError("实体字段阶段只能返回 {fields:[字段定义]}。")
    entity = _Entity.model_validate({**identity, "fields": value["fields"]}).model_dump(exclude_unset=True)
    validate_catalog({"entities": [entity], "contracts": [], "action_bindings": []}, {"pages": []})
    return {"fields": entity["fields"]}


def validate_endpoint_directory(value: dict, *, identity: dict, entities: list, completed: list, product: dict) -> dict:
    """每个接口目录在发布前检查跨 Contract 身份及规范化路由冲突。"""

    part = _Endpoints.model_validate(value).model_dump()
    candidate = {"entities": entities, "contracts": [*completed, {**identity, **part}], "action_bindings": []}
    # 此阶段尚未生成动作映射，只检查全部已完成接口的全局身份和引用；完整覆盖在逐页阶段验证。
    validate_catalog(candidate, {"pages": [{"pageId": page["pageId"], "actions": []} for page in product.get("pages", [])]})
    return part


def validate_page_bindings(value: dict, *, page: dict, catalog: dict) -> dict:
    """逐页完整覆盖业务槽位，接口能力不足时立即进入现有目录修复流程。"""

    if "catalog_issue" in value:
        raise_catalog_issue(value, page)
    if set(value) != {"action_bindings"} or not isinstance(value["action_bindings"], list):
        raise ValueError("页面映射阶段只能返回 {action_bindings:[业务映射]}。")
    bindings = [CatalogBinding.model_validate(binding).model_dump() for binding in value["action_bindings"]]
    validate_catalog_coverage({**catalog, "action_bindings": bindings}, {"pages": [page]})
    return {"action_bindings": bindings}
