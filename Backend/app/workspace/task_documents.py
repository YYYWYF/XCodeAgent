from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.build_task_planner import tasks_from_build_task_plan
from app.workspace.spec_documents import workflow_artifact_root


def build_task_plan_json_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("build_task_plan_path")
    return (
        Path(existing_path)
        if existing_path
        else workflow_artifact_root(state) / "plans" / "build-task-plan.json"
    )


def build_task_dag_markdown_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("build_task_dag_path")
    return (
        Path(existing_path)
        if existing_path and str(existing_path).endswith(".md")
        else workflow_artifact_root(state) / "plans" / "BUILD_TASK_DAG.md"
    )


def repair_task_plan_json_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("repair_task_plan_path")
    return (
        Path(existing_path)
        if existing_path
        else workflow_artifact_root(state) / "plans" / "repair-task-plan.json"
    )


def write_build_task_plan_json(
    state: dict[str, Any],
    build_task_plan: dict[str, Any],
) -> str:
    path = build_task_plan_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_task_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_build_task_plan_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_build_task_dag_markdown(
    state: dict[str, Any],
    build_task_plan: dict[str, Any],
) -> str:
    path = build_task_dag_markdown_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_build_task_dag_markdown(build_task_plan), encoding="utf-8")
    return str(path)


def render_build_task_dag_markdown(build_task_plan: dict[str, Any]) -> str:
    """把 v2 Unit 图与任务图渲染为内部可追踪的 Markdown。"""

    tasks = tasks_from_build_task_plan(build_task_plan)
    summary = build_task_plan.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    lines = [
        "# Build Task DAG",
        "",
        "## Summary",
        "",
        f"- Version: {build_task_plan.get('version', 'unknown')}",
        f"- Total tasks: {summary.get('total', len(tasks))}",
        f"- Frontend tasks: {summary.get('frontend', _count_owner(tasks, 'frontend'))}",
        f"- Backend tasks: {summary.get('backend', _count_owner(tasks, 'backend'))}",
        f"- Database tasks: {summary.get('database', _count_owner(tasks, 'database'))}",
        "",
        "## Units",
        "",
        "| Unit ID | Type | Status | Tasks |",
        "| --- | --- | --- | --- |",
    ]
    build_units = build_task_plan.get("build_units")
    build_units = build_units if isinstance(build_units, dict) else {}
    for unit_id, unit in build_units.items():
        if not isinstance(unit, dict):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(unit_id),
                    _cell(unit.get("kind")),
                    _cell(unit.get("status")),
                    _cell(", ".join(str(item) for item in unit.get("task_ids", []))),
                ]
            )
            + " |"
        )
    if not build_units:
        lines.append("| - | - | - | - |")
    lines.extend(
        [
            "",
            "## Tasks",
            "",
            "| ID | Unit | Owner | Status | Dependencies | Change Scope | Acceptance Criteria |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    if not tasks:
        lines.append("| - | - | - | - | - | - | - |")
    for task in tasks:
        dependencies = task.get("dependencies") or []
        acceptance = task.get("acceptance_criteria") or []
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(task.get("id") or ""),
                    _cell(task.get("unit_id") or ""),
                    _cell(task.get("owner") or ""),
                    _cell(task.get("status") or "pending"),
                    _cell(", ".join(str(item) for item in dependencies) or "-"),
                    _cell(_change_scope_text(task)),
                    _cell("; ".join(str(item) for item in acceptance) if isinstance(acceptance, list) else acceptance),
                ]
            )
            + " |"
        )

    task_graph = build_task_plan.get("task_graph")
    if isinstance(task_graph, dict):
        validation = task_graph.get("validation") if isinstance(task_graph.get("validation"), dict) else {}
        lines.extend(
            [
                "",
                "## DAG Metadata",
                "",
                f"- Parallel batches: {len(task_graph.get('execution_layers', []))}",
                f"- Validation status: {'valid' if validation.get('is_valid') else 'invalid'}",
            ]
        )
    return "\n".join(lines) + "\n"


def write_repair_task_plan_json(
    state: dict[str, Any],
    repair_task_plan: dict[str, Any],
) -> str:
    path = repair_task_plan_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(repair_task_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_repair_task_plan_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _count_owner(tasks: list[dict[str, Any]], owner: str) -> int:
    return len([task for task in tasks if task.get("owner") == owner])


def _change_scope_text(task: dict[str, Any]) -> str:
    scope = task.get("change_scope") or []
    if not isinstance(scope, list):
        return "-"
    paths: list[str] = []
    for item in scope:
        if isinstance(item, dict):
            paths.append(str(item.get("path") or item.get("file") or ""))
        else:
            paths.append(str(item))
    return ", ".join(path for path in paths if path.strip()) or "-"


def _cell(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|").strip()
    return text or "-"
