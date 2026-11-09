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


def _route(path: str) -> str:
    """统一路径参数名称和末尾斜杠，按路由段判断接口归属与冲突。"""

    return re.sub(r"\{[^}]+\}", "{}", path).rstrip("/") or "/"


def _under_path(path: str, base: str) -> bool:
    """根路径包含全部接口，其他前缀必须在完整路径段边界匹配。"""

    return base == "/" or path == base or path.startswith(base + "/")


def _validate_endpoint_ownership(endpoints: list, identity: dict, registry: dict) -> None:
    """拒绝占用其他已冻结能力的接口命名空间或更具体的路由范围。"""

    current_id, base = identity["id"], _route(identity["base_path"])
    for endpoint in endpoints:
        endpoint_id, path = endpoint["id"], _route(endpoint["path"])
        named = [item for item in registry["contracts"] if endpoint_id.startswith(item["id"] + ".")]
        named_owner = max(named, key=lambda item: len(item["id"]), default=identity)
        routed = [item for item in registry["contracts"] if _under_path(path, _route(item["base_path"]))]
        route_depth = max((len(_route(item["base_path"])) for item in routed), default=0)
        route_owners = [item["id"] for item in routed if len(_route(item["base_path"])) == route_depth]
        if named_owner["id"] != current_id or not _under_path(path, base) or (
                route_owners and current_id not in route_owners):
            owners = list(dict.fromkeys([*([named_owner["id"]] if named_owner["id"] != current_id else []), *route_owners]))
            raise ValueError(
                f"Contract {current_id} 接口目录越界：Endpoint {endpoint_id}（{endpoint['method']} {endpoint['path']}）"
                f"不属于当前能力；匹配的 Contract 为 {owners}。本次只生成 {current_id}、路径范围 {identity['base_path']} 的接口，"
                "其他能力的接口由对应 Contract 单独生成，不得复制或改名占用。"
            )


def _validate_endpoint_conflicts(endpoints: list, identity: dict, completed: list) -> None:
    """反馈具体冲突接口及双方归属，避免模型只收到无法定位的重复值提示。"""

    ids, routes = {}, {}
    for contract in [*completed, {**identity, "endpoints": endpoints}]:
        for endpoint in contract["endpoints"]:
            endpoint_id = endpoint["id"]
            route = (endpoint["method"], _route(endpoint["path"]))
            owner = f"{contract['id']} 的 {endpoint_id}"
            if endpoint_id in ids:
                raise ValueError(f"技术规划 Endpoint ID 存在重复值：{endpoint_id} 同时出现在 {ids[endpoint_id]} 和 {owner}；复用已有接口，不得重复声明。")
            if route in routes:
                raise ValueError(f"技术规划 Endpoint method/path 存在重复值：{route[0]} {route[1]} 同时出现在 {routes[route]} 和 {owner}；复用已有接口，不得通过改 ID 重复创建。")
            ids[endpoint_id], routes[route] = owner, owner


def validate_endpoint_directory(value: dict, *, identity: dict, entities: list, completed: list, product: dict, registry: dict) -> dict:
    """接口目录在缓存或复用前检查能力归属、跨 Contract 身份及规范化路由。"""

    part = _Endpoints.model_validate(value).model_dump()
    _validate_endpoint_ownership(part["endpoints"], identity, registry)
    _validate_endpoint_conflicts(part["endpoints"], identity, completed)
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
    # 普通业务动作不能在技术规划阶段扩展成产品未确认的步骤；给下一轮可执行的修复路径。
    direct = {action["actionId"]: action for action in page.get("actions", [])
        if action.get("behavior", {}).get("type") == "business"}
    for binding in bindings:
        action = direct.get(binding["actionId"]) if binding["pageId"] == page["pageId"] else None
        if action and binding["stepId"] is not None:
            raise ValueError(
                f"目录业务覆盖包含未知或重复 action/step：{(binding['pageId'], binding['actionId'], binding['stepId'])}。"
                f"已确认动作 {binding['actionId']} 是普通 business，合法 stepId 只能为 null，不得虚构步骤。"
                f"完整业务结果为：{action['behavior'].get('expectedResult', action.get('description', ''))}。"
                "如只是刷新多个已有读取，选择主体的主要接口，后续页面 endpoint_dependencies 的 trigger 完整声明刷新读取；"
                "如确实缺少所需业务操作或读取，只返回 catalog_issue，kind=business_action，"
                f"pageId={page['pageId']}，actionId={binding['actionId']}，stepId=null，"
                "reason 说明完整结果的能力缺口，交由目录修复；不能丢弃部分业务结果或修改产品动作。"
            )
    validate_catalog_coverage({**catalog, "action_bindings": bindings}, {"pages": [page]})
    return {"action_bindings": bindings}
