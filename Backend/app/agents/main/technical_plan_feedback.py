"""统一技术规划生成和自动修复中的原始修改要求与系统反馈。"""

import json
from typing import Any


def technical_plan_feedback_prompt(requirement_spec: dict[str, Any]) -> str:
    """分别投射持续有效的用户原始要求和仅属于本轮的系统校验反馈。"""

    request = str(requirement_spec.get("planning_adjustment_request") or "").strip()
    feedback = str(requirement_spec.get("technical_plan_validation_feedback") or "").strip()
    scope = requirement_spec.get("technical_plan_revision_scope")
    scope_prompt = (
        "平台已精确匹配本次接口修改范围：\n"
        + json.dumps(scope, ensure_ascii=False)
        + "\n只修改上述接口，保持方法、路径和稳定标识；未点名接口、实体和页面继承基线。"
        "必要请求/响应类型可局部修改；共享类型被非目标接口引用时，为目标接口拆分独立类型，"
        "不能连带改写其他接口。明确点名的类型按该类型处理。生成后仍需完整校验和用户确认。\n"
        if isinstance(scope, dict) else ""
    )
    return (
        scope_prompt
        + "修订约束：用户原始修改要求在每轮生成和自动修复中持续有效。"
        "系统校验反馈只补充需要修复的问题，不能替代、撤销或覆盖用户要求。"
        "在本轮允许修改的范围内落实并保留原始要求，同时遵守已确认上游、"
        "application.json 和当前修复范围；不得为消除校验错误回退已落实的用户修改。\n"
        f"planning_adjustment_request（用户原始修改要求）：\n{request}\n\n"
        f"technical_plan_validation_feedback（本轮系统校验反馈）：\n{feedback}\n\n"
    )
