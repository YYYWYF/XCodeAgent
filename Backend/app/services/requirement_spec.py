from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import Any, Literal

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
    requirement_spec_json_path,
    requirement_spec_markdown_path,
    write_requirement_spec_document,
)


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


def _answer_blocks(request: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    current_question = ""
    for raw_line in request.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        stripped = line.strip("-").strip()
        if stripped.startswith("回答：") or stripped.startswith("回答:"):
            answer = re.split(r"回答[:：]", stripped, maxsplit=1)[1].strip()
            if current_question and answer:
                blocks.append({"question": current_question, "answer": answer})
            current_question = ""
            continue
        if "：" in stripped:
            current_question = stripped
    return blocks


def _answer_values(answer: str) -> list[str]:
    cleaned_parts: list[str] = []
    for part in re.split(r"[；;]", answer):
        part = part.strip()
        if not part:
            continue
        if part.startswith("已选："):
            part = part.split("已选：", 1)[1].strip()
        elif part.startswith("其他补充："):
            part = part.split("其他补充：", 1)[1].strip()
        cleaned_parts.append(part)

    values: list[str] = []
    for part in cleaned_parts:
        if _negative_optional_answer(part):
            continue
        values.extend(
            item.strip()
            for item in re.split(r"[、，,/\n]", part)
            if item.strip() and not _negative_optional_answer(item.strip())
        )
    return _dedupe(values)


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


def _stable_id(prefix: str, name: str, index: int) -> str:
    ascii_slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"{prefix}_{ascii_slug or index + 1}"


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


def merge_clarification_answers_into_spec(
    spec: dict[str, Any],
    request: str,
) -> dict[str, Any]:
    """将结构化澄清答案合并到 RequirementSpec 字段中。

    这是澄清恢复时的确定性保护；模型仍应返回完整 Spec，但常见维度的答案
    不能丢失，也不能因为合并过程再次触发相同问题。
    """

    blocks = _answer_blocks(request)
    if not blocks:
        return spec

    merged = deepcopy(spec)
    for block in blocks:
        question = block["question"]
        values = _answer_values(block["answer"])
        if not values:
            continue
        if "角色" in question:
            merged["user_roles"] = [
                {
                    "id": _stable_id("role", value, index),
                    "name": value,
                    "description": f"以{value}身份使用系统。",
                }
                for index, value in enumerate(values)
            ]
        elif "页面" in question or "菜单" in question:
            merged["pages"] = [
                {
                    "pageId": _stable_id("page", value, index),
                    "name": value,
                    "path": f"/{_stable_id('page', value, index).removeprefix('page_').replace('_', '-')}",
                    "module_id": "core_management",
                    "description": f"{value}页面。",
                }
                for index, value in enumerate(values)
            ]
        elif "功能" in question or "模块" in question:
            merged["feature_modules"] = [
                {
                    "id": _stable_id("module", value, index),
                    "name": value,
                    "description": f"支持{value}相关业务能力。",
                    "priority": "must",
                }
                for index, value in enumerate(values)
            ]
        elif "数据源" in question or "数据" in question or "存储" in question:
            existing_entities = (
                merged.get("entities")
                if isinstance(merged.get("entities"), list)
                else []
            )
            merged["entities"] = existing_entities or [
                {
                    "id": "CoreEntity",
                    "name": "核心业务对象",
                    "description": "提供核心业务页面所需展示的数据。",
                    "fields": [],
                }
            ]
        elif "验收" in question:
            merged["acceptance_criteria"] = values

    merged["source_request"] = consolidated_requirement_text(request) or request
    merged["summary"] = merged["source_request"]
    return merged


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

    if _contains_any(request, ("登录", "权限", "角色", "鉴权")):
        modules.append(
            {
                "id": "access_control",
                "name": "登录与权限",
                "description": "支持用户登录、角色区分和页面/操作权限控制。",
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
        ),
        _entity_definition(
            "Role",
            "角色",
            "权限分组，决定用户可以访问的页面和操作。",
            [
                {"label": "角色名称", "description": "角色显示名称。"},
                {"label": "角色编码", "description": "角色唯一编码。"},
            ],
        ),
    ]

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
        "主要业务流程可以由对应角色按预期步骤完成。",
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
    modules = _feature_modules(source_text)
    app_name = _app_name(source_text)
    roles = [
        {
            "id": "admin",
            "name": "管理员",
            "description": "负责查看全部数据、管理配置和执行高权限操作。",
        },
        {
            "id": "user",
            "name": "普通用户",
            "description": "负责日常业务查看和处理。",
        },
    ]

    default_spec = {
        "version": "0.1.0",
        "status": "draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": source_text,
        "source_request": source_text,
        "app_info": {
            "name": app_name,
            "summary": source_text,
            "target": "生成一个可在本地运行的前后端应用工程。",
        },
        "user_roles": roles,
        "feature_modules": modules,
        "pages": _pages(modules),
        "entities": _entities(modules),
        "business_flows": _business_flows(modules),
        "acceptance_criteria": _acceptance_criteria(app_name),
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
        "user_roles": ("role", {"name": "用户", "description": "使用应用。"}),
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
            "summary": source_text,
            "source_request": source_text,
            "agent_note": agent_note,
            "agent_spec_used": isinstance(agent_spec, dict),
            "approved": True,
        }
    )
    spec["app_info"] = {
        **default_spec["app_info"],
        **(spec.get("app_info") if isinstance(spec.get("app_info"), dict) else {}),
        "summary": source_text,
    }
    spec.pop("data_sources", None)
    return apply_authoritative_datasource_type(spec, effective_datasource_type)


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
    "user_roles": ("id", "name", "description", "permissions"),
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

    request = str(existing_spec.get("source_request") or existing_spec.get("summary") or "")
    normalized = create_requirement_spec(
        request,
        agent_note="synchronized from RequirementSpec summary editor",
        agent_spec=merged,
        existing_spec=existing_spec,
        authoritative_agent_spec=True,
        datasource_type=effective_datasource_type,
    )
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
    json_path = requirement_spec_json_path(state)
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
    markdown_path = Path(write_requirement_spec_document(state, synchronized_spec))
    return {
        "requirementSpec": synchronized_spec,
        "artifact": {
            "id": "requirement_spec",
            "name": markdown_path.name,
            "path": str(requirement_spec_markdown_path(state)),
            "format": "markdown",
            "content": render_requirement_spec_markdown(synchronized_spec),
        },
    }
