from __future__ import annotations

import json
import shlex
from typing import Any, Dict, List, Optional, Set

from app.config import Settings
from app.tools import workspace as workspace_tools
from app.tools.requirement_planner import RequirementPlannerRuntime


ORCHESTRATION_DATA_START = "<orchestration-data>"
ORCHESTRATION_DATA_END = "</orchestration-data>"

_TASK_TYPES = {"inspect", "shared", "feature", "test", "verify"}
_SHELL_META = {"&&", "||", ";", "|", ">", ">>", "<", "$(", "`"}


class DevelopmentOrchestratorRuntime:
    """Coordinates requirement clarification, unified planning, task dispatch, and verification."""

    def __init__(self, settings: Settings, planner: RequirementPlannerRuntime) -> None:
        self.settings = settings
        self.planner = planner

    async def run(
        self,
        message: str,
        *,
        orchestrator_state: Optional[Dict[str, Any]] = None,
        planner_state: Optional[Dict[str, Any]] = None,
        application: Optional[Dict[str, Any]] = None,
        workspace_root: Optional[str] = None,
        action: str = "answer",
    ) -> Dict[str, Any]:
        state = _normalize_state(orchestrator_state)
        if message.strip() and not state.get("requirement"):
            state["requirement"] = message.strip()

        normalized_action = action if action in {"start", "answer", "finalize", "dispatch", "verify"} else "answer"
        if normalized_action == "verify":
            return self._run_verification(
                message,
                state=state,
                workspace_root=workspace_root,
            )

        planner_payload = await self.planner.run(
            message,
            planner_state=_optional_dict(state.get("plannerState")) or planner_state,
            application=application,
            action="finalize" if normalized_action in {"finalize", "dispatch"} else normalized_action,
        )
        state["plannerState"] = planner_payload.get("state")

        if planner_payload.get("status") == "questions":
            state["phase"] = "clarifying"
            state["status"] = "questions"
            return {
                "tool": "development_orchestrator",
                "status": "questions",
                "phase": "clarifying",
                "message": planner_payload.get("message")
                or "我需要先确认几个会影响开发方案的问题。",
                "questions": planner_payload.get("questions") or [],
                "planner": planner_payload,
                "state": state,
            }

        plan = _optional_dict(planner_payload.get("plan")) or {}
        workspace = _inspect_workspace(workspace_root)
        plan["verificationPlan"] = _normalize_verification_plan(
            plan.get("verificationPlan"),
            workspace=workspace,
        )
        task_graph = _normalize_task_graph(plan.get("taskGraph"), plan=plan)
        plan["taskGraph"] = task_graph
        execution_batches = _build_execution_batches(task_graph["tasks"])

        state.update(
            {
                "phase": "dispatch",
                "status": "ready",
                "plan": plan,
                "workspace": workspace,
                "taskGraph": task_graph,
                "executionBatches": execution_batches,
                "verification": {"status": "not_run", "commands": [], "message": "尚未执行验证。"},
            }
        )

        return {
            "tool": "development_orchestrator",
            "status": "ready",
            "phase": "dispatch",
            "message": "统一开发计划和任务分发已经生成，可以按批次开始执行。",
            "questions": [],
            "plan": plan,
            "workspace": workspace,
            "taskGraph": task_graph,
            "executionBatches": execution_batches,
            "verification": state["verification"],
            "planner": planner_payload,
            "state": state,
        }

    def _run_verification(
        self,
        message: str,
        *,
        state: Dict[str, Any],
        workspace_root: Optional[str],
    ) -> Dict[str, Any]:
        plan = _optional_dict(state.get("plan")) or {}
        workspace = _inspect_workspace(workspace_root)
        verification_plan = _normalize_verification_plan(plan.get("verificationPlan"), workspace=workspace)
        commands = _command_strings(verification_plan.get("commands"))[:6]
        command_results: List[Dict[str, Any]] = []

        for command in commands:
            if not _is_safe_command(command):
                command_results.append(
                    {
                        "command": command,
                        "status": "skipped",
                        "returncode": None,
                        "summary": "命令包含 shell 控制符，已跳过；请拆成单条安全命令。",
                    }
                )
                continue

            result = workspace_tools.terminal_exec(
                workspace_tools.TerminalExecRequest(
                    workspace_root=workspace_root,
                    command=command,
                    timeout_seconds=120,
                    max_output_chars=16000,
                )
            )
            if result.get("requires_approval"):
                command_results.append(
                    {
                        "command": command,
                        "status": "requires_approval",
                        "returncode": None,
                        "summary": "该验证命令需要用户审批后才能执行。",
                        "approval": result.get("approval"),
                        "risk": result.get("risk"),
                    }
                )
                break

            returncode = result.get("returncode")
            stderr = str(result.get("stderr") or "").strip()
            stdout = str(result.get("stdout") or "").strip()
            command_results.append(
                {
                    "command": command,
                    "status": "passed" if returncode == 0 else "failed",
                    "returncode": returncode,
                    "summary": _summarize_command_output(stdout=stdout, stderr=stderr),
                    "stdout": stdout,
                    "stderr": stderr,
                    "timedOut": bool(result.get("timed_out")),
                }
            )

        diff = _git_diff(workspace_root)
        verification = {
            "status": _verification_status(command_results),
            "commands": command_results,
            "checks": verification_plan.get("checks") or [],
            "diff": diff,
        }
        state.update({"phase": "verifying", "status": verification["status"], "verification": verification})
        return {
            "tool": "development_orchestrator",
            "status": verification["status"],
            "phase": "verifying",
            "message": _verification_message(verification),
            "questions": [],
            "plan": plan,
            "workspace": workspace,
            "taskGraph": _optional_dict(state.get("taskGraph")) or {"tasks": []},
            "executionBatches": state.get("executionBatches") or [],
            "verification": verification,
            "state": state,
        }


def orchestrator_capabilities() -> Dict[str, Any]:
    return {
        "name": "development_orchestrator",
        "description": "Clarify requirements, produce a unified SDD plan, dispatch task DAG batches, and run verification.",
        "input": {
            "action": ["start", "answer", "finalize", "dispatch", "verify"],
            "orchestratorState": "State returned from the previous orchestrator run.",
            "plannerState": "Optional state returned from requirement_planner.",
            "application": "Optional XCodeAgent application metadata.",
            "workspaceRoot": "Optional absolute workspace root selected by the desktop app.",
        },
        "output": {
            "status": ["questions", "ready", "passed", "failed", "partial", "not_run"],
            "plan": "Unified SDD plan grouped by feature slices.",
            "taskGraph": "Normalized task DAG with target files and dependencies.",
            "executionBatches": "Serial/parallel batches computed from dependencies and target-file conflicts.",
            "verification": "Command results and diff summary when action=verify.",
        },
    }


def attach_orchestration_data(message: str, payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{message.rstrip()}\n\n{ORCHESTRATION_DATA_START}{encoded}{ORCHESTRATION_DATA_END}"


def summarize_orchestration_payload(payload: Dict[str, Any]) -> str:
    status = payload.get("status")
    if status == "questions":
        questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
        lines = [str(payload.get("message") or "我先确认几个关键问题。")]
        lines.extend(
            f"{index + 1}. {question.get('title')}"
            for index, question in enumerate(questions[:3])
            if isinstance(question, dict) and question.get("title")
        )
        return "\n".join(lines)

    if payload.get("phase") == "verifying":
        verification = _optional_dict(payload.get("verification")) or {}
        commands = verification.get("commands") if isinstance(verification.get("commands"), list) else []
        lines = [str(payload.get("message") or "验证已执行。")]
        for item in commands[:4]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('command')}: {item.get('status')}")
        return "\n".join(lines)

    plan = _optional_dict(payload.get("plan")) or {}
    task_graph = _optional_dict(payload.get("taskGraph")) or {"tasks": []}
    batches = payload.get("executionBatches") if isinstance(payload.get("executionBatches"), list) else []
    title = str(plan.get("title") or "统一开发计划")
    task_count = len(task_graph.get("tasks") or [])
    parallel_count = sum(1 for batch in batches if isinstance(batch, dict) and batch.get("mode") == "parallel")
    return (
        f"已生成《{title}》。\n"
        f"任务图包含 {task_count} 个任务，调度为 {len(batches)} 个执行批次，其中 {parallel_count} 个可并行批次。\n"
        "下一步可以按批次执行，最后运行 verificationPlan 做验收。"
    )


def _normalize_state(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "requirement": "",
            "phase": "intake",
            "status": "new",
            "plannerState": None,
            "plan": None,
            "taskGraph": {"tasks": []},
            "executionBatches": [],
            "verification": {"status": "not_run"},
        }
    state = dict(value)
    state.setdefault("requirement", "")
    state.setdefault("phase", "intake")
    state.setdefault("status", "new")
    state.setdefault("taskGraph", {"tasks": []})
    state.setdefault("executionBatches", [])
    state.setdefault("verification", {"status": "not_run"})
    return state


def _inspect_workspace(workspace_root: Optional[str]) -> Dict[str, Any]:
    workspace: Dict[str, Any] = {"errors": []}
    try:
        info = workspace_tools.workspace_info(workspace_tools.WorkspaceRequest(workspace_root=workspace_root))
        workspace.update(info.get("workspace") or {})
        workspace["git"] = info.get("git")
    except Exception as exc:  # pragma: no cover - defensive boundary for external workspaces
        workspace["errors"].append(f"workspace.info failed: {exc}")

    try:
        tree = workspace_tools.workspace_tree(
            workspace_tools.TreeRequest(
                workspace_root=workspace_root,
                path=".",
                max_depth=3,
                include_hidden=False,
                limit=400,
            )
        )
        workspace["tree"] = tree.get("tree")
        workspace["treeTruncated"] = tree.get("truncated")
    except Exception as exc:  # pragma: no cover - defensive boundary for external workspaces
        workspace["errors"].append(f"workspace.tree failed: {exc}")

    workspace["project"] = _detect_project(workspace_root)
    return workspace


def _detect_project(workspace_root: Optional[str]) -> Dict[str, Any]:
    markers = {
        "package.json": False,
        "pnpm-lock.yaml": False,
        "yarn.lock": False,
        "package-lock.json": False,
        "pyproject.toml": False,
        "requirements.txt": False,
    }
    for marker in markers:
        try:
            result = workspace_tools.search_files(
                workspace_tools.SearchFilesRequest(
                    workspace_root=workspace_root,
                    query=marker,
                    path=".",
                    include_hidden=False,
                    limit=5,
                )
            )
            markers[marker] = bool(result.get("matches"))
        except Exception:
            markers[marker] = False

    package = _read_package_json(workspace_root)
    manager = _package_manager(markers, package)
    commands = _default_verification_commands(
        workspace_root=workspace_root,
        markers=markers,
        package=package,
        manager=manager,
    )
    return {
        "markers": markers,
        "packageManager": manager,
        "packageScripts": sorted((_optional_dict(package.get("scripts")) or {}).keys()) if package else [],
        "recommendedVerificationCommands": commands,
    }


def _read_package_json(workspace_root: Optional[str]) -> Dict[str, Any]:
    try:
        result = workspace_tools.read_file(
            workspace_tools.ReadFileRequest(
                workspace_root=workspace_root,
                path="package.json",
                max_lines=400,
                max_chars=40000,
            )
        )
        data = json.loads(str(result.get("content") or "{}"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _package_manager(markers: Dict[str, bool], package: Dict[str, Any]) -> str:
    package_manager = str(package.get("packageManager") or "")
    if package_manager:
        return package_manager.split("@", 1)[0]
    if markers.get("pnpm-lock.yaml"):
        return "pnpm"
    if markers.get("yarn.lock"):
        return "yarn"
    return "npm" if markers.get("package.json") else ""


def _default_verification_commands(
    *,
    workspace_root: Optional[str],
    markers: Dict[str, bool],
    package: Dict[str, Any],
    manager: str,
) -> List[str]:
    commands: List[str] = []
    scripts = _optional_dict(package.get("scripts")) or {}
    for script in ("typecheck", "lint", "test", "build"):
        if script in scripts:
            commands.append(_script_command(manager or "npm", script))
    if not commands and markers.get("package.json"):
        commands.append(_script_command(manager or "npm", "build"))
    if markers.get("pyproject.toml") or markers.get("requirements.txt"):
        python_command = _python_compile_command(workspace_root)
        if python_command:
            commands.append(python_command)
    return commands[:5]


def _script_command(manager: str, script: str) -> str:
    if manager == "npm":
        return "npm test" if script == "test" else f"npm run {script}"
    return f"{manager} {script}"


def _python_compile_command(workspace_root: Optional[str]) -> str:
    try:
        result = workspace_tools.search_files(
            workspace_tools.SearchFilesRequest(
                workspace_root=workspace_root,
                query=".py",
                path=".",
                include_hidden=False,
                limit=25,
            )
        )
    except Exception:
        return ""

    entries = result.get("matches") if isinstance(result.get("matches"), list) else []
    paths = [
        str(entry.get("path"))
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("path") or "").endswith(".py")
    ]
    if not paths:
        return ""
    return "python3 -m py_compile " + " ".join(shlex.quote(path) for path in paths[:20])


def _normalize_verification_plan(value: Any, *, workspace: Dict[str, Any]) -> Dict[str, Any]:
    plan = value if isinstance(value, dict) else {}
    project = _optional_dict(workspace.get("project")) or {}
    commands = _command_strings(plan.get("commands"))
    if not commands:
        commands = _command_strings(project.get("recommendedVerificationCommands"))
    checks = _string_list(plan.get("checks"))
    if not checks:
        checks = ["检查 git diff", "运行可用的构建、测试或静态检查", "按功能验收标准做 smoke check"]
    return {"commands": commands, "checks": checks}


def _normalize_task_graph(value: Any, *, plan: Dict[str, Any]) -> Dict[str, Any]:
    raw_tasks = []
    if isinstance(value, dict) and isinstance(value.get("tasks"), list):
        raw_tasks = value["tasks"]
    tasks = [_normalize_task(item, index) for index, item in enumerate(raw_tasks) if isinstance(item, dict)]
    if not tasks:
        tasks = _fallback_tasks(plan)
    tasks = _ensure_required_tasks(tasks)
    return {
        "tasks": tasks,
        "parallelismRules": [
            "依赖未完成的任务不能执行。",
            "会修改同一 targetFile 的任务必须串行。",
            "inspect/shared/verify 默认串行；独立 feature/test 可并行。",
            "验证任务永远在最后一批执行。",
        ],
    }


def _normalize_task(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    task_type = str(item.get("type") or "feature")
    if task_type not in _TASK_TYPES:
        task_type = "feature"
    task_id = str(item.get("id") or f"task-{index + 1}")
    return {
        "id": task_id,
        "title": str(item.get("title") or task_id),
        "type": task_type,
        "featureId": item.get("featureId"),
        "dependsOn": _string_list(item.get("dependsOn")),
        "targetFiles": _string_list(item.get("targetFiles")),
        "canRunInParallel": bool(item.get("canRunInParallel", task_type in {"feature", "test"})),
        "acceptanceCriteria": _string_list(item.get("acceptanceCriteria")),
        "verificationCommands": _command_strings(item.get("verificationCommands")),
    }


def _fallback_tasks(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = [
        {
            "id": "inspect-workspace",
            "title": "读取工程结构并识别关键入口",
            "type": "inspect",
            "featureId": None,
            "dependsOn": [],
            "targetFiles": [],
            "canRunInParallel": False,
            "acceptanceCriteria": ["识别技术栈、路由、接口调用和验证命令"],
            "verificationCommands": [],
        }
    ]
    features = plan.get("features") if isinstance(plan.get("features"), list) else []
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        feature_id = str(feature.get("id") or f"feature-{index + 1}")
        tasks.append(
            {
                "id": f"implement-{feature_id}",
                "title": f"实现{feature.get('name') or feature_id}",
                "type": "feature",
                "featureId": feature_id,
                "dependsOn": ["inspect-workspace"],
                "targetFiles": [],
                "canRunInParallel": True,
                "acceptanceCriteria": _string_list(feature.get("acceptanceCriteria")),
                "verificationCommands": _command_strings(feature.get("verification")),
            }
        )
    if len(tasks) == 1:
        tasks.append(
            {
                "id": "implement-core-feature",
                "title": "实现核心功能切片",
                "type": "feature",
                "featureId": "core-feature",
                "dependsOn": ["inspect-workspace"],
                "targetFiles": [],
                "canRunInParallel": True,
                "acceptanceCriteria": ["核心功能满足 SDD 验收标准"],
                "verificationCommands": [],
            }
        )
    return tasks


def _ensure_required_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    task_ids = {task["id"] for task in tasks}
    if "inspect-workspace" not in task_ids:
        tasks.insert(
            0,
            {
                "id": "inspect-workspace",
                "title": "读取工程结构并识别关键入口",
                "type": "inspect",
                "featureId": None,
                "dependsOn": [],
                "targetFiles": [],
                "canRunInParallel": False,
                "acceptanceCriteria": ["识别技术栈、路由、接口调用和验证命令"],
                "verificationCommands": [],
            },
        )
    task_ids = {task["id"] for task in tasks}
    for task in tasks:
        if task["id"] != "inspect-workspace" and task["type"] not in {"inspect", "verify"}:
            deps = set(task["dependsOn"])
            deps.add("inspect-workspace")
            task["dependsOn"] = sorted(deps)
    non_verify_ids = [task["id"] for task in tasks if task["type"] != "verify"]
    for task in tasks:
        if task["type"] == "verify":
            deps = set(task["dependsOn"])
            deps.update(task_id for task_id in non_verify_ids if task_id != task["id"])
            task["dependsOn"] = sorted(deps)
    if not any(task["type"] == "verify" for task in tasks):
        tasks.append(
            {
                "id": "verify-generated-code",
                "title": "验证生成代码",
                "type": "verify",
                "featureId": None,
                "dependsOn": non_verify_ids,
                "targetFiles": [],
                "canRunInParallel": False,
                "acceptanceCriteria": ["验证计划中的命令和检查点完成"],
                "verificationCommands": [],
            }
        )
    return tasks


def _build_execution_batches(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    remaining = {task["id"]: task for task in tasks}
    completed: Set[str] = set()
    batches: List[Dict[str, Any]] = []
    batch_index = 1

    while remaining:
        ready = [
            task
            for task in remaining.values()
            if set(task.get("dependsOn") or []).issubset(completed)
        ]
        if not ready:
            batches.append(
                {
                    "index": batch_index,
                    "mode": "blocked",
                    "tasks": sorted(remaining),
                    "reason": "任务依赖存在环或依赖了不存在的任务。",
                }
            )
            break

        batch = _select_ready_batch(ready)
        mode = "parallel" if len(batch) > 1 else "serial"
        batches.append(
            {
                "index": batch_index,
                "mode": mode,
                "tasks": [task["id"] for task in batch],
                "reason": _batch_reason(batch, mode),
            }
        )
        for task in batch:
            completed.add(task["id"])
            remaining.pop(task["id"], None)
        batch_index += 1

    return batches


def _select_ready_batch(ready: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    serial = [task for task in ready if task["type"] in {"inspect", "shared", "verify"} or not task["canRunInParallel"]]
    if serial:
        serial.sort(key=lambda task: _task_priority(task))
        return [serial[0]]

    batch: List[Dict[str, Any]] = []
    used_targets: Set[str] = set()
    for task in sorted(ready, key=lambda task: task["id"]):
        targets = {target for target in task.get("targetFiles") or [] if target}
        if targets and used_targets.intersection(targets):
            continue
        batch.append(task)
        used_targets.update(targets)
    return batch or [sorted(ready, key=lambda task: task["id"])[0]]


def _task_priority(task: Dict[str, Any]) -> int:
    order = {"inspect": 0, "shared": 1, "feature": 2, "test": 3, "verify": 4}
    return order.get(str(task.get("type")), 9)


def _batch_reason(batch: List[Dict[str, Any]], mode: str) -> str:
    if mode == "parallel":
        return "这些任务依赖已满足，且未声明同一 targetFile 冲突。"
    task_type = batch[0].get("type") if batch else "task"
    if task_type == "inspect":
        return "工程侦察需要先完成，后续任务依赖它的结果。"
    if task_type == "shared":
        return "共享能力会影响多个功能切片，先串行落地。"
    if task_type == "verify":
        return "最终验证必须在实现任务之后执行。"
    return "该任务需要串行执行。"


def _git_diff(workspace_root: Optional[str]) -> Dict[str, Any]:
    try:
        result = workspace_tools.git_diff(
            workspace_tools.GitDiffRequest(
                workspace_root=workspace_root,
                max_chars=30000,
            )
        )
        return {
            "returncode": result.get("returncode"),
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
        }
    except Exception as exc:
        return {"returncode": None, "stdout": "", "stderr": str(exc)}


def _verification_status(command_results: List[Dict[str, Any]]) -> str:
    if not command_results:
        return "partial"
    statuses = {str(item.get("status")) for item in command_results}
    if "failed" in statuses:
        return "failed"
    if "requires_approval" in statuses or "skipped" in statuses:
        return "partial"
    return "passed"


def _verification_message(verification: Dict[str, Any]) -> str:
    status = verification.get("status")
    if status == "passed":
        return "验证通过，当前改动满足可执行检查。"
    if status == "failed":
        return "验证失败，需要根据命令输出继续修复。"
    return "验证部分完成，还有命令需要审批、拆分或补充。"


def _summarize_command_output(*, stdout: str, stderr: str) -> str:
    text = stderr or stdout
    if not text:
        return "命令执行完成，无输出。"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-8:])[:2000]


def _is_safe_command(command: str) -> bool:
    if any(token in command for token in _SHELL_META):
        return False
    try:
        return bool(shlex.split(command))
    except ValueError:
        return False


def _command_strings(value: Any) -> List[str]:
    return [item for item in _string_list(value) if _is_safe_command(item)]


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


def _optional_dict(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) else None
