from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


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
            "id": "dashboard_page",
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
                    "id": "login_page",
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
                    "id": f"{module_id}_list_page",
                    "name": f"{module['name']}列表页",
                    "path": f"/{module_id.replace('_', '-')}",
                    "module_id": module_id,
                    "description": f"展示{module['name']}数据，支持搜索、筛选和主要操作。",
                },
                {
                    "id": f"{module_id}_detail_page",
                    "name": f"{module['name']}详情页",
                    "path": f"/{module_id.replace('_', '-')}/:id",
                    "module_id": module_id,
                    "description": f"展示单条{module['name']}记录详情和关联信息。",
                },
            ]
        )

    return pages


def _data_sources(modules: list[dict[str, Any]], request: str) -> list[dict[str, Any]]:
    storage = "database" if _contains_any(request, ("数据库", "数据源")) else "mock"
    data_sources = [
        {
            "id": "user_source",
            "name": "用户数据源",
            "type": storage,
            "entities": ["User", "Role"],
            "description": "提供登录用户、角色和权限相关数据。",
        }
    ]

    for module in modules:
        module_id = module["id"]
        if module_id in {"dashboard", "access_control"}:
            continue
        entity_name = "".join(part.title() for part in module_id.split("_"))
        data_sources.append(
            {
                "id": f"{module_id}_source",
                "name": f"{module['name']}数据源",
                "type": storage,
                "entities": [entity_name],
                "description": f"提供{module['name']}相关列表、详情和状态数据。",
            }
        )

    return data_sources


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
    return [
        f"用户可以启动并访问{spec_name}本地预览地址。",
        "主要页面可以正常打开，无前端运行错误。",
        "页面清单中的每个页面都有可见标题、主要内容区、加载态、空态和错误态。",
        "数据源清单中的核心实体可以被页面读取并展示。",
        "如包含登录或权限模块，未授权用户不能访问受保护页面。",
        "集成测试和质量门禁通过后才进入用户验收。",
    ]


def create_requirement_spec(
    request: str,
    agent_note: str = "live main-agent requirements analysis",
) -> dict[str, Any]:
    modules = _feature_modules(request)
    app_name = _app_name(request)
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

    spec = {
        "version": "0.1.0",
        "status": "draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": request,
        "source_request": request,
        "app_info": {
            "name": app_name,
            "summary": request,
            "target": "生成一个可在本地运行的前后端应用工程。",
        },
        "user_roles": roles,
        "feature_modules": modules,
        "pages": _pages(modules),
        "data_sources": _data_sources(modules, request),
        "business_flows": _business_flows(modules),
        "acceptance_criteria": _acceptance_criteria(app_name),
        "assumptions": [],
        "clarification_questions": [],
        "agent_note": agent_note,
        "approved": True,
    }
    return spec
