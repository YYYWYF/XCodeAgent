from __future__ import annotations

from typing import Any, AsyncIterator

from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
    AgUiActionResult,
    ProgressReporter,
    TextDeltaReporter,
    build_ag_ui_action_stream,
)

from app.services.application_page_planning import (
    ConfirmPagePlanRequest,
    GeneratePagePlanRequest,
    PagePlanningQuestionsRequest,
    confirm_application_page_plan,
    generate_application_page_plan,
    generate_page_planning_questions,
)


PAGE_PLANNING_EVENT_NAME = "application-page-planning"


def application_page_planning_capabilities() -> dict[str, Any]:
    """发布独立页面规划端点及其分阶段进度能力。"""

    return {
        "name": "application-page-planning",
        "endpoint": "/application-page-planning/run",
        "transport": "ag-ui-sse",
        "actions": ["questions", "plan", "confirm"],
        "progressEvents": True,
        "textMessageStreaming": True,
        "customEventName": PAGE_PLANNING_EVENT_NAME,
        "stateSnapshotKey": "pagePlanning",
        "workflowIndependent": True,
    }


def build_application_page_planning_ag_ui_stream(
    *, payload: dict[str, Any], accept: str | None = None
) -> AsyncIterator[str]:
    """把提问、设计和确认动作投射为完整 AG-UI 生命周期。"""

    planning_input = _page_planning_input(payload)

    async def operation(
        report: ProgressReporter,
        report_text: TextDeltaReporter,
    ) -> AgUiActionResult:
        """执行业务动作，并通过同一 AG-UI 运行持续报告可读阶段。"""

        action = planning_input.get("action")
        if action == "questions":
            await report(
                AgUiActionProgress(
                    stage="analyzing_context",
                    message="正在分析应用场景和可能影响页面目录的业务边界…",
                    detail="识别目标用户、核心任务、数据范围和权限边界。",
                    percent=15,
                    data={"action": action},
                )
            )
            request = PagePlanningQuestionsRequest.model_validate(planning_input)
            await report(
                AgUiActionProgress(
                    stage="generating_questions",
                    message="正在整理需要用户补充的关键页面规划问题…",
                    detail="只保留会实际影响页面划分或 API 职责的问题。",
                    percent=45,
                    data={"action": action},
                )
            )
            response = await generate_page_planning_questions(request, report_text)
            await report(
                AgUiActionProgress(
                    stage="validating_questions",
                    message="正在校验问题是否聚焦页面、角色、流程和权限边界…",
                    detail="去除重复、实现细节和不影响信息架构的问题。",
                    percent=90,
                    data={"action": action},
                )
            )
            result: dict[str, Any] = {
                "questions": response.model_dump(by_alias=True)["questions"]
            }
            message = "页面规划细节问题已生成。"
        elif action == "plan":
            revision = bool(planning_input.get("currentPlan"))
            await report(
                AgUiActionProgress(
                    stage="analyzing_requirements",
                    message=(
                        "正在理解修改意见并重新梳理业务流程…"
                        if revision
                        else "正在综合应用信息和补充回答，梳理核心业务流程…"
                    ),
                    detail="综合应用场景、用户回答以及本轮修改意见。",
                    percent=10,
                    data={"action": action},
                )
            )
            request = GeneratePagePlanRequest.model_validate(planning_input)
            await report(
                AgUiActionProgress(
                    stage="designing_pages",
                    message="正在规划页面目录、页面职责和页面之间的关联…",
                    detail="确定页面层级、路由、核心功能与跨页面跳转关系。",
                    percent=30,
                    data={"action": action},
                )
            )
            await report(
                AgUiActionProgress(
                    stage="designing_interactions_and_apis",
                    message="正在设计各页面的用户交互流程及所需 API 功能契约…",
                    detail="补充触发动作、系统反馈、API 方法、请求与响应语义。",
                    percent=50,
                    data={"action": action},
                )
            )
            response = await generate_application_page_plan(request, report_text)
            await report(
                AgUiActionProgress(
                    stage="validating_plan",
                    message="正在校验页面、交互与 API 引用关系，清理无效关联…",
                    detail="检查页面 id、API id、跳转目标和使用方引用是否一致。",
                    percent=88,
                    data={"action": action},
                )
            )
            result = {"plan": response.model_dump(by_alias=True)["plan"]}
            message = "页面与 API 设计方案已生成，请审核。"
        elif action == "confirm":
            await report(
                AgUiActionProgress(
                    stage="validating_confirmation",
                    message="正在校验已确认的页面目录和 API 设计方案…",
                    detail="确认写入内容来自当前用户审核版本且结构完整。",
                    percent=25,
                    data={"action": action},
                )
            )
            request = ConfirmPagePlanRequest.model_validate(planning_input)
            await report(
                AgUiActionProgress(
                    stage="persisting_application",
                    message="正在将 menus 和 apis 原子写入 application.json…",
                    detail="保留既有应用配置，并以临时文件替换避免部分写入。",
                    percent=65,
                    data={"action": action},
                )
            )
            response = confirm_application_page_plan(request)
            result = {
                "confirmation": response.model_dump(
                    by_alias=True, exclude_none=True
                )
            }
            message = "页面与 API 设计方案已确认并写入 application.json。"
        else:
            raise ValueError("pagePlanning.action 必须是 questions、plan 或 confirm。")
        return AgUiActionResult(data={"action": action, **result}, message=message)

    return build_ag_ui_action_stream(
        payload=payload,
        event_name=PAGE_PLANNING_EVENT_NAME,
        state_key="pagePlanning",
        run_id_prefix="page-planning",
        streaming_operation=operation,
        error_message_prefix="页面规划失败",
        accept=accept,
    )


def _page_planning_input(payload: dict[str, Any]) -> dict[str, Any]:
    forwarded_props = payload.get("forwardedProps")
    if not isinstance(forwarded_props, dict):
        return {}
    page_planning = forwarded_props.get("pagePlanning")
    return page_planning if isinstance(page_planning, dict) else {}
