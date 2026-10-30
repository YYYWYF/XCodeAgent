"""统一技术规划生成和自动修复中的原始修改要求与系统反馈。"""

from typing import Any


def technical_plan_feedback_prompt(requirement_spec: dict[str, Any]) -> str:
    """分别投射持续有效的用户原始要求和仅属于本轮的系统校验反馈。"""

    request = str(requirement_spec.get("planning_adjustment_request") or "").strip()
    feedback = str(requirement_spec.get("technical_plan_validation_feedback") or "").strip()
    return (
        "修订约束：用户原始修改要求在每轮生成和自动修复中持续有效。"
        "系统校验反馈只补充需要修复的问题，不能替代、撤销或覆盖用户要求。"
        "在本轮允许修改的范围内落实并保留原始要求，同时遵守已确认上游、"
        "application.json 和当前修复范围；不得为消除校验错误回退已落实的用户修改。\n"
        f"planning_adjustment_request（用户原始修改要求）：\n{request}\n\n"
        f"technical_plan_validation_feedback（本轮系统校验反馈）：\n{feedback}\n\n"
    )
