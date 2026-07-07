from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _task_status(task: dict[str, Any]) -> str:
    return task.get("status", "pending")


def _data_source_task(data_source: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"data_source:{data_source['id']}",
        "owner": "data_source",
        "description": f"生成数据源 {data_source['name']}、API 契约和示例数据。",
        "dependencies": [],
        "status": "pending",
        "source_ref": {
            "type": "data_source",
            "id": data_source["id"],
        },
        "allowed_paths": [
            "app/backend/**",
            "app/shared/api/**",
            "tests/backend/**",
        ],
        "acceptance_criteria": [
            f"数据源 {data_source['id']} 的实体模型被创建。",
            "相关 API 路径与 ProjectPlan 中的 API 契约一致。",
            "提供可用于页面联调的示例数据。",
        ],
    }


def _frontend_task(page_detail_plan: dict[str, Any]) -> dict[str, Any]:
    data_source_dependencies = [
        f"data_source:{source['id']}" for source in page_detail_plan.get("data_sources", [])
    ]
    return {
        "id": f"page:{page_detail_plan['page_id']}",
        "owner": "frontend",
        "description": f"生成页面 {page_detail_plan['page_name']}（{page_detail_plan['path']}）。",
        "dependencies": data_source_dependencies,
        "status": "pending",
        "source_ref": {
            "type": "page_detail_plan",
            "id": page_detail_plan["id"],
            "page_id": page_detail_plan["page_id"],
        },
        "allowed_paths": [
            "app/frontend/**",
            "app/shared/api/**",
            "tests/frontend/**",
        ],
        "acceptance_criteria": page_detail_plan["acceptance_criteria"],
    }


def _deduplicate_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduplicated: dict[str, dict[str, Any]] = {}
    for task in tasks:
        existing = deduplicated.get(task["id"])
        if existing and _task_status(existing) == "completed":
            continue
        deduplicated[task["id"]] = task
    return list(deduplicated.values())


def create_build_task_plan(
    project_plan: dict[str, Any],
    agent_note: str = "live main-agent build task preparation",
) -> dict[str, Any]:
    """Create executable build tasks from the Main Agent's ProjectPlan.

    Only confirmed page detail plans are converted into frontend generation
    tasks. Their data-source dependencies are converted into data-source tasks.
    """

    page_detail_plans = project_plan.get("page_detail_plans", [])
    required_data_source_ids = {
        source["id"]
        for detail_plan in page_detail_plans
        for source in detail_plan.get("data_sources", [])
    }
    data_sources = [
        source
        for source in project_plan.get("data_sources", [])
        if source["id"] in required_data_source_ids
    ]

    tasks = _deduplicate_tasks(
        [
            *[_data_source_task(source) for source in data_sources],
            *[_frontend_task(detail_plan) for detail_plan in page_detail_plans],
        ]
    )

    return {
        "version": "0.1.0",
        "status": "ready",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_project_plan_version": project_plan["version"],
        "tasks": tasks,
        "summary": {
            "total": len(tasks),
            "frontend": len([task for task in tasks if task["owner"] == "frontend"]),
            "data_source": len([task for task in tasks if task["owner"] == "data_source"]),
        },
        "coordination": {
            "owner": "main-agent",
            "strategy": "Dispatch data-source tasks first, then frontend tasks whose dependencies are completed.",
        },
        "agent_note": agent_note,
    }
