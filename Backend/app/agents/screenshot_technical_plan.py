"""Bound TechnicalPlan model output for screenshot-created applications.

The text-input planner remains the authoritative implementation. Screenshot
projects use the same prompt, parser, normalizer and validators, but request one
ProductPlan page at a time so an API output limit cannot truncate the whole app.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import logging
import re
from typing import Any, Callable

from app.agents.main.planner import (
    _invoke_live_chat_model,
    _parse_technical_plan_model_output,
)
from app.config import Settings
from app.services.project_plan import create_technical_plan


logger = logging.getLogger(__name__)


def _page_baseline(plan: dict[str, Any] | None, page_id: str) -> dict[str, Any] | None:
    """Keep a revision prompt local to its page instead of repeating the entire plan."""

    if not isinstance(plan, dict):
        return None
    pages = [
        page for page in plan.get("pages", [])
        if isinstance(page, dict) and page.get("pageId") == page_id
    ]
    endpoint_ids = {
        str(item.get("endpoint_id") or "")
        for page in pages
        for item in (page.get("references") or {}).get("endpoint_dependencies", [])
        if isinstance(item, dict)
    }
    contracts = [
        contract for contract in plan.get("api_contracts", [])
        if isinstance(contract, dict)
        and any(
            isinstance(endpoint, dict) and endpoint.get("id") in endpoint_ids
            for endpoint in contract.get("endpoints", [])
        )
    ]
    entity_ids = {
        str(entity_id)
        for contract in contracts
        for entity_id in contract.get("entity_ids", [])
    }
    return {
        "architecture": deepcopy(plan.get("architecture", {})),
        "entities": deepcopy([
            entity for entity in plan.get("entities", [])
            if isinstance(entity, dict) and entity.get("id") in entity_ids
        ]),
        "api_contracts": deepcopy(contracts),
        "pages": deepcopy(pages),
    }


def _namespace_fragment(fragment: dict[str, Any], page_id: str, scope_key: str) -> None:
    """Give independently generated contracts stable page-local identities."""

    slug = re.sub(r"[^a-zA-Z0-9_]", "_", scope_key).strip("_") or "page"
    prefix = f"{slug}_{sha256(scope_key.encode('utf-8')).hexdigest()[:8]}"
    endpoint_map: dict[str, str] = {}
    for contract in fragment["api_contracts"]:
        old_contract_id = str(contract.get("id") or "").strip()
        if not old_contract_id:
            continue
        contract["id"] = f"{prefix}__{old_contract_id}"
        for endpoint in contract.get("endpoints", []):
            if not isinstance(endpoint, dict):
                continue
            old_endpoint_id = str(endpoint.get("id") or "").strip()
            if not old_endpoint_id:
                continue
            if old_endpoint_id in endpoint_map:
                raise ValueError(f"截图技术规划页面 {page_id} 返回重复 endpointId：{old_endpoint_id}")
            endpoint_map[old_endpoint_id] = f"{prefix}__{old_endpoint_id}"
            endpoint["id"] = endpoint_map[old_endpoint_id]
    for page in fragment["pages"]:
        references = page.get("references")
        if not isinstance(references, dict):
            continue
        for dependency in references.get("endpoint_dependencies", []):
            if isinstance(dependency, dict):
                endpoint_id = dependency.get("endpoint_id")
                if endpoint_id in endpoint_map:
                    dependency["endpoint_id"] = endpoint_map[endpoint_id]
        for action in references.get("action_implementations", []):
            if not isinstance(action, dict):
                continue
            endpoint_id = action.get("endpointId")
            if endpoint_id in endpoint_map:
                action["endpointId"] = endpoint_map[endpoint_id]
            for binding in action.get("stepBindings", []):
                if isinstance(binding, dict) and binding.get("endpointId") in endpoint_map:
                    binding["endpointId"] = endpoint_map[binding["endpointId"]]


def _retain_referenced_contracts(fragment: dict[str, Any]) -> None:
    """Discard contracts that a scoped page did not select for its own behavior."""

    used_endpoints: set[str] = set()
    for page in fragment["pages"]:
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        used_endpoints.update(
            str(item.get("endpoint_id") or "")
            for item in references.get("endpoint_dependencies", [])
            if isinstance(item, dict)
        )
        for action in references.get("action_implementations", []):
            if not isinstance(action, dict):
                continue
            used_endpoints.add(str(action.get("endpointId") or ""))
            used_endpoints.update(
                str(binding.get("endpointId") or "")
                for binding in action.get("stepBindings", [])
                if isinstance(binding, dict)
            )
    used_endpoints.discard("")
    if not used_endpoints:
        return
    fragment["api_contracts"] = [
        contract for contract in fragment["api_contracts"]
        if any(
            isinstance(endpoint, dict) and endpoint.get("id") in used_endpoints
            for endpoint in contract.get("endpoints", [])
        )
    ]


def _close_entity_field_refs(plan: dict[str, Any]) -> None:
    """Materialize fields explicitly referenced by the model's retained schemas."""

    entities = {
        str(entity.get("id") or ""): entity
        for entity in plan["entities"] if isinstance(entity, dict)
    }

    def visit(value: Any, property_name: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, property_name)
            return
        if not isinstance(value, dict):
            return
        reference = value.get("entity_field_ref")
        if isinstance(reference, str) and "." in reference:
            entity_id, field_name = reference.split(".", 1)
            entity = entities.get(entity_id)
            if entity is not None and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field_name):
                fields = entity.get("fields")
                if not isinstance(fields, list):
                    fields = []
                    entity["fields"] = fields
                if not any(
                    isinstance(field, dict) and field.get("name") == field_name
                    for field in fields
                ):
                    schema_type = value.get("type") if isinstance(value.get("type"), str) else ""
                    field_type = {
                        "integer": "number", "number": "decimal", "boolean": "boolean",
                    }.get(schema_type, "text")
                    if schema_type == "string" and value.get("format") == "date-time":
                        field_type = "datetime"
                    elif schema_type == "string" and value.get("format") == "date":
                        field_type = "date"
                    fields.append({
                        "name": field_name,
                        "label": str(value.get("description") or property_name or field_name),
                        "description": str(value.get("description") or property_name or field_name),
                        "type": field_type,
                        "required": False,
                    })
        for key, child in value.items():
            if key != "entity_field_ref":
                visit(child, str(key))

    for contract in plan["api_contracts"]:
        schemas = contract.get("schemas")
        if isinstance(schemas, dict):
            for schema in schemas.values():
                visit(schema)


def _merge_fragments(fragments: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine page-local results without dropping entity fields or page bindings."""

    result: dict[str, Any] = {
        "architecture": deepcopy(fragments[0]["architecture"]),
        "entities": [],
        "api_contracts": [],
        "pages": [],
    }
    entities_by_id: dict[str, dict[str, Any]] = {}
    contracts_by_id: dict[str, dict[str, Any]] = {}
    pages_by_id: dict[str, dict[str, Any]] = {}
    for fragment in fragments:
        for entity in fragment["entities"]:
            entity_id = str(entity.get("id") or "").strip()
            if not entity_id:
                continue
            if entity_id not in entities_by_id:
                entities_by_id[entity_id] = deepcopy(entity)
                result["entities"].append(entities_by_id[entity_id])
                continue
            current = entities_by_id[entity_id]
            fields = current.get("fields")
            if not isinstance(fields, list):
                fields = []
                current["fields"] = fields
            known_fields = {
                str(field.get("name") or "")
                for field in fields if isinstance(field, dict)
            }
            incoming_fields = entity.get("fields")
            for field in incoming_fields if isinstance(incoming_fields, list) else []:
                field_name = str(field.get("name") or "").strip() if isinstance(field, dict) else ""
                if field_name and field_name not in known_fields:
                    fields.append(deepcopy(field))
                    known_fields.add(field_name)
        for contract in fragment["api_contracts"]:
            contract_id = str(contract.get("id") or "")
            if contract_id not in contracts_by_id:
                contracts_by_id[contract_id] = deepcopy(contract)
                result["api_contracts"].append(contracts_by_id[contract_id])
                continue
            current_contract = contracts_by_id[contract_id]
            current_entity_ids = current_contract.get("entity_ids")
            if not isinstance(current_entity_ids, list):
                current_entity_ids = []
                current_contract["entity_ids"] = current_entity_ids
            incoming_entity_ids = contract.get("entity_ids")
            for entity_id in incoming_entity_ids if isinstance(incoming_entity_ids, list) else []:
                if entity_id not in current_entity_ids:
                    current_entity_ids.append(entity_id)
            current_schemas = current_contract.get("schemas")
            if not isinstance(current_schemas, dict):
                current_schemas = {}
                current_contract["schemas"] = current_schemas
            incoming_schemas = contract.get("schemas")
            for schema_id, schema in incoming_schemas.items() if isinstance(incoming_schemas, dict) else []:
                if schema_id not in current_schemas:
                    current_schemas[schema_id] = deepcopy(schema)
            current_endpoints = current_contract.get("endpoints")
            if not isinstance(current_endpoints, list):
                current_endpoints = []
                current_contract["endpoints"] = current_endpoints
            known_endpoints = {
                str(item.get("id") or "")
                for item in current_endpoints if isinstance(item, dict)
            }
            incoming_endpoints = contract.get("endpoints")
            for endpoint in incoming_endpoints if isinstance(incoming_endpoints, list) else []:
                if isinstance(endpoint, dict) and str(endpoint.get("id") or "") not in known_endpoints:
                    current_endpoints.append(deepcopy(endpoint))
                    known_endpoints.add(str(endpoint.get("id") or ""))
        for page in fragment["pages"]:
            page_id = str(page.get("pageId") or "")
            if page_id not in pages_by_id:
                pages_by_id[page_id] = deepcopy(page)
                result["pages"].append(pages_by_id[page_id])
                continue
            current_refs = pages_by_id[page_id].setdefault("references", {})
            incoming_refs = page.get("references") or {}
            for key, identity in (
                ("endpoint_dependencies", "endpoint_id"),
                ("action_implementations", "actionId"),
            ):
                current_items = current_refs.setdefault(key, [])
                known = {
                    str(item.get(identity) or "")
                    for item in current_items if isinstance(item, dict)
                }
                for item in incoming_refs.get(key, []):
                    if isinstance(item, dict) and str(item.get(identity) or "") not in known:
                        current_items.append(deepcopy(item))
                        known.add(str(item.get(identity) or ""))
    _close_entity_field_refs(result)
    return result


def _generate_page_fragments(
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    page: dict[str, Any],
    *,
    settings: Settings,
    existing_plan: dict[str, Any] | None,
    on_token: Callable[[str], None] | None,
    scope_key: str,
) -> list[dict[str, Any]]:
    """Split only a truncated single-page response into smaller action/data scopes."""

    page_id = str(page["pageId"])
    scoped_requirement = {
        **requirement_spec,
        "confirmed_product_plan": {**product_plan, "pages": [page]},
    }
    note = _invoke_live_chat_model(
        scoped_requirement,
        existing_plan=_page_baseline(existing_plan, page_id),
        settings=settings,
        on_token=on_token,
    )
    try:
        fragment = _parse_technical_plan_model_output(note)
    except ValueError as exc:
        truncated = note.lstrip().startswith("{") and not note.rstrip().endswith("}")
        if truncated and existing_plan is None:
            for field in ("actions", "information_items"):
                items = page.get(field)
                if isinstance(items, list) and len(items) > 1:
                    middle = len(items) // 2
                    logger.warning(
                        "screenshot_technical_plan_page_split page_id=%s field=%s items=%s",
                        page_id, field, len(items),
                    )
                    return [
                        fragment
                        for number, part in enumerate((items[:middle], items[middle:]), 1)
                        for fragment in _generate_page_fragments(
                            requirement_spec,
                            product_plan,
                            {**page, field: part},
                            settings=settings,
                            existing_plan=existing_plan,
                            on_token=on_token,
                            scope_key=f"{scope_key}_part{number}",
                        )
                    ]
        raise ValueError(f"截图技术规划页面 {page_id} 生成失败：{exc}") from exc
    generated_ids = [str(item.get("pageId") or "") for item in fragment["pages"]]
    if generated_ids != [page_id]:
        raise ValueError(
            f"截图技术规划页面 {page_id} 返回的页面集合不完整或不匹配：{generated_ids}"
        )
    if existing_plan is None:
        _namespace_fragment(fragment, page_id, scope_key)
    _retain_referenced_contracts(fragment)
    return [fragment]


def plan_screenshot_technical_plan_with_chat_model(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None = None,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Generate one bounded model response per confirmed page, then normalize once."""

    product_plan = requirement_spec.get("confirmed_product_plan")
    if not isinstance(product_plan, dict):
        raise ValueError("截图技术规划缺少已确认 ProductPlan。")
    pages = product_plan.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("截图技术规划缺少 ProductPlan 页面。")
    settings = Settings.from_env()
    fragments: list[dict[str, Any]] = []
    for index, page in enumerate(pages, 1):
        if not isinstance(page, dict) or not str(page.get("pageId") or "").strip():
            raise ValueError("截图技术规划 ProductPlan 包含无效页面身份。")
        page_id = str(page["pageId"])
        logger.info(
            "screenshot_technical_plan_page_start page_id=%s index=%s total=%s",
            page_id, index, len(pages),
        )
        page_fragments = _generate_page_fragments(
            requirement_spec,
            product_plan,
            page,
            settings=settings,
            existing_plan=existing_plan,
            on_token=on_token,
            scope_key=page_id,
        )
        fragments.extend(page_fragments)
        logger.info(
            "screenshot_technical_plan_page_complete page_id=%s contracts=%s entities=%s",
            page_id,
            sum(len(fragment["api_contracts"]) for fragment in page_fragments),
            sum(len(fragment["entities"]) for fragment in page_fragments),
        )
    merged = _merge_fragments(fragments)
    plan = create_technical_plan(requirement_spec, agent_plan=merged)
    # The canonical normalizer can add schema-to-entity references while it
    # compiles contracts, so close those references on the final artifact too.
    _close_entity_field_refs(plan)
    return plan
