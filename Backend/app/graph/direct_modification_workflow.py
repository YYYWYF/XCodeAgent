from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.graph.nodes.direct_modification import (
    classify_direct_modification,
    execute_backend_direct_modification,
    execute_frontend_direct_modification,
    finalize_direct_modification,
    launch_direct_modification_project,
    run_direct_modification_integration_test,
)
from app.graph.state import ProjectState
from app.persistence.checkpoints import workflow_checkpoint_db_path, workflow_checkpointer


def _route_classification(
    state: ProjectState,
) -> Literal["execute_frontend", "execute_backend", "finalize"]:
    """按分类结果选择单端、跨端第一阶段或直接终止。"""

    if state.get("status") != "in_progress":
        return "finalize"
    owner = state.get("direct_modification_owner")
    if owner == "frontend":
        return "execute_frontend"
    if owner in {"backend", "fullstack"}:
        return "execute_backend"
    return "finalize"


def _route_backend(
    state: ProjectState,
) -> Literal["execute_frontend", "integration_test", "finalize"]:
    """后端成功后按 fullstack 或单端路径继续。"""

    if state.get("status") == "failed":
        return "finalize"
    return (
        "execute_frontend"
        if state.get("direct_modification_owner") == "fullstack"
        else "integration_test"
    )


def _route_frontend(
    state: ProjectState,
) -> Literal["integration_test", "finalize"]:
    """前端失败立即结束，成功后统一进入集成测试。"""

    return "finalize" if state.get("status") == "failed" else "integration_test"


def _route_integration_test(
    state: ProjectState,
) -> Literal["launch_project", "finalize"]:
    """测试通过后启动预览，失败则直接形成终态。"""

    return "launch_project" if state.get("quality_gate_passed") is True else "finalize"


def direct_next_node_name(node_name: str, state: ProjectState) -> str | None:
    """按快速修改 Graph 的真实路由返回下一节点，供进度投影提前展示运行态。"""

    if node_name == "classify_intent":
        route = _route_classification(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "execute_backend":
        route = _route_backend(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "execute_frontend":
        route = _route_frontend(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "integration_test":
        route = _route_integration_test(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "launch_project":
        return "finalize_direct_modification"
    return None


def build_direct_modification_graph(*, checkpointer: Any) -> Any:
    """构建不依赖正式规划产物的快速修改 LangGraph。"""

    builder = StateGraph(ProjectState)
    builder.add_node("classify_intent", classify_direct_modification)
    builder.add_node("execute_frontend", execute_frontend_direct_modification)
    builder.add_node("execute_backend", execute_backend_direct_modification)
    builder.add_node("integration_test", run_direct_modification_integration_test)
    builder.add_node("launch_project", launch_direct_modification_project)
    builder.add_node("finalize_direct_modification", finalize_direct_modification)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges(
        "classify_intent",
        _route_classification,
        {
            "execute_frontend": "execute_frontend",
            "execute_backend": "execute_backend",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_conditional_edges(
        "execute_backend",
        _route_backend,
        {
            "execute_frontend": "execute_frontend",
            "integration_test": "integration_test",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_conditional_edges(
        "execute_frontend",
        _route_frontend,
        {
            "integration_test": "integration_test",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_conditional_edges(
        "integration_test",
        _route_integration_test,
        {
            "launch_project": "launch_project",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_edge("launch_project", "finalize_direct_modification")
    builder.add_edge("finalize_direct_modification", END)
    return builder.compile(checkpointer=checkpointer)


_DIRECT_MODIFICATION_GRAPHS: dict[str, object] = {}


async def direct_modification_graph_for_request(
    *,
    workspace: str | None = None,
    project_id: str | None = None,
) -> Any:
    """按工作区 SQLite checkpoint 创建或复用快速修改 Graph。"""

    db_path = workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
    cache_key = str(db_path)
    if cache_key not in _DIRECT_MODIFICATION_GRAPHS:
        _DIRECT_MODIFICATION_GRAPHS[cache_key] = build_direct_modification_graph(
            checkpointer=await workflow_checkpointer(
                workspace=workspace,
                project_id=project_id,
            )
        )
    return _DIRECT_MODIFICATION_GRAPHS[cache_key]


def clear_direct_modification_graph_cache() -> None:
    """清理进程内按工作区缓存的快速修改 Graph。"""

    _DIRECT_MODIFICATION_GRAPHS.clear()
