"""分段生成并校验全局目录，成功后原子发布完整目录。"""

from copy import deepcopy

from app.agents.main.technical_plan_catalog_repair import business_slots
from app.agents.main.technical_plan_contract_budget import fragment_digest
from app.agents.main.technical_plan_catalog_parts_prompts import registry_prompt, entity_prompt, endpoints_prompt, bindings_prompt
from app.agents.main.technical_plan_catalog_parts_validation import (
    validate_registry, validate_entity_fields, validate_endpoint_directory, validate_page_bindings,
)
from app.agents.main.technical_plan_generation_validation import validate_catalog


def generate_catalog(requirement: dict, *, cache: dict, fragment, publish) -> dict:
    """全局只生成轻量身份，其余按实体、Contract 和页面局部生成并持久复用。"""

    if "catalog" in cache:
        return deepcopy(cache["catalog"])
    product = requirement["confirmed_product_plan"]
    prefix = "catalog-part:" + fragment_digest({"initial": True}) + ":"
    frozen = deepcopy(requirement)
    registry = fragment(prefix + "registry", registry_prompt(frozen), lambda value: validate_registry(value, product))
    entities, contracts = [], []
    for identity in registry["entities"]:
        key = prefix + "entity:" + fragment_digest(identity)
        fields = fragment(key, entity_prompt(frozen, registry, identity), lambda value, identity=identity: validate_entity_fields(value, identity))
        entities.append({**identity, **fields})
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
