"""验收阶段子图：启动项目后进入用户验收等待。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph.state import ProjectState


def _launch_project(state: ProjectState) -> dict[str, Any]:
    """延迟调用既有启动节点，避免生命周期模块与子图互相导入。"""

    from app.graph.nodes.lifecycle import launch_project

    result = launch_project(state)
    # 旧启动节点以 requires_user_input 表示“可验收”；在验收子图内启动成功
    # 已是明确的子节点完成态，避免父图把项目启动误判为最终交互门。
    if (
        result.get("status") == "requires_user_input"
        and isinstance(result.get("launch_result"), dict)
        and result["launch_result"].get("status") != "failed"
    ):
        return {**result, "status": "completed", "clarification": {}}
    return result


def _launch_succeeded(state: ProjectState) -> bool:
    """判断当前状态是否已经拥有本轮可复用的成功启动结果。"""

    launch = state.get("launch_result")
    if not isinstance(launch, dict) or launch.get("status") == "failed":
        return False
    preview_url = str(state.get("preview_url") or launch.get("preview_url") or "").strip()
    return bool(preview_url)


def _route_start(state: ProjectState) -> str:
    """首次进入时启动项目，恢复验收时直接跳过已完成的启动步骤。"""

    # 保留既有 accepted/finalize 后端调用能力：已提交通过动作无需再次启动项目。
    if str(state.get("acceptance_decision") or "") == "accepted":
        return "acceptance_review"
    return "acceptance_review" if _launch_succeeded(state) else "launch_project"


def _route_after_launch(state: ProjectState) -> str:
    """启动失败结束子图，启动成功才进入验收等待子节点。"""

    launch = state.get("launch_result")
    if state.get("status") == "failed" or (
        isinstance(launch, dict) and launch.get("status") == "failed"
    ):
        return END
    return "acceptance_review"


def acceptance_review(state: ProjectState) -> dict[str, Any]:
    """展示验收等待状态，已提交 accepted 时保留原有完成语义。"""

    decision = str(state.get("acceptance_decision") or "")
    if decision == "accepted":
        return {
            "phase": "acceptance",
            "status": "completed",
            "accepted": True,
            "clarification": {},
            "timeline": ["acceptance"],
        }

    launch = state.get("launch_result")
    launch = launch if isinstance(launch, dict) else {}
    preview_url = state.get("preview_url") or launch.get("preview_url")
    acceptance_request = state.get("acceptance_request")
    acceptance_request = (
        dict(acceptance_request) if isinstance(acceptance_request, dict) else {}
    )
    if preview_url and not acceptance_request:
        acceptance_request = {
            "status": "requires_user_input",
            "message": "项目已启动，请预览应用并完成验收。",
            "preview_url": preview_url,
        }
    return {
        "phase": "acceptance",
        "status": "requires_user_input",
        "accepted": False,
        "preview_url": preview_url,
        "launch_result": launch,
        "acceptance_request": acceptance_request,
        "clarification": {
            "mode": "page_acceptance",
            "status": "requires_user_input",
            "message": "请预览页面并完成最终验收。",
            "questions": [],
        },
        "timeline": ["acceptance"],
    }


def build_acceptance_subgraph():
    """构建启动与验收等待串联的验收子图。"""

    builder = StateGraph(ProjectState)
    builder.add_node("launch_project", _launch_project)
    builder.add_node("acceptance_review", acceptance_review)
    builder.add_conditional_edges(
        START,
        _route_start,
        {"launch_project": "launch_project", "acceptance_review": "acceptance_review"},
    )
    builder.add_conditional_edges(
        "launch_project",
        _route_after_launch,
        {"acceptance_review": "acceptance_review", END: END},
    )
    builder.add_edge("acceptance_review", END)
    return builder.compile()


acceptance_subgraph = build_acceptance_subgraph()


def run_acceptance_subgraph(
    state: ProjectState, config: Any | None = None
) -> dict[str, Any]:
    """执行验收子图并返回主图可持久化的状态增量。"""

    return acceptance_subgraph.invoke(dict(state), config=config or {})
