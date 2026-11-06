"""分段生成全局目录，成功后原子发布完整目录及下游依赖失效。"""

from copy import deepcopy

from app.agents.main.technical_plan_catalog_repair import CatalogRepairRequired, business_slots, repaired_catalog_fragments
from app.agents.main.technical_plan_contract_budget import fragment_digest
from app.agents.main.technical_plan_catalog_parts_prompts import registry_prompt, entity_prompt, endpoints_prompt, bindings_prompt
from app.agents.main.technical_plan_catalog_parts_validation import (
    catalog_registry, validate_registry, validate_entity_fields, validate_endpoint_directory, validate_page_bindings,
)
from app.agents.main.technical_plan_generation_validation import validate_catalog


def generate_catalog(requirement: dict, *, cache: dict, fragment, publish) -> dict:
    """全局只生成轻量身份，其余按实体、Contract 和页面局部生成并持久复用。"""

    if "catalog" in cache:
        return deepcopy(cache["catalog"])
    product = requirement["confirmed_product_plan"]
    repair = cache.get("catalog_repair")
    prefix = "catalog-part:" + fragment_digest(repair or {"initial": True}) + ":"
    frozen = deepcopy(requirement)
    if repair:
        frozen["technical_plan_validation_feedback"] = ""
    registry = fragment(prefix + "registry", registry_prompt(frozen, repair), lambda value: validate_registry(value, product))
    previous = repair["catalog"] if repair else {"entities": [], "contracts": [], "action_bindings": []}
    old_registry = catalog_registry(previous)
    old_entities = {entity["id"]: entity for entity in previous["entities"]}
    old_contracts = {contract["id"]: contract for contract in previous["contracts"]}
    affected_entities = {entity_id for contract in previous["contracts"] if repair and repair["issue"]["pageId"] in contract["page_ids"]
        for entity_id in contract["entity_ids"]}
    entities, contracts = [], []
    for identity in registry["entities"]:
        old = old_entities.get(identity["id"])
        if old and identity in old_registry["entities"] and identity["id"] not in affected_entities:
            entities.append(deepcopy(old))
            continue
        context = {"issue": repair["issue"], "previous_entity": old} if repair and identity["id"] in affected_entities else None
        key = prefix + "entity:" + fragment_digest(identity)
        fields = fragment(key, entity_prompt(frozen, registry, identity, context), lambda value, identity=identity: validate_entity_fields(value, identity))
        entities.append({**identity, **fields})
    changed_entities = {entity["id"] for entity in entities if entity != old_entities.get(entity["id"])}
    for identity in registry["contracts"]:
        old = old_contracts.get(identity["id"])
        affected = bool(repair and (repair["issue"]["pageId"] in identity["page_ids"] or changed_entities.intersection(identity["entity_ids"])))
        if old and identity in old_registry["contracts"] and not affected:
            # 复用旧目录前仍检查它与本轮已完成接口是否冲突，不能把旧候选当成全局特权。
            try:
                validate_endpoint_directory({"endpoints": old["endpoints"]}, identity=identity, entities=entities, completed=contracts, product=product)
            except ValueError:
                affected = True
            else:
                contracts.append(deepcopy(old))
                continue
        context = {"issue": repair["issue"], "previous_endpoints": (old or {}).get("endpoints", [])} if affected else None
        key = prefix + "endpoints:" + fragment_digest({"identity": identity, "entities": entities, "completed": contracts})
        endpoints = fragment(key, endpoints_prompt(frozen, identity, entities, contracts, context),
            lambda value, identity=identity: validate_endpoint_directory(value, identity=identity, entities=entities, completed=contracts, product=product))
        contracts.append({**identity, **endpoints})
    catalog = {"entities": entities, "contracts": contracts, "action_bindings": []}
    changed_contracts = {contract["id"] for contract in contracts if contract != old_contracts.get(contract["id"])}
    changed_pages = {page_id for contract in [*previous["contracts"], *contracts] if contract["id"] in changed_contracts
        or changed_entities.intersection(contract["entity_ids"]) for page_id in contract["page_ids"]}
    for page in product.get("pages", []):
        if not business_slots({"pages": [page]}):
            continue
        old = {"action_bindings": [binding for binding in previous["action_bindings"] if binding["pageId"] == page["pageId"]]}
        if repair and page["pageId"] not in changed_pages and page["pageId"] != repair["issue"]["pageId"]:
            try:
                bindings = validate_page_bindings(old, page=page, catalog=catalog)
            except ValueError:
                pass
            else:
                catalog["action_bindings"].extend(bindings["action_bindings"])
                continue
        context = {"issue": repair["issue"]} if repair and page["pageId"] == repair["issue"]["pageId"] else None
        key = prefix + "bindings:" + fragment_digest({"page": page, "contracts": contracts})
        try:
            bindings = fragment(key, bindings_prompt(frozen, page, catalog, context),
                lambda value, page=page: validate_page_bindings(value, page=page, catalog=catalog))
        except CatalogRepairRequired as exc:
            publish({**{key: value for key, value in cache.items() if key != "catalog" and not key.startswith("catalog-part:")},
                "catalog_repair": {"catalog": catalog, "baseline": repair["baseline"] if repair else catalog, "issue": exc.issue}})
            raise
        catalog["action_bindings"].extend(bindings["action_bindings"])
    validated = validate_catalog(catalog, product)
    try:
        published = repaired_catalog_fragments(cache, validated) if repair else {**cache, "catalog": validated}
    except ValueError:
        # 完整目录仍是无效修复时，撤下本轮目录候选，下一次预算必须真的发起修复请求。
        publish({key: value for key, value in cache.items() if not key.startswith("catalog-part:")})
        raise
    publish({key: value for key, value in published.items() if not key.startswith("catalog-part:")})
    return deepcopy(validated)
