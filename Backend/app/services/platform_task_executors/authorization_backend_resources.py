"""在已确认 Build 中执行后端权限常量任务并持久化最近成功清单。"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.services.application_config import read_application_config
from app.services.authorization_constants_projection import apply_authorization_constants_projection
from app.services.backend_auth_resource_candidate import is_backend_auth_resource_task
from app.services.backend_authorization_resources import (
    BACKEND_AUTH_EXECUTOR, BACKEND_AUTH_PATH, backend_resources_applied,
    compile_backend_resource_constants, normalize_backend_constants, record_backend_resources,
)


def execute_authorization_backend_resources(
    task: Mapping[str, Any], context: Mapping[str, Any],
) -> dict[str, Any]:
    """核对当前正式资源与任务清单，写入常量后保存完成记录。"""

    base = {
        "task_id": task.get("id"), "owner": "backend", "execution_strategy": "deterministic",
        "platform_executor": BACKEND_AUTH_EXECUTOR, "commands": [], "change_request": None,
        "executed_by": {"agent": "platform", "mode": "deterministic", "source": BACKEND_AUTH_EXECUTOR},
    }
    changed = []
    failure_category = "authorization_resource_input_invalid"
    try:
        if (not is_backend_auth_resource_task(task, task.get("target_files", ()))
                or task.get("allowed_paths") != [BACKEND_AUTH_PATH]
                or len(task.get("change_scope", ())) != 1
                or task["change_scope"][0].get("path") != BACKEND_AUTH_PATH
                or task["change_scope"][0].get("operation") != "modify"):
            raise ValueError("后端权限任务只能修改既有 AuthConstants.java。")
        workspace = Path(context["workspace"]).resolve()
        plan = context.get("formal_plan")
        if not workspace.is_dir() or not isinstance(plan, Mapping) or plan.get("confirmation_status") != "confirmed":
            raise ValueError("后端权限任务需要工作区和已确认的技术规划。")
        constants = normalize_backend_constants(task["source_refs"]["constants"])
        current = compile_backend_resource_constants(plan, read_application_config(workspace))
        if not constants or constants != current:
            raise ValueError("任务资源清单与当前应用配置或已确认技术规划不一致，请重新规划。")
        target = workspace / BACKEND_AUTH_PATH
        if not target.resolve().is_relative_to(workspace):
            raise ValueError("AuthConstants.java 的实际位置超出工作区。")
        # 同一清单已成功应用时不再写入；技术规划改变后直接比较完整清单。
        if backend_resources_applied(workspace, constants):
            return {**base, "status": "already_satisfied", "changed_files": [],
                    "agent_note": "当前后端权限资源常量清单已成功应用，无需重复执行。",
                    "satisfaction_evidence": {"constants": constants, "record_status": "completed"}}
        failure_category = "backend_authorization_resource_write_failed"
        projection = apply_authorization_constants_projection(workspace, constants)
        if projection["changed"]:
            changed = [BACKEND_AUTH_PATH]
        record_backend_resources(workspace, constants)
        return {**base, "status": "completed", "changed_files": changed,
                "agent_note": "已生成全部接口的权限资源常量，保存最近成功清单。"}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {**base, "status": "failed", "changed_files": changed,
                "failure_category": failure_category, "failure_reason": str(exc),
                "agent_note": f"后端权限资源任务失败：{exc}"}
