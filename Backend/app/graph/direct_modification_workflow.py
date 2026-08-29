from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from app.graph.nodes.direct_modification import (
    classify_direct_modification,
    execute_backend_direct_modification,
    execute_frontend_direct_modification,
    execute_workspace_direct_modification,
    finalize_direct_modification,
    launch_direct_modification_project,
    respond_to_casual_conversation,
    respond_to_workspace_question,
    run_direct_modification_integration_test,
    scan_change_impact_code,
)
from app.graph.nodes.workspace_inspection import scan_workspace_code
from app.graph.nodes.direct_repair import direct_modification_repair
from app.graph.state import ProjectState
from app.persistence.checkpoints import workflow_checkpoint_db_path, workflow_checkpointer


_DIRECT_ENTRY_NODES = {
    "scan_workspace_code",
    "scan_change_impact_code",
    "execute_frontend",
    "execute_backend",
    "execute_workspace",
    "finalize_direct_modification",
}


def _route_direct_entry(state: ProjectState) -> str:
    """按服务端确认续跑标记选择快速修改 Graph 的首个业务节点。"""

    requested = str(state.get("direct_modification_resume_node") or "").strip()
    if requested in _DIRECT_ENTRY_NODES:
        return requested
    return "scan_workspace_code"


def _route_classification(
    state: ProjectState,
) -> Literal[
    "respond_conversation",
    "answer_workspace",
    "execute_frontend",
    "execute_backend",
    "execute_workspace",
    "scan_change_impact_code",
    "finalize",
]:
    """按契约分析后的消息意图选择回答、代码证据扫描、局部修改或终止。"""

    if state.get("status") != "in_progress":
        return "finalize"
    intent = state.get("conversation_intent")
    if intent == "casual_chat":
        return "respond_conversation"
    if intent == "workspace_question":
        return "answer_workspace"
    if (
        intent == "implementation_fix"
        and state.get("change_impact_enabled") is True
        and state.get("change_impact_code_scan_required") is True
    ):
        return "scan_change_impact_code"
    owner = state.get("direct_modification_owner")
    if owner == "frontend":
        return "execute_frontend"
    if owner in {"backend", "fullstack"}:
        return "execute_backend"
    if owner == "workspace":
        return "execute_workspace"
    return "finalize"


def _route_scan_workspace(
    state: ProjectState,
) -> Literal["classify_intent", "finalize"]:
    """扫描完成后进入有工作区证据的消息分类阶段。"""

    return "classify_intent" if state.get("status") == "completed" else "finalize"


def _route_backend(
    state: ProjectState,
) -> Literal["execute_frontend", "integration_test", "finalize"]:
    """后端成功后按 fullstack 或单端路径继续。"""

    if state.get("status") != "in_progress":
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

    return "finalize" if state.get("status") != "in_progress" else "integration_test"


def _route_integration_test(
    state: ProjectState,
) -> Literal["launch_project", "direct_modification_repair", "finalize"]:
    """测试通过后启动预览，失败且有证据时进入自由对话修复节点。"""

    if state.get("quality_gate_passed") is True:
        return "launch_project"
    if state.get("integration_next_action") == "direct_modification_repair":
        return "direct_modification_repair"
    return "finalize"


def _route_direct_repair(
    state: ProjectState,
) -> Literal["integration_test", "finalize"]:
    """修复节点成功后重新验收，失败或需要确认时结束本次自由运行。"""

    if (
        state.get("status") == "in_progress"
        and state.get("integration_next_action") == "integration_test"
    ):
        return "integration_test"
    return "finalize"


def direct_next_node_name(node_name: str, state: ProjectState) -> str | None:
    """按快速修改 Graph 的真实路由返回下一节点，供进度投影提前展示运行态。"""

    if node_name == "scan_workspace_code":
        route = _route_scan_workspace(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "classify_intent":
        route = _route_classification(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "scan_change_impact_code":
        if (
            state.get("status") == "in_progress"
            and _has_change_impact_code_findings(state)
        ):
            return "execute_frontend" if state.get("direct_modification_owner") == "frontend" else (
                "execute_backend"
                if state.get("direct_modification_owner") in {"backend", "fullstack"}
                else "finalize_direct_modification"
            )
        return "finalize_direct_modification"
    if node_name in {"respond_conversation", "answer_workspace"}:
        return "finalize_direct_modification"
    if node_name == "execute_backend":
        route = _route_backend(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "execute_frontend":
        route = _route_frontend(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "execute_workspace":
        return "finalize_direct_modification"
    if node_name == "integration_test":
        route = _route_integration_test(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "direct_modification_repair":
        route = _route_direct_repair(state)
        return "finalize_direct_modification" if route == "finalize" else route
    if node_name == "launch_project":
        return "finalize_direct_modification"
    return None


def build_direct_modification_graph(*, checkpointer: Any) -> Any:
    """构建不依赖正式规划产物的快速修改 LangGraph。"""

    builder = StateGraph(ProjectState)
    builder.add_node("classify_intent", classify_direct_modification)
    builder.add_node("scan_workspace_code", scan_workspace_code)
    builder.add_node("scan_change_impact_code", scan_change_impact_code)
    builder.add_node("respond_conversation", respond_to_casual_conversation)
    builder.add_node("answer_workspace", respond_to_workspace_question)
    builder.add_node("execute_frontend", execute_frontend_direct_modification)
    builder.add_node("execute_backend", execute_backend_direct_modification)
    builder.add_node("execute_workspace", execute_workspace_direct_modification)
    builder.add_node("integration_test", run_direct_modification_integration_test)
    builder.add_node("direct_modification_repair", direct_modification_repair)
    builder.add_node("launch_project", launch_direct_modification_project)
    builder.add_node("finalize_direct_modification", finalize_direct_modification)

    # 新请求先建立有界的工作区上下文，再由分类节点读取当前 JSON 契约。
    # 确认续跑由协议层从同一 thread 的服务端 checkpoint 写入首节点标记，
    # 因此可以跳过重复的导航扫描和意图分类，但仍经过必要的 code.scan 闸门。
    builder.add_conditional_edges(
        START,
        _route_direct_entry,
        {
            "scan_workspace_code": "scan_workspace_code",
            "scan_change_impact_code": "scan_change_impact_code",
            "execute_frontend": "execute_frontend",
            "execute_backend": "execute_backend",
            "execute_workspace": "execute_workspace",
            "finalize_direct_modification": "finalize_direct_modification",
        },
    )
    builder.add_conditional_edges(
        "scan_workspace_code",
        _route_scan_workspace,
        {
            "classify_intent": "classify_intent",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_conditional_edges(
        "classify_intent",
        _route_classification,
        {
            "respond_conversation": "respond_conversation",
            "answer_workspace": "answer_workspace",
            "execute_frontend": "execute_frontend",
            "execute_backend": "execute_backend",
            "execute_workspace": "execute_workspace",
            "scan_change_impact_code": "scan_change_impact_code",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_conditional_edges(
        "scan_change_impact_code",
        lambda state: (
            "execute_frontend"
            if state.get("status") == "in_progress"
            and _has_change_impact_code_findings(state)
            and state.get("direct_modification_owner") == "frontend"
            else "execute_backend"
            if state.get("status") == "in_progress"
            and _has_change_impact_code_findings(state)
            and state.get("direct_modification_owner") in {"backend", "fullstack"}
            else "finalize"
        ),
        {
            "execute_frontend": "execute_frontend",
            "execute_backend": "execute_backend",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_edge("respond_conversation", "finalize_direct_modification")
    builder.add_edge("answer_workspace", "finalize_direct_modification")
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
    builder.add_edge("execute_workspace", "finalize_direct_modification")
    builder.add_conditional_edges(
        "integration_test",
        _route_integration_test,
        {
            "launch_project": "launch_project",
            "direct_modification_repair": "direct_modification_repair",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_conditional_edges(
        "direct_modification_repair",
        _route_direct_repair,
        {
            "integration_test": "integration_test",
            "finalize": "finalize_direct_modification",
        },
    )
    builder.add_edge("launch_project", "finalize_direct_modification")
    builder.add_edge("finalize_direct_modification", END)
    return builder.compile(checkpointer=checkpointer)


def _has_change_impact_code_findings(state: ProjectState) -> bool:
    """判断 code.scan 是否真的返回了可交给写 Agent 的源码定位。"""

    scan = state.get("change_impact_code_scan")
    if not isinstance(scan, dict) or scan.get("performed") is not True:
        return False
    findings = scan.get("findings")
    return isinstance(findings, list) and any(isinstance(item, dict) for item in findings)


_DIRECT_MODIFICATION_GRAPHS: dict[str, tuple[object, object]] = {}


async def direct_modification_graph_for_request(
    *,
    workspace: str | None = None,
    project_id: str | None = None,
) -> Any:
    """按工作区 SQLite checkpoint 创建或复用快速修改 Graph。"""

    db_path = workflow_checkpoint_db_path(workspace=workspace, project_id=project_id)
    cache_key = str(db_path)
    checkpointer = await workflow_checkpointer(workspace=workspace, project_id=project_id)
    cached = _DIRECT_MODIFICATION_GRAPHS.get(cache_key)
    if cached is None or cached[0] is not checkpointer:
        cached = (
            checkpointer,
            build_direct_modification_graph(checkpointer=checkpointer),
        )
        _DIRECT_MODIFICATION_GRAPHS[cache_key] = cached
    return cached[1]


def clear_direct_modification_graph_cache() -> None:
    """清理进程内按工作区缓存的快速修改 Graph。"""

    _DIRECT_MODIFICATION_GRAPHS.clear()
