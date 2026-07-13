from app.graph.state import ProjectState
from app.services.project_launcher import launch_frontend_project


def launch_project(state: ProjectState) -> dict:
    launch = launch_frontend_project(state)
    preview_url = launch.get("preview_url")
    if launch.get("status") == "failed":
        return {
            "phase": "launch_project",
            "status": "failed",
            "launch_result": launch,
            "acceptance_request": {
                "status": "failed",
                "message": f"项目启动失败：{launch.get('message')}",
            },
            "timeline": ["launch_project"],
        }

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
