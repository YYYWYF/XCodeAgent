"""测试通过后的代码审查确认与只读扫描节点。"""

from __future__ import annotations

from typing import Any

from app.agents.code_analyze.analyzer import _safe_review_text, analyze_workspace_code
from app.graph.state import ProjectState


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


def code_review(state: ProjectState) -> dict[str, Any]:
    """调用只读 CodeAnalyze Agent 扫描指定前后端源码并返回结构化结果。"""

    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    try:
        result = analyze_workspace_code(state, workspace)
    except Exception as exc:  # noqa: BLE001 - 节点边界统一将 Agent 异常转为失败状态。
        return {
            "phase": "code_review",
            "status": "failed",
            "message": "前后端代码审查失败。",
            "error": _safe_review_text(
                f"{type(exc).__name__}: {str(exc)[:500]}",
                workspace,
            ),
            "code_review_result": {},
            "code_review_next_action": "handle_failure",
            "timeline": ["code_review"],
        }
    return {
        "phase": "code_review",
        "status": "completed",
        "message": result.get("summary") or "前后端代码审查完成。",
        "code_review_result": result,
        "code_review_next_action": "launch_project",
        "timeline": ["code_review"],
    }
