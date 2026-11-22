"""只对确定发生冲突的分组联合建模，并原子发布校验后的目录。"""

from copy import deepcopy

from pydantic import Field

from app.agents.main.technical_plan_catalog_conflict import CatalogContractConflict
from app.agents.main.technical_plan_catalog_parts_prompts import _page_facts, _prompt
from app.agents.main.technical_plan_catalog_parts_validation import (
    ContractIdentity, validate_endpoint_directory, validate_registry,
)
from app.agents.main.technical_plan_contract_budget import fragment_digest
from app.agents.main.technical_plan_generation_validation import _Endpoint, _StrictFragment


class _JointContract(ContractIdentity):
    """联合接口修正同时声明分组归属和完整的精简接口目录。"""

    endpoints: list[_Endpoint] = Field(min_length=1)


class _JointRegistry(_StrictFragment):
    """分组阶段只修正身份，不提前生成接口或实体字段。"""

    contracts: list[ContractIdentity] = Field(min_length=1)


class _JointEndpoints(_StrictFragment):
    """接口冲突阶段一次接收所有冲突分组，避免分别生成造成重复决策。"""

    contracts: list[_JointContract] = Field(min_length=1)


def endpoint_fragment_key(prefix: str, identity: dict, entities: list, completed: list) -> str:
    """以当前归属、实体和前置目录内容标识接口片段。"""

    return prefix + "endpoints:" + fragment_digest({"identity": identity, "entities": entities, "completed": completed})


def _replace_groups(registry: dict, ids: set[str], replacements: list[dict]) -> dict:
    """只替换冲突范围，保留其他分组的身份、内容及相对顺序。"""

    contracts = []
    by_id = {item["id"]: item for item in replacements}
    for identity in registry["contracts"]:
        if identity["id"] not in ids:
            contracts.append(deepcopy(identity))
        elif identity["id"] in by_id:
            contracts.append({key: deepcopy(by_id[identity["id"]][key]) for key in ContractIdentity.model_fields})
    return {**registry, "contracts": contracts}


def _validate_repair(value: dict, *, registry: dict, affected: list, entities: list, completed: list, product: dict, with_endpoints: bool) -> dict:
    """禁止联合修正丢失业务范围、修改无关分组或绕过全局接口校验。"""

    model = _JointEndpoints if with_endpoints else _JointRegistry
    repaired = model.model_validate(value).model_dump()
    contracts = repaired["contracts"]
    ids = {item["id"] for item in affected}
    returned = [item["id"] for item in contracts]
    if len(set(returned)) != len(returned) or set(returned) - ids:
        raise ValueError("联合修正只能保留冲突分组的原 ID，合并时保留其中一个 ID，不得新增或重复分组。")
    for key in ("entity_ids", "page_ids"):
        expected = {entry for item in affected for entry in item[key]}
        actual = {entry for item in contracts for entry in item[key]}
        if actual != expected:
            raise ValueError(f"联合修正必须完整保留冲突分组的 {key} 并集，不得遗漏或引入其他业务范围。")
    candidate = _replace_groups(registry, ids, contracts)
    try:
        validate_registry({"contracts": candidate["contracts"]}, product, entities=entities)
        if with_endpoints:
            # 新边界也不能夺走只读分组已有的接口归属；完整复验当前已生成目录。
            by_id = {item["id"]: item for item in completed if item["id"] not in ids}
            by_id.update({item["id"]: item for item in contracts})
            reserved = []
            for identity in candidate["contracts"]:
                if identity["id"] in by_id:
                    part = by_id[identity["id"]]
                    validate_endpoint_directory({"endpoints": part["endpoints"]}, identity=identity,
                        entities=entities, completed=reserved, product=product, registry=candidate)
                    reserved.append(part)
    except CatalogContractConflict as exc:
        # 同一联合片段的错误仍消耗其三次预算，不能递归启动另一轮联合模型调用。
        raise ValueError(str(exc)) from exc
    return repaired


def _cached_directories(prefix: str, registry: dict, entities: list, cache: dict) -> dict[str, dict]:
    """按原依赖链读取已完成接口，供原子替换时重算键并保留未受影响结果。"""

    completed = []
    for identity in registry["contracts"]:
        part = cache.get(endpoint_fragment_key(prefix, identity, entities, completed))
        if part is None:
            break
        completed.append({**identity, **part})
    return {item["id"]: item for item in completed}


def repair_contract_conflict(requirement: dict, registry: dict, conflict: CatalogContractConflict, *,
        entities: list, completed: list, prefix: str, registry_key: str, cache: dict, fragment, publish,
        with_endpoints: bool) -> dict:
    """将冲突双方交给同一个模型片段，全部校验后一次发布分组及接口的修正。"""

    ids = set(conflict.contract_ids)
    affected = [item for item in registry["contracts"] if item["id"] in ids]
    if len(affected) < 2:
        raise conflict
    page_ids = {page_id for item in affected for page_id in item["page_ids"]}
    entity_ids = {entity_id for item in affected for entity_id in item["entity_ids"]}
    proposed = {item["id"]: item for item in conflict.contracts}
    context = {
        "conflict": str(conflict),
        "contracts": [{**item, **({"endpoints": proposed[item["id"]].get("endpoints", [])} if with_endpoints else {})} for item in affected],
        "entities": [item for item in entities if item["id"] in entity_ids],
        "pages": [_page_facts(page) for page in requirement["confirmed_product_plan"].get("pages", []) if page["pageId"] in page_ids],
        "readonly_contracts": [item for item in registry["contracts"] if item["id"] not in ids],
        "reserved_endpoints": [item for item in completed if item["id"] not in ids],
    }
    rules = (
        "这是一项跨分组联合修正。contracts 是本轮全部可修改分组，必须一起审阅，不得只重写后生成的一组。\n"
        "根据已确认页面业务判断边界：同一业务主体因页面、管理/展示或读写被拆开时，合并并共享接口；"
        "确属不同业务聚合时保留分组，明确各自范围。允许调整冲突组的 name、base_path、entity_ids 和 page_ids。\n"
        "只能使用冲突分组的原 ID；合并时保留其中一个。entity_ids 和 page_ids 的并集必须完整保留，"
        "不得为了消除冲突丢失读取、写入或页面业务。readonly_contracts 与 reserved_endpoints 不得修改。\n"
        "同一 method/path 及 Endpoint ID 在全应用只定义一次；共享接口所属分组的 page_ids 必须覆盖所有使用页面。"
        "实体字段和 ProductPlan 只读，不输出 Schema 或动作映射。\n"
        + ("只返回 {contracts:[{id,name,entity_ids,base_path,page_ids,endpoints:[{id,name,method,path,summary}]}]}，每组提供完整接口目录。"
           if with_endpoints else "只返回 {contracts:[{id,name,entity_ids,base_path,page_ids}]}，不生成接口。")
    )
    key = prefix + "joint:" + fragment_digest(context)

    def validate(value: dict) -> dict:
        """为当前冲突范围绑定同一份上游事实与只读接口。"""

        return _validate_repair(value, registry=registry, affected=affected, entities=entities,
            completed=completed, product=requirement["confirmed_product_plan"], with_endpoints=with_endpoints)

    repaired = fragment(key, _prompt("catalog_joint_repair（冲突分组联合修正）", rules, context, requirement), validate)
    # 缓存响应也复验；成功保存响应与原子替换之间退出时，下次仍须按同一来源核对。
    repaired = validate(repaired)["contracts"]
    candidate = _replace_groups(registry, ids, repaired)
    updated = {name: value for name, value in cache.items()
        if name != "catalog" and not name.startswith((prefix + "bindings:", prefix + "joint:"))}
    updated[registry_key] = {"contracts": candidate["contracts"]}
    if with_endpoints:
        known = _cached_directories(prefix, registry, entities, cache)
        known.update({item["id"]: item for item in completed})
        known = {name: value for name, value in known.items() if name not in ids}
        known.update({item["id"]: item for item in repaired})
        updated = {name: value for name, value in updated.items() if not name.startswith(prefix + "endpoints:")}
        previous = []
        for identity in candidate["contracts"]:
            part = known.get(identity["id"])
            if part is None:
                break
            updated[endpoint_fragment_key(prefix, identity, entities, previous)] = {"endpoints": part["endpoints"]}
            previous.append({**identity, "endpoints": part["endpoints"]})
    # 原子替换失败保留原目录；不泄露只修改了一侧的半成品。
    publish(updated)
    return candidate
