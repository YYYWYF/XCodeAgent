"""定义用户验收后的结构化调整类型及其主工作流恢复节点。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


AcceptanceAdjustmentType = Literal[
    "local_fix",
    "page_design_change",
    "endpoint_change",
    "data_source_change",
    "project_plan_change",
]


class AcceptanceAdjustment(BaseModel):
    """校验验收调整载荷，避免用自然语言直接决定 Workflow 路由。"""

    model_config = ConfigDict(extra="forbid")

    type: AcceptanceAdjustmentType
    feedback: str = Field(min_length=1, max_length=4_000)


_ADJUSTMENT_RESUME_NODES: dict[AcceptanceAdjustmentType, str] = {
    "local_fix": "small_task_repair",
    # 历史 page_design_change 不再回到 PageDetail，统一升级为技术规划调整。
    "page_design_change": "project_planning",
    "endpoint_change": "detail_confirmation",
    "data_source_change": "detail_confirmation",
    "project_plan_change": "project_planning",
}


def normalize_acceptance_adjustment(
    value: Any,
    *,
    default_type: AcceptanceAdjustmentType = "project_plan_change",
) -> dict[str, str] | None:
    """把不可信的验收调整输入收敛为稳定的类型和反馈文本。"""

    if value is None:
        return None
    if isinstance(value, str):
        payload: dict[str, Any] = {"type": default_type, "feedback": value}
    elif isinstance(value, dict):
        payload = {
            "type": value.get("type") or value.get("adjustmentType") or default_type,
            "feedback": value.get("feedback") or value.get("message") or "",
        }
    else:
        raise ValueError("acceptance_adjustment 必须是对象或字符串。")

    payload["feedback"] = str(payload.get("feedback") or "").strip()
    try:
        adjustment = AcceptanceAdjustment.model_validate(payload)
    except ValidationError as exc:
        raise ValueError("acceptance_adjustment 的类型或反馈内容无效。") from exc
    return adjustment.model_dump(mode="json")


def acceptance_adjustment_resume_node(adjustment: dict[str, Any] | None) -> str:
    """根据已校验的调整类型返回主 Graph 的安全恢复节点。"""

    if not isinstance(adjustment, dict):
        return _ADJUSTMENT_RESUME_NODES["project_plan_change"]
    adjustment_type = str(adjustment.get("type") or "").strip()
    return _ADJUSTMENT_RESUME_NODES.get(
        adjustment_type,  # type: ignore[arg-type]
        _ADJUSTMENT_RESUME_NODES["project_plan_change"],
    )
