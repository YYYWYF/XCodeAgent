from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from app.observability.agent_events import make_event


RUNS_DIR = ".xcodeagent/runs"
CONTRACTS_DIR = ".xcodeagent/contracts"


def create_run_artifacts(
    *,
    workspace_root: Optional[str],
    contract: Dict[str, Any],
    task_graph: Dict[str, Any],
    verification: Dict[str, Any],
) -> Dict[str, Any]:
    run_id = f"run-{uuid4().hex[:12]}"
    run_dir = _run_dir(workspace_root, run_id)
    (run_dir / "subagents").mkdir(parents=True, exist_ok=True)

    _write_json(run_dir / "contract.json", contract)
    _write_json(run_dir / "task-graph.json", task_graph)
    _write_json(run_dir / "verification.json", verification)
    _write_text(run_dir / "summary.md", _summary_markdown(contract, task_graph, verification))
    append_event(
        workspace_root=workspace_root,
        run_id=run_id,
        event=make_event("run.started", run_id=run_id),
    )
    append_event(
        workspace_root=workspace_root,
        run_id=run_id,
        event=make_event(
            "contract.created",
            run_id=run_id,
            payload={"contractId": contract.get("id")},
        ),
    )
    for task in task_graph.get("tasks") if isinstance(task_graph.get("tasks"), list) else []:
        if isinstance(task, dict):
            append_event(
                workspace_root=workspace_root,
                run_id=run_id,
                event=make_event(
                    "task.scheduled",
                    run_id=run_id,
                    task_id=str(task.get("id") or ""),
                    payload={"mode": task.get("executionMode"), "assignedAgent": task.get("assignedAgent")},
                ),
            )

    return {
        "runId": run_id,
        "runPath": str(run_dir),
        "artifacts": {
            "contract": str(run_dir / "contract.json"),
            "taskGraph": str(run_dir / "task-graph.json"),
            "events": str(run_dir / "events.jsonl"),
            "verification": str(run_dir / "verification.json"),
            "summary": str(run_dir / "summary.md"),
            "subagents": str(run_dir / "subagents"),
        },
        "retention": {
            "successfulRuns": 10,
            "failedRuns": "kept-until-user-clears",
            "contracts": "saved-only-on-user-request",
        },
    }


def append_event(*, workspace_root: Optional[str], run_id: str, event: Dict[str, Any]) -> None:
    run_dir = _run_dir(workspace_root, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def save_subagent_result(
    *,
    workspace_root: Optional[str],
    run_id: str,
    task_id: str,
    result: Dict[str, Any],
) -> Dict[str, str]:
    output = _run_dir(workspace_root, run_id) / "subagents" / f"{task_id}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, result)
    return {"path": str(output)}


def run_store_capabilities() -> Dict[str, Any]:
    return {
        "runsDir": RUNS_DIR,
        "contractsDir": CONTRACTS_DIR,
        "retention": {
            "successfulRuns": 10,
            "failedRuns": "kept-until-user-clears",
            "contracts": "saved-only-on-user-request",
        },
    }


def _workspace_root(value: Optional[str]) -> Path:
    return Path(value).expanduser().resolve() if value else Path.cwd().resolve()


def _run_dir(workspace_root: Optional[str], run_id: str) -> Path:
    return _workspace_root(workspace_root) / RUNS_DIR / run_id


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _summary_markdown(contract: Dict[str, Any], task_graph: Dict[str, Any], verification: Dict[str, Any]) -> str:
    tasks = task_graph.get("tasks") if isinstance(task_graph.get("tasks"), list) else []
    commands = verification.get("commands") if isinstance(verification.get("commands"), list) else []
    return "\n".join(
        [
            f"# {contract.get('title') or 'XCodeAgent Run'}",
            "",
            str(contract.get("summary") or ""),
            "",
            "## Tasks",
            *(f"- {task.get('id')}: {task.get('title')} ({task.get('executionMode')})" for task in tasks if isinstance(task, dict)),
            "",
            "## Verification",
            *(f"- {command}" for command in commands),
            "",
        ]
    )
