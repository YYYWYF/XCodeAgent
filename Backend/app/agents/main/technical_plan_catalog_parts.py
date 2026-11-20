"""分段生成并校验全局目录，成功后原子发布完整目录。"""

from copy import deepcopy
from graphlib import CycleError, TopologicalSorter

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
    contract_registry = fragment(
        prefix + "registry:" + entity_registry_digest,
        registry_prompt(frozen, entity_identities),
        lambda value: validate_registry(value, product, entities=entity_identities),
    )
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
    contracts = []
    for identity in registry["contracts"]:
        key = prefix + "endpoints:" + fragment_digest({"identity": identity, "entities": entities, "completed": contracts})
        current_requirement = frozen
        if key in cache:
            try:
                validate_endpoint_directory(cache[key], identity=identity, entities=entities, completed=contracts, product=product, registry=registry)
            except ValueError as exc:
                # 当前候选缓存同样必须通过归属校验；原子撤下错误片段后才允许下一次模型调用。
                # 后续目录及映射的键包含已完成接口内容，来源变化后不会命中错误依赖。
                publish({name: value for name, value in cache.items() if name != key})
                current_requirement = {**frozen, "technical_plan_validation_feedback":
                    str(frozen.get("technical_plan_validation_feedback") or "") + "\n" + str(exc)}
        endpoints = fragment(key, endpoints_prompt(current_requirement, identity, entities, contracts, registry=registry),
            lambda value, identity=identity: validate_endpoint_directory(value, identity=identity, entities=entities, completed=contracts, product=product, registry=registry))
        contracts.append({**identity, **endpoints})
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
