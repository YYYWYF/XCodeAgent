"""需求文档直接修订复用通用 ChangeSet，需求与产品作为同一内存事务共同校验。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.main.technical_plan_fragment_output import check_output_finish
from app.agents.model_factory import create_chat_model, thinking_disabled_kwargs
from app.agents.model_operations import observe_model_operation
from app.config import Settings
from app.domain.application_planning_recovery import application_planning_sha256
from app.domain.technical_plan_changeset import TechnicalPlanChangeSet
from app.services.technical_plan_changeset import apply_document_changeset, verify_revision_effects
from app.services.requirement_spec import validate_authorization_requirements, validate_requirement_spec_confirmation_readiness
from app.services.requirement_spec_merger import requirement_revision_diff, strip_change_status
from app.services.product_plan import _authorization_targets, requirement_spec_sha256, validate_product_plan
from app.tools.ask_user import AskUserInput, build_ask_user_payload

SPEC_FIELDS = ("app_info", "summary", "user_roles", "feature_modules", "pages", "entities", "business_flows", "authorization_requirements", "authentication_requirements", "acceptance_criteria")
PRODUCT_FIELDS = ("app", "business_flows", "pages", "product_acceptance_criteria")


def requirement_revision_document(spec: dict, product: dict | None) -> dict:
    """提取两份文档的可编辑业务投影，模型不能改元数据、配置、哈希或确认状态。"""

    def clean(value: Any) -> Any:
        """清理展示标记和墓碑，删除记录不作为下一轮可修改目标。"""

        if isinstance(value, dict):
            return {key: clean(child) for key, child in value.items() if key not in {"_change_status", "_app_change_status"}}
        if isinstance(value, list):
            return [clean(child) for child in value if not (isinstance(child, dict) and child.get("_change_status") == "deleted")]
        return deepcopy(value)
    document = {"requirement_spec": {key: clean(spec[key]) for key in SPEC_FIELDS if key in spec}}
    if isinstance(product, dict) and product:
        document["product_plan"] = {key: clean(product[key]) for key in PRODUCT_FIELDS if key in product}
    return document


@observe_model_operation("revise_requirements", business_stage="requirements")
def plan_requirement_changeset(document: dict, request: str, feedback: list[str]) -> TechnicalPlanChangeSet | AskUserInput:
    """原生工具输出只能是完整 ChangeSet 或澄清问题，禁止候选重写与混合部分修改。"""

    model = create_chat_model(Settings.from_env(), extra_model_kwargs=thinking_disabled_kwargs())
    prompt = (
        "把需求文档的全部修改意见翻译为唯一 ChangeSet。只调用一个结构化工具："
        "TechnicalPlanChangeSet（通用变更集）或 AskUserInput（确有歧义时提问）。不要生成代码、"
        "重写整份文档或同时提交问题和部分操作。\n"
        "ChangeSet 固定 schemaVersion=1、baseSha256、intents[{id,sourceText,operations}]。"
        "所有 sourceText 为连续原文，合起来覆盖整次请求；每项有实际修改，不能只 test 或无变化。"
        "op 只能 add/remove/replace/move/test；add=value，remove=expectedBefore，"
        "replace=expectedBefore+value，move=from+expectedBefore，test=value；不要多余字段。\n"
        "path/from 使用稳定身份逻辑路径：对象按键；数组按 id/pageId/actionId/stepId/"
        "endpoint_id/endpointId/itemId/ruleId/resourceKey/name/key，参数用 in.name；禁止数字下标。"
        "无 name/id 的实体字段可使用唯一 label 作为身份；无稳定身份的字符串/顺序数组 replace 整个数组。~ 转义 ~0，/ 转义 ~1。"
        "同一意图的关联操作按顺序执行，expectedBefore 符合当步旧值，不同意图不得互相覆盖。\n"
        "可编辑根为 requirement_spec、product_plan。用户看到的是联合需求文档，若 ProductPlan"
        "已存在，必须把页面信息项、操作、流程与验收要求的关联修改一起放进同一个 ChangeSet，"
        "不要只改需求概述让产品内容仍保留旧字段。所有未涉及子树保持原样。删除、改名要显式"
        "清理全部引用；页面 ID、模块、跳转、角色授权必须闭合。不能依赖旧合并器补回。"
        "首次尚无 ProductPlan 时只改 requirement_spec，随后正常产品规划。\n"
        "application.json 是配置事实源，不能修改应用能力开关或确认状态。权限仅修改业务规则，"
        "不写 authorizationTargets/资源键/配置副本；平台重算派生字段。简体中文说明，"
        "歧义通过 AskUserInput 澄清，回答不是确认，全部完成后仍须需求/产品联合确认。\n"
        f"baseSha256={application_planning_sha256(document)}\n"
        f"用户原始请求：\n{request}\n当前业务文档：\n{json.dumps(document, ensure_ascii=False)}\n"
        f"本轮平台诊断（不能撤销原要求）：\n{json.dumps(feedback, ensure_ascii=False)}"
    )
    response = model.bind_tools([TechnicalPlanChangeSet, AskUserInput], tool_choice="auto").invoke(prompt)
    check_output_finish(response)
    calls = getattr(response, "tool_calls", None) or []
    if len(calls) != 1:
        raise ValueError("需求修订必须提交一个完整 ChangeSet 或一组澄清问题，不能用正文冒充修改。")
    call = calls[0]
    if call["name"] == "TechnicalPlanChangeSet":
        return TechnicalPlanChangeSet.model_validate(call["args"])
    if call["name"] == "AskUserInput":
        return AskUserInput.model_validate(call["args"])
    raise ValueError("需求修订返回了不支持的工具。")


def _requirement_errors(spec: dict) -> list[str]:
    """校验完整需求身份、模块引用和业务结构，拒绝规范化替换被执行的值。"""

    errors = [*validate_requirement_spec_confirmation_readiness(deepcopy(spec)), *validate_authorization_requirements(spec)]
    for field, identity in (("pages", "pageId"), ("user_roles", "id"), ("feature_modules", "id"), ("entities", "id"), ("business_flows", "id")):
        values = spec.get(field)
        if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
            errors.append(f"RequirementSpec.{field} 必须为对象数组。")
            continue
        ids = [row.get(identity) for row in values]
        if any(not isinstance(key, str) or not key.strip() for key in ids):
            errors.append(f"RequirementSpec.{field} 稳定身份不能为空。")
        elif len(ids) != len(set(ids)):
            errors.append(f"RequirementSpec.{field} 稳定身份重复。")
    modules = {row.get("id") for row in spec.get("feature_modules") or [] if isinstance(row, dict)}
    paths = []
    for page in spec.get("pages") or []:
        if isinstance(page, dict):
            if page.get("module_id") not in modules:
                errors.append(f"页面 {page.get('pageId')} 引用了不存在的模块。")
            paths.append(page.get("path"))
    if len(paths) != len(set(paths)):
        errors.append("RequirementSpec 页面路由重复。")
    return errors


def new_iteration_revision_state(
    state: dict,
    existing_spec: dict,
    request: str,
) -> dict | None:
    """识别新迭代首次输入，并恢复上一轮两份正式产物作为联合修订基线。"""

    if (
        state.get("workflow_scope") != "application_planning"
        or state.get("application_planning_interaction")
        or state.get("design_change_submission")
        or state.get("design_change_generation_target")
        or not request.strip()
        or existing_spec.get("confirmation_status") != "confirmed"
        or not state.get("requirement_spec_loaded_from_disk")
    ):
        return None
    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    if not workspace:
        return None
    from app.services.requirement_spec_carryover import read_requirement_spec_carryover

    # carryover 只由显式发起新迭代登记；首次创建即使读到旧文件也不能误入增量事务。
    if read_requirement_spec_carryover(workspace) is None:
        return None
    from app.domain.application_lifecycle import ApplicationLifecycleStage
    from app.services.application_lifecycle import load_application_lifecycle

    lifecycle = load_application_lifecycle(workspace)
    if lifecycle is None or lifecycle.initialization.stage not in {
        ApplicationLifecycleStage.COLLECTING_REQUIREMENT,
        # 真实 Graph 包装器会在业务节点前把收集需求推进为分析中。
        ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
    }:
        return None
    from app.services.product_plan_carryover import read_product_plan_iteration_baseline

    return {
        **state,
        "product_plan": read_product_plan_iteration_baseline(workspace) or {},
        # 上一轮正式产品文件已在发起迭代时清理，本轮只能写新的草稿路径。
        "product_plan_path": "",
        "product_plan_json_path": "",
        "requirement_changeset_context": {},
    }


def generate_requirement_revision(state: dict, existing_spec: dict, request: str) -> dict:
    """执行联合原子修改，澄清保留事务基线，失败不写文件也不进入下游。"""

    context = state.get("requirement_changeset_context") or {}
    continuing = (state.get("application_planning_interaction") or {}).get("action") == "answer" and bool(context)
    spec = deepcopy(context["spec"] if continuing else existing_spec)
    product = deepcopy(context.get("product") if continuing else state.get("product_plan"))
    if not continuing:
        # 修改按钮不等于丢弃用户右侧已编辑内容；先同步成内存 JSON，再冻结本轮事务。
        from app.workspace.spec_documents import edited_requirement_spec_markdown
        from app.workspace.product_plan_documents import edited_product_plan_markdown
        from app.agents.main.document_sync import sync_requirement_spec_from_markdown, sync_product_plan_from_markdown
        from app.services.data_source_policy import datasource_type_from_artifact

        spec_path = str(state.get("requirement_spec_path") or "")
        markdown = edited_requirement_spec_markdown(state, spec) if spec_path and Path(spec_path).is_file() else None
        if markdown is not None:
            spec = sync_requirement_spec_from_markdown(spec, markdown, datasource_type=datasource_type_from_artifact(spec, fallback="database"))
        if product:
            product_markdown = edited_product_plan_markdown(state, product)
            if product_markdown is not None:
                product = sync_product_plan_from_markdown(product, spec, product_markdown)
    request = (context["request"] + "\n" + request) if continuing and context["request"] not in request else request
    document = requirement_revision_document(spec, product)
    feedback: list[str] = []
    for _attempt in range(3):
        try:
            decision = plan_requirement_changeset(document, request, feedback)
            if isinstance(decision, AskUserInput):
                pending = {**deepcopy(spec), "confirmation_status": "pending_user_input"}
                return {
                    "phase": "requirements", "status": "requires_user_input", "requirement_spec": pending,
                    "requirements_confirmed": False,
                    "requirement_changeset_context": {"spec": spec, "product": product, "request": request},
                    "requirement_spec_baseline": spec,
                    "clarification": build_ask_user_payload(decision.questions), "timeline": ["requirements"],
                }
            patched, effects = apply_document_changeset(
                document, decision, request=request, editable_sections=tuple(document),
                forbidden_value_keys={"_change_status", "_app_change_status", "authorizationTargets", "authorization_manifest", "confirmation_status", "sourceConfigRevision", "basedOn"},
            )
            if set(patched) != set(document) or set(patched["requirement_spec"]) - set(SPEC_FIELDS) or (
                "product_plan" in patched and set(patched["product_plan"]) - set(PRODUCT_FIELDS)
            ):
                raise ValueError("需求修订不能修改平台字段或删除整份文档。")
            candidate = {**{key: value for key, value in deepcopy(spec).items() if key not in SPEC_FIELDS},
                         **patched["requirement_spec"], "confirmation_status": "pending_user_confirmation",
                         "clarification_questions": [], "clarification_status": "clear"}
            errors = _requirement_errors(candidate)
            product_candidate = None
            if "product_plan" in patched:
                product_candidate = {**{key: value for key, value in deepcopy(product).items() if key not in PRODUCT_FIELDS},
                                     **patched["product_plan"],
                                     "requirement_spec_sha256": requirement_spec_sha256(candidate),
                                     "confirmation_status": "pending_user_confirmation"}
                product_candidate["authorizationTargets"] = _authorization_targets(candidate, product_candidate["pages"])
                errors.extend(validate_product_plan(product_candidate, candidate))
            if errors:
                feedback = errors[:12]
                continue
            verify_revision_effects(requirement_revision_document(candidate, product_candidate), effects)
            return {
                "requirement_spec": candidate,
                "requirement_spec_baseline": spec,
                "requirement_spec_diff": requirement_revision_diff(spec, candidate),
                "requirement_revision_product_candidate": product_candidate or {},
                "requirement_changeset_context": {},
                "requirements_confirmed": False,
            }
        except (TypeError, ValueError) as exc:
            feedback = [str(exc)]
    raise ValueError("需求修订未完成，原草稿已保留：" + "；".join(feedback))


def write_joint_revision_drafts(state: dict, revision: dict) -> dict:
    """两份草稿同一保存事务，任何写入失败恢复原文件，确认仍由原联合门处理。"""

    from app.workspace.spec_documents import requirement_spec_draft_json_path, requirement_spec_draft_markdown_path, write_requirement_spec_draft_document
    from app.workspace.product_plan_documents import product_plan_draft_json_path, product_plan_draft_markdown_path, write_product_plan_documents
    from app.services.generated_document_inclusion import write_generated_documents
    from app.graph.nodes.product_planning import _inherit_page_change_status
    from app.graph.nodes.requirements import _requirement_spec_draft_payload

    spec = revision["requirement_spec"]
    product = _inherit_page_change_status(revision["requirement_revision_product_candidate"], spec, revision["requirement_spec_diff"])
    paths = (requirement_spec_draft_json_path(state), requirement_spec_draft_markdown_path(state),
             product_plan_draft_json_path(state), product_plan_draft_markdown_path(state))
    backups = [path.read_bytes() if path.is_file() else None for path in paths]
    try:
        spec_path = write_generated_documents(state, [paths[1]], write_requirement_spec_draft_document, state, spec)
        product_path, product_json = write_generated_documents(state, [paths[3]], write_product_plan_documents, state, product)
    except Exception:
        for path, content in zip(paths, backups):
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    return {**revision, "phase": "requirements", "status": "requires_user_input",
            "requirements_clarification_round": 0, "product_plan": product,
            "product_plan_path": product_path, "product_plan_json_path": product_json,
            "requirement_revision_product_candidate": product,
            "requirement_spec_path": spec_path, "requirement_spec_json_path": str(paths[0]),
            "clarification": _requirement_spec_draft_payload(spec), "timeline": ["requirements"]}
