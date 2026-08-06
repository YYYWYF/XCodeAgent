from app.graph.state import ProjectState
from app.services.project_launcher import launch_project_preview
from app.workspace.spec_documents import workspace_root


def launch_project(state: ProjectState) -> dict:
    """按应用权威数据源类型启动预览，并返回完整启动证据。"""

    root = workspace_root(state).resolve()
    launch = launch_project_preview(root)
    if launch.get("status") == "failed":
        return _failed_project_launch(launch)
    preview_url = launch.get("preview_url")
    return {
        "phase": "launch_project",
        "status": "requires_user_input",
        "preview_url": preview_url,
        "launch_result": launch,
        "acceptance_request": {
            "status": "requires_user_input",
            "message": "项目已通过集成测试并启动预览，请用户验收。",
            "preview_url": preview_url,
            "package_json_path": launch.get("package_json_path"),
            "server": launch.get("server"),
        },
        "clarification": {
            "mode": "page_acceptance",
            "status": "requires_user_input",
            "message": "请预览页面并完成最终验收。",
            "questions": [],
        },
        "timeline": ["launch_project"],
    }


def _failed_project_launch(launch: dict) -> dict:
    """将任一启动阶段失败统一映射为 Workflow 失败结果。"""

    failure_reason = str(launch.get("message") or "未知启动错误。")
    # 失败状态下前端不会自动导航，复用 preview_url 字段传递可见的失败原因。
    launch["preview_url"] = failure_reason
    return {
        "phase": "launch_project",
        "status": "failed",
        "preview_url": failure_reason,
        "launch_result": launch,
        "acceptance_request": {
            "status": "failed",
            "message": f"项目启动失败：{failure_reason}",
            "preview_url": failure_reason,
        },
        "timeline": ["launch_project"],
    }


def acceptance(state: ProjectState) -> dict:
    decision = str(state.get("acceptance_decision") or "")
    if decision != "accepted":
        return {
            "phase": "acceptance",
            "status": "requires_user_input",
            "accepted": False,
            "clarification": {
                "mode": "plan_adjustment",
                "status": "requires_user_input",
                "message": "已记录修改请求，请调整计划后重新执行并验收。",
                "questions": [],
            },
            "timeline": ["acceptance"],
        }
    return {
        "phase": "acceptance",
        "status": "completed",
        "accepted": True,
        "timeline": ["acceptance"],
    }


def finalize_project(state: ProjectState) -> dict:
    return {
        "phase": "completed",
        "status": "completed",
        "timeline": ["finalize_project"],
    }


def handle_failure(state: ProjectState) -> dict:
    return {
        "phase": "failed",
        "status": "failed",
        "timeline": ["handle_failure"],
    }
