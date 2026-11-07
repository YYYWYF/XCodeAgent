"""在原 Contract 身份内生成可恢复的批次，最终由代码拼成一个完整 Contract。"""

from copy import deepcopy

from app.agents.main.technical_plan_contract_budget import (
    approximate_tokens, contract_part_prefix, fragment_digest, needs_batches, output_targets, pack_groups,
)
from app.agents.main.technical_plan_contract_batch_prompts import (
    endpoint_batch_prompt, schema_fields_prompt, schema_plan_prompt,
)
from app.agents.main.technical_plan_contract_batch_validation import (
    SchemaPlanRepairRequired, contract_body, validate_endpoint_batch, validate_schema_fields, validate_schema_plan, validate_schema_semantics,
    validate_frozen_schema_responses,
)
from app.agents.main.technical_plan_fragment_output import PlanningOutputTruncated
from app.agents.main.technical_plan_generation_prompts import contract_prompt
from app.agents.main.technical_plan_generation_validation import validate_contract


def _discard_contract(cache: dict, key: str, prefix: str, publish, *, batched: bool = False) -> None:
    """冻结 Schema 决策有误时撤下当前 Contract，保留其他业务能力的已完成候选。"""

    retained = {name: value for name, value in cache.items() if name != key and not name.startswith(prefix)}
    if batched or prefix + "mode" in cache:
        retained[prefix + "mode"] = {"batched": True}
    publish(retained)


def generate_contract(requirement: dict, catalog: dict, spec: dict, *, cache: dict, fragment, publish, max_output_tokens: int) -> dict:
    """小契约沿用一次生成，大契约将完成片段持久化后再推进下一个批次。"""

    key = f"contract:{spec['id']}"
    prefix = contract_part_prefix(spec["id"])
    if key in cache:
        try:
            return validate_contract({"api_contracts": [deepcopy(cache[key])]}, catalog, spec)
        except ValueError as exc:
            _discard_contract(cache, key, prefix, publish)
            # 未通过当前校验的缓存视为未命中，只生成一次；模型失败仍由外层三轮预算处理。
            requirement = {**requirement, "technical_plan_validation_feedback":
                str(requirement.get("technical_plan_validation_feedback") or "") + "\n" + str(exc)}
    if not needs_batches(catalog, spec, max_output_tokens) and prefix + "mode" not in cache:
        try:
            return fragment(key, contract_prompt(requirement, catalog, spec), lambda value: validate_contract(value, catalog, spec))
        except PlanningOutputTruncated:
            # 缩批只改变下次现有外层重试的任务形态，不偷偷增加一次内部重试。
            publish({**cache, prefix + "mode": {"batched": True}})
            raise
    plan = fragment(prefix + "plan", schema_plan_prompt(requirement, catalog, spec), lambda value: validate_schema_plan(value, spec, catalog))
    identity = fragment_digest(plan)
    _, target = output_targets(max_output_tokens)

    def group_parts(kind: str, items: list, prompt, validate) -> list[dict]:
        """按内容身份复用片段；只有明确截断才记录二分，下一轮复用已经成功的邻居。"""

        task = prefix + kind + ":" + fragment_digest({"plan": identity, "items": items})
        split_key = task + ":split"
        if split_key in cache:
            middle = len(items) // 2
            return group_parts(kind, items[:middle], prompt, validate) + group_parts(kind, items[middle:], prompt, validate)
        try:
            return [fragment(task, prompt(items), lambda value: validate(value, items))]
        except PlanningOutputTruncated as exc:
            if len(items) <= 1:
                raise ValueError("单个接口或 Schema 字段仍达到输出上限，无法继续二分；请精简定义或提高输出预算。") from exc
            publish({**cache, split_key: {"split": True}})
            raise

    schemas = {}
    for schema in plan["schemas"]:
        properties = {}
        groups = pack_groups(schema["fields"], limit=24, target=target,
            weight=lambda field: 140 + approximate_tokens(field))
        for fields in groups:
            try:
                parts = group_parts("schema:" + schema["name"], fields,
                    lambda current: schema_fields_prompt(requirement, catalog, spec, plan, schema, current),
                    lambda value, current: validate_schema_fields(value, schema=schema, fields=current, catalog=catalog, spec=spec, plan=plan))
            except SchemaPlanRepairRequired:
                # 字段阶段不能修订冻结的引用决策；原子撤下所属 Contract 后由原外层预算重建目录。
                _discard_contract(cache, key, prefix, publish, batched=True)
                raise
            for part in parts:
                properties.update(part["properties"])
        schemas[schema["name"]] = deepcopy(schema["base"])
        if schema["base"].get("type") == "object":
            schemas[schema["name"]].update(properties=properties, required=schema["required"])
    validate_schema_semantics(schemas, catalog=catalog, spec=spec, plan=plan)
    try:
        validate_frozen_schema_responses(schemas, spec=spec, plan=plan)
    except SchemaPlanRepairRequired:
        # 接口批次不能改写已冻结的必填目录或字段类型，先撤下所属契约再交回原重试预算。
        _discard_contract(cache, key, prefix, publish, batched=True)
        raise
    endpoints = []
    # 相同请求/响应 Schema 的接口相邻生成，减少每批重复携带的定义；正式顺序仍由目录恢复。
    refs = {item["endpointId"]: item for item in plan["endpoint_refs"]}
    families = {}
    for endpoint in spec["endpoints"]:
        ref = refs[endpoint["id"]]
        families.setdefault((ref["request_schema_ref"], ref["response_schema_ref"]), []).append(endpoint)
    groups = pack_groups([endpoint for family in families.values() for endpoint in family], limit=6, target=target,
        weight=lambda endpoint: 450 + approximate_tokens(endpoint))
    for group in groups:
        parts = group_parts("endpoints", group,
            lambda current: endpoint_batch_prompt(requirement, catalog, spec, plan, schemas, current),
            lambda value, current: validate_endpoint_batch(value, endpoints=current, schemas=schemas, plan=plan, catalog=catalog, spec=spec))
        endpoints.extend(endpoint for part in parts for endpoint in part["endpoints"])
    # 接口顺序由目录决定，不受模型返回或缩批顺序影响；最终仍走原 Contract 完整校验。
    by_id = {endpoint["id"]: endpoint for endpoint in endpoints}
    body = contract_body(spec, plan, schemas, [by_id[endpoint["id"]] for endpoint in spec["endpoints"]])
    validated = validate_contract({"api_contracts": [body]}, catalog, spec)
    publish({**cache, key: validated})
    return deepcopy(validated)
