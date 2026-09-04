"""由 Build dispatch 注册调用的 authorization.frontend_resources 确定性执行器。"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

from app.services.authorization_frontend_projection import (
    AuthorizationFrontendProjectionError,
    RESOURCES_RELATIVE_PATH,
    apply_frontend_resources_projection,
    compile_frontend_resources_projection,
    verify_frontend_resources_projection,
)
from app.services.authorization_resource_catalog import (
    compile_frontend_resource_catalog,
    resource_catalog_fingerprint,
)
from app.services.deterministic_unit_candidates import (
    AUTH_GUARD_UNIT_ID,
    AUTH_RESOURCES_PATH,
    is_deterministic_auth_resource_task,
)


EXECUTOR_NAME = "authorization.frontend_resources"
_FINGERPRINT_PATTERN = re.compile(r"[0-9a-f]{64}")


def execute_authorization_frontend_resources(
    task: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """按 Task 绑定的正式资源目录写 resources.ts，并返回标准 Task execution result。

    context 当前契约只接受 ``workspace`` 与 ``formal_plan``。formal_plan 必须是已确认
    TechnicalPlan；本函数不读取 PendingPlan、不推断 planning 状态，也不参与 scheduler dispatch。
    """

    target_error = _target_contract_error(task)
    if target_error:
        return _failure_result(task, "invalid_task_target", target_error)
    task_error = _task_contract_error(task)
    if task_error:
        return _failure_result(task, "invalid_task_contract", task_error)

    workspace, formal_plan, context_error = _execution_context(context)
    if context_error:
        return _failure_result(task, "invalid_execution_context", context_error)

    expected_fingerprint = str(task["source_refs"]["resource_catalog_fingerprint"])
    try:
        manifest = formal_plan.get("authorization_manifest")
        catalog = compile_frontend_resource_catalog(manifest)
        if catalog is None:
            raise AuthorizationFrontendProjectionError("已确认权限资源目录不能处于 disabled 状态。")
        current_fingerprint = resource_catalog_fingerprint(catalog)
    except (AuthorizationFrontendProjectionError, TypeError, ValueError) as exc:
        return _failure_result(
            task,
            "authorization_resource_compile_failed",
            f"正式权限资源目录编译失败：{exc}",
        )
    if current_fingerprint != expected_fingerprint:
        return _failure_result(
            task,
            "stale_authorization_resource_catalog",
            "Task 绑定的资源目录 fingerprint 已过期，拒绝写入 resources.ts。",
        )

    try:
        projection = compile_frontend_resources_projection(formal_plan)
        if projection is None:
            raise AuthorizationFrontendProjectionError("已确认权限资源投影不能为空。")
    except (AuthorizationFrontendProjectionError, TypeError, ValueError) as exc:
        return _failure_result(
            task,
            "authorization_resource_compile_failed",
            f"前端权限资源投影编译失败：{exc}",
        )

    target = workspace / RESOURCES_RELATIVE_PATH
    if not _safe_target(workspace, target):
        return _failure_result(
            task,
            "invalid_task_target",
            "resources.ts 的真实写入位置越出当前 workspace。",
        )
    try:
        already_identical = target.is_file() and verify_frontend_resources_projection(
            workspace,
            projection,
        ).get("verified") is True
    except (AuthorizationFrontendProjectionError, OSError, RuntimeError):
        already_identical = False

    if already_identical:
        return _success_result(
            task,
            status="already_satisfied",
            changed_files=[],
            fingerprint=current_fingerprint,
        )

    wrote_target = False
    try:
        apply_frontend_resources_projection(workspace, projection)
        wrote_target = True
    except (AuthorizationFrontendProjectionError, OSError, RuntimeError, ValueError) as exc:
        return _failure_result(
            task,
            "authorization_resource_write_failed",
            f"resources.ts 确定性写入失败：{exc}",
        )

    try:
        validation = verify_frontend_resources_projection(workspace, projection)
        if validation.get("verified") is not True:
            raise AuthorizationFrontendProjectionError("写后校验没有返回 verified=true。")
    except (AuthorizationFrontendProjectionError, OSError, RuntimeError, ValueError) as exc:
        return _failure_result(
            task,
            "authorization_resource_output_validation_failed",
            f"resources.ts 写后确定性校验失败：{exc}",
            changed_files=[AUTH_RESOURCES_PATH] if wrote_target else [],
        )
    return _success_result(
        task,
        status="completed",
        changed_files=[AUTH_RESOURCES_PATH],
        fingerprint=current_fingerprint,
    )


def _target_contract_error(task: Mapping[str, Any]) -> str:
    """先验证唯一目标路径，确保任何其他文件声明都在写入前失败。"""

    if not isinstance(task, Mapping):
        return "authorization.frontend_resources Task 必须是对象。"
    for field in ("target_files", "allowed_paths"):
        if _exact_paths(task.get(field)) != [AUTH_RESOURCES_PATH]:
            return f"Task {field} 必须且只能声明 {AUTH_RESOURCES_PATH}。"
    source_refs = task.get("source_refs")
    if not isinstance(source_refs, Mapping) or _exact_paths(source_refs.get("paths")) != [AUTH_RESOURCES_PATH]:
        return f"Task source_refs.paths 必须且只能声明 {AUTH_RESOURCES_PATH}。"
    deliverables = task.get("deliverables")
    if not isinstance(deliverables, list) or len(deliverables) != 1:
        return "Task 必须且只能声明一个 resources.ts deliverable。"
    deliverable = deliverables[0]
    if not isinstance(deliverable, Mapping) or _exact_paths(deliverable.get("paths")) != [AUTH_RESOURCES_PATH]:
        return f"Task deliverable.paths 必须且只能声明 {AUTH_RESOURCES_PATH}。"
    return ""


def _task_contract_error(task: Mapping[str, Any]) -> str:
    """验证 executor、Unit、source refs、能力与完整 fingerprint 身份。"""

    source_refs = task.get("source_refs")
    if not isinstance(source_refs, Mapping):
        return "Task source_refs 必须是对象。"
    fingerprint = source_refs.get("resource_catalog_fingerprint")
    if not isinstance(fingerprint, str) or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
        return "Task resource_catalog_fingerprint 必须是 64 位小写 SHA-256。"
    capability = f"frontend.auth.resources:{fingerprint}"
    if any(
        source_refs.get(key) != value
        for key, value in {
            "artifact": "technical-plan",
            "kind": "frontend.auth.resources",
            "capability_id": capability,
        }.items()
    ):
        return "Task source refs 与 frontend.auth.resources capability 不一致。"
    if task.get("unit_id") != AUTH_GUARD_UNIT_ID:
        return f"Task unit_id 必须是 {AUTH_GUARD_UNIT_ID}。"
    if task.get("owner") != "frontend" or task.get("execution_strategy") != "deterministic":
        return "Task 必须使用 frontend owner 和 deterministic execution strategy。"
    if task.get("platform_executor") != EXECUTOR_NAME:
        return f"Task platform_executor 必须是 {EXECUTOR_NAME}。"
    if not is_deterministic_auth_resource_task(task, [AUTH_RESOURCES_PATH]):
        return "Task ID 或 provides_capabilities 与资源目录 fingerprint 不一致。"
    if _exact_texts(task.get("provides_capabilities")) != [capability]:
        return "Task provides_capabilities 必须且只能提供当前资源目录 capability。"
    deliverable = task["deliverables"][0]
    if deliverable.get("target_id") != capability or _exact_texts(deliverable.get("provides")) != [capability]:
        return "Task deliverable 与资源目录 capability 不一致。"
    return ""


def _execution_context(
    context: Mapping[str, Any],
) -> tuple[Path, Mapping[str, Any], str]:
    """解析唯一执行上下文，并要求资源数据来自已确认的 formal plan。"""

    if not isinstance(context, Mapping):
        return Path(), {}, "authorization executor context 必须是对象。"
    workspace_value = context.get("workspace")
    if not isinstance(workspace_value, (str, Path)) or not str(workspace_value).strip():
        return Path(), {}, "authorization executor context 缺少 workspace。"
    workspace = Path(workspace_value).expanduser().resolve()
    if not workspace.is_dir():
        return Path(), {}, "authorization executor workspace 不存在或不是目录。"
    formal_plan = context.get("formal_plan")
    if not isinstance(formal_plan, Mapping) or formal_plan.get("confirmation_status") != "confirmed":
        return workspace, {}, "authorization executor 只接受已确认的 formal_plan。"
    return workspace, formal_plan, ""


def _safe_target(workspace: Path, target: Path) -> bool:
    """拒绝通过父目录或目标符号链接把唯一写入引到 workspace 外。"""

    try:
        return target.parent.resolve().is_relative_to(workspace) and target.resolve().is_relative_to(workspace)
    except (OSError, RuntimeError):
        return False


def _exact_paths(value: Any) -> list[str]:
    """读取精确路径数组；非字符串、重复或额外目标均返回空数组触发拒绝。"""

    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return []
    if len(value) != len(set(value)):
        return []
    return list(value)


def _exact_texts(value: Any) -> list[str]:
    """读取精确文本数组，不做 trim、别名或历史格式兼容。"""

    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return []
    return list(value)


def _success_result(
    task: Mapping[str, Any],
    *,
    status: str,
    changed_files: list[str],
    fingerprint: str,
) -> dict[str, Any]:
    """构造 completed/already_satisfied 标准平台 Task 结果。"""

    already_satisfied = status == "already_satisfied"
    summary = (
        "resources.ts 已与正式权限资源目录一致，无需写入。"
        if already_satisfied
        else "resources.ts 已按正式权限资源目录完成确定性写入和校验。"
    )
    return {
        **_base_result(task),
        "status": status,
        "changed_files": changed_files,
        "agent_note": summary,
        "failure_category": None,
        "failure_reason": None,
        "change_request": None,
        **(
            {
                "satisfaction_evidence": {
                    "path": AUTH_RESOURCES_PATH,
                    "resource_catalog_fingerprint": fingerprint,
                    "validation": "deterministic",
                }
            }
            if already_satisfied
            else {}
        ),
    }


def _failure_result(
    task: Mapping[str, Any],
    category: str,
    reason: str,
    *,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """构造由 scheduler 归类为 terminal_failure 的标准平台 Task 失败结果。"""

    return {
        **_base_result(task),
        "status": "failed",
        "changed_files": list(changed_files or []),
        "agent_note": reason,
        "failure_category": category,
        "failure_reason": reason,
        "change_request": None,
    }


def _base_result(task: Mapping[str, Any]) -> dict[str, Any]:
    """构造所有平台执行结果共享的稳定字段。"""

    task_value = task if isinstance(task, Mapping) else {}
    return {
        "task_id": str(task_value.get("id") or "<unknown>"),
        "owner": task_value.get("owner"),
        "execution_strategy": "deterministic",
        "platform_executor": EXECUTOR_NAME,
        "commands": [],
        "executed_by": {
            "agent": "platform",
            "mode": "deterministic",
            "source": EXECUTOR_NAME,
        },
    }
