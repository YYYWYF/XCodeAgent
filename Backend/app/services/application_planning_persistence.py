from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.data_source_policy import CANONICAL_DATASOURCE_TYPES
from app.services.entity_definitions import plan_data_sources
from app.services.frontend_page_tree import is_menu_node, project_plan_page_records
from app.services.product_plan import require_current_product_plan
from app.services.project_plan import TECHNICAL_PLAN_ARTIFACT_TYPE


def _dict_items(value: Any) -> list[dict[str, Any]]:
    """只保留列表中的对象项。"""

    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text_items(value: Any) -> list[str]:
    """把列表规范为非空文本集合。"""

    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _safe_id(value: Any, fallback: str) -> str:
    """生成适合 application.json 引用的稳定短标识。"""

    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").lower()).strip("-_")
    return normalized or fallback


def _page_key_from_page_id(page_id: str) -> str:
    """将 snake_case 的 pageId 转换为 PascalCase 的 PageKey。

    与前端 templateApi.ts 的 pageKeyFromPageId 和后端 build_context_resolver.py 保持一致：
    按 _ / - / 空格分段，每段首字母大写后拼接，保留所有段（含 "page" 后缀）。
    例：dashboard_page → DashboardPage，order_list_page → OrderListPage。
    """

    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(page_id or "page")).strip("-")
    segments = [s for s in re.split(r"[-_\s]+", cleaned) if s]
    if not segments:
        return "Page"
    pascal = "".join(seg[:1].upper() + seg[1:].lower() for seg in segments)
    if not pascal[:1].isalpha():
        pascal = "Page" + pascal
    return pascal


def _page_detail_map(project_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按页面 id 索引当前 TechnicalPlan 的运行时实现契约。"""

    implementation_contracts = {
        str(item["pageId"]): {
            "permissions": [
                binding.get("roleId")
                for binding in _dict_items(item.get("permissionBindings"))
                if binding.get("roleId") and binding.get("access") == "allow"
            ],
            "acceptance_criteria": _text_items(item.get("productAcceptance")),
            "response_bindings": item.get("responseBindings", []),
            "page_navigation": item.get("navigationBindings", []),
            "operation_interactions": [
                {
                    "id": binding.get("actionId"),
                    "action": binding.get("actionId"),
                    "binding_type": binding.get("bindingType"),
                    "endpoint_id": binding.get("endpointId"),
                    "target_page_id": binding.get("targetPageId"),
                    "local_effect": binding.get("localEffect"),
                    "external_target": binding.get("externalTarget"),
                    "steps": binding.get("steps", []),
                }
                for binding in _dict_items(item.get("actionBindings"))
                if binding.get("actionId")
            ],
        }
        for item in _dict_items(project_plan.get("page_implementation_contracts"))
        if item.get("pageId")
    }
    return implementation_contracts


def _layout(detail: dict[str, Any]) -> dict[str, Any]:
    """把 Workflow 页面布局投射为应用页面设计布局。"""

    value = detail.get("layout_design") if isinstance(detail.get("layout_design"), dict) else {}
    regions = [
        {
            "id": _safe_id(item.get("id") or item.get("name"), f"region-{index}"),
            "name": str(item.get("name") or item.get("area") or f"区域 {index}"),
            "responsibility": str(item.get("responsibility") or item.get("purpose") or "承载页面业务内容。"),
            "presentation": str(item.get("presentation") or value.get("primary_content_presentation") or "按业务信息层级呈现。"),
            "actions": _text_items(item.get("actions")),
        }
        for index, item in enumerate(_dict_items(value.get("regions")), start=1)
    ]
    if not regions:
        regions = [{
            "id": "main",
            "name": "主要内容区",
            "responsibility": "承载页面核心业务内容。",
            "presentation": str(value.get("primary_content_presentation") or "按业务信息层级呈现。"),
            "actions": [],
        }]
    return {
        "overall": str(value.get("overall_layout") or "按页面目标组织主要内容和关键操作。"),
        "regions": regions,
        "responsiveStrategy": str(value.get("responsive_strategy") or "优先桌面端，窄屏保持核心内容优先。"),
        "density": "medium",
    }


def _states(detail: dict[str, Any]) -> list[dict[str, str]]:
    """把状态反馈投射为页面可视状态。"""

    component_by_state = {
        "loading": "Spin",
        "empty": "Empty",
        "error": "Alert",
        "ready": "Content",
        "success": "Message",
        "confirm": "Modal.confirm",
        "validation": "Form.Item",
    }
    result = []
    for item in _dict_items(detail.get("state_feedback")):
        state = str(item.get("state") or "ready")
        result.append({
            "state": state,
            "behavior": str(item.get("behavior") or "展示对应页面反馈。"),
            "feedbackComponent": str(item.get("feedback_component") or component_by_state.get(state, "Alert")),
        })
    return result or [
        {"state": "loading", "behavior": "加载页面数据。", "feedbackComponent": "Spin"},
        {"state": "empty", "behavior": "展示空状态。", "feedbackComponent": "Empty"},
        {"state": "error", "behavior": "展示错误和重试入口。", "feedbackComponent": "Alert"},
    ]


def _interactions(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """把页面操作和导航统一投射为稳定交互列表。"""

    result: list[dict[str, Any]] = []
    for index, item in enumerate(_dict_items(detail.get("operation_interactions")), start=1):
        name = str(item.get("action") or item.get("name") or f"页面操作 {index}")
        result.append({
            "id": _safe_id(item.get("id") or name, f"interaction-{index}"),
            "name": name,
            "trigger": str(item.get("trigger") or "用户操作"),
            "userAction": str(item.get("user_action") or item.get("trigger") or name),
            "systemResponse": str(
                item.get("behavior")
                or item.get("system_response")
                or item.get("local_effect")
                or "按已确认技术绑定执行操作。"
            ),
            **({"bindingType": str(item["binding_type"])} if item.get("binding_type") else {}),
            **({"endpointId": str(item["endpoint_id"])} if item.get("endpoint_id") else {}),
            **({"targetMenuKey": str(item["target_page_id"])} if item.get("target_page_id") else {}),
            **({"localEffect": str(item["local_effect"])} if item.get("local_effect") else {}),
            **({"externalTarget": str(item["external_target"])} if item.get("external_target") else {}),
            **({"steps": [dict(step) for step in _dict_items(item.get("steps"))]} if item.get("steps") else {}),
        })
    for index, item in enumerate(_dict_items(detail.get("page_navigation")), start=1):
        target = str(item.get("targetPageId") or "")
        if not target:
            continue
        result.append({
            "id": _safe_id(item.get("id") or item.get("trigger"), f"navigation-{index}"),
            "name": str(item.get("trigger") or "页面跳转"),
            "trigger": str(item.get("trigger") or "用户点击导航入口"),
            "userAction": str(item.get("action") or item.get("trigger") or "打开目标页面"),
            "systemResponse": str(item.get("behavior") or "切换到目标页面。"),
            "targetMenuKey": target,
        })
    return result


def _page_design(page: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    """构造 application.json 中单个页面的完整设计对象。"""

    visibility = _dict_items(detail.get("operation_visibility"))
    operation_roles = {
        str(item.get("action")): _text_items(item.get("visible_to"))
        for item in visibility
        if item.get("action")
    }
    return {
        "pageGoal": str(detail.get("page_goal") or page.get("description") or page.get("name") or "完成页面核心任务。"),
        "layout": _layout(detail),
        "states": _states(detail),
        "interactions": _interactions(detail),
        "responseBindings": [
            {
                "endpointId": str(item.get("endpoint_id") or ""),
                "sourcePath": str(item.get("source_path") or ""),
                "target": str(item.get("page_field") or item.get("target") or "页面内容"),
            }
            for item in _dict_items(detail.get("response_bindings"))
            if item.get("endpoint_id") and item.get("source_path")
        ],
        "access": {
            "roleIds": _text_items(detail.get("permissions") or page.get("permissions")),
            "operationRoles": operation_roles,
            "unauthorizedBehavior": "隐藏无权操作入口或展示无权限提示。",
        },
        "acceptanceCriteria": _text_items(detail.get("acceptance_criteria")) or ["页面核心流程可完成。"],
    }


def _page_menu_item(
    page: dict[str, Any],
    details: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """把单个页面叶子投射为 application.json 可消费的页面菜单节点。"""

    pageId = str(page.get("pageId") or page.get("id") or "").strip()
    detail = details.get(pageId, {})
    features = [
        str(item.get("action"))
        for item in _dict_items(detail.get("operation_interactions"))
        if item.get("action")
    ]
    page_key = _page_key_from_page_id(pageId) if pageId else _safe_id(page.get("path") or page.get("name"), "page")
    return {
        "key": pageId or _safe_id(page.get("path") or page.get("name"), "page"),
        "path": str(page.get("path") or "/"),
        "label": str(page.get("name") or pageId or "未命名页面"),
        "type": "page",
        "purpose": str(
            page.get("description")
            or detail.get("page_goal")
            or page.get("name")
            or "业务页面"
        ),
        "keyFeatures": features
        or [str(page.get("description") or page.get("name") or "页面核心功能")],
        "pageKey": page_key,
        "design": _page_design(page, detail),
    }


def _menu_tree_items(
    nodes: Any,
    details: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """递归把当前计划 pages 投射为 application.json 菜单结构。"""

    items: list[dict[str, Any]] = []
    for node in _dict_items(nodes):
        if is_menu_node(node):
            children = _menu_tree_items(node.get("children"), details)
            if not children:
                continue
            unique_path = str(node.get("unique_path") or "").strip()
            key = unique_path or _safe_id(node.get("name"), "menu")
            items.append(
                {
                    "key": key,
                    "path": unique_path,
                    "label": str(node.get("name") or "未命名菜单").strip() or "未命名菜单",
                    "type": "menu",
                    "uniquePath": unique_path,
                    "children": children,
                }
            )
            continue
        items.append(_page_menu_item(node, details))
    return items


def _home_menu_key(project_plan: dict[str, Any]) -> str:
    """优先选择树中的首个页面叶子作为 homeMenuKey。"""

    pages = project_plan_page_records(project_plan)
    if not pages:
        return ""
    first_page = pages[0]
    return str(first_page.get("pageId") or first_page.get("id") or "").strip()


def _menus(project_plan: dict[str, Any]) -> dict[str, Any]:
    """把 ProjectPlan 页面清单和详细设计投射为菜单。"""

    details = _page_detail_map(project_plan)
    items = _menu_tree_items(project_plan_page_records(project_plan), details)
    if not items:
        raise ValueError("已确认的项目计划中没有可写入的页面清单。")
    return {"homeMenuKey": _home_menu_key(project_plan) or items[0]["key"], "items": items}


def _api_payload(project_plan: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """展开 ProjectPlan API contracts，并汇总唯一 Schema。"""

    apis: list[dict[str, Any]] = []
    schemas: dict[str, Any] = {}
    for contract in _dict_items(project_plan.get("api_contracts")):
        contract_schemas = contract.get("schemas") if isinstance(contract.get("schemas"), dict) else {}
        schemas.update(contract_schemas)
        auth = contract.get("authentication") if isinstance(contract.get("authentication"), dict) else {}
        for endpoint in _dict_items(contract.get("endpoints")):
            apis.append({
                "id": str(endpoint.get("id") or f"endpoint-{len(apis) + 1}"),
                "name": str(endpoint.get("summary") or endpoint.get("id") or "业务接口"),
                "method": str(endpoint.get("method") or "GET").upper(),
                "path": str(endpoint.get("path") or contract.get("base_path") or "/api"),
                "purpose": str(endpoint.get("summary") or "支撑页面业务交互。"),
                "parameters": _dict_items(endpoint.get("parameters")),
                **({"requestSchemaRef": str(endpoint["request_schema_ref"])} if endpoint.get("request_schema_ref") else {}),
                **({"responseSchemaRef": str(endpoint["response_schema_ref"])} if endpoint.get("response_schema_ref") else {}),
                "errors": [
                    {"code": code, "httpStatus": 400, "description": code}
                    for code in _text_items(endpoint.get("error_codes"))
                ],
                "access": {
                    "authenticationRequired": bool(auth.get("required", True)),
                    "roleIds": _text_items(auth.get("roles")),
                },
            })
    return apis, schemas


def _data_sources(project_plan: dict[str, Any]) -> list[dict[str, Any]]:
    """把 ProjectPlan 数据源规范为 application.json 的稳定驼峰结构。"""

    result = []
    for source in plan_data_sources(project_plan):
        source_type = str(source.get("type") or "")
        if source_type not in CANONICAL_DATASOURCE_TYPES:
            raise ValueError(
                f"ProjectPlan 数据源 {source.get('id') or 'unknown'} 使用了无效类型。"
            )
        schema_refs = _text_items(source.get("schema_refs"))
        raw_entities = source.get("entities")
        entity_items = raw_entities if isinstance(raw_entities, list) else []
        result.append({
            "id": str(source.get("id") or f"data-source-{len(result) + 1}"),
            "name": str(source.get("name") or source.get("id") or "数据源"),
            "description": str(source.get("description") or ""),
            "type": source_type,
            "entities": [
                _projected_entity(item, index, schema_refs)
                for index, item in enumerate(entity_items)
            ],
            "relations": [],
            "seedStrategy": str(source.get("seed_strategy") or "由实现阶段确定"),
        })
    return result


def _projected_entity(
    item: Any,
    index: int,
    schema_refs: list[str],
) -> dict[str, Any]:
    """把实体对象或旧字符串投影为稳定的 {name, schemaRef} 并附带字段摘要。"""

    if isinstance(item, dict):
        name = str(item.get("name") or item.get("id") or f"Entity{index + 1}")
        description = str(item.get("description") or "")
        fields = item.get("fields")
        field_summary = [
            {
                "name": str(field.get("name") or ""),
                "label": str(field.get("label") or field.get("name") or ""),
                "type": str(field.get("type") or "text"),
                "required": bool(field.get("required")),
                "description": str(field.get("description") or ""),
            }
            for field in fields
            if isinstance(field, dict) and str(field.get("name") or "").strip()
        ] if isinstance(fields, list) else []
    else:
        name = str(item or f"Entity{index + 1}")
        description = ""
        field_summary = []
    projected: dict[str, Any] = {
        "name": name,
        "schemaRef": (
            schema_refs[index]
            if index < len(schema_refs)
            else (schema_refs[0] if schema_refs else name)
        ),
    }
    if description:
        projected["description"] = description
    if field_summary:
        projected["fields"] = field_summary
    return projected


def project_plan_application_payload(project_plan: dict[str, Any]) -> dict[str, Any]:
    """把已确认 ProjectPlan 延迟投射为工作台后续阶段需要的应用结构。"""

    menus = _menus(project_plan)
    apis, schemas = _api_payload(project_plan)
    return {
        "menus": menus,
        "apis": apis,
        "schemas": schemas,
        "dataSources": _data_sources(project_plan),
    }


def _document_descriptor(
    workspace: Path,
    value: Any,
    label: str,
    artifact_format: str,
    expected_directory: str,
    accepted_confirmation_statuses: tuple[str, ...] = ("confirmed",),
) -> dict[str, str]:
    """校验规划产物位于指定目录、状态合法，并生成相对路径与内容摘要。"""

    candidate = Path(str(value or "")).expanduser()
    document = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
    try:
        relative_path = document.relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"{label} 不在应用工作区内：{document}") from exc
    if not document.is_file():
        raise ValueError(f"{label} 不存在：{document}")
    if relative_path.parent.as_posix() != expected_directory:
        raise ValueError(f"{label} 必须位于 {expected_directory}：{document}")
    content = document.read_bytes()
    if not content.strip():
        raise ValueError(f"{label} 不能为空：{document}")
    if artifact_format == "json":
        parsed = json.loads(content)
        if (
            not isinstance(parsed, dict)
            or parsed.get("confirmation_status") not in accepted_confirmation_statuses
        ):
            if accepted_confirmation_statuses == ("confirmed",):
                raise ValueError(f"{label} 必须是已确认的 JSON 对象：{document}")
            allowed_statuses = "、".join(accepted_confirmation_statuses)
            raise ValueError(f"{label} 必须是状态为 {allowed_statuses} 的 JSON 对象：{document}")
    return {
        "format": artifact_format,
        "path": relative_path.as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _confirmed_artifacts(state: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """校验需求、产品、UI、技术四阶段正式产物并返回索引，UI允许明确跳过。"""

    requirement_spec = state.get("requirement_spec")
    product_plan = state.get("product_plan")
    technical_plan = state.get("technical_plan")
    if not isinstance(requirement_spec, dict) or requirement_spec.get("confirmation_status") != "confirmed":
        raise ValueError("需求文档必须经用户确认后才能进入工作区。")
    ui_designs = state.get("ui_designs")
    if (
        not isinstance(technical_plan, dict)
        or technical_plan.get("artifact_type") != TECHNICAL_PLAN_ARTIFACT_TYPE
        or technical_plan.get("confirmation_status") != "confirmed"
    ):
        raise ValueError("技术规划必须经开发角色确认后才能进入工作区。")
    product_plan = require_current_product_plan(product_plan, requirement_spec)
    if not isinstance(product_plan, dict) or product_plan.get("confirmation_status") != "confirmed":
        raise ValueError("产品规划必须经产品角色确认后才能进入工作区。")
    if (
        not isinstance(ui_designs, dict)
        or ui_designs.get("confirmation_status") not in {"confirmed", "skipped"}
    ):
        raise ValueError("UI 设计稿必须经产品角色确认或明确跳过后才能进入工作区。")
    requirement_markdown = Path(str(state.get("requirement_spec_path") or ""))
    product_plan_markdown = Path(str(state.get("product_plan_path") or ""))
    technical_plan_markdown = Path(str(state.get("technical_plan_path") or ""))
    ui_design_json = workspace / ".xcodeagent" / "specs" / "ui-designs.json"
    return {
        "requirementSpec": {
            "markdown": _document_descriptor(
                workspace, requirement_markdown, "需求文档", "markdown", ".xcodeagent/specs"
            ),
            "json": _document_descriptor(
                workspace,
                state.get("requirement_spec_json_path") or requirement_markdown.with_suffix(".json"),
                "需求文档内部数据",
                "json",
                ".xcodeagent/specs",
            ),
        },
        "productPlan": {
            "markdown": _document_descriptor(
                workspace, product_plan_markdown, "产品规划", "markdown", ".xcodeagent/plans"
            ),
            "json": _document_descriptor(
                workspace,
                state.get("product_plan_json_path") or product_plan_markdown.with_suffix(".json"),
                "产品规划内部数据",
                "json",
                ".xcodeagent/plans",
            ),
        },
        "uiDesigns": {
            "json": _document_descriptor(
                workspace,
                ui_design_json,
                "UI 设计索引",
                "json",
                ".xcodeagent/specs",
                accepted_confirmation_statuses=("confirmed", "skipped"),
            ),
        },
        "technicalPlan": {
            "markdown": _document_descriptor(
                workspace, technical_plan_markdown, "技术规划", "markdown", ".xcodeagent/plans"
            ),
            "json": _document_descriptor(
                workspace,
                state.get("technical_plan_json_path") or technical_plan_markdown.with_suffix(".json"),
                "技术规划内部数据",
                "json",
                ".xcodeagent/plans",
            ),
        },
    }


def confirm_application_planning_artifacts(state: dict[str, Any]) -> dict[str, Any]:
    """确认 specs/plans 产物完整，不读取或改写 application.json。"""

    workspace = Path(str(state.get("workspace") or "")).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"应用工作区不存在：{workspace}")
    confirmed_at = datetime.now(UTC).isoformat()
    return {
        "confirmedAt": confirmed_at,
        "directories": {
            "specs": ".xcodeagent/specs",
            "plans": ".xcodeagent/plans",
        },
        "artifacts": _confirmed_artifacts(state, workspace),
    }
