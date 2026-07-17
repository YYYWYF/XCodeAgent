from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    ProgressReporter,
    TextDeltaReporter,
    build_ag_ui_action_stream,
)
from app.services.application_development_planning import (
    ConfirmDevelopmentPlanRequest,
    GenerateDevelopmentPlanRequest,
    confirm_application_development_plan,
    generate_application_development_plan,
)


DEVELOPMENT_PLANNING_EVENT_NAME = "application-development-planning"


def application_development_planning_capabilities() -> dict[str, Any]:
    """发布工作台开发计划端点的 AG-UI 能力。"""

    return {
        "name": "application-development-planning",
        "endpoint": "/application-development-planning/run",
        "transport": "ag-ui-sse",
        "actions": ["plan", "confirm"],
        "progressEvents": True,
        "textMessageStreaming": True,
        "customEventName": DEVELOPMENT_PLANNING_EVENT_NAME,
        "stateSnapshotKey": "developmentPlanning",
        "workflowIndependent": True,
    }


def build_application_development_planning_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """把开发计划生成和确认投射为完整 AG-UI 生命周期。"""

    planning_input = _development_planning_input(payload)

    async def operation(
        report: ProgressReporter,
        report_text: TextDeltaReporter,
    ) -> AgUiActionResult:
        """执行规划动作并持续报告加载阶段与模型文本。"""

        action = planning_input.get("action")
        if action == "plan":
            await report(AgUiActionProgress(
                stage="reading_application",
                message="正在读取 application.json 的页面、功能与 API 关系…",
                detail="只提取当前任务拆分需要的应用配置，避免加载无关代码和历史记录。",
                percent=12,
                data={"action": action},
            ))
            request = GenerateDevelopmentPlanRequest.model_validate(planning_input)
            await report(AgUiActionProgress(
                stage="identifying_shared_modules",
                message="正在确认已有基础能力与业务开发边界…",
                detail="复用现有路由、API 调用、导航和布局能力，只规划菜单业务功能。",
                percent=34,
                data={"action": action},
            ))
            await report(AgUiActionProgress(
                stage="planning_dependencies",
                message="正在拆分页面任务并编排依赖与阻塞关系…",
                detail="确保每个菜单功能都有任务、验收条件和明确的前置工作。",
                percent=56,
                data={"action": action},
            ))
            response = await generate_application_development_plan(request, report_text)
            await report(AgUiActionProgress(
                stage="validating_plan",
                message="正在校验菜单覆盖、任务引用和执行顺序…",
                detail="检查全部菜单项、编号任务、验收项及全局任务 id 是否一致。",
                percent=92,
                data={"action": action},
            ))
            result = response.model_dump(by_alias=True, exclude_none=True)
            message = "需要补充少量信息后再生成计划。" if response.questions else "应用开发计划已生成，请确认。"
        elif action == "confirm":
            await report(AgUiActionProgress(
                stage="persisting_plan",
                message="正在把已确认任务写入 application.json…",
                detail="逐项更新 menus，并保留现有页面、交互和 API 设计。",
                percent=65,
                data={"action": action},
            ))
            response = confirm_application_development_plan(
                ConfirmDevelopmentPlanRequest.model_validate(planning_input)
            )
            result = {"confirmation": response.model_dump(by_alias=True)}
            message = "开发计划已确认并写入每个菜单项。"
        else:
            raise ValueError("developmentPlanning.action 必须是 plan 或 confirm。")
        return AgUiActionResult(data={"action": action, **result}, message=message)

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=DEVELOPMENT_PLANNING_EVENT_NAME,
        state_key="developmentPlanning",
        run_id_prefix="development-planning",
        streaming_operation=operation,
        error_message_prefix="应用开发计划失败",
        accept=accept,
    )


def _development_planning_input(payload: dict[str, Any]) -> dict[str, Any]:
    """从 AG-UI forwardedProps 中读取独立开发计划输入。"""

    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    value = forwarded_props.get("developmentPlanning")
    return value if isinstance(value, dict) else {}
