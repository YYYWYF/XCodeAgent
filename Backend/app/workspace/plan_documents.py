from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workspace_root


def _bullet_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_project_plan_markdown(plan: dict[str, Any]) -> str:
    api_contracts = "\n".join(
        "\n".join(
            [
                f"### `{contract['base_path']}` {contract['resource']}",
                *[
                    f"- `{endpoint['method']} {endpoint['path']}`：{endpoint['description']}"
                    for endpoint in contract["endpoints"]
                ],
            ]
        )
        for contract in plan["api_contracts"]
    )
    pages = "\n".join(
        f"- `{page['path']}` {page['name']}：数据依赖 {page['data_dependencies'] or ['无']}，权限 {page['permissions']}"
        for page in plan["frontend_pages"]
    )
    data_sources = "\n".join(
        f"- `{source['id']}` {source['name']}：实体 {source['entities']}，类型 {source['type']}"
        for source in plan["data_sources"]
    )
    frontend_tasks = "\n".join(
        f"- `{task['task_id']}`：{task['description']} 依赖 {task['depends_on'] or ['无']}"
        for task in plan["task_inputs"]["frontend"]
    )
    data_source_tasks = "\n".join(
        f"- `{task['task_id']}`：{task['description']}"
        for task in plan["task_inputs"]["data_source"]
    )
    coordination = "\n".join(
        f"- {stage}：{item['strategy']} 输出 {item['outputs']}"
        for stage, item in plan["coordination_plan"].items()
    )
    page_details = "\n\n".join(
        "\n".join(
            [
                f"### {detail['page_name']} `{detail['path']}`",
                f"- 页面目标：{detail['page_goal']}",
                f"- 基本布局：{'、'.join(detail['basic_layout']['structure'])}",
                f"- 页面交互：{'；'.join(detail['interactions'])}",
                f"- 数据来源：{[source['id'] for source in detail['data_sources']] or ['无']}",
                f"- 页面权限：{detail['permissions']}",
                f"- 状态：{detail['status']}",
            ]
        )
        for detail in plan.get("page_detail_plans", [])
    )

    return f"""# {plan['app']['name']}总体计划书

## 项目概述

- 应用：{plan['app']['name']}
- 摘要：{plan['app']['summary']}
- 状态：{plan['status']}
- 版本：{plan['version']}

## 技术架构

- 前端：{plan['architecture']['frontend']}
- 后端：{plan['architecture']['backend']}
- 数据：{plan['architecture']['data']}
- 测试：{plan['architecture']['testing']}

## API 契约

{api_contracts}

## 前端页面清单

{pages}

## 数据源清单

{data_sources}

## 后续任务拆分输入

### 前端任务

{frontend_tasks}

### 数据源任务

{data_source_tasks}

## Main Agent 协调计划

- 规划来源：{plan['planning_source']}
- 规划 Agent：{plan.get('planned_by', {}).get('agent', 'main-agent')}

{coordination}

## 页面详细设计

{page_details or "- 尚未确认页面详细设计"}

## 验收标准

{_bullet_items(plan['acceptance_criteria'])}

## 风险与待细化点

{_bullet_items(plan['risks'])}
"""


def write_project_plan_document(state: dict[str, Any], plan: dict[str, Any]) -> str:
    plans_dir = workspace_root(state) / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)

    path = plans_dir / "project-plan.md"
    path.write_text(render_project_plan_markdown(plan), encoding="utf-8")
    write_project_plan_json(state, plan)
    return str(path)


def project_plan_json_path(state: dict[str, Any]) -> Path:
    return workspace_root(state) / "plans" / "project-plan.json"


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
