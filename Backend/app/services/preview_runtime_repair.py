"""预览启动失败的独立、有界、逐轮确认修复。"""

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from app.services.preview_runtime_state import read_logs, redact, runtime_root, runtime_snapshot


def repair_path(workspace: str, thread_id: str) -> Path:
    """使用会话摘要定位当前修复文档，避免把外部身份作为路径。"""
    key = hashlib.sha256(thread_id.encode()).hexdigest()[:24]
    return runtime_root(workspace) / f"repair-{key}.json"


def load_repair(workspace: str, thread_id: str) -> dict[str, Any]:
    """读取当前会话修复状态，不推断其他会话的修复。"""
    path = repair_path(workspace, thread_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_repair(workspace: str, thread_id: str, repair: dict[str, Any]) -> None:
    """原子保存修复状态，并把待确认计划公开为 Markdown。"""
    path = repair_path(workspace, thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(repair, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    path.with_suffix(".md").write_text(str(repair.get("markdown") or repair.get("message") or ""), encoding="utf-8")


def safe_file(workspace: str, path: str) -> str:
    """将虚拟绝对路径规范为工作区相对路径，并限制到精确非敏感文件。"""
    from app.agents.small_task.scope import _is_forbidden_path
    from app.workspace.workspace import SENSITIVE_FILE_NAMES

    virtual_path = str(path or "").strip().replace("\\", "/")
    relative_path = virtual_path.lstrip("/")
    parts = PurePosixPath(relative_path).parts
    normalized = "/".join(parts)
    sensitive_names = {name.casefold() for name in SENSITIVE_FILE_NAMES}
    invalid_path = (
        not parts
        or any(marker in virtual_path for marker in "*?[]{}")
        or any(part.casefold() in sensitive_names for part in parts)
        or parts[0] not in {"frontend", "Frontend", "backend", "Backend"}
        or ".." in parts
        or _is_forbidden_path(normalized)
    )
    if invalid_path:
        raise ValueError(f"修复计划包含不允许的路径：{virtual_path}")
    workspace_root = Path(workspace).resolve()
    target = workspace_root / normalized
    if (
        not target.resolve().is_relative_to(workspace_root)
        or target.is_dir()
        or any(part.casefold().startswith(".env") for part in parts)
    ):
        raise ValueError("修复范围必须是工作区内非敏感的精确文件。")
    return normalized


def source_digest(workspace: str, paths: list[str]) -> str:
    """绑定待确认文件内容，确认时拒绝过期计划。"""
    digest = hashlib.sha256()
    for name in sorted(paths):
        path = Path(workspace) / safe_file(workspace, name)
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"<missing>")
    return digest.hexdigest()


def prepare_repair(workspace: str, thread_id: str, previous: dict[str, Any], feedback: str = "") -> dict[str, Any]:
    """仅诊断并生成精确修复计划，停在用户确认边界。"""
    from app.agents.repair_planner import plan_build_failure_repair_with_repair_planner_agent
    # 诊断使用实时校准后的失败事实，避免持久记录仍为 running 时把成功摘要交给模型。
    record = runtime_snapshot(workspace, logs=False)
    evidence = {"launch": record, "logs": read_logs(workspace), "validation": previous.get("validation")}
    iteration = int(previous.get("iteration", 0))
    if iteration >= 3:
        return {**previous, "status": "failed", "message": "已达到 3 轮修复上限，请查看日志并手动处理。"}
    roots = [name for name in ("frontend", "Frontend", "backend", "Backend") if (Path(workspace) / name).is_dir()]
    plan = plan_build_failure_repair_with_repair_planner_agent(
        workspace=workspace,
        repair_input={"source": "preview_startup", "failure": evidence, "feedback": feedback[:4000], "change_scope": {"allowed_paths": roots}, "policy": "Repair startup failures only. Missing credentials, missing system tools or unavailable external services require user action; do not invent credentials. Do not change formal contracts or application.json. Return exact files; do not run tests or edit during planning."},
    )
    if plan.get("decision") != "repair":
        return {**previous, "iteration": iteration, "status": "requires_revision" if plan.get("decision") == "requires_user_confirmation" else "failed", "message": redact(str(plan.get("reason") or "无法根据启动日志生成安全修复计划，请手动检查环境。"))}
    tasks: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, task in enumerate(plan.get("repair_tasks") or []):
        changes = task.get("change_scope") or []
        # RepairPlanner 按工作区虚拟根返回 `/frontend/...`；这里只规范化一次，
        # 后续授权、目标文件和变更范围必须复用同一值，避免范围身份发生漂移。
        normalized_changes = [
            {**item, "path": safe_file(workspace, str(item.get("path") or ""))}
            for item in changes
            if isinstance(item, dict)
        ]
        selected = [str(item["path"]) for item in normalized_changes]
        if not selected or len(selected) > 30:
            raise ValueError("修复计划必须明确列出 1–30 个文件。")
        paths.extend(selected)
        for owner in ("backend", "frontend"):
            owner_paths = [path for path in selected if path.lower().startswith(owner + "/")]
            if owner_paths:
                tasks.append(
                    {
                        "id": f"preview:{iteration + 1}:{index}:{owner}",
                        "owner": owner,
                        "title": task.get("title"),
                        "description": task.get("description"),
                        "allowed_paths": owner_paths,
                        "target_files": owner_paths,
                        "change_scope": [
                            item
                            for item in normalized_changes
                            if item["path"] in owner_paths
                        ],
                        "failure_evidence": evidence,
                    }
                )
    if not tasks:
        raise ValueError("诊断没有产生可执行的修复任务。")
    if len(tasks) > 10 or len(set(paths)) > 100:
        raise ValueError("修复范围过大，请拆分问题或进入正式修订。")
    markdown = "# 预览服务修复计划\n\n" + str(plan.get("strategy") or "修复当前启动失败") + "\n\n" + "\n".join(f"- {task['title']}：{task['description']}\n  文件：{', '.join(task['allowed_paths'])}" for task in tasks)
    return {"status": "awaiting_confirmation", "planId": uuid4().hex, "attemptId": record.get("attemptId"), "iteration": iteration, "tasks": tasks, "paths": paths, "digest": source_digest(workspace, paths), "markdown": markdown, "message": "诊断完成，请确认本轮修复计划。", "codeChangeSets": previous.get("codeChangeSets", [])}


def execute_repair(workspace: str, repair: dict[str, Any], report: Any, cancelled: Any) -> dict[str, Any]:
    """执行已确认计划并复用受影响层检查和统一启动器。"""
    from app.services.small_task import execute_small_task_batch
    from app.services.integration_test_runner import run_integration_checks
    from app.services.project_launcher import launch_project_preview
    if source_digest(workspace, repair["paths"]) != repair["digest"]:
        raise ValueError("待确认文件已发生变化，请重新诊断。")
    state = {"workspace": workspace}
    result = execute_small_task_batch(state=state, tasks=repair["tasks"], source="preview_runtime", on_tool_activity=lambda activity: report("tool", str(activity.get("tool_name") or activity.get("name") or "正在执行修复工具")))
    updated = {**repair, "iteration": repair["iteration"], "codeChangeSets": [*repair.get("codeChangeSets", []), *result.get("codeChangeSets", [])]}
    if cancelled.is_set():
        return {**updated, "status": "stopped", "message": "修复已停止，已产生的修改保留。"}
    if result.get("unauthorizedPaths") or any(item.get("status") not in {"completed", "already_satisfied"} for item in result["results"]):
        return {**updated, "status": "failed", "message": redact("；".join(str(item.get("failureReason") or item.get("summary") or "修复失败") for item in result["results"]))}
    report("validation", "正在验证受影响的构建和静态检查…")
    checks = run_integration_checks(state, phase="build", artifact_namespace="preview-repair", affected_layers={str(task["owner"]) for task in repair["tasks"]})
    updated["validation"] = checks
    if cancelled.is_set():
        return {**updated, "status": "stopped", "message": "修复已停止，已产生的修改保留。"}
    if any(item.get("passed") is False for item in checks.get("test_results", [])):
        return {**updated, "status": "retry", "message": "修复后的检查仍失败，需要重新诊断。"}
    report("restart", "正在重新启动前后端服务…")
    launch = launch_project_preview(workspace, force_restart=True, on_progress=lambda stage, status, message: report(stage, message))
    if cancelled.is_set():
        return {**updated, "status": "stopped", "message": "修复已停止。"}
    if launch.get("status") == "running":
        return {**updated, "status": "completed", "message": "预览服务已恢复。"}
    if source_digest(workspace, repair["paths"]) == repair["digest"]:
        return {**updated, "status": "failed", "message": "修复没有产生进展，服务仍启动失败，已停止自动重试。"}
    return {**updated, "status": "retry", "message": "服务仍启动失败，将根据本轮日志重新诊断。"}
