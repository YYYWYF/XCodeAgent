from __future__ import annotations

from copy import deepcopy
from typing import Any


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从列表中筛选结构化对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _endpoint_ids(technical_plan: dict[str, Any]) -> set[str]:
    """汇总当前 TechnicalPlan 已声明的全部 Endpoint 标识。"""

    return {
        str(endpoint.get("id") or "").strip()
        for contract in _dict_items(technical_plan.get("api_contracts"))
        for endpoint in _dict_items(contract.get("endpoints"))
        if str(endpoint.get("id") or "").strip()
    }


def validate_technical_action_binding_patch(
    patch: dict[str, Any],
    *,
    binding_issues: list[dict[str, Any]],
    existing_plan: dict[str, Any],
) -> list[str]:
    """严格校验局部 Action Binding Patch 的目标、形状和 Endpoint 引用。"""

    errors: list[str] = []
    if set(patch) != {"bindings"} or not isinstance(patch.get("bindings"), list):
        return ["Action Binding 修复结果只能包含 bindings 数组。"]

    issue_by_target = {
        (
            str(issue.get("pageId") or "").strip(),
            str(issue.get("actionId") or "").strip(),
        ): issue
        for issue in binding_issues
        if str(issue.get("pageId") or "").strip()
        and str(issue.get("actionId") or "").strip()
    }
    bindings = _dict_items(patch.get("bindings"))
    actual_targets = [
        (
            str(binding.get("pageId") or "").strip(),
            str(binding.get("actionId") or "").strip(),
        )
        for binding in bindings
    ]
    if len(bindings) != len(patch["bindings"]):
        errors.append("Action Binding 修复结果的 bindings 必须全部为对象。")
    if set(actual_targets) != set(issue_by_target) or len(actual_targets) != len(
        set(actual_targets)
    ):
        errors.append("Action Binding 修复结果必须完整且只能覆盖本轮结构化 issue。")

    endpoint_ids = _endpoint_ids(existing_plan)
    for binding in bindings:
        page_id = str(binding.get("pageId") or "").strip()
        action_id = str(binding.get("actionId") or "").strip()
        issue = issue_by_target.get((page_id, action_id))
        if issue is None:
            continue
        required_step_ids = {
            str(step_id).strip()
            for step_id in issue.get("requiredStepIds", [])
            if str(step_id).strip()
        }
        if not required_step_ids:
            if set(binding) != {"pageId", "actionId", "endpointId"}:
                errors.append(
                    f"页面 {page_id} 的直接业务 action {action_id} 修复项只能声明 endpointId。"
                )
                continue
            endpoint_id = str(binding.get("endpointId") or "").strip()
            if endpoint_id not in endpoint_ids:
                errors.append(
                    f"页面 {page_id} 的业务 action {action_id} 引用了不存在的 endpoint {endpoint_id or '空'}。"
                )
            continue

        if set(binding) != {"pageId", "actionId", "stepBindings"}:
            errors.append(
                f"页面 {page_id} 的组合 action {action_id} 修复项只能声明 stepBindings。"
            )
            continue
        raw_step_bindings = binding.get("stepBindings")
        if not isinstance(raw_step_bindings, list):
            errors.append(
                f"页面 {page_id} 的组合 action {action_id} stepBindings 必须为数组。"
            )
            continue
        step_bindings = _dict_items(raw_step_bindings)
        actual_step_ids = [
            str(step.get("stepId") or "").strip() for step in step_bindings
        ]
        if len(step_bindings) != len(raw_step_bindings) or any(
            set(step) != {"stepId", "endpointId"} for step in step_bindings
        ):
            errors.append(
                f"页面 {page_id} 的组合 action {action_id} stepBindings 只能包含 stepId 和 endpointId。"
            )
        if set(actual_step_ids) != required_step_ids or len(actual_step_ids) != len(
            set(actual_step_ids)
        ):
            errors.append(
                f"页面 {page_id} 的组合 action {action_id} 修复项必须逐一覆盖全部业务 stepId。"
            )
        for step in step_bindings:
            step_id = str(step.get("stepId") or "").strip()
            endpoint_id = str(step.get("endpointId") or "").strip()
            if endpoint_id not in endpoint_ids:
                errors.append(
                    f"页面 {page_id} 的业务步骤 {action_id}/{step_id} 引用了不存在的 endpoint {endpoint_id or '空'}。"
                )
    return list(dict.fromkeys(errors))


def apply_technical_action_binding_patch(
    technical_plan: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """幂等替换指定 Action Binding，不改写 TechnicalPlan 的其他技术事实。"""

    updated = deepcopy(technical_plan)
    pages = _dict_items(updated.get("pages"))
    page_by_id = {
        str(page.get("pageId") or "").strip(): page
        for page in pages
        if str(page.get("pageId") or "").strip()
    }
    replacements_by_page: dict[str, dict[str, dict[str, Any]]] = {}
    for binding in _dict_items(patch.get("bindings")):
        page_id = str(binding.get("pageId") or "").strip()
        action_id = str(binding.get("actionId") or "").strip()
        if page_id not in page_by_id:
            raise ValueError(f"Action Binding 修复目标页面不存在：{page_id or '空'}。")
        normalized = {"actionId": action_id}
        if isinstance(binding.get("stepBindings"), list):
            normalized["stepBindings"] = deepcopy(binding["stepBindings"])
        else:
            normalized["endpointId"] = str(binding.get("endpointId") or "").strip()
        replacements_by_page.setdefault(page_id, {})[action_id] = normalized

    for page_id, replacements in replacements_by_page.items():
        page = page_by_id[page_id]
        references = (
            deepcopy(page.get("references"))
            if isinstance(page.get("references"), dict)
            else {}
        )
        implementations = _dict_items(references.get("action_implementations"))
        merged: list[dict[str, Any]] = []
        applied: set[str] = set()
        for implementation in implementations:
            action_id = str(implementation.get("actionId") or "").strip()
            if action_id in replacements:
                if action_id not in applied:
                    merged.append(deepcopy(replacements[action_id]))
                    applied.add(action_id)
            else:
                merged.append(deepcopy(implementation))
        for action_id in sorted(set(replacements) - applied):
            merged.append(deepcopy(replacements[action_id]))
        references["action_implementations"] = merged
        page["references"] = references
    return updated
