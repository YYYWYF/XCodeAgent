"""从最终技术规划计算审阅差异及删除记录，不执行候选合并或修改授权推断。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.technical_plan_logical_patch import canonical_json, item_identity

CHANGE_STATUS_FIELD = "_change_status"
_LIST_FIELDS = {"entities": "id", "api_contracts": "id", "pages": "pageId"}


def strip_change_status(plan: dict[str, Any]) -> dict[str, Any]:
    """剥离展示标记，保留现行下游所需的删除记录，不补回任何存活业务节点。"""

    def clean(value: Any) -> Any:
        """递归清理标记，避免嵌套字段或接口上的展示状态污染正式校验。"""

        if isinstance(value, dict):
            deleted = value.get(CHANGE_STATUS_FIELD) == "deleted"
            return {key: clean(child) for key, child in value.items()
                    if key != CHANGE_STATUS_FIELD or deleted}
        if isinstance(value, list):
            return [clean(child) for child in value]
        return deepcopy(value)

    return clean(plan)


def _items(value: Any) -> list[dict[str, Any]]:
    """提取现行文档的对象列表，非列表由正式校验报告。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _index(value: Any, id_key: str) -> dict[str, dict[str, Any]]:
    """按稳定身份索引实际对象，不用模糊名称或数组位置对齐。"""

    return {str(item[id_key]): item for item in _items(value) if item.get(id_key)}


def preserve_deletion_records(previous: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    """由执行前后差异产生现行删除记录，仅供开发清理；不会把删除项恢复成可执行节点。"""

    result = deepcopy(strip_change_status(candidate))
    previous = strip_change_status(previous or {})
    for field, id_key in _LIST_FIELDS.items():
        current = _index(result.get(field), id_key)
        rows = result.setdefault(field, [])
        for old in _items(previous.get(field)):
            identity = str(old.get(id_key) or "")
            if identity and identity not in current:
                rows.append({**deepcopy(old), CHANGE_STATUS_FIELD: "deleted"})
        if field == "api_contracts":
            previous_contracts = _index(previous.get(field), id_key)
            for contract in _items(rows):
                old = previous_contracts.get(str(contract.get("id") or ""), {})
                endpoints = contract.setdefault("endpoints", [])
                current_endpoints = _index(endpoints, "id")
                for endpoint in _items(old.get("endpoints")):
                    if endpoint.get("id") not in current_endpoints:
                        endpoints.append({**deepcopy(endpoint), CHANGE_STATUS_FIELD: "deleted"})
    return result


def _schema_names(schemas: dict[str, Any], roots: set[str]) -> set[str]:
    """收集接口直接与间接引用的类型，循环引用使用已访问集合终止。"""

    found = set()
    def visit(value: Any) -> None:
        """递归访问 Schema 的引用，类型表只是差异比较输入，不参与内容补全。"""

        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                add(reference.split("/")[-1])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    def add(name: str) -> None:
        """首次看到类型时展开其子引用。"""

        if name and name not in found:
            found.add(name)
            visit(schemas.get(name))
    for root in roots:
        add(root)
    return found


def _schema_changed(old_contract: dict, contract: dict, old_endpoint: dict, endpoint: dict) -> bool:
    """按接口前后引用集合检查真实类型变化，不相信模型声明的修改结论。"""

    roots = {str(item.get(key) or "").split("/")[-1]
             for item in (old_endpoint, endpoint) for key in ("request_schema_ref", "response_schema_ref")}
    old_schemas, schemas = old_contract.get("schemas") or {}, contract.get("schemas") or {}
    names = _schema_names(old_schemas, roots) | _schema_names(schemas, roots)
    return any(canonical_json(old_schemas.get(name)) != canonical_json(schemas.get(name)) for name in names)


def _nested_status(old: Any, value: Any) -> Any:
    """递归给同身份对象数组计算字段/参数/绑定标记，标量与类型定义保持原内容。"""

    if isinstance(value, dict):
        previous = old if isinstance(old, dict) else {}
        return {key: _nested_status(previous.get(key), child) for key, child in value.items()}
    if isinstance(value, list):
        old_rows = old if isinstance(old, list) else []
        identities = [item_identity(item) for item in value]
        if not identities or not all(identities) or len(set(identities)) != len(identities):
            return deepcopy(value)
        indexed = {item_identity(item): item for item in old_rows if item_identity(item)}
        result = []
        for item in value:
            if item.get(CHANGE_STATUS_FIELD) == "deleted":
                result.append(deepcopy(item))
                continue
            previous = indexed.get(item_identity(item))
            tagged = _nested_status(previous, item)
            tagged[CHANGE_STATUS_FIELD] = "added" if previous is None else "unchanged" if canonical_json(previous) == canonical_json(item) else "modified"
            result.append(tagged)
        return result
    return deepcopy(value)


def technical_plan_revision_diff(base_plan: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    """只根据前后内容生成展示标记，基线永远不能覆盖最终操作结果。"""

    base, display = strip_change_status(base_plan or {}), deepcopy(strip_change_status(candidate))
    for field, id_key in _LIST_FIELDS.items():
        previous = _index(base.get(field), id_key)
        for item in _items(display.get(field)):
            if item.get(CHANGE_STATUS_FIELD) == "deleted":
                continue
            old = previous.get(str(item.get(id_key) or ""))
            original = deepcopy(item)
            tagged = _nested_status(old, item)
            item.clear()
            item.update(tagged)
            item[CHANGE_STATUS_FIELD] = "added" if old is None or old.get(CHANGE_STATUS_FIELD) == "deleted" else "unchanged" if canonical_json(old) == canonical_json(original) else "modified"
            if field == "api_contracts":
                old_endpoints = _index((old or {}).get("endpoints"), "id")
                for endpoint in _items(item.get("endpoints")):
                    if endpoint.get(CHANGE_STATUS_FIELD) == "deleted":
                        continue
                    prior = old_endpoints.get(str(endpoint.get("id") or ""))
                    if prior and _schema_changed(old or {}, original, prior, endpoint):
                        endpoint[CHANGE_STATUS_FIELD] = "modified"
    return display
