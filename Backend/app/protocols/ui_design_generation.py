"""UI 单页停止使用独立 AG-UI 动作，成功只表示真实任务已退出。"""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from app.protocols.ag_ui_action_stream import AgUiActionProgress, AgUiActionResult, build_ag_ui_action_stream
from app.services.ui_design_generation_pool import get_ui_design_generation_pool


class UiDesignGenerationInput(BaseModel):
    """停止请求必须绑定当前工作区、页面及服务端生成代次。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["cancel"]
    workspaceRoot: str = Field(min_length=1, max_length=4096)
    pageId: str = Field(min_length=1, max_length=256)
    generationId: str = Field(min_length=1, max_length=128)


def ui_design_generation_capabilities() -> dict[str, Any]:
    """发布独立停止动作和真实退出回执约定。"""
    return {"endpoint": "/ui-design-generation/run", "transport": "ag-ui-sse",
        "actionField": "forwardedProps.uiDesignGeneration", "actions": ["cancel"],
        "customEventName": "ui-design-generation", "stateSnapshotKey": "uiDesignGeneration",
        "requiresGenerationId": True, "successMeansWorkerExited": True}


def build_ui_design_generation_stream(*, payload: dict[str, Any], accept: str | None = None):
    """沿标准生命周期报告正在停止、成功或失败，不进入主规划 Graph。"""
    async def operation(report):
        """校验代次后等待 writer 完整退出，超时不会签发停止成功。"""
        forwarded = payload.get("forwardedProps") or {}
        request = UiDesignGenerationInput.model_validate(forwarded.get("uiDesignGeneration"))
        await report(AgUiActionProgress(stage="stopping", message="正在等待设计稿生成退出…", percent=0,
            data={"pageId": request.pageId, "generationId": request.generationId}))
        cancelled = await get_ui_design_generation_pool().cancel_page(
            request.workspaceRoot, request.pageId, generation_id=request.generationId)
        if not cancelled:
            raise ValueError("该次生成已结束，请刷新当前设计稿状态。")
        return AgUiActionResult(data={"pageId": request.pageId,
            "generationId": request.generationId, "cancelled": True}, message="设计稿生成已停止。")

    return build_ag_ui_action_stream(payload=payload, event_name="ui-design-generation",
        state_key="uiDesignGeneration", run_id_prefix="ui-design-stop", progress_operation=operation,
        error_message_prefix="停止设计稿生成失败", accept=accept, register_workspace_run=False)
