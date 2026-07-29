from app.domain.models import BuildTask


def compile_demo_tasks() -> list[BuildTask]:
    """返回用于演示数据库、后端和前端顺序的最小任务 DAG。"""

    return [
        BuildTask(
            id="database",
            owner="database",
            description="Prepare the demo database schema.",
            unit_id="database:demo",
            task_type="database.change",
            provides_capabilities=["database:demo:ready"],
        ),
        BuildTask(
            id="backend",
            owner="backend",
            description="Create the demo backend API.",
            dependencies=["database"],
            unit_id="backend:endpoint:demo-api:demo.list",
            task_type="backend.code",
            requires_capabilities=["database:demo:ready"],
        ),
        BuildTask(
            id="page",
            owner="frontend",
            description="Create the demo page.",
            dependencies=["backend"],
            task_type="frontend.code",
        ),
    ]
