"""在 Build 派发前应用并记录平台拥有的权限共享投影。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.authorization_constants_projection import (
    AuthorizationConstantsProjectionError,
    apply_authorization_constants_projection,
)
from app.services.authorization_route_projection import (
    AuthorizationRouteProjectionError,
    apply_authorization_route_projection,
)
from app.workspace.task_documents import build_task_plan_sha256
from app.workspace.code_changes import capture_workspace_changes


class AuthorizationPlatformProjectionError(ValueError):
    """表示确认后的平台权限投影无法安全应用。"""


def apply_authorization_platform_projections(
    workspace: str | Path,
    build_task_plan: dict[str, Any],
    *,
    build_run_id: str | None = None,
    plan_sha256: str | None = None,
) -> dict[str, Any]:
    """应用确认 DAG 的共享投影，并把源码差异作为平台证据单独返回。"""

    if not isinstance(build_task_plan, dict):
        raise AuthorizationPlatformProjectionError("Build DAG 根结构无效，不能应用权限共享投影。")
    actual_plan_sha256 = build_task_plan_sha256(build_task_plan)
    if plan_sha256 and plan_sha256 != actual_plan_sha256:
        raise AuthorizationPlatformProjectionError("Build Run 绑定的任务计划摘要与投影输入不一致。")
    route_projection = build_task_plan.get("authorization_route_projection")
    constants_projection = build_task_plan.get("authorization_constants_projection")
    if route_projection is None and constants_projection is None:
        return {
            "status": "skipped",
            "source": "platform.authorization_projection",
            "reason": "authorization_disabled_or_no_confirmed_projection",
            "buildRunId": build_run_id,
            "planSha256": actual_plan_sha256,
            "files": [],
            "summary": {"files": 0, "additions": 0, "deletions": 0},
        }

    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.is_dir():
        raise AuthorizationPlatformProjectionError("权限共享投影工作区不存在或不是目录。")

    def _apply() -> dict[str, Any]:
        """严格按确认 DAG 的内容调用两个模板托管区写入器。"""

        try:
            return {
                "routeGuard": apply_authorization_route_projection(
                    workspace_path,
                    route_projection,
                ),
                "authConstants": apply_authorization_constants_projection(
                    workspace_path,
                    constants_projection,
                ),
            }
        except (
            AuthorizationRouteProjectionError,
            AuthorizationConstantsProjectionError,
            OSError,
            ValueError,
        ) as exc:
            raise AuthorizationPlatformProjectionError(str(exc)) from exc

    # 平台写入发生在任何 Agent 快照之前；返回的差异不能并入 Agent code_change_sets。
    captured = capture_workspace_changes(
        workspace=str(workspace_path),
        source_tool="platform.authorization_projection",
        action=_apply,
    )
    change_set = captured.code_change_set or {}
    return {
        "status": "applied",
        "source": "platform.authorization_projection",
        "buildRunId": build_run_id,
        "planSha256": actual_plan_sha256,
        "routeGuard": captured.value["routeGuard"],
        "authConstants": captured.value["authConstants"],
        "files": list(change_set.get("files") or []),
        "summary": dict(
            change_set.get("summary")
            or {"files": 0, "additions": 0, "deletions": 0}
        ),
    }
