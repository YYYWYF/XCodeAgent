"""把已确认需求中的明确文本替换确定性投射到 TechnicalPlan。

这里只处理“把 A 改成 B”且 A/B 在需求中都有明确引号界定的场景。
RequirementSpec 的差异证明用户改过需求；TechnicalPlan 的模型输出不再拥有
决定接口修改范围的权限，避免把模型润色误当作用户意图。
"""

from __future__ import annotations

import re
from typing import Any


_DIRECT_ENDPOINT_PATTERN = re.compile(
    r"(?:(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+[`\"“']?)?"
    r"(/[A-Za-z0-9_\-/{}.:]+)", re.IGNORECASE,
)


def direct_endpoint_revision_targets(
    request: str, base_plan: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """按当前基线精确解析直接技术修订的接口目标，不猜测别名或 HTTP 方法。"""

    endpoints = [
        (contract, endpoint)
        for contract in _items(base_plan.get("api_contracts"))
        for endpoint in _items(contract.get("endpoints"))
        if endpoint.get("_change_status") != "deleted"
    ]
    targets: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    # 编号和句号只分隔修改项；分页、响应字段中的“删除”不能被当成删除接口。
    clauses = re.split(r"[。；;\n]+|(?:^|\s)\d+[、)]", request)
    for clause in clauses:
        previous_end = 0
        for match in _DIRECT_ENDPOINT_PATTERN.finditer(clause):
            method = str(match.group(1) or "").upper()
            path = match.group(2).rstrip("/.")
            prefix = clause[previous_end:match.start()]
            previous_end = match.end()
            deleted = bool(re.search(r"(?:删除|移除|去掉)\s*(?:接口\s*)?[`\"“'\s]*$", prefix))
            # 否定表达不构成删除授权；接口之后的明确删除也支持，但删除响应字段不算。
            negated = bool(re.search(r"(?:不要|不应|不允许|不能|禁止|无需|不必|不得|别|不)\s*(?:删除|移除|去掉)", prefix))
            following = clause[match.end():]
            suffix_deleted = bool(re.match(r"[`\"”'\s]*(?:接口\s*)?(?:删除|移除|去掉)\s*(?:$|[，,])", following))
            deleted = (deleted and not negated) or suffix_deleted
            matches = [
                (contract, endpoint) for contract, endpoint in endpoints
                if str(endpoint.get("path") or "").strip().rstrip("/") == path
                and (not method or str(endpoint.get("method") or "").upper() == method)
            ]
            label = f"{method} {path}".strip()
            if not matches:
                errors.append(f"点名接口未匹配：{label}。当前技术规划中不存在该接口，未自动替换为相近路径。")
                continue
            if len(matches) != 1:
                methods = "、".join(sorted({str(endpoint.get("method") or "") for _, endpoint in matches}))
                errors.append(f"点名接口存在歧义：{label}，匹配方法为 {methods}。请明确 HTTP 方法及唯一接口。")
                continue
            contract, endpoint = matches[0]
            endpoint_id = str(endpoint.get("id") or "").strip()
            target = {
                "contract_id": str(contract.get("id") or "").strip(),
                "method": str(endpoint.get("method") or "").upper(),
                "path": path,
                "deleted": deleted,
            }
            if not endpoint_id or not target["contract_id"]:
                errors.append(f"点名接口缺少稳定标识：{label}。")
            elif endpoint_id in targets and targets[endpoint_id] != target:
                errors.append(f"点名接口的修改与删除要求冲突：{label}。请拆分并明确最终操作。")
            else:
                targets[endpoint_id] = target
    return targets, list(dict.fromkeys(errors))


def direct_revision_schema_targets(
    request: str, base_plan: dict[str, Any],
) -> tuple[dict[str, set[str]], list[str]]:
    """按用户原文中明确点名的类型匹配修改范围，支持原有类型字段局部编辑。"""

    owners: dict[str, list[str]] = {}
    for contract in _items(base_plan.get("api_contracts")):
        for name in (contract.get("schemas") or {}):
            owners.setdefault(name, []).append(str(contract.get("id") or ""))
    targets: dict[str, set[str]] = {}
    errors: list[str] = []
    for name, contract_ids in owners.items():
        # 标识边界排除更长类型名，不把模型输出的类型或相近名称作为授权证据。
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", request):
            continue
        if len(contract_ids) != 1:
            errors.append(f"点名类型存在歧义：{name} 出现在多个接口分组，请明确分组及修改范围。")
        else:
            targets.setdefault(contract_ids[0], set()).add(name)
    return targets, errors


TextReplacement = tuple[str, str]
ENDPOINTS_FIELD = "endpoints"
_TEXT_FIELDS = {"description", "summary", "title"}
_QUOTED_VALUE_PATTERN = re.compile(r"“([^”\r\n]+)”|\"([^\"\r\n]+)\"")
_API_OWNER_PATTERN = re.compile(r"接口|API|后端", re.IGNORECASE)
_API_CONTENT_PATTERN = re.compile(r"请求|获取|返回|响应|数据|字符串")


def _items(value: Any) -> list[dict[str, Any]]:
    """把未知值收敛为字典条目列表。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _page_id(page: dict[str, Any]) -> str:
    """读取页面稳定 ID。"""

    return str(page.get("pageId") or "").strip()


def _page_requirement_text(page: dict[str, Any]) -> str:
    """拼接页面需求中可能声明展示值或接口返回值的文本。"""

    parts: list[Any] = [page.get("description"), page.get("goal"), page.get("name")]
    parts.extend(page.get("acceptance_criteria") or [])
    for item in _items(page.get("information_items")):
        parts.append(item.get("description"))
    for action in _items(page.get("actions")):
        parts.append(action.get("description"))
        behavior = action.get("behavior")
        if isinstance(behavior, dict):
            parts.append(behavior.get("expectedResult"))
    return "\n".join(str(part) for part in parts if isinstance(part, str))


def _quoted_values(text: str) -> list[str]:
    """按原文顺序提取中文或英文双引号界定的值。"""

    return [
        str(chinese or english or "").strip()
        for chinese, english in _QUOTED_VALUE_PATTERN.findall(text)
        if str(chinese or english or "").strip()
    ]


def _api_response_requirement_text(page: dict[str, Any]) -> str:
    """只保留同时描述接口主体和请求/响应内容的语义片段。

    页面标题、视觉样式等普通文案变化不能获得接口修改权限；必须能在需求文本中
    找到“接口/API/后端”以及“请求/获取/返回/响应/数据/字符串”两类证据。
    """

    clauses = re.split(r"[。；;\n]+", _page_requirement_text(page))
    return "\n".join(
        clause
        for clause in clauses
        if _API_OWNER_PATTERN.search(clause) and _API_CONTENT_PATTERN.search(clause)
    )


def _unambiguous_replacement(
    base_page: dict[str, Any], current_page: dict[str, Any]
) -> TextReplacement | None:
    """只在需求差异恰好给出一对旧值和新值时返回替换关系。

    共同的引用值先按出现次数抵消。剩余旧值或新值超过一个就无法证明对应关系，
    此时宁可不自动修改，也不根据模型候选猜测用户意图。
    """

    old_values = _quoted_values(_api_response_requirement_text(base_page))
    remaining_new = _quoted_values(_api_response_requirement_text(current_page))
    removed: list[str] = []
    for value in old_values:
        if value in remaining_new:
            remaining_new.remove(value)
        else:
            removed.append(value)
    if len(removed) != 1 or len(remaining_new) != 1:
        return None
    old, new = removed[0], remaining_new[0]
    return (old, new) if old != new else None


def _page_endpoint_ids(page: dict[str, Any]) -> set[str]:
    """收集基线页面直接依赖和动作实现引用的接口 ID。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    endpoint_ids = {
        str(item.get("endpoint_id") or "").strip()
        for item in _items(references.get("endpoint_dependencies"))
    } | {
        str(item.get("endpointId") or "").strip()
        for item in _items(references.get("action_implementations"))
    }
    endpoint_ids.discard("")
    return endpoint_ids


def confirmed_endpoint_text_replacements(
    requirement_spec: dict[str, Any] | None,
    base_requirement_spec: dict[str, Any] | None,
    base_plan: dict[str, Any] | None,
    *,
    relevant_pages: set[str],
) -> dict[str, TextReplacement]:
    """把无歧义的需求文本替换映射到本轮变更页面引用的既有接口。

    同一接口被多个变更页面引用时，所有页面必须给出同一对替换值；否则无法判断
    应该采用哪一种业务含义，该接口不返回任何自动替换权限。
    """

    if not isinstance(base_plan, dict) or not isinstance(base_requirement_spec, dict):
        return {}
    base_spec_pages = {
        _page_id(page): page for page in _items(base_requirement_spec.get("pages")) if _page_id(page)
    }
    current_spec_pages = {
        _page_id(page): page for page in _items((requirement_spec or {}).get("pages")) if _page_id(page)
    }
    replacements_by_page: dict[str, TextReplacement] = {}
    for page_id in relevant_pages:
        base_page = base_spec_pages.get(page_id)
        current_page = current_spec_pages.get(page_id)
        if base_page is None or current_page is None:
            continue
        replacement = _unambiguous_replacement(base_page, current_page)
        if replacement is not None:
            replacements_by_page[page_id] = replacement

    candidates: dict[str, set[TextReplacement]] = {}
    for page in _items(base_plan.get("pages")):
        replacement = replacements_by_page.get(_page_id(page))
        if replacement is None:
            continue
        for endpoint_id in _page_endpoint_ids(page):
            candidates.setdefault(endpoint_id, set()).add(replacement)
    return {
        endpoint_id: next(iter(replacements))
        for endpoint_id, replacements in candidates.items()
        if len(replacements) == 1
    }


def replace_endpoint_text(
    endpoint: dict[str, Any], replacement: TextReplacement
) -> dict[str, Any]:
    """只替换接口说明文本，保留路径、方法、错误码和鉴权等技术字段。"""

    old, new = replacement
    result = dict(endpoint)
    for key in ("summary", "description"):
        value = result.get(key)
        if isinstance(value, str) and old in value:
            result[key] = value.replace(old, new)
    return result


def replace_schema_text(value: Any, replacement: TextReplacement) -> Any:
    """递归替换 Schema 的说明文字，不改变字段名、类型、引用或约束。"""

    old, new = replacement
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in _TEXT_FIELDS and isinstance(item, str):
                result[key] = item.replace(old, new)
            else:
                result[key] = replace_schema_text(item, replacement)
        return result
    if isinstance(value, list):
        return [replace_schema_text(item, replacement) for item in value]
    return value


def _endpoint_schema_names(endpoint: dict[str, Any]) -> set[str]:
    """读取接口直接引用的请求和响应 Schema 名称。"""

    names: set[str] = set()
    for key in ("request_schema_ref", "response_schema_ref"):
        reference = endpoint.get(key)
        if isinstance(reference, str) and reference.strip():
            names.add(reference.strip().split("/")[-1])
    return names


def schema_text_replacements(
    contract: dict[str, Any], endpoint_replacements: dict[str, TextReplacement]
) -> dict[str, TextReplacement]:
    """把接口文本替换映射到其直接引用的 Schema，冲突时不自动修改。"""

    candidates: dict[str, set[TextReplacement]] = {}
    for endpoint in _items(contract.get(ENDPOINTS_FIELD)):
        replacement = endpoint_replacements.get(str(endpoint.get("id") or "").strip())
        if replacement is None:
            continue
        for name in _endpoint_schema_names(endpoint):
            candidates.setdefault(name, set()).add(replacement)
    return {
        name: next(iter(replacements))
        for name, replacements in candidates.items()
        if len(replacements) == 1
    }
