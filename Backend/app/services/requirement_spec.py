from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.services.data_source_policy import (
    DatasourceType,
    apply_authoritative_datasource_type,
    datasource_type_from_artifact,
    ensure_requirements_datasource_type,
)
from app.services.entity_definitions import (
    merge_entities,
    normalize_entities,
)
from app.workspace.spec_documents import (
    load_requirement_spec_json,
    render_requirement_spec_markdown,
    requirement_spec_draft_json_path,
    requirement_spec_draft_markdown_path,
    write_requirement_spec_draft_document,
)


_LOWER_SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _default_authorization_requirements(
    enabled: bool = False,
) -> dict[str, Any]:
    """构造当前 RequirementSpec 使用的权限需求默认结构。"""

    return {
        "enabled": bool(enabled),
        "restrictedPages": [],
        "restrictedOperations": [],
        "dataRules": [],
    }


def _explicit_authorization_flag(text: str, marker: str) -> bool | None:
    """从创建应用规划请求中读取权限开关事实，避免模型自行覆盖表单选择。"""

    match = re.search(
        rf"{re.escape(marker)}\s*[:：]\s*(是|否|启用|不启用|开启|关闭|true|false)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).lower() in {"是", "启用", "开启", "true"}


def _authorization_enabled_from_request(text: str) -> bool | None:
    """读取权限是否启用的显式规划事实。"""

    for marker in ("涉及权限控制", "权限控制", "应用级资源授权"):
        value = _explicit_authorization_flag(text, marker)
        if value is not None:
            return value
    return None


class SaveRequirementSpecDraftRequest(BaseModel):
    """校验需求概览编辑器提交的独立保存动作。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal["save"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    spec: dict[str, Any]


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """判断需求文本是否包含指定业务关键词。"""

    return any(keyword in text for keyword in keywords)


def consolidated_requirement_text(request: str) -> str:
    """Collapse original request plus confirmation answers into one readable summary."""

    text = request.strip()
    if not text:
        return ""

    original = _section_after(text, "原始需求：", ("用户补充确认：",))
    answers = _section_after(text, "用户补充确认：", ())
    if not original and not answers:
        return _compact_lines(text)

    parts = [_compact_lines(original)]
    answer_facts = _answer_facts(answers)
    if answer_facts:
        parts.append("补充确认：" + "；".join(answer_facts))
    return "；".join(part for part in parts if part).strip("；")


def _section_after(text: str, marker: str, stop_markers: tuple[str, ...]) -> str:
    if marker not in text:
        return ""
    section = text.split(marker, 1)[1]
    for stop_marker in stop_markers:
        if stop_marker in section:
            section = section.split(stop_marker, 1)[0]
    return section.strip()


def _compact_lines(value: str) -> str:
    return "；".join(
        line.strip().strip("-").strip()
        for line in value.splitlines()
        if line.strip()
        and not line.strip().startswith("请基于")
        and line.strip() not in {"原始需求：", "用户补充确认："}
    )


def _answer_facts(value: str) -> list[str]:
    facts = []
    current_question = ""
    for raw_line in value.splitlines():
        line = raw_line.strip().strip("-").strip()
        if not line:
            continue
        if "回答：" in line:
            answer = line.split("回答：", 1)[1].strip()
            if answer:
                facts.append(
                    f"{current_question}为{answer}" if current_question else answer
                )
            current_question = ""
            continue
        current_question = line.split("：", 1)[0].strip() if "：" in line else line
    return facts


def _negative_optional_answer(value: str) -> bool:
    normalized = value.replace(" ", "")
    return normalized in {
        "无",
        "暂无",
        "没有",
        "不需要",
        "无需",
        "否",
        "不用",
        "没有其他",
        "不需要其他",
        "无需其他",
    }


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _string_list(value: Any) -> list[str]:
    """把来源引用等自由输入归一为去重后的非空字符串列表。"""

    if not isinstance(value, list):
        return []
    return _dedupe([str(item).strip() for item in value if str(item).strip()])


def _is_lower_snake_case(value: object) -> bool:
    """判断稳定业务标识是否符合当前 lower_snake_case 契约。"""

    return bool(_LOWER_SNAKE_CASE_PATTERN.fullmatch(str(value or "").strip()))


def _rule_signature(item: dict[str, Any], field_name: str) -> tuple[str, str]:
    """构造业务候选的稳定匹配键，用于保留已有 ruleId 而不采信模型 ID。"""

    name = str(item.get("name") or "").strip().casefold()
    if field_name == "dataRules":
        detail = "\n".join(
            str(item.get(field) or "").strip().casefold()
            for field in ("includes", "excludes")
        )
    else:
        detail = str(item.get("description") or "").strip().casefold()
    return name, detail


def _existing_rules(
    value: Any,
    field_name: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """收集已有唯一规则，供同步时保留内部稳定字段。"""

    items = value.get(field_name) if isinstance(value, dict) else None
    result: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates: set[tuple[str, str]] = set()
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("ruleId") or "").strip()
        signature = _rule_signature(item, field_name)
        if not rule_id or not signature[0] or signature in result:
            duplicates.add(signature)
            continue
        result[signature] = item
    return {key: item for key, item in result.items() if key not in duplicates}


def normalize_authorization_requirements(
    value: Any,
    *,
    enabled_hint: bool | None = None,
    existing_value: Any = None,
    pages: list[dict[str, Any]] | None = None,
    entities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """归一化 RequirementSpec 权限候选，只保留需求阶段的业务语义。"""

    raw = value if isinstance(value, dict) else {}
    raw_enabled = raw.get("enabled")
    enabled = (
        bool(enabled_hint)
        if enabled_hint is not None
        else bool(raw_enabled)
        if isinstance(raw_enabled, bool)
        else False
    )
    # 页面和实体参数保留在函数签名中，避免调用方把候选绑定到尚未完成的规划产物。
    # RequirementSpec 阶段不读取它们，也不生成或校验 pageId/entityId。
    _ = pages, entities
    existing_rules = {
        field_name: _existing_rules(existing_value, field_name)
        for field_name in ("restrictedPages", "restrictedOperations", "dataRules")
    }

    def normalize_rule(item: Any, field_name: str) -> dict[str, Any] | None:
        """归一化单个受控页面候选，只保存页面业务含义。"""

        if not isinstance(item, dict):
            return None
        signature_source = {
            "name": str(item.get("name") or "").strip(),
            "description": str(item.get("description") or ""),
        }
        if field_name == "dataRules":
            signature_source["includes"] = str(item.get("includes") or "").strip()
            signature_source["excludes"] = str(item.get("excludes") or "").strip()
        signature = _rule_signature(signature_source, field_name)
        existing_rule = existing_rules[field_name].get(signature, {})
        normalized = {
            "name": signature_source["name"],
            "description": signature_source["description"],
            "sourceRefs": _string_list(item.get("sourceRefs"))
            or _string_list(existing_rule.get("sourceRefs")),
            "defaultGrantedRoleIds": _string_list(item.get("defaultGrantedRoleIds"))
            or _string_list(existing_rule.get("defaultGrantedRoleIds")),
            "ruleId": str(existing_rule.get("ruleId") or "").strip() or str(uuid4()),
        }
        if field_name != "dataRules":
            normalized["rationale"] = str(item.get("rationale") or "")
        else:
            normalized["dataRuleKey"] = str(
                existing_rule.get("dataRuleKey") or item.get("dataRuleKey") or ""
            ).strip()
            normalized["includes"] = signature_source["includes"] or str(
                existing_rule.get("includes") or ""
            ).strip()
            normalized["excludes"] = signature_source["excludes"] or str(
                existing_rule.get("excludes") or ""
            ).strip()
        return normalized

    def raw_list(field_name: str) -> list[Any]:
        """读取权限候选数组，非法形状按空数组处理。"""

        value = raw.get(field_name)
        return value if isinstance(value, list) else []

    normalized = {
        "enabled": enabled,
        "restrictedPages": [
            rule
            for item in raw_list("restrictedPages")
            if (rule := normalize_rule(item, "restrictedPages")) is not None
        ]
        if enabled
        else [],
        "restrictedOperations": [
            rule
            for item in raw_list("restrictedOperations")
            if (rule := normalize_rule(item, "restrictedOperations")) is not None
        ]
        if enabled
        else [],
        "dataRules": [
            rule
            for item in raw_list("dataRules")
            if (rule := normalize_rule(item, "dataRules")) is not None
        ]
        if enabled
        else [],
    }
    initial_admin_role_id = str(raw.get("initialAdminRoleId") or "").strip()
    if not initial_admin_role_id and isinstance(existing_value, dict):
        initial_admin_role_id = str(existing_value.get("initialAdminRoleId") or "").strip()
    if enabled and initial_admin_role_id:
        normalized["initialAdminRoleId"] = initial_admin_role_id
    return normalized


def validate_authorization_requirements(
    value: dict[str, Any],
    *,
    require_initial_admin: bool = True,
) -> list[str]:
    """校验权限候选的业务语义，并可延后初始系统管理员确认。"""

    authorization = value.get("authorization_requirements") if isinstance(value, dict) else None
    if authorization is None and isinstance(value, dict):
        authorization = value
    if not isinstance(authorization, dict):
        return []

    enabled = authorization.get("enabled") is True
    errors: list[str] = []

    if not enabled:
        for field in ("restrictedPages", "restrictedOperations", "dataRules"):
            if authorization.get(field):
                errors.append(f"权限未启用时 {field} 必须为空")
        if authorization.get("initialAdminRoleId"):
            errors.append("权限未启用时不能保留 initialAdminRoleId")
        return errors

    for field_name, label in (
        ("restrictedPages", "受控页面"),
        ("restrictedOperations", "受控操作"),
        ("dataRules", "数据范围"),
    ):
        if not isinstance(authorization.get(field_name), list):
            errors.append(f"{label}候选必须是数组")

    restricted_pages = authorization.get("restrictedPages")
    restricted_pages = restricted_pages if isinstance(restricted_pages, list) else []
    restricted_operations = authorization.get("restrictedOperations")
    restricted_operations = (
        restricted_operations if isinstance(restricted_operations, list) else []
    )
    data_rules = authorization.get("dataRules")
    data_rules = data_rules if isinstance(data_rules, list) else []
    if "unauthorizedBehavior" in authorization:
        errors.append("RequirementSpec 不支持 unauthorizedBehavior")

    roles = value.get("user_roles") if isinstance(value, dict) else None
    roles = roles if isinstance(roles, list) else []
    role_ids: set[str] = set()
    initial_roles: list[dict[str, Any]] = []
    for role in roles:
        if not isinstance(role, dict):
            errors.append("业务参与者必须是对象")
            continue
        role_id = str(role.get("id") or "").strip()
        if not _is_lower_snake_case(role_id):
            errors.append(f"业务参与者 {role.get('name') or '未命名'} 的 id 必须为 lower_snake_case")
        elif role_id in role_ids:
            errors.append(f"业务参与者 {role_id} 的 id 重复")
        else:
            role_ids.add(role_id)
        if not isinstance(role.get("isSystemRole"), bool):
            errors.append(f"业务参与者 {role_id or '未命名'} 缺少 isSystemRole")
        if not isinstance(role.get("isInitialAdminRole"), bool):
            errors.append(f"业务参与者 {role_id or '未命名'} 缺少 isInitialAdminRole")
        if role.get("isInitialAdminRole") is True:
            initial_roles.append(role)
            if role.get("isSystemRole") is not True:
                errors.append(f"初始系统管理员角色 {role_id or '未命名'} 必须同时是系统角色")
    if require_initial_admin and len(initial_roles) != 1:
        errors.append("权限启用时必须且只能选择一个初始系统管理员角色")
    initial_admin_role_id = str(authorization.get("initialAdminRoleId") or "").strip()
    if require_initial_admin and not initial_admin_role_id:
        errors.append("权限启用时缺少 initialAdminRoleId")
    elif initial_admin_role_id and initial_admin_role_id not in role_ids:
        errors.append("initialAdminRoleId 必须引用 user_roles 中的角色")
    elif initial_admin_role_id and not any(
        str(role.get("id") or "").strip() == initial_admin_role_id for role in initial_roles
    ):
        errors.append("initialAdminRoleId 必须引用唯一初始系统管理员角色")

    rule_ids: set[str] = set()
    data_rule_keys: set[str] = set()
    for item in restricted_pages:
        if not isinstance(item, dict):
            errors.append("受控页面规则必须是对象")
            continue
        page_name = str(item.get("name") or "").strip()
        if not page_name:
            errors.append("受控页面缺少业务对象名称")
        if not str(item.get("description") or "").strip():
            errors.append(f"受控页面 {page_name or '未命名'} 缺少业务说明")
        _validate_authorization_rule_metadata(
            item, page_name or "未命名", rule_ids, role_ids, errors
        )

    for item in restricted_operations:
        if not isinstance(item, dict):
            errors.append("受控操作规则必须是对象")
            continue
        operation_name = str(item.get("name") or "").strip()
        if not operation_name:
            errors.append("受控操作缺少业务操作名称")
        if not str(item.get("description") or "").strip():
            errors.append(f"受控操作 {operation_name or '未命名'} 缺少业务描述")
        _validate_authorization_rule_metadata(
            item, operation_name or "未命名", rule_ids, role_ids, errors
        )

    for item in data_rules:
        if not isinstance(item, dict):
            errors.append("数据范围规则必须是对象")
            continue
        data_name = str(item.get("name") or "").strip()
        if not data_name:
            errors.append("数据范围缺少业务对象名称")
        if not str(item.get("includes") or "").strip():
            errors.append(f"数据范围 {data_name or '未命名'} 缺少 includes 业务边界")
        if not str(item.get("excludes") or "").strip():
            errors.append(f"数据范围 {data_name or '未命名'} 缺少 excludes 业务边界")
        technical_text = " ".join(
            str(item.get(field_name) or "")
            for field_name in ("name", "description", "includes", "excludes")
        )
        if re.search(
            r"(?:resourcekey|policykey|\bsql\b|\bselect\b|\bwhere\b|\bjoin\b|字段|列名|数据库)",
            technical_text,
            flags=re.IGNORECASE,
        ):
            errors.append(f"数据范围 {data_name or '未命名'} 必须使用产品语言，不能包含技术字段或 SQL")
        data_rule_key = str(item.get("dataRuleKey") or "").strip()
        if not _is_lower_snake_case(data_rule_key):
            errors.append(f"数据范围 {data_name or '未命名'} 的 dataRuleKey 必须为 lower_snake_case")
        elif data_rule_key in data_rule_keys:
            errors.append(f"数据范围 {data_name or '未命名'} 的 dataRuleKey 重复")
        else:
            data_rule_keys.add(data_rule_key)
        _validate_authorization_rule_metadata(
            item, data_name or "未命名", rule_ids, role_ids, errors
        )

    return _dedupe(errors)


def _validate_authorization_rule_metadata(
    item: dict[str, Any],
    name: str,
    rule_ids: set[str],
    role_ids: set[str],
    errors: list[str],
) -> None:
    """校验候选的内部稳定 ID 与可追溯业务来源。"""

    rule_id = str(item.get("ruleId") or "").strip()
    if not rule_id:
        errors.append(f"权限规则 {name} 缺少 ruleId")
    elif rule_id in rule_ids:
        errors.append(f"权限规则 {name} 的 ruleId 重复")
    else:
        rule_ids.add(rule_id)
    if not _string_list(item.get("sourceRefs")):
        errors.append(f"权限规则 {name} 缺少业务来源")
    granted_role_ids = _string_list(item.get("defaultGrantedRoleIds"))
    if not granted_role_ids:
        errors.append(f"权限规则 {name} 缺少 defaultGrantedRoleIds")
    elif unknown_role_ids := set(granted_role_ids) - role_ids:
        errors.append(
            f"权限规则 {name} 的 defaultGrantedRoleIds 引用了未知角色："
            + "、".join(sorted(unknown_role_ids))
        )


def validate_requirement_spec_confirmation_readiness(spec: dict[str, Any]) -> list[str]:
    """校验需求文档进入用户确认前必须具备的最小业务结构。"""

    errors: list[str] = []
    app_info = spec.get("app_info")
    if not isinstance(app_info, dict):
        errors.append("应用信息必须是对象")
    else:
        if not str(app_info.get("name") or "").strip():
            errors.append("应用名称不能为空")
        if not str(app_info.get("summary") or "").strip():
            errors.append("应用需求摘要不能为空")

    for field_name, label in (
        ("user_roles", "业务参与者"),
        ("feature_modules", "功能模块"),
        ("pages", "页面清单"),
        ("entities", "实体清单"),
        ("business_flows", "业务流程"),
    ):
        if not isinstance(spec.get(field_name), list):
            errors.append(f"{label}必须是数组")

    modules = spec.get("feature_modules")
    if isinstance(modules, list) and not modules:
        errors.append("功能模块不能为空")
    pages = spec.get("pages")
    if isinstance(pages, list):
        if not pages:
            errors.append("页面清单不能为空")
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append(f"页面清单第 {index + 1} 项必须是对象")
                continue
            if not str(page.get("pageId") or page.get("id") or "").strip():
                errors.append(f"页面清单第 {index + 1} 项缺少 pageId")
            for field_name in ("name", "path", "module_id", "description"):
                if not str(page.get(field_name) or "").strip():
                    errors.append(f"页面清单第 {index + 1} 项缺少 {field_name}")

    return _dedupe(errors)


def _path_from_pageId(pageId: str) -> str:
    """根据 pageId 生成稳定页面路由。"""

    route = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(pageId or "page")).strip("-_")
    route = route.replace("_", "-").lower() or "page"
    if route.endswith("-page") and route != "dashboard-page":
        route = route[: -len("-page")] or route
    return "/" if route in {"dashboard", "dashboard-page", "home", "index"} else f"/{route}"


def _unique_page_path(path: str, pageId: str, used_paths: set[str]) -> str:
    """把重复页面路由改成基于 pageId 的唯一值。"""

    normalized = path.strip() or _path_from_pageId(pageId)
    if normalized != "/" and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if normalized not in used_paths:
        used_paths.add(normalized)
        return normalized

    candidate = _path_from_pageId(pageId)
    if candidate == "/" or candidate in used_paths:
        base = candidate if candidate != "/" else f"/{pageId.replace('_', '-').lower() or 'page'}"
        suffix = 2
        candidate = base
        while candidate in used_paths:
            candidate = f"{base}-{suffix}"
            suffix += 1
    used_paths.add(candidate)
    return candidate


def _app_name(request: str) -> str:
    if "订单" in request:
        return "订单管理应用"
    if "库存" in request:
        return "库存管理应用"
    if "客户" in request or "crm" in request.lower():
        return "客户管理应用"
    if "审批" in request:
        return "审批流程应用"
    return "业务管理应用"


def _feature_modules(request: str) -> list[dict[str, Any]]:
    modules = [
        {
            "id": "dashboard",
            "name": "概览看板",
            "description": "展示核心指标、最近动态和快捷入口。",
            "priority": "should",
        }
    ]

    if "订单" in request:
        modules.append(
            {
                "id": "order_management",
                "name": "订单管理",
                "description": "支持订单列表、筛选、详情查看和状态跟踪。",
                "priority": "must",
            }
        )
    elif "库存" in request:
        modules.append(
            {
                "id": "inventory_management",
                "name": "库存管理",
                "description": "支持库存列表、库存变更和库存预警。",
                "priority": "must",
            }
        )
    elif "客户" in request or "crm" in request.lower():
        modules.append(
            {
                "id": "customer_management",
                "name": "客户管理",
                "description": "支持客户列表、客户详情和跟进记录。",
                "priority": "must",
            }
        )
    else:
        modules.append(
            {
                "id": "core_management",
                "name": "核心业务管理",
                "description": "围绕用户原始需求提供核心对象的增删改查和状态查看。",
                "priority": "must",
            }
        )

    if _contains_any(request, ("登录", "鉴权", "认证")) or (
        _authorization_enabled_from_request(request) is True
    ):
        modules.append(
            {
                "id": "access_control",
                "name": "登录与权限",
                "description": "支持用户登录和基于资源的页面/操作授权，角色关系在运行态配置。",
                "priority": "must",
            }
        )

    return modules


def _pages(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages = [
        {
            "pageId": "dashboard_page",
            "name": "概览页",
            "path": "/",
            "module_id": "dashboard",
            "description": "应用首页，展示业务概览和快捷操作。",
        }
    ]

    for module in modules:
        module_id = module["id"]
        if module_id == "dashboard":
            continue
        if module_id == "access_control":
            pages.append(
                {
                    "pageId": "login_page",
                    "name": "登录页",
                    "path": "/login",
                    "module_id": module_id,
                    "description": "用户输入账号信息并进入系统。",
                }
            )
            continue
        pages.extend(
            [
                {
                    "pageId": f"{module_id}_list_page",
                    "name": f"{module['name']}列表页",
                    "path": f"/{module_id.replace('_', '-')}",
                    "module_id": module_id,
                    "description": f"展示{module['name']}数据，支持搜索、筛选和主要操作。",
                },
                {
                    "pageId": f"{module_id}_detail_page",
                    "name": f"{module['name']}详情页",
                    "path": f"/{module_id.replace('_', '-')}/:id",
                    "module_id": module_id,
                    "description": f"展示单条{module['name']}记录详情和关联信息。",
                },
            ]
        )

    return pages


def _entities(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """需求阶段只生成业务实体与展示信息，不绑定任何数据源。"""

    entities = [
        _entity_definition(
            "User",
            "用户",
            "登录系统的账号主体，记录身份和联系方式。",
            [
                {"label": "账号", "description": "登录账号，全局唯一。"},
                {"label": "姓名", "description": "用户显示名称。"},
                {"label": "邮箱", "description": "联系邮箱。"},
                {"label": "手机号", "description": "联系手机号。"},
                {"label": "状态", "description": "账号启用状态。"},
            ],
        )
    ]
    # 启用应用级权限后，角色由模板固定管理页和运行态 RBAC 提供，不生成业务 Role 实体。
    if not any(module.get("id") == "access_control" for module in modules):
        entities.append(
            _entity_definition(
                "Role",
                "角色",
                "业务参与者分组信息，仅用于业务描述，不决定授权。",
                [
                    {"label": "角色名称", "description": "业务分组显示名称。"},
                    {"label": "角色编码", "description": "业务分组编码。"},
                ],
            )
        )

    for module in modules:
        module_id = module["id"]
        if module_id in {"dashboard", "access_control"}:
            continue
        entity_name = _module_entity_name(module_id)
        entities.append(
            {
                **_entity_definition(
                    entity_name,
                    entity_name,
                    f"{module['name']}相关的核心业务对象。",
                    _default_entity_fields(),
                ),
                "module_id": module_id,
            }
        )

    return entities


def _module_entity_name(module_id: str) -> str:
    """从模块 id 派生业务实体名，去掉 management 等后缀得到干净实体名。"""

    parts = [
        part for part in str(module_id or "").split("_") if part
    ]
    if parts and parts[-1] == "management":
        parts = parts[:-1]
    if not parts:
        parts = [str(module_id or "core")]
    return "".join(part.title() for part in parts)


def _default_entity_fields() -> list[dict[str, Any]]:
    """生成不依赖需求关键词的通用实体展示信息兜底（不含字段名与类型）。"""

    return [
        {
            "label": "名称",
            "description": "业务对象名称。",
        },
        {
            "label": "编码",
            "description": "业务编码，可用于检索与去重。",
        },
        {
            "label": "状态",
            "description": "业务状态。",
        },
        {
            "label": "说明",
            "description": "补充说明。",
        },
    ]


def _entity_definition(
    entity_id: str,
    name: str,
    description: str,
    fields: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造业务实体对象，供确定性默认数据源使用。"""

    return {
        "id": entity_id,
        "name": name,
        "description": description,
        "fields": fields,
    }


def _business_flows(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flows = [
        {
            "id": "open_dashboard",
            "name": "查看业务概览",
            "steps": ["进入应用", "查看核心指标", "通过快捷入口进入业务页面"],
        }
    ]

    if any(module["id"] == "access_control" for module in modules):
        flows.insert(
            0,
            {
                "id": "login",
                "name": "用户登录",
                "steps": ["打开登录页", "输入账号信息", "校验身份", "进入有权限的页面"],
            },
        )

    for module in modules:
        if module["id"] in {"dashboard", "access_control"}:
            continue
        flows.append(
            {
                "id": f"{module['id']}_browse",
                "name": f"浏览{module['name']}",
                "steps": [
                    f"进入{module['name']}列表页",
                    "按条件搜索或筛选",
                    "打开详情页",
                    "查看关键字段和状态",
                ],
            }
        )

    return flows


def _acceptance_criteria(spec_name: str) -> list[str]:
    """生成只描述应用用户结果的默认产品验收标准。"""

    return [
        f"目标用户可以访问{spec_name}的主要业务页面。",
        "页面清单中的每个页面都有可见标题、主要内容区、加载态、空态和错误态。",
        "页面所需的核心业务信息可以被正确读取并展示。",
        "用户执行核心操作后可以看到与产品规则一致的成功、失败或校验反馈。",
        "主要业务流程可以由对应业务参与者按预期步骤完成。",
        "如包含登录或权限模块，未授权用户不能访问受保护页面。",
    ]


_WORKFLOW_ACCEPTANCE_PATTERNS = (
    re.compile(r"xcodeagent", re.IGNORECASE),
    re.compile(r"质量门禁"),
    re.compile(r"(?:集成|单元|冒烟|自动化)?测试.*(?:通过|完成).*(?:用户验收|交付)"),
    re.compile(r"(?:编译|构建|lint|typecheck|代码生成).*(?:通过|完成)", re.IGNORECASE),
    re.compile(r"本地预览地址"),
    re.compile(r"前端运行错误"),
    re.compile(r"(?:工作流|流水线).*(?:阶段|节点|通过|完成)"),
)


def product_acceptance_criteria(value: Any) -> list[str]:
    """只保留生成应用自身的产品结果，剔除 XCodeAgent 交付工作流标准。"""

    if not isinstance(value, list):
        return []
    return [
        text
        for item in value
        if (text := str(item).strip())
        and not any(pattern.search(text) for pattern in _WORKFLOW_ACCEPTANCE_PATTERNS)
    ]


def _normalize_requirement_items(
    value: Any,
    *,
    prefix: str,
    defaults: dict[str, Any],
    identity_key: str = "id",
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get(identity_key) or f"{prefix}_{index + 1}")
        normalized_item = {**defaults, **item, identity_key: item_id}
        normalized_item["name"] = str(
            normalized_item.get("name") or item_id
        )
        if "description" in defaults:
            normalized_item["description"] = str(
                normalized_item.get("description") or normalized_item["name"]
            )
        normalized.append(normalized_item)
    return normalized


def create_requirement_spec(
    request: str,
    agent_note: str = "live main-agent requirements analysis",
    agent_spec: dict[str, Any] | None = None,
    existing_spec: dict[str, Any] | None = None,
    authoritative_agent_spec: bool = False,
    datasource_type: DatasourceType | None = None,
    allow_inferred_defaults: bool = True,
) -> dict[str, Any]:
    """生成完整 RequirementSpec，并把数据源类型限定为应用策略类型。"""

    # 非工作流单元测试和纯函数调用没有 workspace，因此只使用明确的 database 兜底，绝不再根据需求猜测类型。
    effective_datasource_type = (
        ensure_requirements_datasource_type(datasource_type)
        if datasource_type is not None
        else "database"
    )
    requirement_summary = consolidated_requirement_text(request)
    source_text = requirement_summary or request
    merged_summary = _merged_requirement_summary(
        source_text,
        agent_spec=agent_spec,
        existing_spec=existing_spec,
    )
    # 模型正在 ask_user 时只允许保留已有或明确返回的事实，禁止用默认页面填充未决需求。
    modules = _feature_modules(source_text) if allow_inferred_defaults else []
    app_name = _app_name(source_text) if allow_inferred_defaults else ""
    request_authorization_enabled = _authorization_enabled_from_request(source_text)
    roles = [
        {
            "id": "business_user",
            "name": "业务使用者",
            "description": "使用应用完成已确认的业务流程。",
            "isSystemRole": False,
            "isInitialAdminRole": False,
        },
    ] if allow_inferred_defaults else []

    default_app_info = {
        "name": app_name,
        "summary": merged_summary,
        "target": "生成一个可在本地运行的前后端应用工程。"
        if allow_inferred_defaults
        else "",
    }

    default_spec = {
        "version": "0.1.0",
        "status": "draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": merged_summary,
        "source_request": source_text,
        "app_info": default_app_info,
        "user_roles": roles,
        "feature_modules": modules,
        "pages": _pages(modules) if allow_inferred_defaults else [],
        "entities": _entities(modules) if allow_inferred_defaults else [],
        "business_flows": _business_flows(modules) if allow_inferred_defaults else [],
        "authorization_requirements": _default_authorization_requirements(
            enabled=(request_authorization_enabled is True),
        ),
        "acceptance_criteria": (
            _acceptance_criteria(app_name) if allow_inferred_defaults else []
        ),
        "clarification_questions": [],
        "agent_note": agent_note,
        "approved": True,
    }
    spec = {
        **default_spec,
        **(deepcopy(existing_spec) if isinstance(existing_spec, dict) else {}),
    }
    if isinstance(agent_spec, dict):
        spec.update(agent_spec)
        spec["app_info"] = {
            **default_spec["app_info"],
            **(
                agent_spec.get("app_info", {})
                if isinstance(agent_spec.get("app_info"), dict)
                else {}
            ),
        }

    item_defaults = {
        "user_roles": (
            "role",
            {
                "name": "用户",
                "description": "使用应用。",
                "isSystemRole": False,
                "isInitialAdminRole": False,
            },
        ),
        "feature_modules": (
            "module",
            {"name": "业务模块", "description": "完成核心业务。", "priority": "must"},
        ),
        "pages": (
            "page",
            {"name": "业务页面", "path": "/", "module_id": "core", "description": "业务页面。"},
            "pageId",
        ),
        "entities": (
            "entity",
            {"name": "业务实体", "description": "业务实体。", "fields": []},
        ),
        "business_flows": ("flow", {"name": "业务流程", "steps": []}),
    }
    for key, config in item_defaults.items():
        prefix, defaults = config[0], config[1]
        identity_key = config[2] if len(config) > 2 else "id"
        normalized = _normalize_requirement_items(
            spec.get(key),
            prefix=prefix,
            defaults=defaults,
            identity_key=identity_key,
        )
        has_authoritative_list = (
            authoritative_agent_spec
            and isinstance(agent_spec, dict)
            and isinstance(agent_spec.get(key), list)
        )
        spec[key] = normalized if normalized or has_authoritative_list else default_spec[key]
    # 用户角色保留首次系统管理员种子元数据，但不携带资源关系或运行态授权。
    for role in spec["user_roles"]:
        for forbidden_key in ("permissions", "allowed_roles", "allowedRoleIds", "roleIds"):
            role.pop(forbidden_key, None)
        role["isSystemRole"] = bool(role.get("isSystemRole"))
        role["isInitialAdminRole"] = bool(role.get("isInitialAdminRole"))
    spec["entities"] = normalize_entities(
        spec.get("entities"),
        with_types=False,
    )
    spec = _migrate_legacy_data_sources(spec)
    # 保留已有实体的稳定 id：新内容覆盖业务字段，旧 id 不被模型或编辑器改写。
    spec["entities"] = merge_entities(
        (existing_spec or {}).get("entities"),
        spec["entities"],
        with_types=False,
    )
    for flow in spec["business_flows"]:
        steps = flow.get("steps")
        flow["steps"] = (
            [str(item) for item in steps if str(item).strip()]
            if isinstance(steps, list)
            else []
        )
        # 编辑态允许删空；保存时仍用流程说明兜底，避免后续规划拿到空步骤。
        if not flow["steps"] and str(flow.get("description") or "").strip():
            flow["steps"] = [str(flow["description"]).strip()]
    used_page_paths: set[str] = set()
    route_root = str(spec.get("app_info", {}).get("route_root_path") or "").strip().rstrip("/")
    for page in spec["pages"]:
        pageId = str(page.get("pageId") or "")
        path = str(page.get("path") or "")
        # 去除 LLM 可能引入的重复 route_root_path 前缀，例如 /page/page/projects → /page/projects
        if route_root and path.startswith(f"{route_root}/{route_root.lstrip('/')}"):
            path = route_root + path[len(f"{route_root}/{route_root.lstrip('/')}"):] if path.startswith(f"{route_root}/{route_root.lstrip('/')}/") else route_root
        page["path"] = _unique_page_path(path, pageId, used_page_paths)

    # 权限候选在业务页面和实体归一化后统一整理，但不建立跨产物技术绑定。
    agent_authorization = (
        agent_spec.get("authorization_requirements")
        if isinstance(agent_spec, dict)
        else None
    )
    existing_authorization = (
        existing_spec.get("authorization_requirements")
        if isinstance(existing_spec, dict)
        else None
    )
    authorization_source = (
        agent_authorization
        if isinstance(agent_authorization, dict)
        else existing_authorization
    )
    explicit_authorization_enabled = _authorization_enabled_from_request(source_text)
    # 权限总开关由创建表单约束；业务行为和 ruleId 只来自候选及已有内部状态。
    spec["authorization_requirements"] = normalize_authorization_requirements(
        authorization_source
        if isinstance(authorization_source, dict)
        else default_spec["authorization_requirements"],
        enabled_hint=explicit_authorization_enabled,
        existing_value=existing_authorization,
        pages=spec["pages"],
        entities=spec["entities"],
    )
    if spec["authorization_requirements"].get("enabled") is not True:
        # 关闭权限时不保留任何系统管理员角色种子，避免配置与需求事实冲突。
        for role in spec["user_roles"]:
            role["isSystemRole"] = False
            role["isInitialAdminRole"] = False

    criteria = spec.get("acceptance_criteria")
    normalized_criteria = product_acceptance_criteria(criteria)
    has_authoritative_criteria = (
        authoritative_agent_spec
        and isinstance(agent_spec, dict)
        and isinstance(agent_spec.get("acceptance_criteria"), list)
    )
    spec["acceptance_criteria"] = (
        normalized_criteria
        if normalized_criteria or has_authoritative_criteria
        else default_spec["acceptance_criteria"]
    )
    # RequirementSpec 只保留正式产品事实，必须清除模型越界返回的产品假设。
    spec.pop("assumptions", None)
    spec.update(
        {
            "version": str(spec.get("version") or "0.1.0"),
            "status": "draft",
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": merged_summary,
            "source_request": source_text,
            "agent_note": agent_note,
            "agent_spec_used": isinstance(agent_spec, dict),
            "approved": True,
        }
    )
    spec["app_info"] = {
        **default_spec["app_info"],
        **(spec.get("app_info") if isinstance(spec.get("app_info"), dict) else {}),
        "summary": merged_summary,
    }
    spec.pop("data_sources", None)
    return apply_authoritative_datasource_type(spec, effective_datasource_type)


def _merged_requirement_summary(
    latest_request: str,
    *,
    agent_spec: dict[str, Any] | None,
    existing_spec: dict[str, Any] | None,
) -> str:
    """修订时保留完整需求语义，禁止把本轮增量输入直接覆盖原摘要。"""

    agent_app_info = (
        agent_spec.get("app_info")
        if isinstance(agent_spec, dict) and isinstance(agent_spec.get("app_info"), dict)
        else {}
    )
    agent_summary = str(agent_app_info.get("summary") or "").strip()
    if agent_summary:
        return agent_summary
    if not isinstance(existing_spec, dict):
        return latest_request
    existing_app_info = (
        existing_spec.get("app_info")
        if isinstance(existing_spec.get("app_info"), dict)
        else {}
    )
    existing_summary = str(
        existing_app_info.get("summary") or existing_spec.get("summary") or ""
    ).strip()
    if not existing_summary:
        return latest_request
    if not latest_request or latest_request in existing_summary:
        return existing_summary
    return f"{existing_summary}；最新调整：{latest_request}"


def _migrate_legacy_data_sources(spec: dict[str, Any]) -> dict[str, Any]:
    """把旧版 RequirementSpec 的 data_sources 实体拍平为顶层 entities。"""

    if spec.get("entities"):
        spec.pop("data_sources", None)
        return spec
    legacy_entities: list[Any] = []
    for source in spec.get("data_sources") if isinstance(spec.get("data_sources"), list) else []:
        if not isinstance(source, dict):
            continue
        legacy_entities.extend(
            item
            for item in (source.get("entities") if isinstance(source.get("entities"), list) else [])
            if isinstance(item, (dict, str)) and str(item).strip()
        )
    spec.pop("data_sources", None)
    if legacy_entities:
        spec["entities"] = legacy_entities
    return spec


_EDITOR_ITEM_FIELDS: dict[str, tuple[str, ...]] = {
    "user_roles": ("id", "name", "description", "isSystemRole", "isInitialAdminRole"),
    "pages": ("pageId", "name", "path", "module_id", "description", "components"),
    "business_flows": ("id", "name", "description", "steps"),
    "entities": ("id", "name", "description", "fields"),
}


def apply_requirement_spec_editor_changes(
    existing_spec: dict[str, Any],
    edited_spec: dict[str, Any],
    datasource_type: DatasourceType | None = None,
) -> dict[str, Any]:
    """将概览编辑器的可见字段合并回 RequirementSpec。

    数组以用户提交为准，以支持新增和删除；页面依据 pageId、其余条目依据 id
    保留未在界面中展示的内部字段。
    """

    # 独立编辑器单测可能没有 workspace；正式工作流会由调用方传入 application.json 类型。
    effective_datasource_type = datasource_type or "database"

    merged = deepcopy(existing_spec)
    existing_app = (
        existing_spec.get("app_info")
        if isinstance(existing_spec.get("app_info"), dict)
        else {}
    )
    edited_app = edited_spec.get("app_info")
    if isinstance(edited_app, dict):
        merged["app_info"] = {
            **existing_app,
            **{
                key: str(edited_app.get(key) or "").strip()
                for key in ("name", "target", "description", "summary")
                if key in edited_app
            },
        }

    for field_name, allowed_fields in _EDITOR_ITEM_FIELDS.items():
        edited_items = edited_spec.get(field_name)
        if not isinstance(edited_items, list):
            continue
        identity_key = "pageId" if field_name == "pages" else "id"
        existing_items = {
            str(item.get(identity_key)): item
            for item in existing_spec.get(field_name, [])
            if isinstance(item, dict) and item.get(identity_key)
        }
        sanitized_items: list[dict[str, Any]] = []
        for item in edited_items:
            if field_name == "entities" and isinstance(item, str):
                # 兼容旧字符串实体：编辑器提交字符串时归一为实体对象。
                item = {
                    "id": item.strip(),
                    "name": item.strip(),
                    "description": "",
                    "fields": [],
                }
            if not isinstance(item, dict):
                continue
            item_id = str(item.get(identity_key) or "").strip()
            existing_item = existing_items.get(item_id)
            sanitized = deepcopy(existing_item or {})
            sanitized.update(
                {
                    key: deepcopy(item[key])
                    for key in allowed_fields
                    if key in item
                }
            )
            sanitized_items.append(sanitized)
        merged[field_name] = sanitized_items

    # 权限章节是需求确认中的业务候选编辑区，整体替换候选数组但保留内部元数据。
    edited_authorization = edited_spec.get("authorization_requirements")
    if isinstance(edited_authorization, dict):
        existing_authorization = (
            existing_spec.get("authorization_requirements")
            if isinstance(existing_spec.get("authorization_requirements"), dict)
            else {}
        )
        merged["authorization_requirements"] = {
            **deepcopy(existing_authorization),
            **deepcopy(edited_authorization),
        }

    request = str(existing_spec.get("source_request") or existing_spec.get("summary") or "")
    normalized = create_requirement_spec(
        request,
        agent_note="synchronized from RequirementSpec summary editor",
        agent_spec=merged,
        existing_spec=existing_spec,
        authoritative_agent_spec=True,
        datasource_type=effective_datasource_type,
    )
    authorization_errors = validate_authorization_requirements(normalized)
    if authorization_errors:
        raise ValueError("编辑后的权限需求存在不一致：" + "；".join(authorization_errors))
    normalized["editor_sync"] = {
        "status": "synchronized",
        "source": "requirement_spec_summary_editor",
    }
    return normalized


def save_requirement_spec_draft(
    request: SaveRequirementSpecDraftRequest,
) -> dict[str, Any]:
    """合并编辑器草稿并重写待确认的 RequirementSpec Markdown 与内部 JSON。"""

    workspace = Path(request.workspace_root).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("需求文档工作区不存在或不是目录。")
    # 应用级不再有数据源类型；数据源由实体设计阶段选择，需求草稿保存不再读取 application.json。
    datasource_type = datasource_type_from_artifact({}, fallback="database")

    state: dict[str, Any] = {"workspace": str(workspace)}
    json_path = requirement_spec_draft_json_path(state)
    if not json_path.is_file():
        raise ValueError("尚未生成可编辑的需求文档。")

    existing_spec = load_requirement_spec_json(json_path)
    if not isinstance(existing_spec, dict):
        raise ValueError("需求文档内部数据必须是对象。")
    if existing_spec.get("confirmation_status") != "pending_user_confirmation":
        raise ValueError("只有待确认的需求文档才能保存编辑草稿。")

    synchronized_spec = apply_requirement_spec_editor_changes(
        existing_spec,
        request.spec,
        datasource_type=datasource_type,
    )
    synchronized_spec.update(
        {
            "confirmation_status": "pending_user_confirmation",
            "clarification_status": existing_spec.get("clarification_status", "clear"),
            "clarification_questions": existing_spec.get("clarification_questions", []),
        }
    )
    markdown_path = Path(
        write_requirement_spec_draft_document(state, synchronized_spec)
    )
    return {
        "requirementSpec": synchronized_spec,
        "artifact": {
            "id": "requirement_spec",
            "name": markdown_path.name,
            "path": str(requirement_spec_draft_markdown_path(state)),
            "format": "markdown",
            "content": render_requirement_spec_markdown(synchronized_spec),
        },
    }
