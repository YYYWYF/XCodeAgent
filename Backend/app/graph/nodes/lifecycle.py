from app.graph.state import ProjectState
from app.services.project_launcher import (
    launch_backend_project,
    launch_frontend_project,
    stop_backend_project,
)
from app.workspace.spec_documents import workspace_root


def launch_project(state: ProjectState) -> dict:
    """先启动 Java 后端再启动前端，并在任一步失败时返回完整启动证据。"""

    root = workspace_root(state).resolve()
    backend = launch_backend_project(root)
    backend_process = backend.pop("_process", None)
    if backend.get("status") == "failed":
        launch = {
            "status": "failed",
            "message": backend.get("message"),
            "workspace": backend.get("workspace"),
            "preview_url": None,
            "package_json_path": None,
            "server": None,
            "backend": backend,
            "frontend": None,
            "failed_stage": backend.get("failed_stage"),
        }
        return _failed_project_launch(launch)

    frontend = launch_frontend_project(root)
    if frontend.get("status") == "failed":
        stop_backend_project(backend, backend_process)
        launch = {
            **frontend,
            "backend": backend,
            "frontend": frontend,
            "failed_stage": "frontend_start",
        }
        return _failed_project_launch(launch)

    launch = {
        **frontend,
        "message": "Java 后端与前端项目均已启动并就绪。",
        "backend": backend,
        "frontend": frontend,
        "failed_stage": None,
    }
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
    return {
        "phase": "acceptance",
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
