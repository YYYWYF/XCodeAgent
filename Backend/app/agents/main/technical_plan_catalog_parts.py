"""分段生成并校验全局目录，成功后原子发布完整目录。"""

from copy import deepcopy
from graphlib import CycleError, TopologicalSorter

from app.agents.main.technical_plan_catalog_conflict import CatalogContractConflict
from app.agents.main.technical_plan_catalog_joint_repair import endpoint_fragment_key, repair_contract_conflict
from app.agents.main.technical_plan_fragment_retry import PlanningFragmentFailed
from app.agents.main.technical_plan_catalog_repair import business_slots
from app.agents.main.technical_plan_contract_budget import fragment_digest
from app.agents.main.technical_plan_catalog_parts_prompts import (
    bindings_prompt, endpoints_prompt, entity_prompt, registry_prompt, relationships_prompt,
)
from app.agents.main.technical_plan_catalog_parts_validation import (
    validate_endpoint_directory, validate_entity_fields, validate_page_bindings, validate_registry, validate_relationships,
)
from app.agents.main.technical_plan_generation_validation import validate_catalog


def _entity_generation_order(entities_by_id: dict[str, dict]) -> list[str]:
    """按从属实体先于主体的顺序生成字段，保留环路外的依赖。"""

    dependencies = {
        entity_id: [owned_id for owned_id in dict.fromkeys(entity["owned_entity_ids"]) if owned_id in entities_by_id]
        for entity_id, entity in entities_by_id.items()
    }
    while True:
        try:
            return list(TopologicalSorter(dependencies).static_order())
        except CycleError as exc:
            # 模型给出环路时，只解除环内的一条等待关系，其他依赖继续参与排序。
            cycle = exc.args[1]
            dependencies[cycle[-1]].remove(cycle[-2])


def generate_catalog(requirement: dict, *, cache: dict, fragment, publish) -> dict:
    """全局生成轻量身份与实体从属关系，其余按实体、Contract 和页面局部生成并复用。"""

    if "catalog" in cache:
        return deepcopy(cache["catalog"])
    product = requirement["confirmed_product_plan"]
    prefix = "catalog-part:" + fragment_digest({"initial": True}) + ":"
    frozen = deepcopy(requirement)
    relationships = fragment(prefix + "relationships", relationships_prompt(frozen), validate_relationships)
    entity_identities = relationships["entities"]
    entity_registry_digest = fragment_digest(entity_identities)
    registry_key = prefix + "registry:" + entity_registry_digest
    try:
        # 当前候选也须执行分组校验，不能因先前已保存就跳过本轮冲突处理。
        if registry_key in cache:
            validate_registry(cache[registry_key], product, entities=entity_identities)
        contract_registry = fragment(registry_key, registry_prompt(frozen, entity_identities),
            lambda value: validate_registry(value, product, entities=entity_identities))
    except CatalogContractConflict as exc:
        repaired = repair_contract_conflict(frozen, {**relationships, "contracts": exc.contracts}, exc,
            entities=entity_identities, completed=[], prefix=prefix, registry_key=registry_key,
            cache=cache, fragment=fragment, publish=publish, with_endpoints=False)
        contract_registry = {"contracts": repaired["contracts"]}
    registry = {**relationships, **contract_registry}
    entities_by_id = {entity["id"]: entity for entity in entity_identities}
    generated_entities = {}
    for entity_id in _entity_generation_order(entities_by_id):
        registered_entity = entities_by_id[entity_id]
        # 从属关系仅用于字段生成上下文，完整目录使用正式实体身份。
        identity = {key: registered_entity[key] for key in ("id", "name", "description")}
        owned_entities = [
            generated_entities[owned_id]
            for owned_id in dict.fromkeys(registered_entity["owned_entity_ids"])
            if owned_id in generated_entities
        ]
        fragment_inputs = {"identity": identity, "registry": entity_registry_digest}
        if owned_entities:
            fragment_inputs["owned_fields"] = fragment_digest(owned_entities)
        key = prefix + "entity:" + fragment_digest(fragment_inputs)
        fields = fragment(key, entity_prompt(frozen, registry, registered_entity, owned_entities=owned_entities),
            lambda value, identity=identity: validate_entity_fields(value, identity))
        generated_entities[entity_id] = {**identity, **fields}
    # 对外目录沿用 registry 顺序，生成顺序只用于准备当前实体的上下文。
    entities = [generated_entities[entity_id] for entity_id in entities_by_id]
    contracts, repair_counts = [], {}
    index = 0
    while index < len(registry["contracts"]):
        identity = registry["contracts"][index]
        key = endpoint_fragment_key(prefix, identity, entities, contracts)
        current_requirement = frozen
        try:
            if key in cache:
                try:
                    # fragment 命中缓存不会再调校验器，必须先复验当前候选。
                    validate_endpoint_directory(cache[key], identity=identity, entities=entities, completed=contracts, product=product, registry=registry)
                except CatalogContractConflict:
                    # 跨分组冲突保留双方候选，交给联合处理，不能仅删除当前一侧。
                    raise
                except ValueError as exc:
                    # 普通局部错误仍沿原路径撤下当前片段并定向重生成。
                    publish({name: value for name, value in cache.items() if name != key})
                    current_requirement = {**frozen, "technical_plan_validation_feedback":
                        str(frozen.get("technical_plan_validation_feedback") or "") + "\n" + str(exc)}
            endpoints = fragment(key, endpoints_prompt(current_requirement, identity, entities, contracts, registry=registry),
                lambda value, identity=identity: validate_endpoint_directory(value, identity=identity, entities=entities, completed=contracts, product=product, registry=registry))
        except CatalogContractConflict as exc:
            pair = tuple(sorted(exc.contract_ids))
            repair_counts[pair] = repair_counts.get(pair, 0) + 1
            if repair_counts[pair] > 3:
                raise PlanningFragmentFailed(key, 3, f"冲突分组联合修正后再次冲突：{exc}") from exc
            registry = repair_contract_conflict(frozen, registry, exc, entities=entities, completed=contracts,
                prefix=prefix, registry_key=registry_key, cache=cache, fragment=fragment, publish=publish, with_endpoints=True)
            # 原子更新已重算已完成接口的缓存键，重读目录只会复验，不重放有效模型调用。
            contracts, index = [], 0
            continue
        contracts.append({**identity, **endpoints})
        index += 1
    catalog = {"entities": entities, "contracts": contracts, "action_bindings": []}
    for page in product.get("pages", []):
        if not business_slots({"pages": [page]}):
            continue
        key = prefix + "bindings:" + fragment_digest({"page": page, "contracts": contracts})
        bindings = fragment(key, bindings_prompt(frozen, page, catalog),
            lambda value, page=page: validate_page_bindings(value, page=page, catalog=catalog))
        catalog["action_bindings"].extend(bindings["action_bindings"])
    validated = validate_catalog(catalog, product)
    published = {**cache, "catalog": validated}
    publish({key: value for key, value in published.items() if not key.startswith("catalog-part:")})
    return deepcopy(validated)
