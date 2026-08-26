from __future__ import annotations

import logging
import json
import re
from typing import Any

from langgraph.config import get_stream_writer

from app.agents.main.product_planner import plan_product_with_chat_model
from app.agents.main.document_sync import sync_product_plan_from_markdown
from app.graph.nodes.confirmation import user_confirmed_text
from app.graph.nodes.requirements import prepare_requirement_spec_confirmation
from app.graph.state import ProjectState
from app.services.data_source_policy import datasource_type_from_artifact
from app.services.model_transport_retry import run_with_transport_retry
from app.services.product_plan import (
    PRODUCT_PLAN_SCHEMA_VERSION,
    authorization_operation_action_coverage,
    authorization_operation_action_coverage_errors,
    create_product_plan,
    requirement_spec_sha256,
    validate_product_plan,
)
from app.tools.ask_user import AskUserQuestion, build_ask_user_payload
from app.workspace.product_plan_documents import (
    confirmed_product_plan_json_path,
    confirmed_product_plan_markdown_path,
    edited_product_plan_markdown,
    product_plan_draft_json_path,
    product_plan_draft_markdown_path,
    write_confirmed_product_plan_documents,
    write_product_plan_documents,
)
from app.workspace.spec_documents import (
    confirmed_requirement_spec_json_path,
    confirmed_requirement_spec_markdown_path,
    requirement_spec_draft_json_path,
    requirement_spec_draft_markdown_path,
    write_confirmed_requirement_spec_document,
)


logger = logging.getLogger("uvicorn.error")
_PRODUCT_PLAN_GENERATION_ATTEMPTS = 3


class ProductPlanOperationCoverageError(ValueError):
    """表示模型重试后仍缺少可确定绑定的受限业务操作。"""

    def __init__(self, candidate: dict[str, Any], coverage: list[dict[str, Any]]) -> None:
        """保存最后一个候选及其待用户消歧的操作规则。"""

        self.candidate = candidate
        self.coverage = coverage
        names = "、".join(str(item.get("name") or "") for item in coverage)
        super().__init__(f"受限操作无法唯一映射到产品 action：{names}")


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
                header="需求文档确认",
                question=(
                    "需求分析与产品规划已合并为一份需求文档。请确认应用信息、用户角色、功能模块、"
                    "页面目标、业务信息、核心操作、页面跳转和产品验收标准。"
                    "正确时回复“确认需求文档，继续”；需要调整时直接写出修改意见。"
                ),
                type="text",
                placeholder="例如：确认需求文档，继续 / 订单列表需要增加批量导出操作。",
            )
        ]
    )
    payload["mode"] = "requirement_document_confirmation"
    payload["message"] = "请确认需求文档（含产品规划）后再进入 UI 设计。"
    payload["plan_summary"] = plan.get("app", {}).get("name", "未命名应用")
    return payload


def _confirmed_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """构造 ProductPlan 已确认的清理载荷。"""

    return {
        "mode": "requirement_document_confirmation",
        "status": "clear",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": [],
        "assumptions": [],
        "message": "需求文档已确认，可以进入 UI 设计。",
        "plan_summary": plan.get("app", {}).get("name", "未命名应用"),
    }


def _validation_retry_feedback(user_feedback: str, errors: list[str]) -> str:
    """把有界校验错误回灌给模型，避免把内部重试转嫁给用户。"""

    diagnostics = "\n".join(f"- {error}" for error in errors[:8])
    repair_instruction = (
        "系统 ProductPlan 一致性校验未通过。请自行修复下列问题并返回完整 ProductPlan；"
        "原始模型 JSON 的根对象只能包含 app、business_flows、pages、product_acceptance_criteria。"
        "authorizationTargets 是服务端在原始 JSON 格式校验通过后确定性生成的内部字段，"
        "即使需求包含权限规则也必须从你的 JSON 中完全删除；"
        "对于每条“缺少受限操作 action”诊断，必须补充一个 name 完全相同的唯一普通产品 action；"
        "对于“受限操作 action 重复”诊断，必须只保留一个同名 action；"
        "对于 pageId 或 actionId 的格式诊断，必须使用 lower_snake_case 重写错误 ID；"
        "lower_snake_case 只能包含小写字母、数字和单个下划线（例如 order_list、order_list_export），"
        "不得使用大写、连字符、空格或中文。pageId 必须原样使用 RequirementSpec 中对应页面的 pageId；"
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
    last_candidate: dict[str, Any] | None = None
    last_coverage: list[dict[str, Any]] = []
    for attempt in range(1, _PRODUCT_PLAN_GENERATION_ATTEMPTS + 1):
        try:
            candidate = run_with_transport_retry(
                lambda: plan_product_with_chat_model(
                    requirement_spec,
                    existing_plan=retry_base,
                    user_feedback=retry_feedback,
                    on_token=_product_planning_token,
                ),
                operation_name="产品规划模型调用",
            )
            last_coverage = authorization_operation_action_coverage(
                requirement_spec,
                candidate,
            )
            last_errors = [
                *authorization_operation_action_coverage_errors(
                    requirement_spec,
                    candidate,
                ),
                *validate_product_plan(candidate, requirement_spec),
            ]
            last_candidate = candidate
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
    generic_operation_error = (
        "ProductPlan.authorizationTargets.operationRules 必须与已确认 restrictedOperations 一一对应。"
    )
    non_coverage_errors = [
        error
        for error in last_errors
        if error != generic_operation_error
        and not error.startswith("缺少受限操作 action：")
        and not error.startswith("受限操作 action 重复：")
    ]
    if last_candidate is not None and last_coverage and not non_coverage_errors:
        raise ProductPlanOperationCoverageError(last_candidate, last_coverage)
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


def _operation_coverage_clarification(
    requirement_spec: dict[str, Any],
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造无法由模型唯一消歧时的产品 action 归属选择题。"""

    pages = [
        item
        for item in requirement_spec.get("pages", [])
        if isinstance(item, dict) and str(item.get("pageId") or "").strip()
    ]
    questions: list[dict[str, Any]] = []
    for item in coverage:
        rule_id = str(item["ruleId"])
        name = str(item["name"])
        candidates = item.get("candidates") if isinstance(item.get("candidates"), list) else []
        if candidates:
            options = [
                {
                    "label": f"{candidate['pageName']} · {candidate['actionName']}",
                    "value": f"action:{candidate['pageId']}:{candidate['actionId']}",
                    "description": "将该权限规则绑定到此现有产品操作。",
                }
                for candidate in candidates
            ]
            question = f"受限操作“{name}”出现了多个同名 action，请选择实际需要受限的操作。"
        else:
            options = [
                {
                    "label": str(page.get("name") or page["pageId"]),
                    "value": f"page:{page['pageId']}",
                    "description": f"在该页面补充“{name}”这一受限操作。",
                }
                for page in pages
            ]
            question = f"需求已确认受限操作“{name}”，但产品规划未包含该操作；请选择它所属的页面。"
        questions.append(
            {
                "id": f"authorization_operation_rule_{rule_id}",
                "header": "受限操作归属",
                "dimension": "权限操作",
                "question": question,
                "type": "choice",
                "multiSelect": False,
                "allowOther": False,
                "options": options,
            }
        )
    return {
        "mode": "authorization_operation_action_resolution",
        "status": "requires_user_input",
        "question_schema": "gemini_cli.ask_user.v1",
        "questions": questions,
        "message": "无法自动确定受限操作对应的产品 action；请完成归属选择后继续。",
    }


def _selected_answer(value: object) -> str:
    """读取选择题的单选值，兼容 AG-UI 的 selected 数组表达。"""

    if isinstance(value, dict):
        selected = value.get("selected")
        if isinstance(selected, list):
            return str(selected[0] if selected else "").strip()
        return str(selected or "").strip()
    return str(value or "").strip()


def _resolve_operation_coverage(
    requirement_spec: dict[str, Any],
    candidate: dict[str, Any],
    coverage: list[dict[str, Any]],
    answers: dict[str, Any],
) -> dict[str, Any]:
    """按用户选择补齐缺失 action 或确定重复 action 的唯一权限绑定。"""

    selected_actions: dict[str, tuple[str, str]] = {}
    plan = create_product_plan(
        requirement_spec,
        agent_plan=candidate,
        existing_plan=candidate,
    )
    for item in coverage:
        rule_id = str(item["ruleId"])
        selection = _selected_answer(answers.get(f"authorization_operation_rule_{rule_id}"))
        if not selection:
            raise ValueError(f"请为受限操作“{item['name']}”选择归属。")
        if selection.startswith("page:"):
            page_id = selection.removeprefix("page:").strip()
            page = next(
                (item for item in plan["pages"] if item.get("pageId") == page_id),
                None,
            )
            if not isinstance(page, dict):
                raise ValueError("受限操作归属页面不存在，请刷新后重试。")
            normalized_rule_id = re.sub(r"[^a-z0-9]+", "_", rule_id.lower()).strip("_")
            action_id = f"authorized_{normalized_rule_id or 'action'}"
            page["actions"].append(
                {
                    "actionId": action_id,
                    "name": str(item["name"]),
                    "description": str(item["description"]),
                    "requiresConfirmation": False,
                    "behavior": {
                        "type": "business",
                        "expectedResult": f"已完成{item['name']}操作。",
                    },
                }
            )
        elif selection.startswith("action:"):
            selected_value = selection.removeprefix("action:").strip()
            page_id, separator, action_id = selected_value.partition(":")
            if not separator or not page_id or not action_id:
                raise ValueError("受限操作归属选择缺少 pageId/actionId，请刷新后重试。")
            selected_actions[rule_id] = (page_id, action_id)
        else:
            raise ValueError("受限操作归属选择无效，请刷新后重试。")

    # 新增 action 后重建服务端派生映射；重复项再以用户选中的 action 覆盖唯一目标。
    plan = create_product_plan(requirement_spec, agent_plan=plan, existing_plan=plan)
    operation_rules = plan["authorizationTargets"]["operationRules"]
    operation_rules.extend(
        {"ruleId": rule_id, "pageId": page_id, "actionId": action_id}
        for rule_id, (page_id, action_id) in selected_actions.items()
    )
    return plan


def _operation_coverage_update(
    state: ProjectState,
    candidate: dict[str, Any],
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    """保存无法自动修复的候选，并以原生 AG-UI 澄清等待用户选择。"""

    markdown_path, json_path = write_product_plan_documents(state, candidate)
    return {
        "phase": "product_planning",
        "status": "requires_user_input",
        "product_plan": candidate,
        "product_plan_path": markdown_path,
        "product_plan_json_path": json_path,
        "clarification": _operation_coverage_clarification(state["requirement_spec"], coverage),
        "timeline": ["product_planning"],
    }


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
    """生成、修订并联合确认 RequirementSpec 与 ProductPlan。"""

    requirement_spec = state.get("requirement_spec")
    if not isinstance(requirement_spec, dict):
        raise ValueError("产品规划必须读取已校验的 RequirementSpec 草稿。")
    interaction = _application_planning_interaction(state)
    application_planning_scope = state.get("workflow_scope") == "application_planning"
    request = _product_planning_request(state, interaction)
    action = str(interaction.get("action") or "")
    existing = state.get("product_plan")
    clarification = state.get("clarification")
    if (
        isinstance(existing, dict)
        and action == "answer"
        and isinstance(clarification, dict)
        and clarification.get("mode") == "authorization_operation_action_resolution"
    ):
        coverage = authorization_operation_action_coverage(requirement_spec, existing)
        answers = interaction.get("answers") if isinstance(interaction.get("answers"), dict) else {}
        resolved = _resolve_operation_coverage(
            requirement_spec,
            existing,
            coverage,
            answers,
        )
        errors = validate_product_plan(resolved, requirement_spec)
        if errors:
            raise ValueError("受限操作归属处理后 ProductPlan 仍未通过校验：" + "；".join(errors))
        return _pending_product_plan_update(state, resolved)
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
            try:
                repaired = _repair_existing_product_plan(
                    requirement_spec,
                    existing,
                    existing_errors,
                )
            except ProductPlanOperationCoverageError as exc:
                return _operation_coverage_update(state, exc.candidate, exc.coverage)
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
        # 联合确认先在内存中准备两份最终候选；任何校验失败都不能写入单独正式产物。
        confirmed_requirement_spec, requirement_markdown = prepare_requirement_spec_confirmation(
            state,
            requirement_spec,
            datasource_type=datasource_type_from_artifact(requirement_spec, fallback="database"),
        )
        edited_markdown = edited_product_plan_markdown(state, existing)
        synchronized = (
            sync_product_plan_from_markdown(existing, confirmed_requirement_spec, edited_markdown)
            if edited_markdown is not None
            else existing
        )
        confirmed = {
            **synchronized,
            "confirmation_status": "confirmed",
            "requirement_spec_sha256": requirement_spec_sha256(confirmed_requirement_spec),
        }
        errors = validate_product_plan(confirmed, confirmed_requirement_spec)
        if errors:
            logger.warning("confirmed_product_plan_validation_errors: %s", errors)
            try:
                repaired = _repair_existing_product_plan(
                    confirmed_requirement_spec,
                    synchronized,
                    errors,
                )
            except ProductPlanOperationCoverageError as exc:
                return _operation_coverage_update(state, exc.candidate, exc.coverage)
            return _pending_product_plan_update(state, repaired)
        # 两份正式文件与草稿在全部校验后才连续提交；写入中断时必须恢复完整状态。
        # 否则“需求已确认、产品未确认”会绕过联合确认门禁。
        artifact_paths = (
            confirmed_requirement_spec_markdown_path(state),
            confirmed_requirement_spec_json_path(state),
            confirmed_product_plan_markdown_path(state),
            confirmed_product_plan_json_path(state),
            requirement_spec_draft_markdown_path(state),
            requirement_spec_draft_json_path(state),
            product_plan_draft_markdown_path(state),
            product_plan_draft_json_path(state),
        )
        artifact_backup = tuple(path.read_bytes() if path.is_file() else None for path in artifact_paths)
        try:
            requirement_path = write_confirmed_requirement_spec_document(
                state,
                confirmed_requirement_spec,
                markdown=requirement_markdown,
            )
            markdown_path, json_path = write_confirmed_product_plan_documents(
                state,
                confirmed,
                markdown=edited_markdown,
            )
            # 写入后立即回读两份 JSON，确保下游只能看到同一轮联合确认的绑定产物。
            persisted_spec = json.loads(confirmed_requirement_spec_json_path(state).read_text(encoding="utf-8"))
            persisted_plan = json.loads(confirmed_product_plan_json_path(state).read_text(encoding="utf-8"))
            if (
                persisted_spec.get("confirmation_status") != "confirmed"
                or persisted_plan.get("confirmation_status") != "confirmed"
                or persisted_plan.get("requirement_spec_sha256")
                != requirement_spec_sha256(persisted_spec)
            ):
                raise ValueError("联合确认产物回读校验失败。")
        except Exception:
            for path, original in zip(artifact_paths, artifact_backup, strict=True):
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(original)
            raise
        return {
            "phase": "product_planning",
            "status": "completed",
            "requirement_spec": confirmed_requirement_spec,
            "requirements_confirmed": True,
            "requirement_spec_path": requirement_path,
            "requirement_spec_json_path": str(confirmed_requirement_spec_json_path(state)),
            "edited_requirement_spec": {},
            "requirement_spec_feedback": "",
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
    try:
        plan = _generate_valid_product_plan(
            requirement_spec,
            existing_plan=existing if isinstance(existing, dict) else None,
            user_feedback=feedback,
        )
    except ProductPlanOperationCoverageError as exc:
        return _operation_coverage_update(state, exc.candidate, exc.coverage)
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
