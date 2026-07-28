from app.graph.state import ProjectState
from app.services.project_launcher import (
    find_backend_project_root,
    launch_backend_project,
    launch_frontend_project,
    stop_backend_project,
)
from app.workspace.spec_documents import workspace_root


def launch_project(state: ProjectState) -> dict:
    """按工程能力启动可选 Java 后端与前端，并返回完整启动证据。"""

    root = workspace_root(state).resolve()
    backend_process = None
    if find_backend_project_root(root) is None:
        backend = {
            "status": "skipped",
            "message": "未识别到后端 Maven 工程，已跳过后端启动。",
            "workspace": str(root),
            "failed_stage": None,
        }
    else:
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
        if backend_process is not None:
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
        "message": (
            "前端项目已启动并就绪，未识别到后端工程。"
            if backend.get("status") == "skipped"
            else "Java 后端与前端项目均已启动并就绪。"
        ),
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
