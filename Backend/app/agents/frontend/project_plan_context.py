"""仅从已批准的前端任务构造 Prompt 用的 ProjectPlan 视图。"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any

from app.services.api_schema_refs import normalize_local_schema_ref
from app.services.frontend_page_tree import project_plan_page_records


EndpointKey = tuple[str, str]
_SCHEMA_MAPS = {"properties", "patternProperties", "definitions", "$defs", "dependentSchemas"}
_SCHEMA_REFS = {"$ref", "schema_ref", "request_schema_ref", "response_schema_ref"}
_ROUTE_FIELDS = ("pageId", "id", "path", "route", "route_params", "routeParams")


class _UncertainScope(ValueError):
    """表示现有正式数据不足以证明裁剪完整性。"""


class FrontendTaskProjectionError(ValueError):
    """以任务、阶段和引用类别报告范围错误。"""

    def __init__(self, task_id, stage: str, reference: str):
        """保留诊断身份，避免泄露正式契约内容。"""
        self.stage = stage
        super().__init__(f"前端任务 {task_id or 'unknown'} 的 {stage} 范围无法确定（{reference}）。")


def _identity(value: Any) -> str:
    """要求正式标识为非空字符串，避免把错误值转换成身份。"""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _UncertainScope
    return value


def _records(value: Any) -> list[dict[str, Any]]:
    """读取对象列表，阻止格式不合法的引用被静默跳过。"""
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise _UncertainScope
    return value


def _ids(value: Any) -> set[str]:
    """读取正式身份列表，显式空列表代表没有依赖。"""
    if not isinstance(value, list):
        raise _UncertainScope
    return {_identity(item) for item in value}


def _index(items: list[dict[str, Any]], key: str, *, alternate: str = "") -> dict[str, dict[str, Any]]:
    """构造唯一索引并维持正式列表顺序。"""
    indexed = {}
    for item in items:
        identity = _identity(item.get(key) or item.get(alternate))
        if identity in indexed:
            raise _UncertainScope
        indexed[identity] = item
    return indexed


def _endpoint_pairs(value: Any, *, endpoint_field: str = "endpoint_id") -> set[EndpointKey]:
    """读取业务检查和编译来源中的完整接口组合身份。"""
    return {
        (_identity(item["api_contract_id"]), _identity(item[endpoint_field]))
        for item in _records(value)
    }


def _expected_checks(task: dict, kind: str) -> list[dict]:
    """取得当前任务中对应职责的正式验收预期。"""
    checks = _records(task.get("business_acceptance_checks", []))
    return [check["expected"] for check in checks if check["kind"] == kind]


def _api_task_endpoints(task: dict, targets: set[str], catalog: dict[str, set[str]]) -> set[EndpointKey]:
    """优先使用已编译验收，以交付物反向确认接口职责。"""
    checks = _expected_checks(task, "frontend.api_contract")
    selected = set()
    for expected in checks:
        required = _endpoint_pairs(expected.get("required_endpoints", expected.get("endpoints")))
        if not required or ("endpoints" in expected and _endpoint_pairs(expected["endpoints"]) != required):
            raise _UncertainScope
        selected.update(required)

    if not checks:
        if task.get("kind") == "repair":
            raise _UncertainScope
        refs = task.get("source_refs", {})
        explicit_pairs = _endpoint_pairs(refs.get("technical_plan_endpoints", []), endpoint_field="id")
        contract_ids = _ids(refs.get("api_contract_ids", []))
        endpoint_ids = _ids(refs.get("endpoint_ids", []))
        source_target = refs.get("target", {})
        if source_target.get("type") == "endpoint" and source_target.get("api_contract_id"):
            explicit_pairs.add((_identity(source_target["api_contract_id"]), _identity(source_target["id"])))
        # Unit 级端点可能很宽，只在交付物直接点名 Endpoint 时用它交叉验证。
        for target in targets:
            candidates = explicit_pairs & {(contract_id, target) for contract_id in catalog.get(target, set())}
            if not candidates and target in endpoint_ids:
                candidates = {(contract_id, target) for contract_id in contract_ids & catalog.get(target, set())}
            if len(candidates) != 1:
                raise _UncertainScope
            selected.update(candidates)

    # 当前正式任务按 Contract 生成；同名 Endpoint 不得让一项交付物认领两个模块。
    if len({contract_id for contract_id, _ in selected}) != 1:
        raise _UncertainScope
    if any(not ({contract_id, endpoint_id} & targets) for contract_id, endpoint_id in selected):
        raise _UncertainScope
    if targets - {identity for pair in selected for identity in pair}:
        raise _UncertainScope
    return selected


def _page_endpoints(task: dict, page: dict, contract: dict, catalog: dict[str, set[str]]) -> set[EndpointKey]:
    """校核页面合同、技术引用、动作和当前任务的必要接口。"""
    required = _ids(contract["requiredEndpointIds"])
    compiled_refs = task.get("source_refs", {})
    if _ids(compiled_refs.get("endpoint_ids", [])) - required:
        raise _UncertainScope
    explicit_pairs = _endpoint_pairs(compiled_refs.get("technical_plan_endpoints", []), endpoint_field="id")
    references = page.get("references", {})
    technical = contract.get("technicalReferences", {})
    for source, key in ((references, "endpoint_dependencies"), (technical, "endpointDependencies")):
        if key not in source:
            continue
        dependencies = _records(source[key])
        if {_identity(item["endpoint_id"]) for item in dependencies} != required:
            raise _UncertainScope
        explicit_pairs.update(
            (_identity(item["api_contract_id"]), item["endpoint_id"])
            for item in dependencies if "api_contract_id" in item
        )
    checks = _expected_checks(task, "frontend.page_endpoint_usage")
    if task.get("kind") == "repair" and required and not checks:
        raise _UncertainScope
    for expected in checks:
        if expected["page_id"] != contract["pageId"] or _ids(expected["required_endpoint_ids"]) != required:
            raise _UncertainScope
        explicit_pairs.update(_endpoint_pairs(expected.get("endpoints", [])))

    if {endpoint_id for _, endpoint_id in explicit_pairs} - required:
        raise _UncertainScope
    explicit_contracts = defaultdict(set)
    for contract_id, endpoint_id in explicit_pairs:
        explicit_contracts[endpoint_id].add(contract_id)
    selected = set()
    for endpoint_id in required:
        available = catalog.get(endpoint_id, set())
        candidates = explicit_contracts.get(endpoint_id) or available
        if len(candidates) != 1 or not candidates <= available:
            raise _UncertainScope
        selected.add((next(iter(candidates)), endpoint_id))
    return selected


def _navigation_targets(page: dict, contract: dict, required: set[str]) -> set[str]:
    """核对实际动作接口，并读取显式导航目标和技术实现引用。"""
    references = page.get("references", {})
    technical = contract.get("technicalReferences", {})
    targets = {
        _identity(item["targetPageId"])
        for records in (contract.get("navigationBindings", []), technical.get("navigationTargets", []),
                        references.get("navigation_targets", []))
        for item in _records(records)
    }
    pending = [
        *_records(contract.get("actionBindings", [])),
        *_records(technical.get("actionImplementations", [])),
        *_records(references.get("action_implementations", [])),
        *_records(contract.get("responseBindings", [])),
    ]
    while pending:
        action = pending.pop()
        kind = action.get("bindingType") or action.get("type")
        if ("endpointId" in action or kind == "endpoint") and _identity(action.get("endpointId")) not in required:
            raise _UncertainScope
        if "targetPageId" in action or kind == "navigation":
            targets.add(_identity(action.get("targetPageId")))
        pending.extend(_records(action.get("steps", [])))
        pending.extend(_records(action.get("stepBindings", [])))
    return targets


def _schema_closure(contract: dict, endpoints: list[dict]) -> dict:
    """保持本地 Schema 的递归闭包，遇到无法解析的指针时报错。"""
    schemas = contract.get("schemas", {})
    if not isinstance(schemas, dict):
        raise _UncertainScope
    visited: set[str] = set()
    pending: list[Any] = [endpoints, {k: v for k, v in contract.items() if k not in ("endpoints", "schemas")}]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        if not isinstance(value, dict):
            continue
        for key, child in value.items():
            # JSON Schema 的属性名允许叫 schema_ref，属性映射的键并非引用指令。
            if key in _SCHEMA_MAPS and isinstance(child, dict):
                pending.extend(child.values())
            elif key in _SCHEMA_REFS:
                if key != "$ref" and child in (None, ""):
                    continue
                name = normalize_local_schema_ref(_identity(child), contract_id=contract["id"])
                if any(marker in name for marker in ("#", "/", "\\", ":")) or not isinstance(schemas.get(name), dict):
                    raise _UncertainScope("schema_reference")
                if name not in visited:
                    visited.add(name)
                    pending.append(schemas[name])
            else:
                pending.append(child)
    return {name: schema for name, schema in schemas.items() if name in visited}


def _authorization_view(
    manifest: dict, page_contracts: list[dict], endpoint_keys: set[EndpointKey], catalog: dict[str, set[str]],
) -> dict:
    """保留当前页面、操作及接口绑定所引用的完整权限资源。"""
    page_ids = {contract["pageId"] for contract in page_contracts}
    bindings = manifest["bindings"]
    selected = {
        kind: [item for item in _records(bindings[kind]) if item["pageId"] in page_ids]
        for kind in ("pages", "actions")
    }
    keys = {_identity(item["resourceKey"]) for items in selected.values() for item in items}
    page_keys = {
        _identity(item["resourceKey"])
        for contract in page_contracts
        for item in _records(contract.get("permissionBindings", []))
    }
    if not page_keys <= keys:
        raise _UncertainScope

    selected["endpoints"] = []
    endpoint_ids = {endpoint_id for _, endpoint_id in endpoint_keys}
    for item in _records(bindings["endpoints"]):
        endpoint_id = _identity(item["endpointId"])
        if endpoint_id not in endpoint_ids:
            continue
        if "apiContractId" in item:
            contract_id = _identity(item["apiContractId"])
        else:
            candidates = catalog[endpoint_id]
            if len(candidates) != 1:
                raise _UncertainScope
            contract_id = next(iter(candidates))
        if (contract_id, endpoint_id) in endpoint_keys:
            selected["endpoints"].append(item)
            keys.update(_ids(item["operationResourceKeys"]))

    resources = _index(_records(manifest["resources"]), "resourceKey")
    if keys - resources.keys():
        raise _UncertainScope
    return {
        **({"schema_version": manifest["schema_version"]} if "schema_version" in manifest else {}),
        "bindings": selected,
        "resources": [resource for key, resource in resources.items() if key in keys],
    }


def _data_sources_view(value: Any, entity_ids: set[str]) -> list[dict[str, Any]]:
    """只在来源以正式实体身份明确绑定且没有共享歧义时过滤。"""
    selected = []
    for source in _records(value):
        if "entity_ids" in source:
            source_ids = _ids(source["entity_ids"])
        else:
            source_ids = set(_index(_records(source["entities"]), "id"))
        if not source_ids or (source_ids & entity_ids and source_ids - entity_ids):
            raise _UncertainScope
        if source_ids & entity_ids:
            selected.append(source)
    return selected


def _project(project_plan: dict, tasks: list[dict]) -> dict:
    """为当前批次合并职责，并按原计划顺序组装视图。"""
    technical = project_plan.get("artifact_type") == "technical-plan"
    pages_key = "pages" if technical else "frontend_pages"
    raw_pages = _records(project_plan[pages_key])
    if any("children" in page for page in raw_pages):
        # 菜单树的相对路径可能依赖祖先，当前仅支持独立的扁平页面。
        raise _UncertainScope
    pages = _index(project_plan_page_records(project_plan), "pageId", alternate="id")
    page_contracts = _index(_records(project_plan["page_implementation_contracts"]), "pageId")
    contracts = _index(_records(project_plan["api_contracts"]), "id")
    contract_endpoints = {
        contract_id: _index(_records(contract["endpoints"]), "id")
        for contract_id, contract in contracts.items()
    }
    catalog: dict[str, set[str]] = defaultdict(set)
    for contract_id, endpoints in contract_endpoints.items():
        for endpoint_id in endpoints:
            catalog[endpoint_id].add(contract_id)

    page_ids: set[str] = set()
    navigation: set[str] = set()
    endpoint_keys: set[EndpointKey] = set()
    entity_ids: set[str] = set()
    for task in _records(tasks):
        unit = task.get("unit_id", "")
        deliverables = _records(task.get("deliverables", []))
        kinds = {item["kind"] for item in deliverables}
        if unit == "frontend:data:static" or "frontend.static_data_module" in kinds:
            raise _UncertainScope
        if task.get("owner", "frontend") != "frontend":
            raise _UncertainScope
        targets = {_identity(item["target_id"]) for item in deliverables}
        refs = task.get("source_refs", {})
        entity_ids.update(_ids(refs.get("entity_ids", [])))
        if (unit.startswith("page:") or (not unit and kinds == {"frontend.page"})) and kinds <= {"frontend.page"}:
            page_id = _identity(unit.removeprefix("page:")) if unit else next(iter(targets))
            if (targets and targets != {page_id}) or (task.get("kind") == "repair" and not targets):
                raise _UncertainScope
            page, contract = pages[page_id], page_contracts[page_id]
            page_ids.add(page_id)
            page_endpoints = _page_endpoints(task, page, contract, catalog)
            endpoint_keys.update(page_endpoints)
            navigation.update(_navigation_targets(page, contract, {endpoint_id for _, endpoint_id in page_endpoints}))
        elif unit == "frontend:api-client" and kinds == {"frontend.api_module"}:
            endpoint_keys.update(_api_task_endpoints(task, targets, catalog))
        else:
            raise _UncertainScope

    if navigation - pages.keys():
        raise _UncertainScope
    pages_view = []
    for page_id, page in pages.items():
        if page_id in page_ids:
            if not technical and not _identity(page.get("path") or page.get("route")).startswith("/"):
                raise _UncertainScope
            pages_view.append(page)
        elif page_id in navigation:
            if not _identity(page.get("path") or page.get("route")).startswith("/"):
                raise _UncertainScope
            pages_view.append({key: page[key] for key in _ROUTE_FIELDS if key in page})

    selected_by_contract: dict[str, set[str]] = defaultdict(set)
    for contract_id, endpoint_id in endpoint_keys:
        if contract_id not in catalog.get(endpoint_id, set()):
            raise _UncertainScope
        selected_by_contract[contract_id].add(endpoint_id)
    api_view = []
    for contract_id, contract in contracts.items():
        if contract_id in selected_by_contract:
            selected_ids = selected_by_contract[contract_id]
            endpoints = [item for key, item in contract_endpoints[contract_id].items() if key in selected_ids]
            selected_contract = {key: value for key, value in contract.items()
                                 if key not in {"page_ids", "endpoints", "schemas"}}
            api_view.append({**selected_contract, "endpoints": endpoints,
                             "schemas": _schema_closure(contract, endpoints)})
            entity_ids.update(_ids(contract.get("entity_ids", [])))

    entities = _index(_records(project_plan.get("entities", [])), "id")
    if entity_ids - entities.keys():
        raise _UncertainScope
    page_contract_view = [contract for key, contract in page_contracts.items() if key in page_ids]
    view = {
        "app": {key: value for key, value in project_plan.get("app", {}).items()
                if key in {"name", "appName", "route_root_path"}},
        pages_key: pages_view,
        "page_implementation_contracts": page_contract_view,
        "api_contracts": api_view,
        "entities": [entity for key, entity in entities.items() if key in entity_ids],
    }
    if "authorization_manifest" in project_plan:
        view["authorization_manifest"] = _authorization_view(
            project_plan["authorization_manifest"], page_contract_view, endpoint_keys, catalog,
        )
    if "data_sources" in project_plan:
        view["data_sources"] = _data_sources_view(project_plan["data_sources"], entity_ids)
    return view


def project_frontend_task_project_plan(project_plan: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """返回严格隔离的任务级视图，范围无法确定时显式失败。"""
    if not tasks:
        return {}
    try:
        return deepcopy(_project(project_plan, tasks))
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        task_id = tasks[0].get("id") if isinstance(tasks[0], dict) else None
        reference = str(exc) if isinstance(exc, _UncertainScope) and str(exc) else type(exc).__name__
        raise FrontendTaskProjectionError(task_id, "project_plan", reference) from exc
