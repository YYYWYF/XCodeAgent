from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    tasks = [
        task
        for task in build_task_plan.get("tasks", [])
        if isinstance(task, dict)
    ]
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
        f"- Data source tasks: {summary.get('data_source', _count_owner(tasks, 'data_source'))}",
        "",
        "## Tasks",
        "",
        "| ID | Owner | Status | Dependencies | Change Scope | Acceptance Criteria |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if not tasks:
        lines.append("| - | - | - | - | - | - |")
    for task in tasks:
        dependencies = task.get("dependencies") or task.get("dependsOn") or []
        acceptance = task.get("acceptance_criteria") or task.get("acceptanceCriteria") or []
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(task.get("id") or task.get("task_id") or ""),
                    _cell(task.get("owner") or ""),
                    _cell(task.get("status") or "pending"),
                    _cell(", ".join(str(item) for item in dependencies) or "-"),
                    _cell(_change_scope_text(task)),
                    _cell("; ".join(str(item) for item in acceptance) if isinstance(acceptance, list) else acceptance),
                ]
            )
            + " |"
        )

    dag = build_task_plan.get("dag")
    if isinstance(dag, dict):
        validation = dag.get("validation") if isinstance(dag.get("validation"), dict) else {}
        lines.extend(
            [
                "",
                "## DAG Metadata",
                "",
                f"- Parallel batches: {dag.get('parallel_batches', dag.get('parallelBatches', '-'))}",
                f"- Validation status: {validation.get('status', '-')}",
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
    scope = task.get("change_scope") or task.get("changeScope") or []
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
