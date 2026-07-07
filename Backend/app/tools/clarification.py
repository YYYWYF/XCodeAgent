from __future__ import annotations

from typing import Any


def ask_user_about_unclear_requirements(
    request: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    """Return clarification questions for unclear requirement details.

    This is intentionally a tiny local placeholder for a future HITL tool.
    The real implementation can emit AG-UI events or LangGraph interrupts.
    For now it returns questions plus safe default assumptions so the demo
    graph can continue running.
    """

    normalized = request.lower()
    questions: list[dict[str, str]] = []
    assumptions: list[str] = []

    if "权限" not in normalized and "角色" not in normalized:
        questions.append(
            {
                "id": "roles_and_permissions",
                "question": "是否需要区分管理员和普通用户权限？",
                "default_assumption": "默认提供管理员和普通用户两个角色。",
            }
        )
        assumptions.append("默认提供管理员和普通用户两个角色。")

    if "数据源" not in normalized and "数据库" not in normalized:
        questions.append(
            {
                "id": "data_source_scope",
                "question": "数据使用真实数据库、Mock 数据，还是外部 API？",
                "default_assumption": "默认先使用本地 Mock 数据，保留切换真实数据库的边界。",
            }
        )
        assumptions.append("默认先使用本地 Mock 数据。")

    if "验收" not in normalized:
        questions.append(
            {
                "id": "acceptance_priority",
                "question": "验收时最关注页面可用性、数据正确性，还是权限流程？",
                "default_assumption": "默认以主流程可运行、页面可操作、数据可验证作为验收重点。",
            }
        )
        assumptions.append("默认以主流程可运行、页面可操作、数据可验证作为验收重点。")

    return {
        "mode": "live",
        "status": "auto_assumed",
        "questions": questions,
        "assumptions": assumptions,
        "message": "当前最简版不阻塞等待用户输入，先记录问题并使用默认假设继续。",
        "spec_summary": spec.get("app_info", {}).get("name", "未命名应用"),
    }
