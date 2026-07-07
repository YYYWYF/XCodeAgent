from app.graph.state import ProjectState


def launch_project(state: ProjectState) -> dict:
    return {
        "phase": "launch_project",
        "preview_url": "http://127.0.0.1:3000",
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
