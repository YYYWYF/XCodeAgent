"""首次创建和正式修订停止后的新意见复用原节点生成与确认，不消费旧审阅权限。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InitialPlanningFeedback(BaseModel):
    """仅接收补充文本和已有对话目标，不接受客户端指定 Graph 恢复位置。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    request: str = Field(min_length=1, max_length=20000)
    conversation_target: Literal["requirements", "ui_confirmation", "technical_planning"] | None = Field(
        default=None, alias="conversationTarget",
    )


_STAGE_NODES = {
    "collecting_requirement": "requirements",
    "analyzing_requirement": "requirements",
    "awaiting_requirement_clarification": "requirements",
    "generating_requirement_document": "requirements",
    "awaiting_requirement_document_confirmation": "requirements",
    "generating_ui_designs": "ui_confirmation",
    "awaiting_ui_design_confirmation": "ui_confirmation",
    "generating_technical_plan": "technical_planning_begin",
    "awaiting_technical_plan_confirmation": "technical_planning_begin",
}


def initial_feedback_node(lifecycle, feedback: InitialPlanningFeedback) -> str:
    """由已结束的阶段选择生成节点，正式修订只允许继续当前已批准的产物。"""
    archived = lifecycle.ended_planning_execution if lifecycle else None
    node = _STAGE_NODES.get(archived.initialization.stage if archived else "")
    if node is None:
        raise ValueError("当前停止阶段不支持补充生成，请使用当前阶段的操作入口。")
    target = feedback.conversation_target
    if archived.formal_revision is not None:
        # 正式修订必须保留已确认影响的起点；产品规划意见不能扩大为需求范围变更。
        artifact = archived.formal_revision.current_artifact
        if node == "requirements" and artifact == "product-plan":
            node = "product_planning"
        if target is not None and target != ("technical_planning" if node == "technical_planning_begin" else node):
            raise ValueError("停止后的补充仅修改本阶段；上游变更请返回开发阶段重新确认修改影响。")
    if node == "technical_planning_begin":
        if target not in {None, "technical_planning"}:
            raise ValueError("上游修改须先交接到需求会话，请使用对应阶段的输入入口。")
    elif target == "technical_planning":
        raise ValueError("请先完成需求和 UI 确认，再进入技术规划。")
    elif target == "requirements":
        node = "requirements"
    # 需求尚未联合确认时，即使指定 UI，也必须先补齐并确认需求。
    return node


def apply_initial_planning_feedback(state: dict, node: str, feedback: InitialPlanningFeedback) -> None:
    """合并原业务要求与新意见，保留当前磁盘基线并创建需要重新确认的生成事务。"""
    from app.graph.application_planning_revision import (
        begin_current_artifact_revision, cleared_design_change_context,
    )

    original = str(state.get("request") or "").strip()
    request = f"{original}\n\n本轮补充或修改意见：\n{feedback.request}" if original else feedback.request
    state.update(cleared_design_change_context())
    state.update(request=request, clarification={}, application_planning_interaction={})
    if node == "technical_planning_begin":
        from app.services.technical_planning_retry import prepare_technical_planning_retry

        # 首次生成可能尚无完整计划；原 begin/generate/commit 链支持可选基线。
        state.update(prepare_technical_planning_retry(state, new_request=request, allow_initial_generation=True))
    else:
        state.update(begin_current_artifact_revision(state, node_name=node, request=request))
        # 展示仅使用用户本轮意见，完整原需求仍作为生成输入保留，避免聊天进度展开配置全文。
        state["design_change_reason"] = feedback.request
        if node == "ui_confirmation":
            # 即使只生成了部分页面，也把补充意见交给原 UI 增量/首次生成分支。
            state["design_conversation_target"] = "ui_confirmation"
            # 产品事实来自已确认上游；页面调整只消费本次视觉意见，避免原始创建指令干扰 UI 分类。
            state["design_change_request"] = feedback.request
            state["design_change_generation_request"] = feedback.request
