"""测试通过后的代码审查确认与只读扫描节点。"""

from __future__ import annotations

from typing import Any

from app.graph.state import ProjectState
from app.graph.subgraphs.code_review import run_code_review_subgraph
from app.graph.nodes.common import workspace_from_state
from langchain_core.runnables import RunnableConfig


def review_phase_confirmation(state: ProjectState) -> dict[str, Any]:
    """在集成质量门禁通过后等待用户确认进入审查阶段。"""

    if state.get("quality_gate_passed") is not True:
        return {
            "phase": "review_phase_confirmation",
            "status": "failed",
            "message": "集成测试尚未通过，不能进入审查阶段。",
            "error": "只有集成质量门禁通过后才能进入代码审查。",
            "timeline": ["review_phase_confirmation"],
        }
    submission = state.get("review_phase_confirmation")
    confirmed = isinstance(submission, dict) and submission.get("action") == "confirm"
    if confirmed:
        return {
            "phase": "review_phase_confirmation",
            "status": "completed",
            "clarification": {},
            "code_review_next_action": "code_review",
            "timeline": ["review_phase_confirmation"],
        }
    return {
        "phase": "review_phase_confirmation",
        "status": "requires_user_input",
        "clarification": {
            "mode": "review_phase_confirmation",
            "status": "requires_user_input",
            "message": "测试已通过，是否进入审查阶段？",
            "questions": [],
        },
        "code_review_next_action": "await_user_input",
        "timeline": ["review_phase_confirmation"],
    }


def code_review(
    state: ProjectState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """调用代码审查子图，统一处理扫描、修复确认、构建和失败路由。"""

    result = run_code_review_subgraph(state, config)
    workspace = workspace_from_state(state)
    update: dict[str, Any] = {
        "phase": "code_review",
        "status": result.get("status", "failed"),
        "message": result.get("message") or "前后端代码审查完成。",
        "clarification": result.get("clarification", {}),
        "code_review_result": result.get("code_review_result", state.get("code_review_result", {})),
        "code_review_repair_status": result.get(
            "code_review_repair_status", state.get("code_review_repair_status", "not_required")
        ),
        "code_review_repair_result": result.get(
            "code_review_repair_result", state.get("code_review_repair_result", {})
        ),
        "code_review_build_results": result.get(
            "code_review_build_results", state.get("code_review_build_results", [])
        ),
        "code_review_repair_iteration": result.get(
            "code_review_repair_iteration", state.get("code_review_repair_iteration", 0)
        ),
        "code_review_max_repair_iterations": result.get(
            "code_review_max_repair_iterations",
            state.get("code_review_max_repair_iterations", 3),
        ),
        "code_review_events": result.get("code_review_events", []),
        "code_review_next_action": result.get("code_review_next_action", "handle_failure"),
        "timeline": result.get("timeline", ["code_review"]),
    }
    if result.get("error"):
        update["error"] = str(result["error"])[:2_000]
    if result.get("code_changes"):
        update["code_changes"] = result["code_changes"]
    if result.get("code_change_sets"):
        update["code_change_sets"] = result["code_change_sets"]
    if not workspace:
        update["status"] = "failed"
        update["error"] = "代码审查需要显式用户 workspaceRoot。"
        update["code_review_next_action"] = "handle_failure"
    return update
