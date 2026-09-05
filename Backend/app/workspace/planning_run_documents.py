from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.planning_run_contracts import PlanningRun
from app.workspace.json_documents import write_json_atomic
from app.workspace.spec_documents import workflow_artifact_root


def planning_run_json_path(state: dict[str, Any]) -> Path:
    """返回当前工作区唯一的轻量 PlanningRun 快照路径。"""

    return workflow_artifact_root(state) / "plans" / "planning-run.json"


def load_planning_run(state: dict[str, Any]) -> dict[str, Any] | None:
    """读取轻量 PlanningRun 投影。

    文件不存在时返回空，损坏内容直接报错。
    """

    path = planning_run_json_path(state)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        raise ValueError("planning-run.json 必须是 JSON object。")
    return payload


def project_planning_run(planning_run: PlanningRun) -> dict[str, Any]:
    """以同一规则生成落盘和 Controller 发布的轻量副本，不暴露 Candidate 正文。"""

    snapshot = PlanningRun.model_validate(planning_run)
    return snapshot.model_dump(mode="json", exclude={"candidates"})


def write_planning_run_atomic(
    state: dict[str, Any], planning_run: PlanningRun
) -> str:
    """供 Controller 原子写入轻量投影；Worker 不得直接调用本存储边界。"""

    payload = project_planning_run(planning_run)
    path = planning_run_json_path(state)
    write_json_atomic(path, payload)
    return str(path)


def delete_planning_run(state: dict[str, Any]) -> bool:
    """仅删除 PlanningRun 临时快照，并返回文件是否曾存在。"""

    path = planning_run_json_path(state)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
