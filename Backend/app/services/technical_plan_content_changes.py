"""技术规划变更后的全局结构与引用不变量，不解析自然语言或执行内容合并。"""

from __future__ import annotations

from typing import Any

from app.services.technical_plan_changeset import revision_document
from app.services.technical_plan_logical_patch import item_identity


def technical_plan_content_errors(plan: dict[str, Any], product_plan: dict[str, Any] | None = None) -> list[str]:
    """统一检查所有技术节点的身份、数组结构及引用闭合，失败不补回删除内容。"""

    errors: list[str] = []
    try:
        document = revision_document(plan)
    except KeyError as exc:
        return [f"TechnicalPlan 缺少必需段：{exc.args[0]}。"]
    architecture = document["architecture"]
    if not isinstance(architecture, dict) or set(architecture) != {"frontend", "backend", "data"}:
        errors.append("TechnicalPlan architecture 必须且只能包含 frontend/backend/data。")
    elif any(not isinstance(value, str) or not value.strip() for value in architecture.values()):
        errors.append("TechnicalPlan architecture 说明必须是非空字符串。")

    def rows(value: Any, owner: str, identity: str | None = None) -> list[dict[str, Any]]:
        """校验对象数组及稳定身份唯一性，避免归一化过滤隐藏非法输入。"""

        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            errors.append(f"{owner} 必须是对象数组。")
            return []
        identities = [item.get(identity) if identity else item_identity(item) for item in value]
        if any(not isinstance(key, str) or not key.strip() for key in identities):
            errors.append(f"{owner} 的稳定身份必须为非空字符串。")
        elif len(set(identities)) != len(identities):
            errors.append(f"{owner} 的稳定身份重复。")
        return value

    entities = rows(document["entities"], "entities", "id")
    fields = {}
    for entity in entities:
        identity = entity.get("id")
        entity_fields = rows(entity.get("fields"), f"entities/{identity}/fields", "name")
        fields[str(identity)] = {str(field.get("name")) for field in entity_fields}
    contracts = rows(document["api_contracts"], "api_contracts", "id")
    endpoints: set[str] = set()
    routes: set[tuple[str, str]] = set()

    def schema_references(value: Any, definitions: dict, owner: str) -> None:
        """只遍历实际 Schema 子节点，示例和默认值不被误当作引用或平台字段。"""

        if not isinstance(value, dict):
            return
        ref = value.get("$ref")
        if ref is not None and (not isinstance(ref, str) or ref.split("/")[-1] not in definitions):
            errors.append(f"{owner} 引用了未知 Schema：{ref}。")
        field_ref = value.get("entity_field_ref")
        if field_ref is not None:
            target = str(field_ref).split(".", 1)
            if len(target) != 2 or target[1] not in fields.get(target[0], set()):
                errors.append(f"{owner} 引用了不存在的实体字段：{field_ref}。请显式清理或解除该引用。")
        properties = value.get("properties")
        required = value.get("required")
        if isinstance(properties, dict) and isinstance(required, list) and set(required) - properties.keys():
            errors.append(f"{owner} 的 required 引用了不存在的属性。")
        for key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"):
            children = value.get(key)
            if isinstance(children, dict):
                for name, child in children.items():
                    schema_references(child, definitions, owner + "/" + name)
        for key in ("items", "allOf", "anyOf", "oneOf", "prefixItems", "not", "if", "then", "else", "additionalProperties", "contains"):
            child = value.get(key)
            for node in child if isinstance(child, list) else [child]:
                schema_references(node, definitions, owner + "/" + key)

    for contract in contracts:
        owner = f"api_contracts/{contract.get('id')}"
        definitions = contract.get("schemas")
        if not isinstance(definitions, dict) or any(not isinstance(value, dict) for value in definitions.values()):
            errors.append(f"{owner}/schemas 必须是 Schema 对象表。")
            definitions = {}
        for name, schema in definitions.items():
            schema_references(schema, definitions, owner + "/schemas/" + name)
        for endpoint in rows(contract.get("endpoints"), owner + "/endpoints", "id"):
            endpoint_id = str(endpoint.get("id"))
            if endpoint_id in endpoints:
                errors.append(f"Endpoint ID 重复：{endpoint_id}。")
            endpoints.add(endpoint_id)
            route = (str(endpoint.get("method")), str(endpoint.get("path")))
            if route in routes:
                errors.append(f"Endpoint 方法与路径重复：{route[0]} {route[1]}。")
            routes.add(route)
            if "parameters" in endpoint:
                for parameter in rows(endpoint["parameters"], owner + "/endpoints/" + endpoint_id + "/parameters"):
                    schema_references(parameter.get("schema"), definitions, owner + "/parameters")

    pages = rows(document["pages"], "pages", "pageId")
    if isinstance(product_plan, dict):
        confirmed_ids = {page.get("pageId") for page in product_plan.get("pages") or []
                         if isinstance(page, dict) and page.get("_change_status") != "deleted"}
        if {page.get("pageId") for page in pages} != confirmed_ids:
            errors.append("技术页面范围必须与已确认 ProductPlan 一致；页面增删或产品动作变更请进入上游正式修订。")
    for page in pages:
        owner = f"pages/{page.get('pageId')}"
        references = page.get("references")
        if not isinstance(references, dict):
            errors.append(f"{owner}/references 必须是对象。")
            continue
        for key in ("endpoint_dependencies", "action_implementations"):
            if key not in references:
                continue
            for binding in rows(references[key], owner + "/references/" + key):
                targets = [binding.get("endpoint_id"), binding.get("endpointId")]
                if "stepBindings" in binding:
                    targets += [step.get("endpointId") for step in rows(binding["stepBindings"], owner + "/stepBindings", "stepId")]
                for target in targets:
                    if target and target not in endpoints:
                        errors.append(f"{owner} 引用了不存在的 Endpoint：{target}。请显式清理或替换该引用。")
    return errors
