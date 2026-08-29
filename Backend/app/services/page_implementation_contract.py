from __future__ import annotations

import hashlib
import json
from typing import Any

from app.services.project_plan import TECHNICAL_PLAN_ARTIFACT_TYPE
from app.services.ui_design_manifest import persisted_ui_manifest, ui_action_bindings


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """从列表中筛选结构化对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text_items(value: Any) -> list[str]:
    """把列表规范为非空文本。"""

    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _artifact_hash(value: Any) -> str:
    """生成正式上游产物的稳定 SHA-256。"""

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _endpoint_catalog(project_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """把 TechnicalPlan API Contract 展开为 endpoint 索引。"""

    result: dict[str, dict[str, Any]] = {}
    for contract in _dict_items(project_plan.get("api_contracts")):
        contract_id = str(contract.get("id") or "").strip()
        for endpoint in _dict_items(contract.get("endpoints")):
            endpoint_id = str(endpoint.get("id") or "").strip()
            if endpoint_id:
                result[endpoint_id] = {**endpoint, "api_contract_id": contract_id}
    return result


_BINDING_TYPES = {"endpoint", "navigation", "local", "sequence", "external"}
_STEP_TYPES = _BINDING_TYPES - {"sequence"}


def _technical_implementations(page: dict[str, Any]) -> list[dict[str, Any]]:
    """读取 TechnicalPlan 的纯 endpoint 实现决策。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    return _dict_items(references.get("action_implementations"))


def _product_behavior(action: dict[str, Any]) -> dict[str, Any]:
    """读取 ProductPlan 已确认的权威行为。"""

    behavior = action.get("behavior")
    return behavior if isinstance(behavior, dict) else {}


def _ui_effects(ui_page: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """按 actionId 和 stepId 索引 UiManifest 已确认的本地交互效果。"""

    actions = {
        str(binding.get("actionId") or "").strip(): str(binding.get("uiEffect") or "").strip()
        for binding in ui_action_bindings(ui_page)
        if str(binding.get("actionId") or "").strip()
        and str(binding.get("uiEffect") or "").strip()
    }
    steps = {
        str(binding.get("actionId") or "").strip(): {
            str(step.get("stepId") or "").strip(): str(step.get("uiEffect") or "").strip()
            for step in _dict_items(binding.get("stepEffects"))
            if str(step.get("stepId") or "").strip() and str(step.get("uiEffect") or "").strip()
        }
        for binding in ui_action_bindings(ui_page)
        if str(binding.get("actionId") or "").strip()
    }
    return actions, steps


def _implementation_index(page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按 actionId 索引 TechnicalPlan endpoint 实现，供确定性合并使用。"""

    return {
        str(item.get("actionId") or "").strip(): item
        for item in _technical_implementations(page)
        if str(item.get("actionId") or "").strip()
    }


def _compile_product_action_binding(
    action: dict[str, Any],
    *,
    implementation: dict[str, Any],
    ui_effect: str,
    ui_step_effects: dict[str, str],
    ui_design_skipped: bool = False,
) -> dict[str, Any]:
    """合并产品、UI 与技术三层事实，生成 Build 使用的判别联合绑定。"""

    action_id = str(action.get("actionId") or "").strip()
    behavior = _product_behavior(action)
    behavior_type = str(behavior.get("type") or "business")
    if behavior_type == "business":
        return {
            "actionId": action_id,
            "bindingType": "endpoint",
            "endpointId": str(implementation.get("endpointId") or "").strip(),
        }
    if behavior_type == "navigation":
        return {
            "actionId": action_id,
            "bindingType": "navigation",
            "targetPageId": str(behavior.get("targetPageId") or "").strip(),
        }
    if behavior_type == "interface":
        local_effect = ui_effect
        if ui_design_skipped and not local_effect:
            local_effect = str(behavior.get("expectedResult") or "").strip()
        return {
            "actionId": action_id,
            "bindingType": "local",
            "localEffect": local_effect,
        }
    if behavior_type == "external":
        return {
            "actionId": action_id,
            "bindingType": "external",
            "externalTarget": str(behavior.get("externalTarget") or "").strip(),
        }
    step_bindings = {
        str(step.get("stepId") or "").strip(): str(step.get("endpointId") or "").strip()
        for step in _dict_items(implementation.get("stepBindings"))
    }
    steps: list[dict[str, Any]] = []
    for step in _dict_items(behavior.get("steps")):
        step_id = str(step.get("stepId") or "").strip()
        step_type = str(step.get("type") or "business")
        if step_type == "business":
            steps.append({"type": "endpoint", "endpointId": step_bindings.get(step_id, "")})
        elif step_type == "navigation":
            steps.append({"type": "navigation", "targetPageId": str(step.get("targetPageId") or "")})
        elif step_type == "interface":
            local_effect = ui_step_effects.get(step_id, "")
            if ui_design_skipped and not local_effect:
                local_effect = str(step.get("expectedResult") or "").strip()
            steps.append({"type": "local", "localEffect": local_effect})
        elif step_type == "external":
            steps.append({"type": "external", "externalTarget": str(step.get("externalTarget") or "")})
    return {"actionId": action_id, "bindingType": "sequence", "steps": steps}


def _validate_technical_action_implementations(
    page: dict[str, Any],
    product_page: dict[str, Any],
    *,
    page_id: str,
    endpoint_ids: set[str],
    required_endpoint_ids: set[str],
    errors: list[str],
) -> None:
    """校验 TechnicalPlan 只覆盖产品业务动作及业务步骤的 endpoint 决策。"""

    implementations = _technical_implementations(page)
    by_action = {
        str(item.get("actionId") or "").strip(): item
        for item in implementations
        if str(item.get("actionId") or "").strip()
    }
    if len(by_action) != len(implementations):
        errors.append(f"页面 {page_id} 的 action_implementations.actionId 必须非空且唯一。")
    expected_actions: dict[str, set[str] | None] = {}
    for action in _dict_items(product_page.get("actions")):
        action_id = str(action.get("actionId") or "").strip()
        behavior = _product_behavior(action)
        behavior_type = str(behavior.get("type") or "business")
        if behavior_type == "business":
            expected_actions[action_id] = None
        elif behavior_type == "sequence":
            business_steps = {
                str(step.get("stepId") or "").strip()
                for step in _dict_items(behavior.get("steps"))
                if str(step.get("type") or "business") == "business"
            }
            if business_steps:
                expected_actions[action_id] = business_steps
    missing_actions = sorted(set(expected_actions) - set(by_action))
    extra_actions = sorted(set(by_action) - set(expected_actions))
    if missing_actions:
        errors.append(
            f"页面 {page_id} 的 TechnicalPlan 缺少业务 action endpoint 实现："
            + "、".join(missing_actions)
            + "。"
        )
    if extra_actions:
        errors.append(
            f"页面 {page_id} 的 TechnicalPlan 不得为导航、界面或外部 action 重复决策："
            + "、".join(extra_actions)
            + "。"
        )
    for action_id, expected_steps in expected_actions.items():
        implementation = by_action.get(action_id)
        if implementation is None:
            continue
        if expected_steps is None:
            endpoint_id = str(implementation.get("endpointId") or "").strip()
            if not endpoint_id:
                errors.append(f"页面 {page_id} 的业务 action {action_id} 必须声明 endpointId。")
            elif endpoint_id not in endpoint_ids:
                errors.append(f"页面 {page_id} 的业务 action {action_id} 引用了不存在的 endpoint {endpoint_id}。")
            elif endpoint_id not in required_endpoint_ids:
                errors.append(f"页面 {page_id} 的业务 action {action_id} 使用的 endpoint {endpoint_id} 未列入 requiredEndpointIds。")
            if _dict_items(implementation.get("stepBindings")):
                errors.append(f"页面 {page_id} 的直接业务 action {action_id} 不得声明 stepBindings。")
            continue
        step_bindings = _dict_items(implementation.get("stepBindings"))
        actual_steps = [str(step.get("stepId") or "").strip() for step in step_bindings]
        if set(actual_steps) != expected_steps or len(actual_steps) != len(set(actual_steps)):
            errors.append(f"页面 {page_id} 的组合 action {action_id} 必须逐一绑定全部业务 stepId。")
        for step in step_bindings:
            step_id = str(step.get("stepId") or "").strip()
            endpoint_id = str(step.get("endpointId") or "").strip()
            if not endpoint_id:
                errors.append(f"页面 {page_id} 的业务步骤 {action_id}/{step_id} 必须声明 endpointId。")
            elif endpoint_id not in endpoint_ids:
                errors.append(f"页面 {page_id} 的业务步骤 {action_id}/{step_id} 引用了不存在的 endpoint {endpoint_id}。")
            elif endpoint_id not in required_endpoint_ids:
                errors.append(f"页面 {page_id} 的业务步骤 {action_id}/{step_id} 使用的 endpoint {endpoint_id} 未列入 requiredEndpointIds。")


def _required_endpoint_ids(page: dict[str, Any]) -> list[str]:
    """从 TechnicalPlan 页面引用读取稳定 endpoint 依赖。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    result: list[str] = []
    for dependency in _dict_items(references.get("endpoint_dependencies")):
        endpoint_id = str(dependency.get("endpoint_id") or "").strip()
        if endpoint_id and endpoint_id not in result:
            result.append(endpoint_id)
    return result


def technical_plan_pages(technical_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """读取当前 TechnicalPlan 的紧凑 pages。"""

    if technical_plan.get("artifact_type") != TECHNICAL_PLAN_ARTIFACT_TYPE:
        raise ValueError("只接受当前 TechnicalPlan。")
    return _dict_items(technical_plan.get("pages"))


def _permission_bindings(technical_plan: dict[str, Any], page_id: str, action_ids: list[str]) -> list[dict[str, str]]:
    """从已编译权限目录为页面及其业务操作投影最小权限绑定。"""

    manifest = technical_plan.get("authorization_manifest")
    if not isinstance(manifest, dict) or manifest.get("enabled") is not True:
        return []
    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), dict) else {}
    result = [
        {"targetType": "page", "pageId": page_id, "resourceKey": str(item.get("resourceKey") or "")}
        for item in _dict_items(bindings.get("pages"))
        if str(item.get("pageId") or "") == page_id and str(item.get("resourceKey") or "")
    ]
    action_keys = {
        (str(item.get("pageId") or ""), str(item.get("actionId") or "")): {
            "resourceKey": str(item.get("resourceKey") or ""),
            "mode": str(item.get("mode") or "hidden"),
        }
        for item in _dict_items(bindings.get("actions"))
        if str(item.get("pageId") or "") and str(item.get("actionId") or "") and str(item.get("resourceKey") or "")
    }
    result.extend(
        {"targetType": "action", "pageId": page_id, "actionId": action_id, **action_keys[(page_id, action_id)]}
        for action_id in action_ids
        if (page_id, action_id) in action_keys
    )
    return result


def build_page_implementation_contracts(
    technical_plan: dict[str, Any],
    product_plan: dict[str, Any],
    ui_designs: dict[str, Any],
) -> list[dict[str, Any]]:
    """把产品操作、真实 UI 稿和技术 API 绑定为非视觉页面实现契约。"""

    product_pages = {
        str(page.get("pageId") or "").strip(): page
        for page in _dict_items(product_plan.get("pages"))
        if page.get("pageId")
    }
    ui_pages = {
        str(page.get("pageId") or "").strip(): page
        for page in _dict_items(ui_designs.get("pages"))
        if page.get("pageId")
    }
    ui_design_skipped = ui_designs.get("confirmation_status") == "skipped"
    contracts: list[dict[str, Any]] = []
    for page in technical_plan_pages(technical_plan):
        page_id = str(page.get("pageId") or page.get("id") or "").strip()
        if not page_id:
            continue
        product_page = product_pages.get(page_id, {})
        ui_page = ui_pages.get(page_id, {})
        endpoint_ids = _required_endpoint_ids(page)
        references = page.get("references") if isinstance(page.get("references"), dict) else {}
        product_actions = {
            str(action.get("actionId") or "").strip(): action
            for action in _dict_items(product_page.get("actions"))
            if action.get("actionId")
        }
        ui_controls: dict[str, list[dict[str, str]]] = {}
        for control in ui_action_bindings(ui_page):
            action_id = str(control.get("actionId") or "").strip()
            if not action_id:
                continue
            for control_id in _text_items(control.get("controlIds")):
                ui_controls.setdefault(action_id, []).append(
                    {"controlId": control_id, "label": ""}
                )
        implementations = _implementation_index(page)
        effects, step_effects = _ui_effects(ui_page)
        action_bindings = []
        for action_id, action in product_actions.items():
            normalized = _compile_product_action_binding(
                action,
                implementation=implementations.get(action_id, {}),
                ui_effect=effects.get(action_id, ""),
                ui_step_effects=step_effects.get(action_id, {}),
                ui_design_skipped=ui_design_skipped,
            )
            action_bindings.append(
                {
                    **normalized,
                    "actionName": str(action.get("name") or action_id),
                    "uiControlRefs": ui_controls.get(action_id, []),
                }
            )
        engineering_acceptance = (
            [
                "页面实现应依据 ProductPlan 与 TechnicalPlan 生成，不依赖 UI 设计稿。",
                "页面只能调用 requiredEndpointIds 中声明的接口。",
                "页面权限、跳转和状态处理必须与 ProductPlan 保持一致。",
            ]
            if ui_design_skipped
            else [
                "页面实现必须还原已确认 UI 设计稿。",
                "页面只能调用 requiredEndpointIds 中声明的接口。",
                "页面权限、跳转和状态处理必须与 ProductPlan 保持一致。",
            ]
        )
        contracts.append(
            {
                "schema_version": "page-implementation-contract.v1",
                "pageId": page_id,
                "uiDesignRef": {
                    "path": str(ui_page.get("code_path") or ""),
                    "sha256": str(ui_page.get("code_sha256") or ""),
                },
                "requiredEndpointIds": endpoint_ids,
                "actionBindings": action_bindings,
                "responseBindings": [],
                "permissionBindings": _permission_bindings(
                    technical_plan,
                    page_id,
                    list(product_actions),
                ),
                "navigationBindings": [
                    {"targetPageId": target_id}
                    for target_id in _text_items(product_page.get("navigation_targets"))
                ],
                "productAcceptance": _text_items(product_page.get("acceptance_criteria")),
                "engineeringAcceptance": engineering_acceptance,
                "technicalReferences": {
                    "permissions": references.get("permissions", []),
                    "endpointDependencies": references.get("endpoint_dependencies", []),
                    "navigationTargets": references.get("navigation_targets", []),
                    "actionImplementations": _technical_implementations(page),
                },
            }
        )
    return contracts


def attach_page_implementation_contracts(
    technical_plan: dict[str, Any],
    product_plan: dict[str, Any],
    ui_designs: dict[str, Any],
) -> dict[str, Any]:
    """把当前 TechnicalPlan 的直接上游哈希写回正式计划。"""

    if technical_plan.get("artifact_type") != TECHNICAL_PLAN_ARTIFACT_TYPE:
        raise ValueError("只接受当前 TechnicalPlan。")

    metadata = {
        **technical_plan,
        "product_plan_sha256": _artifact_hash(product_plan),
        "ui_designs_sha256": _artifact_hash(persisted_ui_manifest(ui_designs)),
    }
    return {**metadata, "artifact_type": TECHNICAL_PLAN_ARTIFACT_TYPE}


def materialize_technical_plan_runtime(
    technical_plan: dict[str, Any],
    requirement_spec: dict[str, Any],
    product_plan: dict[str, Any],
    ui_designs: dict[str, Any],
) -> dict[str, Any]:
    """运行时按需合并上游事实，不把派生副本写回 TechnicalPlan。"""

    if technical_plan.get("artifact_type") != TECHNICAL_PLAN_ARTIFACT_TYPE:
        raise ValueError("只接受当前 TechnicalPlan。")
    references_by_page = {
        str(page.get("pageId") or ""): page.get("references", {})
        for page in technical_plan_pages(technical_plan)
        if page.get("pageId")
    }
    product_pages = _dict_items(product_plan.get("pages"))
    pages = [
        {
            key: value
            for key, value in {
                "pageId": page.get("pageId"),
                "name": page.get("name"),
                "path": page.get("path"),
                "module_id": page.get("module_id"),
                "description": page.get("description"),
                "references": references_by_page.get(str(page.get("pageId") or ""), {}),
            }.items()
            if value is not None
        }
        for page in product_pages
        if page.get("pageId")
    ]
    app_info = (
        requirement_spec.get("app_info")
        if isinstance(requirement_spec.get("app_info"), dict)
        else {}
    )
    user_roles = _dict_items(requirement_spec.get("user_roles"))
    runtime = {
        **technical_plan,
        "app": product_plan.get("app")
        or {key: app_info.get(key) for key in ("name", "summary")},
        "requirements_overview": {
            "summary": app_info.get("summary"),
            "target": app_info.get("target"),
            "roles": user_roles,
            "modules": _dict_items(requirement_spec.get("feature_modules")),
            "business_flows": _dict_items(product_plan.get("business_flows")),
            "acceptance_focus": _text_items(
                product_plan.get("product_acceptance_criteria")
            ),
        },
        "project_acceptance_criteria": _text_items(
            product_plan.get("product_acceptance_criteria")
        ),
        "pages": pages,
        "data_sources": [
            dict(source) for source in _dict_items(requirement_spec.get("data_sources"))
        ],
        "business_flows": _dict_items(product_plan.get("business_flows")),
        "acceptance_criteria": _text_items(requirement_spec.get("acceptance_criteria")),
    }
    return {
        **runtime,
        "page_implementation_contracts": build_page_implementation_contracts(
            technical_plan,
            product_plan,
            ui_designs,
        ),
    }


def validate_page_implementation_contracts(
    technical_plan: dict[str, Any],
    product_plan: dict[str, Any],
    ui_designs: dict[str, Any] | None = None,
) -> list[str]:
    """校验当前 TechnicalPlan 的每个业务操作已显式闭合。"""

    if technical_plan.get("artifact_type") != TECHNICAL_PLAN_ARTIFACT_TYPE:
        raise ValueError("只接受当前 TechnicalPlan。")

    expected_pages = {
        str(page.get("pageId") or "").strip()
        for page in _dict_items(product_plan.get("pages"))
        if page.get("pageId")
    }
    contracts = build_page_implementation_contracts(
        technical_plan,
        product_plan,
        ui_designs or {},
    )
    actual_pages = [str(item.get("pageId") or "").strip() for item in contracts]
    errors: list[str] = []
    if set(actual_pages) != expected_pages or len(actual_pages) != len(set(actual_pages)):
        errors.append("TechnicalPlan 必须为每个 ProductPlan 页面生成唯一 PageImplementationContract。")
    endpoint_ids = set(_endpoint_catalog(technical_plan))
    product_pages = {
        str(page.get("pageId") or "").strip(): page
        for page in _dict_items(product_plan.get("pages"))
        if page.get("pageId")
    }
    ui_pages = {
        str(page.get("pageId") or "").strip(): page
        for page in _dict_items((ui_designs or {}).get("pages"))
        if page.get("pageId")
    }
    for contract in contracts:
        page_id = str(contract.get("pageId") or "")
        required_endpoint_ids = set(_text_items(contract.get("requiredEndpointIds")))
        missing = [
            endpoint_id
            for endpoint_id in required_endpoint_ids
            if endpoint_id not in endpoint_ids
        ]
        if missing:
            errors.append(f"页面 {page_id} 引用了不存在的 endpoint：{', '.join(missing)}。")
        product_page = product_pages.get(page_id, {})
        source_page = next(
            (
                page
                for page in technical_plan_pages(technical_plan)
                if str(page.get("pageId") or "") == page_id
            ),
            {},
        )
        source_references = (
            source_page.get("references")
            if isinstance(source_page.get("references"), dict)
            else {}
        )
        product_actions = _dict_items(product_page.get("actions"))
        uses_current_product_contract = product_actions and all(
            isinstance(action.get("behavior"), dict) and action["behavior"].get("type")
            for action in product_actions
        )
        if uses_current_product_contract and _dict_items(source_references.get("action_bindings")):
            errors.append(
                f"页面 {page_id} 的新 TechnicalPlan 不得重复生成 action_bindings；"
                "只允许 action_implementations 保存 endpoint 技术选择。"
            )
        if uses_current_product_contract:
            _validate_technical_action_implementations(
                source_page,
                product_page,
                page_id=page_id,
                endpoint_ids=endpoint_ids,
                required_endpoint_ids=required_endpoint_ids,
                errors=errors,
            )
        expected_action_ids = [
            str(action.get("actionId") or "").strip()
            for action in _dict_items(product_page.get("actions"))
        ]
        bindings = _dict_items(contract.get("actionBindings"))
        actual_action_ids = [str(binding.get("actionId") or "").strip() for binding in bindings]
        if (
            set(actual_action_ids) != set(expected_action_ids)
            or len(actual_action_ids) != len(set(actual_action_ids))
        ):
            errors.append(f"页面 {page_id} 的每个 ProductPlan actionId 必须有且只有一个实现绑定。")
        allowed_navigation_targets = set(_text_items(product_page.get("navigation_targets")))
        for binding in bindings:
            _validate_action_binding(
                binding,
                page_id=page_id,
                endpoint_ids=endpoint_ids,
                required_endpoint_ids=required_endpoint_ids,
                navigation_targets=allowed_navigation_targets,
                validate_endpoint_references=not uses_current_product_contract,
                errors=errors,
            )
        if ui_designs is not None and ui_designs.get("confirmation_status") != "skipped":
            control_action_ids = [
                str(control.get("actionId") or "").strip()
                for control in ui_action_bindings(ui_pages.get(page_id, {}))
            ]
            unknown_controls = sorted(set(control_action_ids) - set(expected_action_ids))
            missing_controls = sorted(set(expected_action_ids) - set(control_action_ids))
            if unknown_controls or missing_controls:
                errors.append(
                    f"页面 {page_id} 的 UiManifest controls 必须与 ProductPlan actions 一一对应。"
                )
    return errors


def _validate_action_binding(
    binding: dict[str, Any],
    *,
    page_id: str,
    endpoint_ids: set[str],
    required_endpoint_ids: set[str],
    navigation_targets: set[str],
    validate_endpoint_references: bool,
    errors: list[str],
) -> None:
    """按判别类型校验单个业务操作，拒绝未知或依赖不闭合的实现方式。"""

    action_id = str(binding.get("actionId") or "")
    binding_type = str(binding.get("bindingType") or "")
    if binding_type not in _BINDING_TYPES:
        errors.append(f"页面 {page_id} 的操作 {action_id} 缺少合法 bindingType。")
        return
    target_fields = {"endpointId", "targetPageId", "localEffect", "externalTarget"}
    allowed_target_field = {
        "endpoint": "endpointId",
        "navigation": "targetPageId",
        "local": "localEffect",
        "external": "externalTarget",
    }.get(binding_type)
    unexpected_fields = sorted(
        field
        for field in target_fields
        if field != allowed_target_field and str(binding.get(field) or "").strip()
    )
    if unexpected_fields:
        errors.append(
            f"页面 {page_id} 的操作 {action_id} 在 {binding_type} 绑定中包含冲突字段："
            + "、".join(unexpected_fields)
            + "。"
        )
    steps = _dict_items(binding.get("steps")) if binding_type == "sequence" else [binding]
    if binding_type == "sequence" and not steps:
        errors.append(f"页面 {page_id} 的组合操作 {action_id} 必须包含 steps。")
        return
    for step in steps:
        step_type = str(step.get("type") or binding_type)
        if step_type not in _STEP_TYPES:
            errors.append(f"页面 {page_id} 的操作 {action_id} 包含非法步骤类型 {step_type or '空'}。")
            continue
        step_allowed_field = {
            "endpoint": "endpointId",
            "navigation": "targetPageId",
            "local": "localEffect",
            "external": "externalTarget",
        }[step_type]
        unexpected_step_fields = sorted(
            field
            for field in target_fields
            if field != step_allowed_field and str(step.get(field) or "").strip()
        )
        if unexpected_step_fields:
            errors.append(
                f"页面 {page_id} 的操作 {action_id} 在 {step_type} 步骤中包含冲突字段："
                + "、".join(unexpected_step_fields)
                + "。"
            )
        if step_type == "endpoint":
            endpoint_id = str(step.get("endpointId") or "")
            if not validate_endpoint_references:
                continue
            if endpoint_id not in endpoint_ids:
                errors.append(f"页面 {page_id} 的操作 {action_id} 引用了不存在的 endpoint {endpoint_id or '空'}。")
            elif endpoint_id not in required_endpoint_ids:
                errors.append(f"页面 {page_id} 的操作 {action_id} 使用的 endpoint {endpoint_id} 未列入 requiredEndpointIds。")
        elif step_type == "navigation":
            target_page_id = str(step.get("targetPageId") or "")
            if target_page_id not in navigation_targets:
                errors.append(f"页面 {page_id} 的操作 {action_id} 引用了未声明的跳转目标 {target_page_id or '空'}。")
        elif step_type == "local" and not str(step.get("localEffect") or "").strip():
            errors.append(f"页面 {page_id} 的本地操作 {action_id} 必须声明 localEffect。")
        elif step_type == "external" and not str(step.get("externalTarget") or "").strip():
            errors.append(f"页面 {page_id} 的外部操作 {action_id} 必须声明 externalTarget。")
