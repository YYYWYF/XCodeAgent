from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workflow_artifact_root


def _bullet_items(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    return "\n".join(f"- {_text_item(item)}" for item in items)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _label_items(value: Any) -> list[str]:
    return [
        str(item.get("name") or item.get("id") or item)
        if isinstance(item, dict)
        else str(item)
        for item in value
        if str(item).strip()
    ] if isinstance(value, list) else []


def _text_item(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "label", "title", "description", "id"):
            if value.get(key):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text_item(item))]


def render_project_plan_markdown(plan: dict[str, Any]) -> str:
    overview = plan.get("requirements_overview", {})
    acceptance_criteria = plan.get("project_acceptance_criteria") or plan.get(
        "acceptance_criteria",
        [],
    )
    api_contracts = "\n".join(
        "\n".join(
            [
                f"### `{contract.get('base_path', '/api/resource')}` {contract.get('resource', contract.get('id', 'Resource'))}",
                f"- Schema：{list(contract.get('schemas', {}))}",
                *[
                    "\n".join(
                        [
                            f"- `{endpoint.get('id', 'endpoint')}` · `{endpoint.get('method', 'GET')} {endpoint.get('path', '')}`：{endpoint.get('summary', '待补充接口说明')}",
                            f"  - 参数：{endpoint.get('parameters', []) or '无'}",
                            f"  - 请求 Schema：{endpoint.get('request_schema_ref') or '无'}",
                            f"  - 响应 Schema：{endpoint.get('response_schema_ref') or '无'}",
                            f"  - 错误码：{endpoint.get('error_codes', []) or '无'}",
                        ]
                    )
                    for endpoint in _dict_items(contract.get("endpoints", []))
                ],
            ]
        )
        for contract in _dict_items(plan.get("api_contracts", []))
    )
    pages = "\n".join(
        f"- `{page.get('path', '/')}` {page.get('name', page.get('id', '未命名页面'))}："
        f"数据依赖 {page.get('data_dependencies') or ['无']}，权限 {page.get('permissions', [])}"
        for page in _dict_items(plan.get("frontend_pages", []))
    )
    data_sources = "\n".join(
        f"- `{source.get('id', 'unknown')}` {source.get('name', '未命名数据源')}："
        f"实体 {source.get('entities', [])}，Schema 引用 {source.get('schema_refs', []) or ['无']}，类型 {source.get('type', 'mock')}"
        for source in _dict_items(plan.get("data_sources", []))
    )
    page_data_dependencies = "\n".join(
        f"- `{item.get('page_id', 'unknown')}`：数据源 {item.get('data_source_ids', []) or ['无']}，API {item.get('api_contract_ids', []) or ['无']}，Endpoint {[(dependency.get('endpoint_id'), dependency.get('usage')) for dependency in _dict_items(item.get('endpoint_dependencies', []))] or ['无']}"
        for item in _dict_items(plan.get("page_data_dependencies", []))
    )
    permissions = plan.get("permission_model", {})
    page_access = "\n".join(
        f"- `{item.get('path', item.get('page_id', 'unknown'))}`：{item.get('allowed_roles', [])}"
        for item in _dict_items(permissions.get("page_access", []))
    )
    operation_permissions = "\n".join(
        f"- `{item.get('role_id', 'unknown')}`：{item.get('operations', [])}"
        for item in _dict_items(permissions.get("operation_permissions", []))
    )
    frontend_tasks = "\n".join(
        f"- `{task.get('task_id', 'task')}`：{task.get('description', '待补充任务说明')} "
        f"依赖 {task.get('depends_on') or ['无']}"
        for task in _dict_items(plan.get("task_inputs", {}).get("frontend", []))
    )
    data_source_tasks = "\n".join(
        f"- `{task.get('task_id', 'task')}`：{task.get('description', '待补充任务说明')}"
        for task in _dict_items(plan.get("task_inputs", {}).get("data_source", []))
    )
    coordination = "\n".join(
        f"- {stage}：{item.get('strategy', '待补充协调策略')} "
        f"输出 {item.get('outputs', [])}"
        for stage, item in plan.get("coordination_plan", {}).items()
        if isinstance(item, dict)
    )
    page_details = "\n\n".join(
        "\n".join(
            [
                f"### {detail.get('page_name', detail.get('page_id', '未命名页面'))} `{detail.get('path', '')}`",
                f"- 页面目标：{detail.get('page_goal', '待补充')}",
                f"- 基本布局：{'、'.join(_text_items(detail.get('basic_layout', {}).get('structure', []))) or '待补充'}",
                f"- 页面交互：{'；'.join(_text_items(detail.get('interactions', []))) or '待补充'}",
                f"- 数据来源：{[source.get('id') for source in _dict_items(detail.get('data_sources', []))] or ['无']}",
                f"- 页面权限：{detail.get('permissions', [])}",
                f"- 状态：{detail.get('status', 'draft')}",
            ]
        )
        for detail in _dict_items(plan.get("page_detail_plans", []))
    )
    data_source_details = "\n\n".join(
        "\n".join(
            [
                f"### {detail.get('data_source_name', detail.get('data_source_id', '未命名数据源'))}",
                f"- 实体：{detail.get('entities', [])}",
                f"- API 契约：{[contract.get('id') for contract in _dict_items(detail.get('api_contracts', []))] or ['无']}",
                f"- 依赖页面：{[page.get('page_id') for page in _dict_items(detail.get('dependent_pages', []))] or ['无']}",
                f"- 状态：{detail.get('status', 'draft')}",
            ]
        )
        for detail in _dict_items(plan.get("data_source_detail_plans", []))
    )

    app = plan.get("app", {})
    architecture = plan.get("architecture", {})

    return f"""# {app.get('name', '未命名应用')}总体计划书

## 项目概述

- 应用：{app.get('name', '未命名应用')}
- 摘要：{app.get('summary', '待补充项目摘要')}
- 目标：{overview.get('target', '生成一个可在本地运行的前后端应用工程。')}
- 状态：{plan.get('status', 'draft')}
- 版本：{plan.get('version', '0.1.0')}

## 需求概述

- 需求摘要：{overview.get('summary', app.get('summary', '待补充需求摘要'))}
- 用户角色：{_label_items(overview.get('roles', []))}
- 功能模块：{_label_items(overview.get('modules', []))}
- 业务流程：{_label_items(overview.get('business_flows', []))}

## 整体需求验收标准

{_bullet_items(acceptance_criteria)}

## 技术架构

- 前端：{architecture.get('frontend', '待补充前端架构')}
- 后端：{architecture.get('backend', '待补充后端架构')}
- 数据：{architecture.get('data', '待补充数据架构')}
- 测试：{architecture.get('testing', '待补充测试策略')}

## API 契约

{api_contracts}

## 前端页面清单

{pages}

## 数据源清单

{data_sources}

## 页面与数据源依赖

{page_data_dependencies or "- 暂无页面数据源依赖"}

## 权限体系

- 默认策略：{permissions.get('default_policy', 'deny_unlisted')}

### 页面访问

{page_access or "- 暂无页面权限规则"}

### 操作权限

{operation_permissions or "- 暂无操作权限规则"}

## 后续任务拆分输入

### 前端任务

{frontend_tasks}

### 数据源任务

{data_source_tasks}

## Main Agent 协调计划

- 规划来源：{plan.get('planning_source', 'main_agent_live')}
- 规划 Agent：{plan.get('planned_by', {}).get('agent', 'main-agent')}

{coordination}

## 页面详细设计

{page_details or "- 尚未确认页面详细设计"}

## 数据源详细设计

{data_source_details or "- 尚未确认数据源详细设计"}

## 风险与待细化点

{_bullet_items(plan.get('risks', []))}
"""


def write_project_plan_document(state: dict[str, Any], plan: dict[str, Any]) -> str:
    existing_path = state.get("project_plan_path")
    path = (
        Path(existing_path)
        if existing_path and str(existing_path).endswith(".md")
        else workflow_artifact_root(state) / "plans" / "project-plan.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_project_plan_markdown(plan), encoding="utf-8")
    write_project_plan_json(state, plan)
    return str(path)


def project_plan_json_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("project_plan_json_path")
    return (
        Path(existing_path)
        if existing_path
        else workflow_artifact_root(state) / "plans" / "project-plan.json"
    )


def write_project_plan_json(state: dict[str, Any], plan: dict[str, Any]) -> str:
    path = project_plan_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_project_plan_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
