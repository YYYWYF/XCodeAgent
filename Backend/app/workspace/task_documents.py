from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any

from app.workspace.spec_documents import workflow_artifact_root


def build_task_plan_json_path(state: dict[str, Any]) -> Path:
    """返回当前工作区唯一的 Build Task Plan JSON 路径，不接受旧 checkpoint 路径覆盖。"""

    return workflow_artifact_root(state) / "plans" / "build-task-plan.json"


def build_task_plan_sha256(build_task_plan: dict[str, Any]) -> str:
    """计算 Build DAG 的规范化内容摘要，作为一次 Build Run 的唯一计划身份。"""

    return hashlib.sha256(
        json.dumps(
            build_task_plan,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_run_task_plan_json_path(state: dict[str, Any], build_run_id: str) -> Path:
    """返回指定 Build Run 的只读任务计划副本路径。"""

    normalized_id = str(build_run_id or "").strip()
    if not re.fullmatch(r"build-[a-f0-9]{32}", normalized_id):
        raise ValueError("Build Run 标识无效，不能创建任务计划副本。")
    return workflow_artifact_root(state) / "plans" / "build-runs" / f"{normalized_id}.json"


def write_build_run_task_plan_json(
    state: dict[str, Any],
    *,
    build_run_id: str,
    build_task_plan: dict[str, Any],
) -> str:
    """持久化一次 Build Run 唯一使用的只读计划副本。"""

    path = build_run_task_plan_json_path(state, build_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_task_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


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
