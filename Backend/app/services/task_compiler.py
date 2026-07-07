from app.domain.models import BuildTask


def compile_demo_tasks() -> list[BuildTask]:
    """Return the smallest task DAG that demonstrates frontend/backend ordering."""
    return [
        BuildTask(
            id="data-source",
            owner="data_source",
            description="Create the demo data source and API.",
        ),
        BuildTask(
            id="page",
            owner="frontend",
            description="Create the demo page.",
            dependencies=["data-source"],
        ),
    ]
