from __future__ import annotations

import logging
from typing import Any

from langgraph.config import get_stream_writer

from app.agents.main.product_planner import plan_product_with_chat_model
from app.agents.main.document_sync import sync_product_plan_from_markdown
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.state import ProjectState
from app.services.product_plan import (
    PRODUCT_PLAN_SCHEMA_VERSION,
    create_product_plan,
    validate_product_plan,
)
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.product_plan_documents import (
    edited_product_plan_markdown,
    write_confirmed_product_plan_documents,
    write_product_plan_documents,
)


logger = logging.getLogger("uvicorn.error")
_PRODUCT_PLAN_GENERATION_ATTEMPTS = 3


def _product_planning_token(token: str) -> None:
    """把产品规划模型的流式文本投射到 AG-UI。"""

    try:
        writer = get_stream_writer()
    except (KeyError, RuntimeError):
        return
    writer({"type": "llm.token", "token": token, "node": "product_planning"})


def _has_explicit_submission(state: ProjectState) -> bool:
    """确保创建流程只消费原生中断恢复产生的产品交互。"""

    return state.get("workflow_scope") != "application_planning" or bool(
        state.get("application_planning_interaction")
    )


def _user_confirmed(request: str) -> bool:
    """识别产品对 ProductPlan 的明确确认。"""

    return user_confirmed_text(
        request,
        positive_signals=("正确", "确认产品规划", "产品规划没问题", "继续", "无误"),
        negative_signals=("修改", "调整", "补充", "不对", "不正确", "重新生成"),
    )


def _application_planning_interaction(state: ProjectState) -> dict[str, Any]:
    """读取当前创建规划的结构化 ProductPlan 动作。"""

    value = state.get("application_planning_interaction")
    return value if isinstance(value, dict) and value else {}


def _product_planning_request(state: ProjectState, interaction: dict[str, Any]) -> str:
    """选择 ProductPlan 的结构化请求，避免创建流程重新解释中文确认词。"""

    if state.get("workflow_scope") == "application_planning" and interaction:
        return str(interaction.get("request") or "").strip()
    return str(state.get("request") or "")


def _confirmation_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """构造 ProductPlan 产品确认交互。"""

    payload = build_ask_user_payload(
        [
            AskUserQuestion(
                header="产品规划确认",
                question=(
                    "请由产品角色确认页面目标、业务信息、核心操作、页面跳转和产品验收标准。"
                    "正确时回复“确认产品规划，继续”；需要调整时直接写出产品修改意见。"
                ),
                type="text",
                placeholder="例如：确认产品规划，继续 / 订单列表需要增加批量导出操作。",
            )
        ]
    )
    payload["mode"] = "product_plan_confirmation"
    payload["message"] = "请由产品角色确认产品规划后再进入 UI 设计。"
    payload["plan_summary"] = plan.get("app", {}).get("name", "未命名应用")
    return payload


def _confirmed_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """构造 ProductPlan 已确认的清理载荷。"""

    return {
        "mode": "product_plan_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "产品规划已确认，可以进入 UI 设计。",
        "plan_summary": plan.get("app", {}).get("name", "未命名应用"),
    }


def _validation_retry_feedback(user_feedback: str, errors: list[str]) -> str:
    """把有界校验错误回灌给模型，避免把内部重试转嫁给用户。"""

    diagnostics = "\n".join(f"- {error}" for error in errors[:8])
    repair_instruction = (
        "系统 ProductPlan 一致性校验未通过。请自行修复下列问题并返回完整 ProductPlan；"
        "不要要求用户重试，也不要解释校验过程：\n"
        f"{diagnostics}"
    )
    return f"{user_feedback}\n\n{repair_instruction}" if user_feedback else repair_instruction


def _generate_valid_product_plan(
    requirement_spec: dict[str, Any],
    *,
    existing_plan: dict[str, Any] | None,
    user_feedback: str,
) -> dict[str, Any]:
    """执行生成、校验、反馈修复的有界循环，只向下游返回合格计划。"""

    retry_feedback = user_feedback
    retry_base = existing_plan
    last_errors: list[str] = []
    for attempt in range(1, _PRODUCT_PLAN_GENERATION_ATTEMPTS + 1):
        try:
            candidate = plan_product_with_chat_model(
                requirement_spec,
                existing_plan=retry_base,
                user_feedback=retry_feedback,
                on_token=_product_planning_token,
            )
            last_errors = validate_product_plan(candidate, requirement_spec)
        except ValueError as exc:
            candidate = None
            last_errors = [str(exc)]
        if not last_errors and candidate is not None:
            return candidate
        logger.warning(
            "product_plan_validation_retry: attempt=%s/%s errors=%s",
            attempt,
            _PRODUCT_PLAN_GENERATION_ATTEMPTS,
            last_errors,
        )
        if candidate is not None:
            retry_base = candidate
        retry_feedback = _validation_retry_feedback(user_feedback, last_errors)
    raise ValueError(
        "ProductPlan 自动修复达到上限后仍未通过一致性校验：" + "；".join(last_errors)
    )


def _repair_existing_product_plan(
    requirement_spec: dict[str, Any],
    existing_plan: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    """先确定性重建旧计划，仍不合格时再进入模型自修复循环。"""

    canonical = create_product_plan(
        requirement_spec,
        agent_plan=existing_plan,
        existing_plan=existing_plan,
    )
    remaining_errors = validate_product_plan(canonical, requirement_spec)
    if not remaining_errors:
        return canonical
    return _generate_valid_product_plan(
        requirement_spec,
        existing_plan=canonical,
        user_feedback=_validation_retry_feedback("", errors),
    )


def _pending_product_plan_update(
    state: ProjectState,
    plan: dict[str, Any],
) -> dict[str, Any]:
    """保存自动修复后的新版本，并保持正式产物必须重新确认的门禁。"""

    pending = {**plan, "confirmation_status": "pending_user_confirmation"}
    markdown_path, json_path = write_product_plan_documents(state, pending)
    return {
        "phase": "product_planning",
        "status": "requires_user_input",
        "product_plan": pending,
        "product_plan_path": markdown_path,
        "product_plan_json_path": json_path,
        "clarification": _confirmation_payload(pending),
        "timeline": ["product_planning"],
    }


def product_planning(state: ProjectState) -> dict[str, Any]:
    """生成、修订并确认独立 ProductPlan。"""

    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        raise ValueError("产品规划必须读取已确认的 RequirementSpec。")
    interaction = _application_planning_interaction(state)
    application_planning_scope = state.get("workflow_scope") == "application_planning"
    request = _product_planning_request(state, interaction)
    action = str(interaction.get("action") or "")
    existing = state.get("product_plan")
    if isinstance(existing, dict):
        if (
            existing.get("schema_version") != PRODUCT_PLAN_SCHEMA_VERSION
            or "frontend_pages" in existing
        ):
            raise ValueError(
                f"当前流程只接受 {PRODUCT_PLAN_SCHEMA_VERSION}，不读取或迁移历史 ProductPlan。"
            )
        existing_errors = validate_product_plan(existing, requirement_spec)
        if existing_errors:
            logger.warning("existing_product_plan_validation_errors: %s", existing_errors)
            repaired = _repair_existing_product_plan(
                requirement_spec,
                existing,
                existing_errors,
            )
            return _pending_product_plan_update(state, repaired)
    if (
        isinstance(existing, dict)
        and existing.get("confirmation_status") == "pending_user_confirmation"
        and not _has_explicit_submission(state)
    ):
        markdown_path, json_path = write_product_plan_documents(state, existing)
        return {
            "phase": "product_planning",
            "status": "requires_user_input",
            "product_plan": existing,
            "product_plan_path": markdown_path,
            "product_plan_json_path": json_path,
            "clarification": _confirmation_payload(existing),
            "timeline": ["product_planning"],
        }

    if isinstance(existing, dict) and (
        action == "confirm"
        if application_planning_scope
        else _user_confirmed(request)
    ):
        edited_markdown = edited_product_plan_markdown(state, existing)
        synchronized = (
            sync_product_plan_from_markdown(existing, requirement_spec, edited_markdown)
            if edited_markdown is not None
            else existing
        )
        confirmed = {**synchronized, "confirmation_status": "confirmed"}
        errors = validate_product_plan(confirmed, requirement_spec)
        if errors:
            logger.warning("confirmed_product_plan_validation_errors: %s", errors)
            repaired = _repair_existing_product_plan(
                requirement_spec,
                synchronized,
                errors,
            )
            return _pending_product_plan_update(state, repaired)
        markdown_path, json_path = write_confirmed_product_plan_documents(
            state,
            confirmed,
            markdown=edited_markdown,
        )
        return {
            "phase": "product_planning",
            "status": "completed",
            "product_plan": confirmed,
            "product_plan_path": markdown_path,
            "product_plan_json_path": json_path,
            "clarification": _confirmed_payload(confirmed),
            "timeline": ["product_planning"],
        }

    feedback = (
        request
        if isinstance(existing, dict)
        and (
            action == "revise" or not interaction
            if application_planning_scope
            else bool(request)
        )
        else ""
    )
    plan = _generate_valid_product_plan(
        requirement_spec,
        existing_plan=existing if isinstance(existing, dict) else None,
        user_feedback=feedback,
    )
    markdown_path, json_path = write_product_plan_documents(state, plan)
    return {
        "phase": "product_planning",
        "status": "requires_user_input",
        "product_plan": plan,
        "product_plan_path": markdown_path,
        "product_plan_json_path": json_path,
        "clarification": _confirmation_payload(plan),
        "timeline": ["product_planning"],
    }
